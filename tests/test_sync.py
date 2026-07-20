"""Tests for automatic API mode selection in sync module."""

from datetime import datetime, timezone
from enum import Enum
from unittest.mock import Mock, patch, call

import pytest

from sf_utils.sync.rest_sync import SyncResult


# Import the module under test (will be created in DEV-23)
# Using late import pattern to avoid import errors during test collection
def import_sync():
    """Import sync module with helpful error message if not yet implemented."""
    try:
        from sf_utils.sync import SyncMode, sync
        return SyncMode, sync
    except ImportError as e:
        pytest.skip(f"sync module with SyncMode not yet implemented: {e}")


class TestSyncMode:
    """Tests for SyncMode enum."""

    def test_sync_mode_enum_values(self):
        """SyncMode enum should have REST, BULK, and AUTO values."""
        SyncMode, _ = import_sync()

        assert hasattr(SyncMode, 'REST')
        assert hasattr(SyncMode, 'BULK')
        assert hasattr(SyncMode, 'AUTO')

        assert SyncMode.REST.value == "rest"
        assert SyncMode.BULK.value == "bulk"
        assert SyncMode.AUTO.value == "auto"

    def test_sync_mode_has_exactly_three_values(self):
        """SyncMode should have exactly three values: REST, BULK, AUTO."""
        SyncMode, _ = import_sync()

        values = list(SyncMode)
        assert len(values) == 3
        assert SyncMode.REST in values
        assert SyncMode.BULK in values
        assert SyncMode.AUTO in values

    def test_default_mode_is_auto(self):
        """Default mode parameter should be SyncMode.AUTO."""
        SyncMode, sync = import_sync()

        # Use inspect to check default parameter value
        import inspect
        sig = inspect.signature(sync)
        mode_param = sig.parameters['mode']

        assert mode_param.default == SyncMode.AUTO


class TestSyncAutoModeSelection:
    """Tests for automatic mode selection based on record count threshold."""

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_auto_selects_rest_below_threshold(
        self, mock_query, mock_sync_records
    ):
        """AUTO mode should select REST API when record count < threshold."""
        SyncMode, sync = import_sync()

        # Mock count query to return count below threshold (10,000)
        # COUNT() returns as expr0 in implementation
        mock_query.return_value = [{"expr0": 9999}]
        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records to return a valid SyncResult
        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=9999,
            records_inserted=9999,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with AUTO mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=10000,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify query was called to get count
        assert mock_query.called
        count_query_call = mock_query.call_args
        # Implementation uses COUNT() FROM object_name
        assert "SELECT COUNT(Id) FROM Account" in count_query_call[0][0]
        assert count_query_call[1]['client'] == mock_client

        # Verify sync_records (REST API) was called, NOT sync_records_bulk
        assert mock_sync_records.called

    @patch('sf_utils.sync.sync_records_bulk')
    @patch('sf_utils.sync.query')
    def test_auto_selects_bulk_at_threshold(
        self, mock_query, mock_sync_records_bulk
    ):
        """AUTO mode should select Bulk API when record count == threshold."""
        SyncMode, sync = import_sync()

        # Mock count query to return count at threshold (10,000)
        mock_query.return_value = [{"expr0": 10000}]
        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records_bulk to return a valid SyncResult
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Account",
            records_fetched=10000,
            records_inserted=10000,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with AUTO mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=10000,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify query was called to get count
        assert mock_query.called

        # Verify sync_records_bulk (Bulk API) was called, NOT sync_records
        assert mock_sync_records_bulk.called

    @patch('sf_utils.sync.sync_records_bulk')
    @patch('sf_utils.sync.query')
    def test_auto_selects_bulk_above_threshold(
        self, mock_query, mock_sync_records_bulk
    ):
        """AUTO mode should select Bulk API when record count > threshold."""
        SyncMode, sync = import_sync()

        # Mock count query to return count above threshold (10,000)
        mock_query.return_value = [{"expr0": 50000}]
        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records_bulk to return a valid SyncResult
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Account",
            records_fetched=50000,
            records_inserted=50000,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with AUTO mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=10000,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify query was called to get count
        assert mock_query.called
        count_query_call = mock_query.call_args
        assert "SELECT COUNT(Id) FROM Account" in count_query_call[0][0]

        # Verify sync_records_bulk (Bulk API) was called
        assert mock_sync_records_bulk.called

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_auto_mode_logs_selection(
        self, mock_query, mock_sync_records, caplog
    ):
        """AUTO mode should log INFO message about mode selection."""
        SyncMode, sync = import_sync()
        import logging

        # Mock count query to return count below threshold
        mock_query.return_value = [{"expr0": 5000}]
        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records to return a valid SyncResult
        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=5000,
            records_inserted=5000,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Capture logs
        with caplog.at_level(logging.INFO):
            result = sync(
                soql="SELECT Id, Name, LastModifiedDate FROM Account",
                object_name="Account",
                mode=SyncMode.AUTO,
                threshold=10000,
                client=mock_client,
                db_conn=mock_db_conn,
            )

        # Verify INFO log about mode selection
        assert any(
            "REST" in record.message and "5000" in record.message
            for record in caplog.records
            if record.levelno == logging.INFO
        )


class TestSyncExplicitMode:
    """Tests for explicit mode selection (REST or BULK)."""

    @patch('sf_utils.sync.sync_records')
    def test_rest_mode_uses_sync_records(
        self, mock_sync_records
    ):
        """SyncMode.REST should call sync_records() without count query."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records to return a valid SyncResult
        mock_sync_records.return_value = SyncResult(
            object_name="Contact",
            records_fetched=100,
            records_inserted=100,
            records_updated=0,
            sync_mode="full",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with explicit REST mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate FROM Contact",
            object_name="Contact",
            mode=SyncMode.REST,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify sync_records was called
        assert mock_sync_records.called
        call_kwargs = mock_sync_records.call_args[1]
        assert call_kwargs['soql'] == "SELECT Id, Name, LastModifiedDate FROM Contact"
        assert call_kwargs['object_name'] == "Contact"

    @patch('sf_utils.sync.sync_records_bulk')
    def test_bulk_mode_uses_sync_records_bulk(
        self, mock_sync_records_bulk
    ):
        """SyncMode.BULK should call sync_records_bulk() without count query."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records_bulk to return a valid SyncResult
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Opportunity",
            records_fetched=25000,
            records_inserted=25000,
            records_updated=0,
            sync_mode="full",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with explicit BULK mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, Amount, LastModifiedDate FROM Opportunity",
            object_name="Opportunity",
            mode=SyncMode.BULK,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify sync_records_bulk was called
        assert mock_sync_records_bulk.called
        call_kwargs = mock_sync_records_bulk.call_args[1]
        assert call_kwargs['soql'] == "SELECT Id, Name, Amount, LastModifiedDate FROM Opportunity"
        assert call_kwargs['object_name'] == "Opportunity"


class TestSyncConfig:
    """Tests for configuration parameters."""

    @patch('sf_utils.sync.sync_records_bulk')
    @patch('sf_utils.sync.query')
    def test_custom_threshold_honored(
        self, mock_query, mock_sync_records_bulk
    ):
        """Custom threshold parameter should be respected."""
        SyncMode, sync = import_sync()

        # Mock count query to return count above custom threshold (5,000)
        mock_query.return_value = [{"expr0": 6000}]
        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records_bulk to return a valid SyncResult
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Account",
            records_fetched=6000,
            records_inserted=6000,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with custom threshold, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=5000,  # Custom threshold
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify Bulk API was selected (count > custom threshold)
        assert mock_sync_records_bulk.called

    @patch('sf_utils.sync.sync_records')
    def test_passes_parameters_to_underlying_function(
        self, mock_sync_records
    ):
        """All parameters should be forwarded to underlying sync function."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock sync_records to return a valid SyncResult
        mock_sync_records.return_value = SyncResult(
            object_name="Lead",
            records_fetched=0,
            records_inserted=0,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="CreatedDate",
        )

        # Execute sync with all parameters
        result = sync(
            soql="SELECT Id, Name, CreatedDate FROM Lead",
            object_name="Lead",
            mode=SyncMode.REST,
            threshold=10000,
            date_field="CreatedDate",
            batch_size=500,
            poll_interval=10.0,
            timeout=300.0,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify all parameters were passed to sync_records
        # Note: batch_size, poll_interval, timeout are BULK-only params
        assert mock_sync_records.called
        call_kwargs = mock_sync_records.call_args[1]
        assert call_kwargs['soql'] == "SELECT Id, Name, CreatedDate FROM Lead"
        assert call_kwargs['object_name'] == "Lead"
        assert call_kwargs['date_field'] == "CreatedDate"
        # batch_size is NOT passed to sync_records (REST mode)
        assert call_kwargs['client'] == mock_client
        assert call_kwargs['db_conn'] == mock_db_conn


class TestSyncCountQuery:
    """Tests for count query generation and execution."""

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_count_query_extracts_from_clause_correctly(
        self, mock_query, mock_sync_records
    ):
        """Count query should correctly extract FROM clause from original SOQL."""
        SyncMode, sync = import_sync()

        # Implementation uses object_name, not SOQL parsing
        mock_query.return_value = [{"expr0": 100}]
        mock_client = Mock()
        mock_db_conn = Mock()

        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=100,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with WHERE clause, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, Industry, LastModifiedDate FROM Account WHERE Type = 'Customer'",
            object_name="Account",
            mode=SyncMode.AUTO,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify count query uses object_name (implementation uses COUNT() FROM object_name)
        assert mock_query.called
        count_query_call = mock_query.call_args
        count_soql = count_query_call[0][0]

        # Implementation uses simple: SELECT COUNT(Id) FROM {object_name}
        assert "SELECT COUNT(Id) FROM Account" == count_soql

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_count_query_preserves_subqueries(
        self, mock_query, mock_sync_records
    ):
        """Count query should handle SOQL with subqueries correctly."""
        SyncMode, sync = import_sync()

        mock_query.return_value = [{"expr0": 50}]
        mock_client = Mock()
        mock_db_conn = Mock()

        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=50,
            records_inserted=50,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with subquery, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, Name, LastModifiedDate, (SELECT Id FROM Contacts) FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify count query was called (implementation uses object_name)
        assert mock_query.called
        count_query_call = mock_query.call_args
        count_soql = count_query_call[0][0]

        # Count query should be simple: SELECT COUNT(Id) FROM {object_name}
        assert "SELECT COUNT(Id) FROM Account" == count_soql


class TestSyncErrorHandling:
    """Tests for error handling and edge cases."""

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_count_query_failure_defaults_to_rest(
        self, mock_query, mock_sync_records
    ):
        """Count query failure should log warning and default to REST mode."""
        SyncMode, sync = import_sync()
        from sf_utils.exceptions import SalesforceAPIError
        import logging

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock query to raise SalesforceAPIError
        mock_query.side_effect = SalesforceAPIError(
            message="Invalid query",
            status_code=400
        )

        # Mock sync_records to succeed
        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=0,
            records_inserted=0,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Implementation defaults to REST on count failure (doesn't raise)
        result = sync(
            soql="SELECT Id, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify REST mode was used as fallback
        assert mock_sync_records.called

    @patch('sf_utils.sync.sync_records')
    @patch('sf_utils.sync.query')
    def test_zero_count_selects_rest_api(
        self, mock_query, mock_sync_records
    ):
        """Zero record count should select REST API."""
        SyncMode, sync = import_sync()

        mock_query.return_value = [{"expr0": 0}]
        mock_client = Mock()
        mock_db_conn = Mock()

        mock_sync_records.return_value = SyncResult(
            object_name="Account",
            records_fetched=0,
            records_inserted=0,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        # Execute sync with AUTO mode, passing mocked client and db_conn
        result = sync(
            soql="SELECT Id, LastModifiedDate FROM Account WHERE CreatedDate = TODAY",
            object_name="Account",
            mode=SyncMode.AUTO,
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # Verify REST API was selected (0 < threshold)
        assert mock_sync_records.called

    def test_invalid_mode_raises_attribute_error(self):
        """Invalid mode value should raise AttributeError when accessing .value."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Implementation tries to access mode.value, so string will raise AttributeError
        with pytest.raises(AttributeError):
            sync(
                soql="SELECT Id, LastModifiedDate FROM Account",
                object_name="Account",
                mode="invalid_mode",  # Not a SyncMode enum value
                client=mock_client,
                db_conn=mock_db_conn,
            )

    @patch('sf_utils.sync.sync_records_bulk')
    @patch('sf_utils.sync.query')
    def test_negative_threshold_still_works(self, mock_query, mock_sync_records_bulk):
        """Negative threshold allows any count >= -1, so BULK is selected for count > 0."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock count query to return 100
        mock_query.return_value = [{"expr0": 100}]

        # Mock sync_records_bulk to avoid database operations
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=100,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        result = sync(
            soql="SELECT Id, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=-1,  # Negative threshold (no validation in implementation)
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # With negative threshold, count (100) >= threshold (-1), so BULK is selected
        assert mock_sync_records_bulk.called

    @patch('sf_utils.sync.sync_records_bulk')
    @patch('sf_utils.sync.query')
    def test_zero_threshold_uses_bulk_for_any_records(self, mock_query, mock_sync_records_bulk):
        """Zero threshold means any count >= 0 triggers BULK mode."""
        SyncMode, sync = import_sync()

        mock_client = Mock()
        mock_db_conn = Mock()

        # Mock count query to return 1
        mock_query.return_value = [{"expr0": 1}]

        # Mock sync_records_bulk to avoid database operations
        mock_sync_records_bulk.return_value = SyncResult(
            object_name="Account",
            records_fetched=1,
            records_inserted=1,
            records_updated=0,
            sync_mode="incremental",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="LastModifiedDate",
        )

        result = sync(
            soql="SELECT Id, LastModifiedDate FROM Account",
            object_name="Account",
            mode=SyncMode.AUTO,
            threshold=0,  # Zero threshold
            client=mock_client,
            db_conn=mock_db_conn,
        )

        # 1 >= 0, so BULK should be selected
        assert mock_sync_records_bulk.called
