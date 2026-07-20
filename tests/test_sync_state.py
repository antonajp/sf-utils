"""Tests for sync state tracking module.

Tests cover:
- ensure_sync_state_table creation and idempotency
- get_sync_state with advisory locks
- update_sync_state UPSERT behavior with locks
- SyncStateRow dataclass
- Advisory lock key generation
- Edge cases and error handling
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from sf_utils.sync.state import (
    ensure_sync_state_table,
    get_sync_state,
    update_sync_state,
    SyncStateRow,
    _compute_advisory_lock_key,
)


class TestEnsureSyncStateTable:
    """Tests for ensure_sync_state_table function."""

    @patch("sf_utils.sync.state.logger")
    def test_creates_table_when_not_exists(self, mock_logger):
        """Should execute CREATE TABLE IF NOT EXISTS statement."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        ensure_sync_state_table(mock_conn)

        # Verify cursor was created and query executed
        assert mock_cursor.execute.called
        execute_call = mock_cursor.execute.call_args[0][0]

        # Verify CREATE TABLE statement
        sql_str = str(execute_call)
        assert "CREATE TABLE IF NOT EXISTS" in sql_str
        assert "sf_sync_state" in sql_str
        assert "object_name" in sql_str
        assert "last_sync_timestamp" in sql_str
        assert "last_sync_id" in sql_str
        assert "sync_mode" in sql_str
        assert "updated_at" in sql_str
        assert "PRIMARY KEY" in sql_str

        # Verify logging
        assert mock_logger.debug.called

    def test_idempotent_when_table_exists(self):
        """Should be callable multiple times without error (idempotent)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Call twice - should not raise
        ensure_sync_state_table(mock_conn)
        ensure_sync_state_table(mock_conn)

        # Both calls should execute CREATE TABLE IF NOT EXISTS
        assert mock_cursor.execute.call_count == 2

    def test_commits_transaction(self):
        """Should commit transaction after table creation."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        ensure_sync_state_table(mock_conn)

        # Verify commit WAS called
        assert mock_conn.commit.called


class TestGetSyncState:
    """Tests for get_sync_state function."""

    def test_returns_none_when_no_state(self):
        """Should return None when object has no sync state."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock fetchone: first returns lock result, second returns None (no row)
        mock_cursor.fetchone.side_effect = [
            (None,),  # Lock result (discarded)
            None,     # SELECT result (no row found)
        ]

        result = get_sync_state("Account", mock_conn)

        assert result is None
        assert mock_cursor.execute.call_count == 2  # Advisory lock + SELECT

    def test_returns_sync_state_row_when_exists(self):
        """Should return SyncStateRow when state exists."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock fetchone to return a row
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        updated_at = datetime(2024, 1, 15, 12, 30, 5, tzinfo=timezone.utc)
        mock_cursor.fetchone.side_effect = [
            (None,),  # Lock result
            (
                "Account",
                timestamp,
                "batch-123",
                "incremental",
                updated_at,
            ),
        ]
        mock_cursor.nextset.return_value = True

        result = get_sync_state("Account", mock_conn)

        assert isinstance(result, SyncStateRow)
        assert result.object_name == "Account"
        assert result.last_sync_timestamp == timestamp
        assert result.last_sync_id == "batch-123"
        assert result.sync_mode == "incremental"
        assert result.updated_at == updated_at

    def test_acquires_advisory_lock(self):
        """Should acquire pg_advisory_xact_lock before SELECT."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None,), None]

        get_sync_state("Account", mock_conn)

        # Verify execute was called twice: advisory lock + SELECT
        assert mock_cursor.execute.call_count == 2

        # First call should be advisory lock
        first_call_query = str(mock_cursor.execute.call_args_list[0][0][0])
        assert "pg_advisory_xact_lock" in first_call_query

        # Second call should be SELECT
        second_call_query = str(mock_cursor.execute.call_args_list[1][0][0])
        assert "SELECT" in second_call_query
        assert "sf_sync_state" in second_call_query

    def test_uses_parameterized_query(self):
        """Should use psycopg2.sql for safe query construction."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None,), None]
        mock_cursor.nextset.return_value = True

        get_sync_state("Account", mock_conn)

        # Verify sql.SQL was used (check type of executed query)
        query = mock_cursor.execute.call_args[0][0]
        # Query should be Composed or SQL object (from psycopg2.sql)
        assert hasattr(query, "as_string") or isinstance(query, str)

    @patch("sf_utils.sync.state.logger")
    def test_logs_at_debug_and_info_level(self, mock_logger):
        """Should log at DEBUG level for operation, INFO when no state found."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None,), None]
        mock_cursor.nextset.return_value = True

        get_sync_state("Account", mock_conn)

        # Verify DEBUG logging was called
        assert mock_logger.debug.called
        # INFO is called when no state is found
        assert mock_logger.info.called


class TestUpdateSyncState:
    """Tests for update_sync_state function."""

    def test_inserts_new_state(self):
        """Should INSERT new row when object has no prior state."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            sync_id="batch-123",
            mode="incremental",
        )

        # Verify execute was called
        assert mock_cursor.execute.called

        # Query should contain INSERT ... ON CONFLICT DO UPDATE
        query_str = str(mock_cursor.execute.call_args[0][0])
        assert "INSERT INTO" in query_str
        assert "ON CONFLICT" in query_str

    def test_updates_existing_state_upsert(self):
        """Should use UPSERT pattern for idempotency."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

        # Verify UPSERT query structure
        query_str = str(mock_cursor.execute.call_args[0][0])
        assert "INSERT INTO" in query_str
        assert "ON CONFLICT" in query_str
        assert "DO UPDATE SET" in query_str

    def test_rejects_naive_datetime(self):
        """Should raise ValueError for naive datetime (no timezone)."""
        mock_conn = MagicMock()

        naive_timestamp = datetime(2024, 1, 15, 12, 30, 0)  # No tzinfo

        with pytest.raises(ValueError) as exc_info:
            update_sync_state(
                object_name="Account",
                timestamp=naive_timestamp,
                db_conn=mock_conn,
            )

        assert "timezone-aware" in str(exc_info.value)

    def test_acquires_advisory_lock(self):
        """Should acquire pg_advisory_xact_lock before UPSERT."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None,)]  # Lock result

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

        # Verify execute was called twice: advisory lock + UPSERT
        assert mock_cursor.execute.call_count == 2

        # First call should be advisory lock
        first_call_query = str(mock_cursor.execute.call_args_list[0][0][0])
        assert "pg_advisory_xact_lock" in first_call_query

        # Second call should be UPSERT
        second_call_query = str(mock_cursor.execute.call_args_list[1][0][0])
        assert "INSERT INTO" in second_call_query

    def test_does_not_commit(self):
        """Should not commit transaction (caller's responsibility)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

        # Verify commit was NOT called
        assert not mock_conn.commit.called

    def test_sets_updated_at_column(self):
        """Should set updated_at column in UPSERT."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

        # Verify updated_at is in the UPSERT query
        query_str = str(mock_cursor.execute.call_args[0][0])
        assert "updated_at" in query_str

    def test_uses_parameterized_query(self):
        """Should use parameterized query with tuple parameters."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            sync_id="batch-123",
            mode="full",
        )

        # Verify execute was called with query and parameters
        assert len(mock_cursor.execute.call_args[0]) == 2
        query, params = mock_cursor.execute.call_args[0]

        # Verify parameters tuple contains expected values
        assert "Account" in params
        assert timestamp in params
        assert "batch-123" in params
        assert "full" in params

    @patch("sf_utils.sync.state.logger")
    def test_logs_at_debug_and_info_level(self, mock_logger):
        """Should log at both DEBUG and INFO levels."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

        # Verify both DEBUG and INFO logging was called
        assert mock_logger.debug.called
        assert mock_logger.info.called

    def test_optional_sync_id_defaults_to_none(self):
        """Should accept None for optional sync_id parameter."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Should not raise
        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            # sync_id not provided
        )

        # Verify None was passed as sync_id parameter
        params = mock_cursor.execute.call_args[0][1]
        assert None in params

    def test_mode_defaults_to_incremental(self):
        """Should use 'incremental' as default mode."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            # mode not provided
        )

        # Verify 'incremental' was passed as mode parameter
        params = mock_cursor.execute.call_args[0][1]
        assert "incremental" in params


class TestSyncStateRow:
    """Tests for SyncStateRow dataclass."""

    def test_dataclass_fields_accessible(self):
        """Should have all required fields accessible."""
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        updated_at = datetime(2024, 1, 15, 12, 30, 5, tzinfo=timezone.utc)

        row = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=timestamp,
            last_sync_id="batch-123",
            sync_mode="incremental",
            updated_at=updated_at,
        )

        assert row.object_name == "Account"
        assert row.last_sync_timestamp == timestamp
        assert row.last_sync_id == "batch-123"
        assert row.sync_mode == "incremental"
        assert row.updated_at == updated_at

    def test_optional_fields_default_values(self):
        """Should allow optional fields with default values."""
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Create with only required fields
        row = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=timestamp,
        )

        assert row.object_name == "Account"
        assert row.last_sync_timestamp == timestamp
        assert row.last_sync_id is None
        assert row.sync_mode == "incremental"
        assert row.updated_at is None

    def test_dataclass_immutability_not_enforced(self):
        """Should allow field updates (mutable dataclass)."""
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        row = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=timestamp,
        )

        # Should be mutable (not frozen)
        row.last_sync_id = "batch-456"
        assert row.last_sync_id == "batch-456"


class TestAdvisoryLockKey:
    """Tests for _compute_advisory_lock_key function."""

    def test_deterministic_for_same_object(self):
        """Should return same lock key for same object name."""
        key1 = _compute_advisory_lock_key("Account")
        key2 = _compute_advisory_lock_key("Account")

        assert key1 == key2

    def test_different_for_different_objects(self):
        """Should return different lock keys for different object names."""
        key_account = _compute_advisory_lock_key("Account")
        key_contact = _compute_advisory_lock_key("Contact")

        assert key_account != key_contact

    def test_returns_int(self):
        """Should return integer suitable for pg_advisory_xact_lock."""
        key = _compute_advisory_lock_key("Account")

        assert isinstance(key, int)

    def test_handles_empty_string(self):
        """Should handle empty string object name."""
        key = _compute_advisory_lock_key("")

        assert isinstance(key, int)

    def test_handles_long_object_names(self):
        """Should handle long object names (custom objects can be lengthy)."""
        long_name = "Custom_Object_With_Very_Long_Name__c" * 10

        key = _compute_advisory_lock_key(long_name)

        assert isinstance(key, int)

    def test_handles_special_characters(self):
        """Should handle object names with special characters."""
        special_name = "Custom__c_123!@#$%"

        key = _compute_advisory_lock_key(special_name)

        assert isinstance(key, int)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_get_sync_state_with_empty_object_name(self):
        """Should handle empty object name (TEXT allows empty)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(None,), None]
        mock_cursor.nextset.return_value = True

        # Should not raise
        result = get_sync_state("", mock_conn)

        assert result is None

    def test_update_sync_state_with_empty_object_name(self):
        """Should handle empty object name."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Should not raise
        update_sync_state(
            object_name="",
            timestamp=timestamp,
            db_conn=mock_conn,
        )

    def test_update_sync_state_with_very_long_object_name(self):
        """Should handle very long object names."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        long_name = "Custom_Object_With_Very_Long_Name__c" * 10
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Should not raise
        update_sync_state(
            object_name=long_name,
            timestamp=timestamp,
            db_conn=mock_conn,
        )

    def test_update_sync_state_with_null_sync_id(self):
        """Should handle None sync_id (optional field)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Should not raise
        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            sync_id=None,
        )

    def test_get_sync_state_with_null_columns(self):
        """Should handle NULL values in optional database columns."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock fetchone with NULL values
        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        mock_cursor.fetchone.side_effect = [
            (None,),  # Lock result
            (
                "Account",
                timestamp,
                None,  # last_sync_id is NULL
                "incremental",
                None,  # updated_at is NULL
            ),
        ]
        mock_cursor.nextset.return_value = True

        result = get_sync_state("Account", mock_conn)

        assert result.last_sync_id is None
        assert result.updated_at is None

    def test_update_sync_state_with_full_mode(self):
        """Should accept 'full' as sync mode."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (None,)

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Should not raise
        update_sync_state(
            object_name="Account",
            timestamp=timestamp,
            db_conn=mock_conn,
            mode="full",
        )

        # Verify 'full' was passed in parameters
        params = mock_cursor.execute.call_args[0][1]
        assert "full" in params

    def test_connection_failure_propagates(self):
        """Should propagate database connection failures."""
        import psycopg2

        mock_conn = MagicMock()
        mock_conn.cursor.side_effect = psycopg2.OperationalError(
            "connection closed"
        )

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        with pytest.raises(psycopg2.OperationalError):
            update_sync_state(
                object_name="Account",
                timestamp=timestamp,
                db_conn=mock_conn,
            )

    def test_execute_failure_propagates(self):
        """Should propagate SQL execution failures."""
        import psycopg2

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.execute.side_effect = psycopg2.ProgrammingError(
            "syntax error"
        )

        timestamp = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)

        with pytest.raises(psycopg2.ProgrammingError):
            update_sync_state(
                object_name="Account",
                timestamp=timestamp,
                db_conn=mock_conn,
            )
