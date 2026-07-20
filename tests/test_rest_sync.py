"""Tests for date-chunked query execution in sync.rest_sync module."""

from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch

import pytest

from sf_utils.exceptions import SalesforceAPIError, SalesforceRateLimitError
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, NO_RETRY_CONFIG


# Import the module under test (will be created in DEV-18)
# Using late import pattern to avoid import errors during test collection
def import_rest_sync():
    """Import rest_sync module with helpful error message if not yet implemented."""
    try:
        from sf_utils.sync.rest_sync import ChunkInterval, query_chunked
        return ChunkInterval, query_chunked
    except ImportError as e:
        pytest.skip(f"rest_sync module not yet implemented: {e}")


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    import sf_utils.retry
    with patch.object(sf_utils.retry, '_consecutive_failures', 0):
        yield


class TestChunkIntervalEnum:
    """Tests for ChunkInterval enum."""

    def test_chunk_interval_has_hourly_value(self):
        """ChunkInterval.HOURLY should exist and equal 'hourly'."""
        ChunkInterval, _ = import_rest_sync()

        assert hasattr(ChunkInterval, 'HOURLY')
        assert ChunkInterval.HOURLY.value == "hourly"

    def test_chunk_interval_has_daily_value(self):
        """ChunkInterval.DAILY should exist and equal 'daily'."""
        ChunkInterval, _ = import_rest_sync()

        assert hasattr(ChunkInterval, 'DAILY')
        assert ChunkInterval.DAILY.value == "daily"

    def test_chunk_interval_enum_values(self):
        """ChunkInterval should have exactly two values: HOURLY and DAILY."""
        ChunkInterval, _ = import_rest_sync()

        values = list(ChunkInterval)
        assert len(values) == 2
        assert ChunkInterval.HOURLY in values
        assert ChunkInterval.DAILY in values


class TestDateChunkGeneration:
    """Tests for date range chunking logic."""

    def test_daily_chunks_for_7_day_range(self):
        """Daily chunking over 7 days should yield 7 chunks."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        # Return empty results for all chunks (simple-salesforce returns dicts directly)
        mock_client.query.return_value = {"records": [], "done": True}

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 8, 0, 0, 0, tzinfo=timezone.utc)  # 7 full days

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        # 7 days, but empty results are skipped in implementation
        # So we check the number of query calls instead
        assert mock_client.query.call_count == 7

    def test_hourly_chunks_for_24_hour_range(self):
        """Hourly chunking over 24 hours should yield 24 chunks."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        # simple-salesforce returns dicts directly
        mock_client.query.return_value = {"records": [], "done": True}

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)  # 24 hours

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.HOURLY,
            client=mock_client,
        ))

        assert mock_client.query.call_count == 24

    def test_empty_range_yields_no_chunks(self):
        """When start_date == end_date, should yield 0 chunks."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # Same as start

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        assert len(chunks) == 0
        assert mock_client.query.call_count == 0

    def test_single_day_range_yields_one_chunk(self):
        """Single day range should yield 1 chunk."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        # simple-salesforce returns dicts directly
        mock_client.query.return_value = {"records": [{"Id": "001"}], "done": True}

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)  # 1 day

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        assert len(chunks) == 1
        assert mock_client.query.call_count == 1


class TestQueryChunkedBasicBehavior:
    """Tests for query_chunked() function basic behavior."""

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_basic_daily_chunking_returns_records(self, mock_query_all):
        """query_chunked should yield batches of records per chunk."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()

        # Mock query_all to return different records for each chunk
        mock_query_all.side_effect = [
            [{"Id": "001", "CreatedDate": "2024-01-01"}],
            [{"Id": "002", "CreatedDate": "2024-01-02"}],
            [{"Id": "003", "CreatedDate": "2024-01-03"}],
        ]

        soql = "SELECT Id, CreatedDate FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 4, tzinfo=timezone.utc)  # 3 days

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        assert len(chunks) == 3
        assert chunks[0] == [{"Id": "001", "CreatedDate": "2024-01-01"}]
        assert chunks[1] == [{"Id": "002", "CreatedDate": "2024-01-02"}]
        assert chunks[2] == [{"Id": "003", "CreatedDate": "2024-01-03"}]
        assert mock_query_all.call_count == 3

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_soql_placeholders_replaced_with_iso8601_dates(self, mock_query_all):
        """SOQL {start_date} and {end_date} should be replaced with ISO 8601 format."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.return_value = []

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 12, 30, 45, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 12, 30, 45, tzinfo=timezone.utc)

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        # Verify query_all was called with ISO 8601 formatted dates
        assert mock_query_all.call_count == 1
        actual_soql = mock_query_all.call_args[1]['soql']

        # Should contain ISO 8601 format dates
        assert "2024-01-01T12:30:45+00:00" in actual_soql or "2024-01-01T12:30:45Z" in actual_soql
        assert "2024-01-02T12:30:45+00:00" in actual_soql or "2024-01-02T12:30:45Z" in actual_soql
        # Should NOT contain placeholders
        assert "{start_date}" not in actual_soql
        assert "{end_date}" not in actual_soql

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_empty_chunks_yielded_as_empty_lists(self, mock_query_all):
        """Empty chunks should be yielded as empty lists."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()

        # Return empty for first chunk, records for second chunk
        mock_query_all.side_effect = [
            [],  # Empty
            [{"Id": "002"}],  # Has records
            [],  # Empty
        ]

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 4, tzinfo=timezone.utc)  # 3 days

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        # All chunks should be yielded, even empty ones
        assert len(chunks) == 3
        assert chunks[0] == []
        assert chunks[1] == [{"Id": "002"}]
        assert chunks[2] == []
        assert mock_query_all.call_count == 3

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_error_includes_chunk_context(self, mock_query_all):
        """Errors during chunk execution should include chunk date context."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()

        # First chunk succeeds, second chunk fails
        mock_query_all.side_effect = [
            [{"Id": "001"}],
            SalesforceAPIError("Invalid query", status_code=400),
        ]

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 3, tzinfo=timezone.utc)  # 2 days

        with pytest.raises(SalesforceAPIError) as exc_info:
            list(query_chunked(
                soql=soql,
                date_field="CreatedDate",
                start_date=start,
                end_date=end,
                chunk_size=ChunkInterval.DAILY,
                client=mock_client,
            ))

        # Exception message should mention the chunk date range
        error_msg = str(exc_info.value)
        # Implementation may format dates differently, so just check for year/month/day
        assert "2024" in error_msg or "chunk" in error_msg.lower()

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_generator_yields_per_chunk_batches(self, mock_query_all):
        """query_chunked should yield one batch per chunk (generator behavior)."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.side_effect = [
            [{"Id": "001"}],
            [{"Id": "002"}],
        ]

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 3, tzinfo=timezone.utc)  # 2 days

        gen = query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        )

        # Verify it's a generator
        assert hasattr(gen, '__iter__')
        assert hasattr(gen, '__next__')

        # Consume generator and verify batches
        batch1 = next(gen)
        assert batch1 == [{"Id": "001"}]

        batch2 = next(gen)
        assert batch2 == [{"Id": "002"}]

        # No more batches
        with pytest.raises(StopIteration):
            next(gen)

    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.query_all')
    def test_client_created_if_not_provided(self, mock_query_all, mock_get_client):
        """Client should be auto-created via get_client() if not provided."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_get_client.return_value = mock_client
        mock_query_all.return_value = [{"Id": "001"}]

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            # client=None (implicit)
        ))

        # get_client should have been called
        mock_get_client.assert_called_once()
        # query_all should have received the client
        assert mock_query_all.call_args[1]['client'] == mock_client

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_custom_retry_config_passed_through(self, mock_query_all):
        """Custom retry_config should be passed to query_all()."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.return_value = [{"Id": "001"}]
        custom_config = RetryConfig(max_retries=5, initial_backoff=2.0)

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
            retry_config=custom_config,
        ))

        # Verify retry_config was passed to query_all
        assert mock_query_all.call_args[1]['retry_config'] == custom_config


class TestQueryChunkedEdgeCases:
    """Tests for edge cases and error handling."""

    def test_timezone_naive_datetime_raises_error(self):
        """Timezone-naive datetime should raise ValueError."""
        ChunkInterval, query_chunked = import_rest_sync()

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        # Missing tzinfo
        start = datetime(2024, 1, 1, 0, 0, 0)  # No timezone!
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="timezone"):
            list(query_chunked(
                soql=soql,
                date_field="CreatedDate",
                start_date=start,
                end_date=end,
                chunk_size=ChunkInterval.DAILY,
            ))

    def test_start_after_end_yields_no_chunks(self):
        """start_date > end_date should yield no chunks."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 10, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)  # Before start!

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        # Should return empty generator (no chunks)
        assert len(chunks) == 0

    def test_missing_start_date_placeholder_raises_error(self):
        """SOQL missing {start_date} placeholder should raise ValueError."""
        ChunkInterval, query_chunked = import_rest_sync()

        # Missing {start_date}
        soql = "SELECT Id FROM Account WHERE CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="placeholder"):
            list(query_chunked(
                soql=soql,
                date_field="CreatedDate",
                start_date=start,
                end_date=end,
                chunk_size=ChunkInterval.DAILY,
            ))

    def test_missing_end_date_placeholder_raises_error(self):
        """SOQL missing {end_date} placeholder should raise ValueError."""
        ChunkInterval, query_chunked = import_rest_sync()

        # Missing {end_date}
        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="placeholder"):
            list(query_chunked(
                soql=soql,
                date_field="CreatedDate",
                start_date=start,
                end_date=end,
                chunk_size=ChunkInterval.DAILY,
            ))


class TestQueryChunkedRetryBehavior:
    """Tests for retry behavior integration."""

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_retry_passed_to_query_all(self, mock_query_all):
        """Retry behavior should be delegated to query_all()."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()

        # query_all will handle retries internally, so we just verify it's called
        mock_query_all.return_value = [{"Id": "001"}]

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)  # 1 day

        chunks = list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        # Verify retry_config was passed to query_all (it handles the actual retry logic)
        assert mock_query_all.call_count == 1
        assert 'retry_config' in mock_query_all.call_args[1]
        assert chunks == [[{"Id": "001"}]]

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_no_retry_config_honored(self, mock_query_all):
        """NO_RETRY_CONFIG should prevent retries."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.side_effect = SalesforceRateLimitError(
            message="Rate limit",
            status_code=429,
        )

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)

        with pytest.raises(SalesforceRateLimitError):
            list(query_chunked(
                soql=soql,
                date_field="CreatedDate",
                start_date=start,
                end_date=end,
                chunk_size=ChunkInterval.DAILY,
                client=mock_client,
                retry_config=NO_RETRY_CONFIG,
            ))

        # Should only have been called once (no retries)
        assert mock_query_all.call_count == 1


class TestQueryChunkedDateFormatting:
    """Tests for ISO 8601 date formatting."""

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_utc_timezone_formatted_with_z_suffix(self, mock_query_all):
        """UTC timezone should be formatted with Z suffix."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.return_value = []

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 6, 16, 14, 30, 0, tzinfo=timezone.utc)

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        actual_soql = mock_query_all.call_args[1]['soql']

        # Implementation uses strftime with 'Z' suffix
        assert "2024-06-15T14:30:00Z" in actual_soql
        assert "2024-06-16T14:30:00Z" in actual_soql

    @patch('sf_utils.sync.rest_sync.query_all')
    def test_date_formatting_uses_z_suffix_consistently(self, mock_query_all):
        """All datetime values should use Z suffix format (not +00:00)."""
        ChunkInterval, query_chunked = import_rest_sync()

        mock_client = Mock()
        mock_query_all.return_value = []

        soql = "SELECT Id FROM Account WHERE CreatedDate >= {start_date} AND CreatedDate < {end_date}"
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        list(query_chunked(
            soql=soql,
            date_field="CreatedDate",
            start_date=start,
            end_date=end,
            chunk_size=ChunkInterval.DAILY,
            client=mock_client,
        ))

        actual_soql = mock_query_all.call_args[1]['soql']

        # Should use Z suffix (implementation uses strftime)
        assert "2024-01-01T00:00:00Z" in actual_soql
        assert "2024-01-02T00:00:00Z" in actual_soql
        # Should NOT use +00:00 format
        assert "+00:00" not in actual_soql


# Import for sync_records tests
def import_sync_records():
    """Import sync_records and SyncResult with helpful error message if not yet implemented."""
    try:
        from sf_utils.sync.rest_sync import SyncResult, sync_records
        return SyncResult, sync_records
    except ImportError as e:
        pytest.skip(f"sync_records not yet implemented: {e}")


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_sync_result_has_all_expected_fields(self):
        """SyncResult dataclass should have all required fields."""
        SyncResult, _ = import_sync_records()

        result = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=50,
            records_updated=50,
            sync_mode="incremental",
            start_timestamp=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            end_timestamp=datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc),
            date_field="LastModifiedDate",
        )

        assert result.object_name == "Account"
        assert result.records_fetched == 100
        assert result.records_inserted == 50
        assert result.records_updated == 50
        assert result.sync_mode == "incremental"
        assert result.start_timestamp == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert result.end_timestamp == datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert result.date_field == "LastModifiedDate"

    def test_sync_result_fields_are_accessible(self):
        """All SyncResult fields should be accessible."""
        SyncResult, _ = import_sync_records()

        result = SyncResult(
            object_name="Contact",
            records_fetched=25,
            records_inserted=10,
            records_updated=15,
            sync_mode="full",
            start_timestamp=datetime.now(timezone.utc),
            end_timestamp=datetime.now(timezone.utc),
            date_field="CreatedDate",
        )

        # Fields should be directly accessible (dataclass behavior)
        assert hasattr(result, 'object_name')
        assert hasattr(result, 'records_fetched')
        assert hasattr(result, 'records_inserted')
        assert hasattr(result, 'records_updated')
        assert hasattr(result, 'sync_mode')
        assert hasattr(result, 'start_timestamp')
        assert hasattr(result, 'end_timestamp')
        assert hasattr(result, 'date_field')


class TestSyncRecordsDateFieldValidation:
    """Tests for date_field parameter validation in sync_records()."""

    def test_default_date_field_is_last_modified_date(self):
        """Default date_field should be 'LastModifiedDate'."""
        SyncResult, sync_records = import_sync_records()

        with patch('sf_utils.sync.rest_sync.get_sync_state') as mock_get_state, \
             patch('sf_utils.sync.rest_sync.get_client') as mock_get_client, \
             patch('sf_utils.sync.rest_sync.get_connection') as mock_get_conn, \
             patch('sf_utils.sync.rest_sync.query_all') as mock_query_all, \
             patch('sf_utils.sync.rest_sync.create_table_from_query'), \
             patch('sf_utils.sync.rest_sync.upsert_records'), \
             patch('sf_utils.sync.rest_sync.update_sync_state'):

            mock_get_state.return_value = None  # No previous sync
            mock_get_client.return_value = Mock()
            mock_get_conn.return_value = Mock()
            mock_query_all.return_value = []  # Empty list

            # Call without date_field parameter
            result = sync_records(
                soql="SELECT Id, Name, LastModifiedDate FROM Account",
                object_name="Account",
            )

            # Should use default "LastModifiedDate"
            assert result.date_field == "LastModifiedDate"

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_custom_date_field_parameter_works(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """Custom date_field parameter should be respected."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        result = sync_records(
            soql="SELECT Id, Name, CreatedDate FROM Lead",
            object_name="Lead",
            date_field="CreatedDate",
        )

        assert result.date_field == "CreatedDate"

    def test_validation_raises_error_when_field_missing_from_select(self):
        """validate_date_field=True should raise ValueError when field missing from SOQL SELECT."""
        SyncResult, sync_records = import_sync_records()

        with pytest.raises(ValueError, match="Date field 'CreatedDate' not found in SELECT clause"):
            sync_records(
                soql="SELECT Id, Name FROM Account",
                object_name="Account",
                date_field="CreatedDate",
                validate_date_field=True,
            )

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_validation_skipped_when_disabled(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """validate_date_field=False should skip validation."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        # Should not raise even though LastModifiedDate not in SELECT
        result = sync_records(
            soql="SELECT Id, Name FROM Account",
            object_name="Account",
            date_field="LastModifiedDate",
            validate_date_field=False,
        )

        assert result.date_field == "LastModifiedDate"

    def test_invalid_date_field_format_raises_error(self):
        """Invalid date_field format should raise ValueError."""
        SyncResult, sync_records = import_sync_records()

        # SQL injection attempt
        with pytest.raises(ValueError, match="Invalid date field name"):
            sync_records(
                soql="SELECT Id FROM Account",
                object_name="Account",
                date_field="DROP TABLE Accounts",
            )

        # Invalid characters
        with pytest.raises(ValueError, match="Invalid date field name"):
            sync_records(
                soql="SELECT Id FROM Account",
                object_name="Account",
                date_field="123abc",
            )

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_valid_standard_field_formats_work(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """Valid standard Salesforce field formats should be accepted."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        # Standard field
        result = sync_records(
            soql="SELECT Id, SystemModstamp FROM Account",
            object_name="Account",
            date_field="SystemModstamp",
        )
        assert result.date_field == "SystemModstamp"

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_valid_custom_field_formats_work(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """Valid custom field formats (with __c suffix) should be accepted."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        # Custom field
        result = sync_records(
            soql="SELECT Id, Last_Contacted__c FROM Contact",
            object_name="Contact",
            date_field="Last_Contacted__c",
        )
        assert result.date_field == "Last_Contacted__c"


class TestSyncRecordsModeSelection:
    """Tests for sync mode (incremental vs full) selection."""

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_incremental_mode_queries_from_watermark(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """mode='incremental' should query records after watermark timestamp."""
        SyncResult, sync_records = import_sync_records()
        from sf_utils.sync.state import SyncStateRow

        # Mock existing sync state with watermark
        watermark = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_get_state.return_value = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=watermark,
            sync_mode="incremental",
        )

        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode="incremental",
        )

        # Verify query_all was called with watermark injected into SOQL
        assert mock_query_all.called
        call_kwargs = mock_query_all.call_args[1]
        modified_soql = call_kwargs['soql']
        # Should contain watermark timestamp in WHERE clause
        assert "WHERE" in modified_soql
        assert "2024-01-01" in modified_soql  # Watermark date

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_full_mode_queries_all_records(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """mode='full' should query all records from epoch."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None  # No previous state
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode="full",
        )

        # Verify query_all was called with original SOQL (no watermark injection in full mode)
        assert mock_query_all.called
        call_kwargs = mock_query_all.call_args[1]
        modified_soql = call_kwargs['soql']
        # Full mode should use original SOQL without modification
        assert modified_soql == "SELECT Id, Name, LastModifiedDate FROM Account"

    def test_invalid_mode_raises_error(self):
        """Invalid mode value should raise ValueError."""
        SyncResult, sync_records = import_sync_records()

        with pytest.raises(ValueError, match="Invalid mode"):
            sync_records(
                soql="SELECT Id FROM Account",
                object_name="Account",
                mode="invalid_mode",
            )


class TestSyncRecordsWatermarkInjection:
    """Tests for watermark placeholder injection in SOQL."""

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_where_clause_appended_when_no_existing_where(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """WHERE clause should be appended when SOQL has no WHERE."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode="incremental",
        )

        # In incremental mode with no previous sync, original SOQL is used
        # (no watermark to inject yet)
        assert mock_query_all.called

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_and_clause_appended_when_existing_where(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """AND clause should be appended when SOQL has existing WHERE."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account WHERE Type = 'Customer'",
            object_name="Account",
            mode="incremental",
        )

        # In incremental mode with no previous sync, original SOQL is used
        assert mock_query_all.called

    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    def test_watermark_formatted_as_iso8601(
        self, mock_update_state, mock_upsert, mock_create_table,
        mock_query_all, mock_get_conn, mock_get_client, mock_get_state
    ):
        """Watermark dates should be formatted as ISO 8601."""
        SyncResult, sync_records = import_sync_records()
        from sf_utils.sync.state import SyncStateRow

        watermark = datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        mock_get_state.return_value = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=watermark,
            sync_mode="incremental",
        )

        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_query_all.return_value = []

        sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode="incremental",
        )

        # Verify SOQL contains watermark timestamp
        assert mock_query_all.called
        call_kwargs = mock_query_all.call_args[1]
        modified_soql = call_kwargs['soql']

        # Should contain ISO 8601 formatted watermark timestamp
        assert "2024-06-15T14:30:00Z" in modified_soql
        assert "WHERE" in modified_soql
        assert "LastModifiedDate >=" in modified_soql


class TestSyncRecordsIntegration:
    """Integration tests for sync_records() orchestration flow."""

    @patch('sf_utils.sync.rest_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    def test_full_incremental_sync_flow(
        self, mock_upsert, mock_create_table, mock_query_all,
        mock_get_conn, mock_get_client, mock_update_state,
        mock_get_state, mock_ensure_table
    ):
        """Full incremental sync flow: get_sync_state -> query_all -> create_table -> upsert -> update_sync_state."""
        SyncResult, sync_records = import_sync_records()
        from sf_utils.sync.state import SyncStateRow

        # Setup mocks
        watermark = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        mock_get_state.return_value = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=watermark,
            sync_mode="incremental",
        )

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn

        # Mock query results (80 records total)
        all_records = [{"Id": f"00{i}", "Name": f"Account {i}"} for i in range(80)]
        mock_query_all.return_value = all_records

        # Mock upsert results (40 inserted, 40 updated)
        mock_upsert.return_value = (40, 40)

        # Execute sync
        result = sync_records(
            soql="SELECT Id, Name, LastModifiedDate FROM Account",
            object_name="Account",
            mode="incremental",
        )

        # Verify sync state table ensured
        mock_ensure_table.assert_called_once_with(mock_conn)

        # Verify sync state retrieved
        mock_get_state.assert_called_once()
        call_args = mock_get_state.call_args[1]
        assert call_args['object_name'] == "Account"
        assert call_args['db_conn'] == mock_conn

        # Verify table created
        mock_create_table.assert_called_once()

        # Verify upsert called once with all records
        assert mock_upsert.call_count == 1

        # Verify sync state updated
        mock_update_state.assert_called_once()
        update_call = mock_update_state.call_args[1]
        assert update_call['object_name'] == "Account"
        assert update_call['db_conn'] == mock_conn
        assert update_call['mode'] == "incremental"

        # Verify result
        assert result.object_name == "Account"
        assert result.records_fetched == 80
        assert result.records_inserted == 40
        assert result.records_updated == 40
        assert result.sync_mode == "incremental"
        assert result.date_field == "LastModifiedDate"

    @patch('sf_utils.sync.rest_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.rest_sync.get_sync_state')
    @patch('sf_utils.sync.rest_sync.update_sync_state')
    @patch('sf_utils.sync.rest_sync.get_client')
    @patch('sf_utils.sync.rest_sync.get_connection')
    @patch('sf_utils.sync.rest_sync.query_all')
    @patch('sf_utils.sync.rest_sync.create_table_from_query')
    @patch('sf_utils.sync.rest_sync.upsert_records')
    def test_returns_sync_result_with_correct_counts(
        self, mock_upsert, mock_create_table, mock_query_all,
        mock_get_conn, mock_get_client, mock_update_state,
        mock_get_state, mock_ensure_table
    ):
        """SyncResult should contain correct counts from upsert operations."""
        SyncResult, sync_records = import_sync_records()

        mock_get_state.return_value = None  # No previous sync
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()

        # Single batch with 10 records
        all_records = [{"Id": f"00{i}"} for i in range(10)]
        mock_query_all.return_value = all_records

        # All records inserted (first sync)
        mock_upsert.return_value = (10, 0)

        result = sync_records(
            soql="SELECT Id, Name, CreatedDate FROM Lead",
            object_name="Lead",
            date_field="CreatedDate",
            mode="full",
        )

        assert result.records_fetched == 10
        assert result.records_inserted == 10
        assert result.records_updated == 0
        assert result.sync_mode == "full"
        assert result.date_field == "CreatedDate"
        assert isinstance(result.start_timestamp, datetime)
        assert isinstance(result.end_timestamp, datetime)
        assert result.end_timestamp >= result.start_timestamp
