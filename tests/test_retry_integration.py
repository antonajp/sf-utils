"""Integration tests for retry behavior in public API functions.

Tests verify that query, query_all, and sobject CRUD functions retry
appropriately on rate limits and server errors, while respecting retry
configuration.

NOTE: simple-salesforce raises exceptions for errors, not (body, status) tuples.
"""

from unittest.mock import Mock, patch, MagicMock

import pytest
from simple_salesforce.exceptions import SalesforceError as SimpleSalesforceError

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


def _make_salesforce_error(status_code: int, message: str) -> SimpleSalesforceError:
    """Create a SimpleSalesforceError with specified status code."""
    error = SimpleSalesforceError(
        url="https://test.salesforce.com/services/data/v61.0/query",
        status=status_code,
        resource_name="query",
        content=message.encode()
    )
    return error


@pytest.fixture
def mock_client():
    """Create a mock Salesforce client."""
    return MagicMock()


class TestQueryRetryBehavior:
    """Tests for query() function retry behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_query_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit (429) is returned, then succeed."""
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return {"records": [{"Id": "001xx", "Name": "Test"}], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id, Name FROM Account")

        assert len(result) == 1
        assert result[0]["Id"] == "001xx"
        assert call_count == 2
        assert mock_sleep.call_count == 1  # Slept once before retry

    def test_query_no_retry_on_auth_error(self, mock_client):
        """Should NOT retry when authentication fails (401)."""
        mock_client.query.side_effect = _make_salesforce_error(401, "INVALID_SESSION_ID")

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAuthError) as exc_info:
                query("SELECT Id FROM Account")

        assert exc_info.value.status_code == 401
        assert mock_client.query.call_count == 1  # Only called once, no retry

    @patch('sf_utils.retry.time.sleep')
    def test_query_respects_no_retry_config(self, mock_sleep, mock_client):
        """Should NOT retry when NO_RETRY_CONFIG is passed."""
        mock_client.query.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                query("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1  # No retries
        assert mock_sleep.call_count == 0  # Never slept

    @patch('sf_utils.retry.time.sleep')
    def test_query_retries_on_server_error(self, mock_sleep, mock_client):
        """Should retry on 5xx server errors."""
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(500, "Internal server error")
            return {"records": [{"Id": "001xx"}], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert len(result) == 1
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_query_no_retry_on_client_error(self, mock_client):
        """Should NOT retry on 4xx client errors (except rate limits)."""
        mock_client.query.side_effect = _make_salesforce_error(400, "INVALID_FIELD")

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAPIError) as exc_info:
                query("SELECT BadField__c FROM Account")

        assert exc_info.value.status_code == 400
        assert mock_client.query.call_count == 1  # No retry on 4xx

    @patch('sf_utils.retry.time.sleep')
    def test_query_respects_retry_after_header(self, mock_sleep, mock_client):
        """Should respect Retry-After header from rate limit response."""
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "Rate limited")
            return {"records": [], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert result == []
        assert call_count == 2
        assert mock_sleep.call_count == 1


class TestQueryAllRetryBehavior:
    """Tests for query_all() function retry behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit is hit during initial query."""
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "Too many requests")
            return {"records": [{"Id": "001xx"}], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 1
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_pagination_error_retries(self, mock_sleep, mock_client):
        """Should retry when rate limit occurs during pagination."""
        mock_client.query.return_value = {
            "records": [{"Id": "001xx"}],
            "done": False,
            "nextRecordsUrl": "/services/data/v61.0/query/01gxx-2000"
        }

        call_count = 0
        def mock_query_more(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "Rate limited")
            return {"records": [{"Id": "002xx"}], "done": True}

        mock_client.query_more = mock_query_more

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 2
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_handles_multiple_pages(self, mock_sleep, mock_client):
        """Should handle pagination across multiple pages with retry."""
        mock_client.query.return_value = {
            "records": [{"Id": "001xx"}],
            "done": False,
            "nextRecordsUrl": "/query/page2"
        }

        call_count = 0
        def mock_query_more(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "Rate limited")
            elif call_count == 2:
                return {"records": [{"Id": "002xx"}], "done": False, "nextRecordsUrl": "/query/page3"}
            else:
                return {"records": [{"Id": "003xx"}], "done": True}

        mock_client.query_more = mock_query_more

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query_all("SELECT Id FROM Account")

        assert len(result) == 3
        assert call_count == 3
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_query_all_no_retry_config(self, mock_sleep, mock_client):
        """Should not retry when NO_RETRY_CONFIG is passed."""
        mock_client.query.side_effect = _make_salesforce_error(429, "Rate limited")

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                query_all("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)

        assert mock_client.query.call_count == 1
        assert mock_sleep.call_count == 0


class TestSObjectRetryBehavior:
    """Tests for sobject CRUD function retry behavior."""

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_retries_on_rate_limit(self, mock_sleep, mock_client):
        """Should retry when rate limit is hit during create."""
        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject

        call_count = 0
        def mock_create(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "Too many requests")
            return {"id": "001xx", "success": True, "errors": []}

        mock_sobject.create = mock_create

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            record_id = create_record("Account", {"Name": "Test Account"})

        assert record_id == "001xx"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_retries_on_server_error(self, mock_sleep, mock_client):
        """Should retry on 500 server errors."""
        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject

        call_count = 0
        def mock_create(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(500, "Internal server error")
            return {"id": "001xx", "success": True, "errors": []}

        mock_sobject.create = mock_create

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            record_id = create_record("Account", {"Name": "Test Account"})

        assert record_id == "001xx"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_record_no_retry_on_client_error(self, mock_client):
        """Should NOT retry on 4xx client errors."""
        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject
        mock_sobject.create.side_effect = _make_salesforce_error(400, "REQUIRED_FIELD_MISSING")

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAPIError) as exc_info:
                create_record("Account", {})

        assert exc_info.value.status_code == 400
        assert mock_sobject.create.call_count == 1  # No retry

    def test_create_record_no_retry_on_auth_error(self, mock_client):
        """Should NOT retry on authentication errors."""
        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject
        mock_sobject.create.side_effect = _make_salesforce_error(401, "Session expired")

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceAuthError):
                create_record("Account", {"Name": "Test"})

        assert mock_sobject.create.call_count == 1

    @patch('sf_utils.retry.time.sleep')
    def test_create_record_no_retry_config(self, mock_sleep, mock_client):
        """Should not retry when NO_RETRY_CONFIG is passed."""
        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject
        mock_sobject.create.side_effect = _make_salesforce_error(429, "Rate limited")

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                create_record("Account", {"Name": "Test"}, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.create.call_count == 1
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

        mock_client.query.side_effect = _make_salesforce_error(429, "Rate limited")

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

        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _make_salesforce_error(429, "Rate limited")
            return {"records": [], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account", retry_config=custom_config)

        assert result == []
        assert call_count == 3
        assert mock_sleep.call_count == 2

        # Should have slept for ~5.0s each time (initial_backoff, no multiplier)
        for call in mock_sleep.call_args_list:
            assert 4.5 <= call[0][0] <= 5.5

    @patch('sf_utils.retry.time.sleep')
    def test_custom_config_on_sobject_functions(self, mock_sleep, mock_client):
        """Should apply custom config to sobject functions."""
        custom_config = RetryConfig(max_retries=0)

        mock_sobject = MagicMock()
        mock_client.Account = mock_sobject
        mock_sobject.create.side_effect = _make_salesforce_error(429, "Rate limited")

        with patch('sf_utils.sobjects.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError):
                create_record("Account", {"Name": "Test"}, retry_config=custom_config)

        # max_retries=0 means no retries
        assert mock_sobject.create.call_count == 1
        assert mock_sleep.call_count == 0


class TestRetryEdgeCases:
    """Tests for edge cases and error scenarios."""

    @patch('sf_utils.retry.time.sleep')
    def test_retry_exhausted_raises_original_error(self, mock_sleep, mock_client):
        """Should raise the original error after retries are exhausted."""
        mock_client.query.side_effect = _make_salesforce_error(429, "Rate limit exceeded")

        with patch('sf_utils.query.get_client', return_value=mock_client):
            with pytest.raises(SalesforceRateLimitError) as exc_info:
                query("SELECT Id FROM Account", retry_config=RetryConfig(max_retries=2))

        assert "Rate limit exceeded" in str(exc_info.value)
        assert mock_client.query.call_count == 3  # Initial + 2 retries

    @patch('sf_utils.retry.time.sleep')
    def test_alternating_errors_retries_appropriately(self, mock_sleep, mock_client):
        """Should handle different error types during retries."""
        call_count = 0
        def mock_query(soql):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(500, "Server error")
            elif call_count == 2:
                raise _make_salesforce_error(429, "Rate limited")
            return {"records": [{"Id": "001xx"}], "done": True}

        mock_client.query = mock_query

        with patch('sf_utils.query.get_client', return_value=mock_client):
            result = query("SELECT Id FROM Account")

        assert len(result) == 1
        assert call_count == 3
        assert mock_sleep.call_count == 2
