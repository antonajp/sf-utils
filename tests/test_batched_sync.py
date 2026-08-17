"""Tests for batched sync operations in sf_utils.sync.batched_sync module."""

from unittest.mock import Mock, patch, call
import pytest

from sf_utils.sync.batched_sync import (
    _validate_salesforce_id,
    _validate_salesforce_ids,
    _batch_ids,
    _construct_in_clause,
    sync_content_document_links,
)


class TestValidateSalesforceId:
    """Tests for _validate_salesforce_id() helper function."""

    def test_valid_15_char_id(self):
        """15-character alphanumeric ID should be valid."""
        assert _validate_salesforce_id("001000000000001") is True
        assert _validate_salesforce_id("001000000000AAA") is True

    def test_valid_18_char_id(self):
        """18-character alphanumeric ID should be valid."""
        assert _validate_salesforce_id("001000000000001AAA") is True
        assert _validate_salesforce_id("001000000000AAA000") is True

    def test_invalid_length(self):
        """IDs with incorrect length should be invalid."""
        assert _validate_salesforce_id("short") is False
        assert _validate_salesforce_id("001") is False
        assert _validate_salesforce_id("001000000000001AAABBB") is False  # 21 chars

    def test_invalid_characters(self):
        """IDs with special characters should be invalid."""
        assert _validate_salesforce_id("001-000-000-000") is False
        assert _validate_salesforce_id("001000000000001!") is False
        assert _validate_salesforce_id("001 000 000 001") is False

    def test_non_string_input(self):
        """Non-string inputs should be invalid."""
        assert _validate_salesforce_id(123) is False
        assert _validate_salesforce_id(None) is False
        assert _validate_salesforce_id([]) is False


class TestValidateSalesforceIds:
    """Tests for _validate_salesforce_ids() batch validation."""

    def test_all_valid_ids(self):
        """All valid IDs should return empty invalid list."""
        ids = ["001000000000001AAA", "001000000000002AAA", "001000000000003"]
        valid, invalid = _validate_salesforce_ids(ids)

        assert len(valid) == 3
        assert len(invalid) == 0
        assert valid == ids

    def test_all_invalid_ids(self):
        """All invalid IDs should return empty valid list."""
        ids = ["invalid", "short", "001-000-000"]
        valid, invalid = _validate_salesforce_ids(ids)

        assert len(valid) == 0
        assert len(invalid) == 3
        assert invalid == ids

    def test_mixed_valid_invalid(self):
        """Mixed valid/invalid IDs should be separated correctly."""
        ids = ["001000000000001AAA", "invalid", "001000000000002"]
        valid, invalid = _validate_salesforce_ids(ids)

        assert len(valid) == 2
        assert len(invalid) == 1
        assert "001000000000001AAA" in valid
        assert "001000000000002" in valid
        assert "invalid" in invalid

    def test_empty_list(self):
        """Empty list should return two empty lists."""
        valid, invalid = _validate_salesforce_ids([])

        assert valid == []
        assert invalid == []


class TestBatchIds:
    """Tests for _batch_ids() batching logic."""

    def test_exact_batch_size(self):
        """IDs exactly matching batch_size should create one batch."""
        ids = ["id1", "id2", "id3"]
        batches = _batch_ids(ids, batch_size=3)

        assert len(batches) == 1
        assert batches[0] == ids

    def test_multiple_full_batches(self):
        """Multiple full batches should be created correctly."""
        ids = ["id1", "id2", "id3", "id4", "id5", "id6"]
        batches = _batch_ids(ids, batch_size=2)

        assert len(batches) == 3
        assert batches[0] == ["id1", "id2"]
        assert batches[1] == ["id3", "id4"]
        assert batches[2] == ["id5", "id6"]

    def test_partial_last_batch(self):
        """Partial last batch should contain remaining IDs."""
        ids = ["id1", "id2", "id3", "id4", "id5"]
        batches = _batch_ids(ids, batch_size=2)

        assert len(batches) == 3
        assert batches[0] == ["id1", "id2"]
        assert batches[1] == ["id3", "id4"]
        assert batches[2] == ["id5"]

    def test_single_id(self):
        """Single ID should create one batch."""
        ids = ["id1"]
        batches = _batch_ids(ids, batch_size=100)

        assert len(batches) == 1
        assert batches[0] == ["id1"]

    def test_empty_list(self):
        """Empty list should create no batches."""
        batches = _batch_ids([], batch_size=10)

        assert batches == []


class TestConstructInClause:
    """Tests for _construct_in_clause() SOQL generation."""

    def test_single_id(self):
        """Single ID should be wrapped in parentheses with quotes."""
        ids = ["001000000000001AAA"]
        in_clause = _construct_in_clause(ids)

        assert in_clause == "('001000000000001AAA')"

    def test_multiple_ids(self):
        """Multiple IDs should be comma-separated with quotes."""
        ids = ["001000000000001AAA", "001000000000002AAA", "001000000000003AAA"]
        in_clause = _construct_in_clause(ids)

        assert in_clause == "('001000000000001AAA', '001000000000002AAA', '001000000000003AAA')"

    def test_preserves_id_order(self):
        """IDs should appear in same order as input."""
        ids = ["zzz", "aaa", "mmm"]
        in_clause = _construct_in_clause(ids)

        assert in_clause == "('zzz', 'aaa', 'mmm')"


class TestSyncContentDocumentLinks:
    """Tests for sync_content_document_links() main function."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_batch_size_exceeds_limit(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """batch_size > 2000 should raise ValueError."""
        with pytest.raises(ValueError, match="batch_size must be between 1 and 2000"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                batch_size=2001,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_batch_size_too_small(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """batch_size < 1 should raise ValueError."""
        with pytest.raises(ValueError, match="batch_size must be between 1 and 2000"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                batch_size=0,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_invalid_table_name(self, mock_get_connection, mock_get_client):
        """Invalid table name should raise ValueError."""
        mock_conn = Mock()
        mock_get_connection.return_value = mock_conn

        with pytest.raises(ValueError, match="Invalid table name"):
            sync_content_document_links(
                source_table="table; DROP TABLE users;",  # SQL injection attempt
                db_conn=mock_conn,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_empty_source_table_returns_zeros(self, mock_get_connection, mock_get_client):
        """Empty source table should return SyncResult with zeros."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []  # No IDs found
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        assert result.records_fetched == 0
        assert result.records_inserted == 0
        assert result.records_updated == 0
        assert result.object_name == "ContentDocumentLink"
        assert result.sync_mode == "batched"

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_invalid_ids_raises_error(self, mock_get_connection, mock_get_client):
        """Invalid IDs in source table should raise ValueError."""
        mock_conn = Mock()
        mock_cursor = Mock()
        # Return invalid IDs
        mock_cursor.fetchall.return_value = [("invalid-id",), ("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        with pytest.raises(ValueError, match="invalid Salesforce IDs"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                db_conn=mock_conn,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_successful_sync_single_batch(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Successful sync with single batch should return correct SyncResult."""
        # Mock PostgreSQL connection
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ("001000000000001AAA",),
            ("001000000000002AAA",),
        ]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit = Mock()
        mock_get_connection.return_value = mock_conn

        # Mock Salesforce client
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock query_all to return ContentDocumentLink records
        mock_query_all.return_value = [
            {"Id": "001", "ContentDocumentId": "001000000000001AAA", "LinkedEntityId": "002"},
            {"Id": "003", "ContentDocumentId": "001000000000002AAA", "LinkedEntityId": "004"},
        ]

        # Mock upsert_records
        mock_upsert.return_value = (2, 0)  # 2 inserted, 0 updated

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            batch_size=200,
            db_conn=mock_conn,
            client=mock_client,
        )

        # Verify result
        assert result.records_fetched == 2
        assert result.records_inserted == 2
        assert result.records_updated == 0
        assert result.object_name == "ContentDocumentLink"
        assert result.sync_mode == "batched"

        # Verify query_all was called once (single batch)
        assert mock_query_all.call_count == 1

        # Verify upsert was called
        assert mock_upsert.call_count == 1

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_successful_sync_multiple_batches(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Successful sync with multiple batches should query each batch."""
        # Mock PostgreSQL connection with 5 IDs (will create 3 batches with batch_size=2)
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ("001000000000001AAA",),
            ("001000000000002AAA",),
            ("001000000000003AAA",),
            ("001000000000004AAA",),
            ("001000000000005AAA",),
        ]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit = Mock()
        mock_get_connection.return_value = mock_conn

        # Mock Salesforce client
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock query_all to return records for each batch
        mock_query_all.side_effect = [
            [{"Id": "001"}, {"Id": "002"}],  # Batch 1
            [{"Id": "003"}, {"Id": "004"}],  # Batch 2
            [{"Id": "005"}],  # Batch 3
        ]

        # Mock upsert_records
        mock_upsert.return_value = (5, 0)

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            batch_size=2,  # Force multiple batches
            db_conn=mock_conn,
            client=mock_client,
        )

        # Verify result
        assert result.records_fetched == 5
        assert result.records_inserted == 5
        assert result.records_updated == 0

        # Verify query_all was called 3 times (3 batches)
        assert mock_query_all.call_count == 3

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_custom_id_column(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Custom id_column should be used in PostgreSQL query."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            id_column="contentdocumentid",
            db_conn=mock_conn,
            client=mock_client,
        )

        execute_call = mock_cursor.execute.call_args[0][0]
        assert "contentdocumentid" in execute_call.lower()

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_custom_soql_template(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Custom SOQL template should be used in queries."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        custom_soql = "SELECT Id FROM ContentDocumentLink WHERE ContentDocumentId IN ({id_list})"

        sync_content_document_links(
            source_table="sf_contentdocument",
            soql_template=custom_soql,
            db_conn=mock_conn,
            client=mock_client,
        )

        query_call = mock_query_all.call_args[1]["soql"]
        assert "SELECT Id FROM ContentDocumentLink" in query_call

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_soql_template_missing_placeholder_raises_error(
        self, mock_get_connection, mock_get_client
    ):
        """SOQL template without {id_list} placeholder should raise ValueError."""
        mock_conn = Mock()
        mock_get_connection.return_value = mock_conn

        with pytest.raises(ValueError, match="{id_list} placeholder"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                soql_template="SELECT Id FROM ContentDocumentLink",
                db_conn=mock_conn,
            )


class TestSyncResultValidation:
    """Tests for SyncResult return value validation."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_result_timestamps_are_utc_aware(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """SyncResult timestamps should be timezone-aware UTC."""
        from datetime import timezone

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        assert result.start_timestamp.tzinfo is not None
        assert result.end_timestamp.tzinfo is not None
        assert result.start_timestamp.tzinfo.tzname(None) == "UTC"
        assert result.end_timestamp.tzinfo.tzname(None) == "UTC"
        assert result.end_timestamp >= result.start_timestamp

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_result_object_name_is_contentdocumentlink(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """SyncResult object_name should always be ContentDocumentLink."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        assert result.object_name == "ContentDocumentLink"

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_result_sync_mode_is_batched(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """SyncResult sync_mode should always be 'batched'."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        assert result.sync_mode == "batched"

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_result_date_field_is_none(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """SyncResult date_field should be None (no incremental sync)."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        assert result.date_field is None


class TestErrorHandling:
    """Tests for error scenarios and exception propagation."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    def test_salesforce_api_error_propagates(
        self, mock_query_all, mock_get_connection, mock_get_client
    ):
        """Salesforce API errors should propagate to caller."""
        from sf_utils.exceptions import SalesforceAPIError

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.side_effect = SalesforceAPIError(
            message="Invalid field: ContentDocumentId",
            status_code=400,
        )

        with pytest.raises(SalesforceAPIError):
            sync_content_document_links(
                source_table="sf_contentdocument",
                db_conn=mock_conn,
                client=mock_client,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_database_error_triggers_rollback(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Database errors should trigger transaction rollback."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = [{"Id": "001"}]
        mock_upsert.side_effect = Exception("Database connection lost")

        with pytest.raises(Exception, match="Database connection lost"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                db_conn=mock_conn,
                client=mock_client,
            )

        mock_conn.rollback.assert_called_once()

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_commit_called_on_success(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Database commit should be called on successful sync."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = [{"Id": "001"}]
        mock_upsert.return_value = (1, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
            client=mock_client,
        )

        mock_conn.commit.assert_called_once()


class TestTableAndRecordHandling:
    """Tests for table creation and record normalization."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_table_creation_called_with_correct_name(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Table creation should use sf_contentdocumentlink table name."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
            client=mock_client,
        )

        call_kwargs = mock_create_table.call_args[1]
        assert call_kwargs["table_name"] == "sf_contentdocumentlink"

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_record_normalization(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Records should be normalized before upsert."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = [
            {
                "Id": "001",
                "ContentDocumentId": "001000000000001AAA",
                "ContentDocument": {"Title": "Test.pdf"},
            }
        ]
        mock_upsert.return_value = (1, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
            client=mock_client,
        )

        upsert_call = mock_upsert.call_args[1]
        records = upsert_call["records"]
        assert len(records) == 1


class TestBatchProgressLogging:
    """Tests for batch progress logging."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_logs_batch_progress_at_info_level(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
        caplog,
    ):
        """Should log batch progress at INFO level."""
        import logging

        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ("001000000000001AAA",),
            ("001000000000002AAA",),
            ("001000000000003AAA",),
        ]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        with caplog.at_level(logging.INFO):
            sync_content_document_links(
                source_table="sf_contentdocument",
                batch_size=2,
                db_conn=mock_conn,
                client=mock_client,
            )

        assert "batch" in caplog.text.lower()
        assert "processing batch" in caplog.text.lower()


class TestSecurityConsiderations:
    """Tests for security aspects (SQL injection prevention, no credential logging)."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_does_not_log_individual_ids(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
        caplog,
    ):
        """Should not log individual Salesforce IDs at any level."""
        import logging

        # Setup mocks with known sensitive ID
        mock_conn = Mock()
        mock_cursor = Mock()
        sensitive_id = "001SENSITIVE00000"  # 17 chars - valid 15+2 but we'll use 15-char
        sensitive_id = "001SENSITIVEAAA"   # 15-char valid ID
        mock_cursor.fetchall.return_value = [(sensitive_id,)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = [{"Id": "001", "ContentDocumentId": sensitive_id}]
        mock_upsert.return_value = (1, 0)

        with caplog.at_level(logging.DEBUG):
            sync_content_document_links(
                source_table="sf_contentdocument",
                db_conn=mock_conn,
                client=mock_client,
            )

        # Verify the sensitive ID never appears in logs
        assert sensitive_id not in caplog.text, (
            f"Sensitive ID '{sensitive_id}' was logged - IDs should never appear in logs"
        )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_prevents_sql_injection_in_table_name(
        self, mock_get_connection, mock_get_client
    ):
        """SQL injection in table name should be prevented."""
        mock_conn = Mock()
        mock_get_connection.return_value = mock_conn

        with pytest.raises(ValueError, match="Invalid table name"):
            sync_content_document_links(
                source_table="sf_contentdocument; DROP TABLE users;",
                db_conn=mock_conn,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_prevents_sql_injection_in_column_name(
        self, mock_get_connection, mock_get_client
    ):
        """SQL injection in column name should be prevented."""
        mock_conn = Mock()
        mock_get_connection.return_value = mock_conn

        with pytest.raises(ValueError, match="Invalid column name"):
            sync_content_document_links(
                source_table="sf_contentdocument",
                id_column="id; DROP TABLE users;",
                db_conn=mock_conn,
            )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    def test_table_name_with_special_chars_rejected(
        self, mock_get_connection, mock_get_client
    ):
        """Table names with special characters should be rejected."""
        mock_conn = Mock()
        mock_get_connection.return_value = mock_conn

        invalid_names = [
            "table-name",
            "table name",
            "table.name",
            "table;name",
        ]

        for invalid_name in invalid_names:
            with pytest.raises(ValueError, match="Invalid table name"):
                sync_content_document_links(
                    source_table=invalid_name,
                    db_conn=mock_conn,
                )


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_exactly_batch_size_ids(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """IDs exactly matching batch_size should create one batch."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [(f"001{str(i).zfill(12)}AAA",) for i in range(200)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            batch_size=200,
            db_conn=mock_conn,
            client=mock_client,
        )

        assert mock_query_all.call_count == 1

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_batch_size_1(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """batch_size=1 should create individual queries for each ID."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [
            ("001000000000001AAA",),
            ("001000000000002AAA",),
            ("001000000000003AAA",),
        ]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            batch_size=1,
            db_conn=mock_conn,
            client=mock_client,
        )

        assert mock_query_all.call_count == 3

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_batch_size_2000_max(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """batch_size=2000 (Salesforce max) should be allowed."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = []
        mock_upsert.return_value = (0, 0)

        sync_content_document_links(
            source_table="sf_contentdocument",
            batch_size=2000,
            db_conn=mock_conn,
            client=mock_client,
        )

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_single_id_in_source_table(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Single ID in source table should work correctly."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = [("001000000000001AAA",)]
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_query_all.return_value = [{"Id": "001", "ContentDocumentId": "001000000000001AAA"}]
        mock_upsert.return_value = (1, 0)

        result = sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
            client=mock_client,
        )

        assert result.records_fetched == 1
        assert mock_query_all.call_count == 1


class TestConnectionManagement:
    """Tests for database and Salesforce connection management."""

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_creates_sf_client_when_not_provided(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Should create Salesforce client when not provided."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        sync_content_document_links(
            source_table="sf_contentdocument",
            db_conn=mock_conn,
        )

        mock_get_client.assert_called_once()

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_creates_db_connection_when_not_provided(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Should create database connection when not provided."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        sync_content_document_links(
            source_table="sf_contentdocument",
            client=mock_client,
        )

        mock_get_connection.assert_called_once()

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_closes_db_connection_when_created(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Should close database connection if it created one."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_connection.return_value = mock_conn

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        sync_content_document_links(
            source_table="sf_contentdocument",
            client=mock_client,
        )

        mock_conn.close.assert_called_once()

    @patch("sf_utils.sync.batched_sync.get_client")
    @patch("sf_utils.sync.batched_sync.get_connection")
    @patch("sf_utils.sync.batched_sync.query_all")
    @patch("sf_utils.sync.batched_sync.create_table_from_query")
    @patch("sf_utils.sync.batched_sync.upsert_records")
    def test_does_not_close_provided_db_connection(
        self,
        mock_upsert,
        mock_create_table,
        mock_query_all,
        mock_get_connection,
        mock_get_client,
    ):
        """Should NOT close database connection if it was provided."""
        mock_conn = Mock()
        mock_cursor = Mock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.close = Mock()
        mock_conn.cursor.return_value = mock_cursor

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        sync_content_document_links(
            source_table="sf_contentdocument",
            client=mock_client,
            db_conn=mock_conn,
        )

        mock_conn.close.assert_not_called()
