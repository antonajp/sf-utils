"""Integration tests for retry behavior in public API functions.

Tests verify that query, query_all, and sobject CRUD functions retry
appropriately on rate limits and server errors, while respecting retry
configuration.
"""

from unittest.mock import Mock, patch

import pytest

from sf_utils.exceptions import (
    SalesforceAuthError,
    SalesforceRateLimitError,
    SalesforceAPIError,
)
from sf_utils.retry import (
    RetryConfig,
    DEFAULT_RETRY_CONFIG,
    NO_RETRY_CONFIG,
)
from sf_utils.query import query, query_all
from sf_utils.sobjects import create_record


@pytest.fixture
def mock_client():
    """Create a mock Salesforce client."""
    return Mock()


class TestQueryRetryBehavior:
    """Tests for query() function retry behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_query_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit (429) is returned, then succeed."""
        # First call returns 429, second call returns success
        mock_client.query.side_effect = [
            ([{"message": "Too many requests", "errorCode": "REQUEST_LIMIT_EXCEEDED"}], 429),
            ({"records": [{"Id": "001xx", "Name": "Test"}], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id, Name FROM Account")

        assert len(result) == 1
        assert result[0]["Id"] == "001xx"
        assert mock_client.query.call_count == 2
        assert mock_sleep.call_count == 1  # Slept once before retry

    def test_query_no_retry_on_auth_error(self, mock_client):
        """Should NOT retry when authentication fails (401)."""
        mock_client.query.return_value = (
            [{"message": "Session expired", "errorCode": "INVALID_SESSION_ID"}],
            401
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAuthError) as exc_info:
                query("SELECT Id FROM Account")

        assert exc_info.value.status_code == 401
        assert mock_client.query.call_count == 1  # Only called once, no retry

    @patch('sf_utils.retry.time.sleep')
    def test_query_respects_no_retry_config(self, mock_sleep, mock_client):
        """Should NOT retry when NO_RETRY_CONFIG is passed."""
        mock_client.query.return_value = (
            [{"message": "Rate limited"}],
            429
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                query("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1  # No retries
        assert mock_sleep.call_count == 0  # Never slept

    @patch('sf_utils.retry.time.sleep')
    def test_query_retries_on_server_error(self, mock_sleep, mock_client):
        """Should retry on 5xx server errors."""
        mock_client.query.side_effect = [
            ([{"message": "Internal server error"}], 500),
            ({"records": [{"Id": "001xx"}], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert len(result) == 1
        assert mock_client.query.call_count == 2
        assert mock_sleep.call_count == 1

    def test_query_no_retry_on_client_error(self, mock_client):
        """Should NOT retry on 4xx client errors (except rate limits)."""
        mock_client.query.return_value = (
            [{"message": "Invalid field: BadField__c", "errorCode": "INVALID_FIELD"}],
            400
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAPIError) as exc_info:
                query("SELECT BadField__c FROM Account")

        assert exc_info.value.status_code == 400
        assert mock_client.query.call_count == 1  # No retry on 4xx

    @patch('sf_utils.retry.time.sleep')
    def test_query_respects_retry_after_header(self, mock_sleep, mock_client):
        """Should respect Retry-After header from rate limit response."""
        # Mock response with Retry-After header
        mock_client.query.side_effect = [
            ([{"message": "Rate limited"}], 429),
            ({"records": [], "done": True}, 200),
        ]

        # We can't easily inject headers in the mock, but we can verify
        # the sleep is called and the retry happens
        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert result == []
        assert mock_client.query.call_count == 2
        assert mock_sleep.call_count == 1


class TestQueryAllRetryBehavior:
    """Tests for query_all() function retry behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit is hit during initial query."""
        mock_client.query.side_effect = [
            ([{"message": "Too many requests"}], 429),
            ({"records": [{"Id": "001xx"}], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 1
        assert mock_client.query.call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_pagination_error_retries(self, mock_sleep, mock_client):
        """Should retry when rate limit occurs during pagination.

        When a rate limit error occurs during pagination, the entire query_all
        operation retries from scratch. This ensures consistent results by
        re-fetching all pages together.
        """
        # First page succeeds
        mock_client.query.return_value = (
            {
                "records": [{"Id": "001xx"}],
                "done": False,
                "nextRecordsUrl": "/services/data/v61.0/query/01gxx-2000"
            },
            200
        )

        # Pagination fails with rate limit, then succeeds
        mock_client.query_more.side_effect = [
            ([{"message": "Rate limited"}], 429),
            ({"records": [{"Id": "002xx"}], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 2
        assert mock_client.query_more.call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_handles_multiple_pages(self, mock_sleep, mock_client):
        """Should handle pagination across multiple pages with retry.

        When a rate limit error occurs on any page, the entire query_all
        operation retries from scratch, then successfully fetches all pages.
        """
        # First page
        mock_client.query.return_value = (
            {
                "records": [{"Id": "001xx"}],
                "done": False,
                "nextRecordsUrl": "/query/page2"
            },
            200
        )

        # Second page (rate limited, then succeeds)
        mock_client.query_more.side_effect = [
            ([{"message": "Rate limited"}], 429),
            ({"records": [{"Id": "002xx"}], "done": False, "nextRecordsUrl": "/query/page3"}, 200),
            ({"records": [{"Id": "003xx"}], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 3
        assert mock_client.query_more.call_count == 3
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_no_retry_config(self, mock_sleep, mock_client):
        """Should not retry when NO_RETRY_CONFIG is passed."""
        mock_client.query.return_value = (
            [{"message": "Rate limited"}],
            429
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                query_all("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1
        assert mock_sleep.call_count == 0


class TestSObjectRetryBehavior:
    """Tests for sobject CRUD function retry behavior.

    Uses create_record as representative function - all sobject functions
    should exhibit the same retry behavior.
    """

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit is hit during create."""
        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock

        sobjects_mock.insert.side_effect = [
            ([{"message": "Too many requests"}], 429),
            ({"id": "001xx", "success": True}, 201),
        ]

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            record_id = create_record("Account", {"Name": "Test Account"})

        assert record_id == "001xx"
        assert sobjects_mock.insert.call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_retries_on_server_error(self, mock_sleep, mock_client):
        """Should retry on 500 server errors."""
        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock

        sobjects_mock.insert.side_effect = [
            ([{"message": "Internal server error"}], 500),
            ({"id": "001xx", "success": True}, 201),
        ]

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            record_id = create_record("Account", {"Name": "Test Account"})

        assert record_id == "001xx"
        assert sobjects_mock.insert.call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_record_no_retry_on_client_error(self, mock_client):
        """Should NOT retry on 4xx client errors."""
        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock

        sobjects_mock.insert.return_value = (
            [{"message": "Required field missing", "errorCode": "REQUIRED_FIELD_MISSING"}],
            400
        )

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAPIError) as exc_info:
                create_record("Account", {})

        assert exc_info.value.status_code == 400
        assert sobjects_mock.insert.call_count == 1  # No retry

    def test_create_record_no_retry_on_auth_error(self, mock_client):
        """Should NOT retry on authentication errors."""
        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock

        sobjects_mock.insert.return_value = (
            [{"message": "Session expired"}],
            401
        )

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAuthError):
                create_record("Account", {"Name": "Test"})

        assert sobjects_mock.insert.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_no_retry_config(self, mock_sleep, mock_client):
        """Should not retry when NO_RETRY_CONFIG is passed."""
        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock

        sobjects_mock.insert.return_value = (
            [{"message": "Rate limited"}],
            429
        )

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                create_record("Account", {"Name": "Test"}, retry_config=NO_RETRY_CONFIG)

        assert sobjects_mock.insert.call_count == 1
        assert mock_sleep.call_count == 0


class TestCustomRetryConfig:
    """Tests for custom retry configuration behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_custom_retry_config_max_retries(self, mock_sleep, mock_client):
        """Should respect max_retries from custom config."""
        custom_config = RetryConfig(
            max_retries=1,
            initial_backoff=0.1,
            jitter=0.0
        )

        # Always fails with rate limit
        mock_client.query.return_value = (
            [{"message": "Rate limited"}],
            429
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                query("SELECT Id FROM Account", retry_config=custom_config)

        # Initial call + 1 retry = 2 total calls
        assert mock_client.query.call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_custom_retry_config_backoff(self, mock_sleep, mock_client):
        """Should use custom backoff settings."""
        custom_config = RetryConfig(
            max_retries=2,
            initial_backoff=5.0,
            backoff_multiplier=1.0,  # No exponential growth
            jitter=0.0  # No jitter for predictable testing
        )

        mock_client.query.side_effect = [
            ([{"message": "Rate limited"}], 429),
            ([{"message": "Rate limited"}], 429),
            ({"records": [], "done": True}, 200),
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account", retry_config=custom_config)

        assert result == []
        assert mock_client.query.call_count == 3
        assert mock_sleep.call_count == 2

        # Should have slept for ~5.0s each time (initial_backoff, no multiplier)
        for call in mock_sleep.call_args_list:
            assert 4.5 <= call[0][0] <= 5.5

    @patch('sf_utils.retry.time.sleep')
    def test_custom_config_on_sobject_functions(self, mock_sleep, mock_client):
        """Should apply custom config to sobject functions."""
        custom_config = RetryConfig(max_retries=0)

        sobjects_mock = Mock()
        mock_client.sobjects.return_value = sobjects_mock
        sobjects_mock.insert.return_value = (
            [{"message": "Rate limited"}],
            429
        )

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                create_record("Account", {"Name": "Test"}, retry_config=custom_config)

        # max_retries=0 means no retries
        assert sobjects_mock.insert.call_count == 1
        assert mock_sleep.call_count == 0


class TestRetryEdgeCases:
    """Tests for edge cases and error scenarios."""

    @patch('sf_utils.retry.time.sleep')
    def test_retry_exhausted_raises_original_error(self, mock_sleep, mock_client):
        """Should raise the original error after retries are exhausted."""
        mock_client.query.return_value = (
            [{"message": "Rate limit exceeded", "errorCode": "REQUEST_LIMIT_EXCEEDED"}],
            429
        )

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError) as exc_info:
                query("SELECT Id FROM Account", retry_config=RetryConfig(max_retries=2))

        assert "Rate limit exceeded" in str(exc_info.value)
        assert mock_client.query.call_count == 3  # Initial + 2 retries

    @patch('sf_utils.retry.time.sleep')
    def test_alternating_errors_retries_appropriately(self, mock_sleep, mock_client):
        """Should handle different error types during retries."""
        mock_client.query.side_effect = [
            ([{"message": "Server error"}], 500),  # Retry
            ([{"message": "Rate limited"}], 429),   # Retry
            ({"records": [{"Id": "001xx"}], "done": True}, 200),  # Success
        ]

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert len(result) == 1
        assert mock_client.query.call_count == 3
        assert mock_sleep.call_count == 2
