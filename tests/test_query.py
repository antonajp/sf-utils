"""Tests for query utilities with retry behavior."""

from unittest.mock import Mock, patch

import pytest

from sf_utils.exceptions import SalesforceRateLimitError, SalesforceAuthError, SalesforceAPIError
from sf_utils.query import query, query_all
from sf_utils.retry import RetryConfig, NO_RETRY_CONFIG, DEFAULT_RETRY_CONFIG


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    import sf_utils.retry
    with patch.object(sf_utils.retry, '_consecutive_failures', 0):
        yield


class TestQueryRetryBehavior:
    """Tests for query() function with retry logic."""

    def test_query_success_no_retry_needed(self):
        """Should return records immediately on success."""
        mock_client = Mock()
        mock_client.query.return_value = (
            {"records": [{"Id": "001", "Name": "Test"}], "done": True},
            200
        )

        records = query("SELECT Id FROM Account", client=mock_client)

        assert len(records) == 1
        assert records[0]["Id"] == "001"
        assert mock_client.query.call_count == 1

    @patch('time.sleep')
    def test_query_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit and succeed."""
        mock_client = Mock()

        # First call raises rate limit, second succeeds
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"records": [{"Id": "001"}], "done": True}, 200)

        mock_client.query = mock_query

        records = query("SELECT Id FROM Account", client=mock_client)

        assert len(records) == 1
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_query_no_retry_with_no_retry_config(self):
        """Should not retry when NO_RETRY_CONFIG is used."""
        mock_client = Mock()
        mock_client.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )

        with pytest.raises(SalesforceRateLimitError):
            query("SELECT Id FROM Account", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1

    def test_query_custom_retry_config(self):
        """Should respect custom retry configuration."""
        mock_client = Mock()
        mock_client.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )

        custom_config = RetryConfig(max_retries=5, initial_backoff=0.1, jitter=0.0)

        with patch('time.sleep'):
            with pytest.raises(SalesforceRateLimitError):
                query("SELECT Id FROM Account", client=mock_client, retry_config=custom_config)

        # Should have called 6 times (initial + 5 retries)
        assert mock_client.query.call_count == 6

    def test_query_auth_error_not_retried(self):
        """Should not retry authentication errors."""
        mock_client = Mock()
        mock_client.query.return_value = (
            {"message": "Unauthorized"},
            401
        )

        with pytest.raises(SalesforceAuthError):
            query("SELECT Id FROM Account", client=mock_client)

        # Should only be called once (no retries)
        assert mock_client.query.call_count == 1

    def test_query_default_retry_config(self):
        """Should use DEFAULT_RETRY_CONFIG by default."""
        mock_client = Mock()
        mock_client.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )

        with patch('time.sleep'):
            with pytest.raises(SalesforceRateLimitError):
                query("SELECT Id FROM Account", client=mock_client)

        # DEFAULT_RETRY_CONFIG has max_retries=3, so 4 total calls
        assert mock_client.query.call_count == 4


class TestQueryAllRetryBehavior:
    """Tests for query_all() function with retry logic."""

    def test_query_all_success_single_page(self):
        """Should return all records from single page."""
        mock_client = Mock()
        mock_client.query.return_value = (
            {"records": [{"Id": "001"}, {"Id": "002"}], "done": True},
            200
        )

        records = query_all("SELECT Id FROM Account", client=mock_client)

        assert len(records) == 2
        assert mock_client.query.call_count == 1

    def test_query_all_success_multiple_pages(self):
        """Should paginate and return all records."""
        mock_client = Mock()

        # First page
        mock_client.query.return_value = (
            {
                "records": [{"Id": "001"}],
                "done": False,
                "nextRecordsUrl": "/query/next1"
            },
            200
        )

        # Second page
        mock_client.query_more.return_value = (
            {
                "records": [{"Id": "002"}],
                "done": True
            },
            200
        )

        records = query_all("SELECT Id FROM Account", client=mock_client)

        assert len(records) == 2
        assert mock_client.query.call_count == 1
        assert mock_client.query_more.call_count == 1

    @patch('time.sleep')
    def test_query_all_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()

        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"records": [{"Id": "001"}], "done": True}, 200)

        mock_client.query = mock_query

        records = query_all("SELECT Id FROM Account", client=mock_client)

        assert len(records) == 1
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_query_all_no_retry_config(self):
        """Should not retry when NO_RETRY_CONFIG is used."""
        mock_client = Mock()
        mock_client.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )

        with pytest.raises(SalesforceRateLimitError):
            query_all("SELECT Id FROM Account", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1

    @patch('time.sleep')
    def test_query_all_retries_with_custom_config(self, mock_sleep):
        """Should use custom retry configuration."""
        mock_client = Mock()
        mock_client.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )

        custom_config = RetryConfig(max_retries=2, initial_backoff=0.1, jitter=0.0)

        with pytest.raises(SalesforceRateLimitError):
            query_all("SELECT Id FROM Account", client=mock_client, retry_config=custom_config)

        # Should call 3 times (initial + 2 retries)
        assert mock_client.query.call_count == 3
        assert mock_sleep.call_count == 2


class TestQueryNoneResponse:
    """Tests for handling None responses."""

    def test_query_none_response_raises_error(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_client.query.return_value = None

        with pytest.raises(SalesforceAPIError, match="no response from Salesforce"):
            query("SELECT Id FROM Account", client=mock_client)

    def test_query_all_none_response_raises_error(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_client.query.return_value = None

        with pytest.raises(SalesforceAPIError, match="no response from Salesforce"):
            query_all("SELECT Id FROM Account", client=mock_client)
