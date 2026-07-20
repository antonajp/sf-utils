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
        # Return empty results for all chunks
        mock_client.query.return_value = ({"records": [], "done": True}, 200)

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
        mock_client.query.return_value = ({"records": [], "done": True}, 200)

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
        mock_client.query.return_value = (
            {"records": [{"Id": "001"}], "done": True},
            200
        )

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
