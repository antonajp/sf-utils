"""Tests for sobjects CRUD operations with retry behavior."""

from unittest.mock import Mock, patch, MagicMock

import pytest
from simple_salesforce.exceptions import SalesforceError as SimpleSalesforceError

from sf_utils.exceptions import SalesforceRateLimitError, SalesforceAuthError, SalesforceAPIError
from sf_utils.sobjects import (
    get_record,
    create_record,
    update_record,
    upsert_record,
    delete_record,
    describe_object,
)
from sf_utils.retry import RetryConfig, NO_RETRY_CONFIG, DEFAULT_RETRY_CONFIG


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    import sf_utils.retry
    with patch.object(sf_utils.retry, '_consecutive_failures', 0):
        yield


def _make_salesforce_error(status_code: int, message: str) -> SimpleSalesforceError:
    """Create a SimpleSalesforceError with specified status code."""
    # simple-salesforce SalesforceError(url, status, resource_name, content)
    error = SimpleSalesforceError(
        url="https://test.salesforce.com/services/data/v61.0/sobjects",
        status=status_code,
        resource_name="Account",
        content=message.encode()
    )
    return error


class TestGetRecordRetryBehavior:
    """Tests for get_record() with retry logic."""

    def test_get_record_success(self):
        """Should retrieve record successfully."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.get.return_value = {"Id": "001", "Name": "Test"}
        mock_client.Account = mock_sobject

        record = get_record("Account", "001", client=mock_client)

        assert record["Id"] == "001"
        assert mock_sobject.get.call_count == 1

    @patch('time.sleep')
    def test_get_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_get(record_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return {"Id": "001", "Name": "Test"}

        mock_sobject.get = mock_get
        mock_client.Account = mock_sobject

        record = get_record("Account", "001", client=mock_client)

        assert record["Id"] == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_get_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.get.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            get_record("Account", "001", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.get.call_count == 1

    def test_get_record_with_fields(self):
        """Should pass fields parameter correctly."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.get.return_value = {"Id": "001", "Name": "Test"}
        mock_client.Account = mock_sobject

        record = get_record("Account", "001", fields=["Id", "Name"], client=mock_client)

        assert record["Id"] == "001"
        mock_sobject.get.assert_called_once_with("001", fields=["Id", "Name"])


class TestCreateRecordRetryBehavior:
    """Tests for create_record() with retry logic."""

    def test_create_record_success(self):
        """Should create record successfully."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.create.return_value = {"id": "001", "success": True, "errors": []}
        mock_client.Account = mock_sobject

        record_id = create_record("Account", {"Name": "Test"}, client=mock_client)

        assert record_id == "001"
        assert mock_sobject.create.call_count == 1

    @patch('time.sleep')
    def test_create_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_create(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return {"id": "001", "success": True, "errors": []}

        mock_sobject.create = mock_create
        mock_client.Account = mock_sobject

        record_id = create_record("Account", {"Name": "Test"}, client=mock_client)

        assert record_id == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.create.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            create_record("Account", {"Name": "Test"}, client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.create.call_count == 1

    def test_create_record_auth_error_not_retried(self):
        """Should not retry authentication errors."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.create.side_effect = _make_salesforce_error(401, "INVALID_SESSION_ID")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAuthError):
            create_record("Account", {"Name": "Test"}, client=mock_client)

        assert mock_sobject.create.call_count == 1


class TestUpdateRecordRetryBehavior:
    """Tests for update_record() with retry logic."""

    def test_update_record_success(self):
        """Should update record successfully."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.update.return_value = 204  # HTTP No Content
        mock_client.Account = mock_sobject

        result = update_record("Account", "001", {"Name": "Updated"}, client=mock_client)

        assert result is True
        assert mock_sobject.update.call_count == 1

    @patch('time.sleep')
    def test_update_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_update(record_id, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return 204

        mock_sobject.update = mock_update
        mock_client.Account = mock_sobject

        result = update_record("Account", "001", {"Name": "Updated"}, client=mock_client)

        assert result is True
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_update_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.update.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            update_record("Account", "001", {"Name": "Test"}, client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.update.call_count == 1


class TestUpsertRecordRetryBehavior:
    """Tests for upsert_record() with retry logic."""

    def test_upsert_record_creates_new(self):
        """Should upsert and create new record."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.upsert.return_value = {"id": "001", "success": True, "created": True, "errors": []}
        mock_client.Account = mock_sobject

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert result["created"] is True
        assert mock_sobject.upsert.call_count == 1

    def test_upsert_record_updates_existing(self):
        """Should upsert and update existing record."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.upsert.return_value = {"id": "001", "success": True, "created": False, "errors": []}
        mock_client.Account = mock_sobject

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert result["created"] is False
        assert mock_sobject.upsert.call_count == 1

    @patch('time.sleep')
    def test_upsert_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_upsert(external_id_path, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return {"id": "001", "success": True, "created": True, "errors": []}

        mock_sobject.upsert = mock_upsert
        mock_client.Account = mock_sobject

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_upsert_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.upsert.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            upsert_record(
                "Account", "ExtId__c", "EXT001", {"Name": "Test"},
                client=mock_client, retry_config=NO_RETRY_CONFIG
            )

        assert mock_sobject.upsert.call_count == 1


class TestDeleteRecordRetryBehavior:
    """Tests for delete_record() with retry logic."""

    def test_delete_record_success(self):
        """Should delete record successfully."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.delete.return_value = 204  # HTTP No Content
        mock_client.Account = mock_sobject

        result = delete_record("Account", "001", client=mock_client)

        assert result is True
        assert mock_sobject.delete.call_count == 1

    @patch('time.sleep')
    def test_delete_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_delete(record_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return 204

        mock_sobject.delete = mock_delete
        mock_client.Account = mock_sobject

        result = delete_record("Account", "001", client=mock_client)

        assert result is True
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_delete_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.delete.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            delete_record("Account", "001", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.delete.call_count == 1


class TestDescribeObjectRetryBehavior:
    """Tests for describe_object() with retry logic."""

    def test_describe_object_success(self):
        """Should describe object successfully."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.describe.return_value = {"name": "Account", "fields": []}
        mock_client.Account = mock_sobject

        result = describe_object("Account", client=mock_client)

        assert result["name"] == "Account"
        assert mock_sobject.describe.call_count == 1

    @patch('time.sleep')
    def test_describe_object_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()

        call_count = 0
        def mock_describe():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
            return {"name": "Account", "fields": []}

        mock_sobject.describe = mock_describe
        mock_client.Account = mock_sobject

        result = describe_object("Account", client=mock_client)

        assert result["name"] == "Account"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_describe_object_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.describe.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceRateLimitError):
            describe_object("Account", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobject.describe.call_count == 1

    @patch('time.sleep')
    def test_describe_object_custom_retry_config(self, mock_sleep):
        """Should use custom retry configuration."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.describe.side_effect = _make_salesforce_error(429, "REQUEST_LIMIT_EXCEEDED")
        mock_client.Account = mock_sobject

        custom_config = RetryConfig(max_retries=2, initial_backoff=0.1, jitter=0.0)

        with pytest.raises(SalesforceRateLimitError):
            describe_object("Account", client=mock_client, retry_config=custom_config)

        # Should call 3 times (initial + 2 retries)
        assert mock_sobject.describe.call_count == 3
        assert mock_sleep.call_count == 2


class TestSObjectExceptionHandling:
    """Tests for exception handling across all sobject functions."""

    def test_get_record_raises_auth_error_on_403(self):
        """Should raise SalesforceAuthError on 403 Forbidden."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.get.side_effect = _make_salesforce_error(403, "INSUFFICIENT_ACCESS")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAuthError) as exc_info:
            get_record("Account", "001", client=mock_client)

        assert exc_info.value.status_code == 403

    def test_create_record_raises_api_error_on_400(self):
        """Should raise SalesforceAPIError on 400 client errors."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.create.side_effect = _make_salesforce_error(400, "REQUIRED_FIELD_MISSING")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_record("Account", {}, client=mock_client)

        assert exc_info.value.status_code == 400

    def test_update_record_raises_api_error_on_404(self):
        """Should raise SalesforceAPIError on 404 not found."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.update.side_effect = _make_salesforce_error(404, "NOT_FOUND")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError) as exc_info:
            update_record("Account", "001", {"Name": "Test"}, client=mock_client)

        assert exc_info.value.status_code == 404

    def test_delete_record_raises_api_error_on_404(self):
        """Should raise SalesforceAPIError on 404 not found."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.delete.side_effect = _make_salesforce_error(404, "NOT_FOUND")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError) as exc_info:
            delete_record("Account", "001", client=mock_client)

        assert exc_info.value.status_code == 404

    @patch('time.sleep')
    def test_describe_object_raises_api_error_on_500(self, mock_sleep):
        """Should retry and raise SalesforceAPIError on persistent 500 errors."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.describe.side_effect = _make_salesforce_error(500, "Internal server error")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError) as exc_info:
            describe_object("Account", client=mock_client)

        assert exc_info.value.status_code == 500
        # Should retry (DEFAULT_RETRY_CONFIG has max_retries=3, so 4 total calls)
        assert mock_sobject.describe.call_count == 4

    def test_upsert_record_raises_auth_error_on_401(self):
        """Should raise SalesforceAuthError on 401 unauthorized."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.upsert.side_effect = _make_salesforce_error(401, "INVALID_SESSION_ID")
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAuthError) as exc_info:
            upsert_record("Account", "External_Id__c", "ext-123", {"Name": "Test"}, client=mock_client)

        assert exc_info.value.status_code == 401


class TestSObjectNoneResponses:
    """Tests for handling None responses."""

    def test_get_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.get.return_value = None
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError, match="Failed to retrieve"):
            get_record("Account", "001", client=mock_client)

    def test_create_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.create.return_value = None
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError, match="Failed to create"):
            create_record("Account", {"Name": "Test"}, client=mock_client)

    def test_describe_object_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = MagicMock()
        mock_sobject = MagicMock()
        mock_sobject.describe.return_value = None
        mock_client.Account = mock_sobject

        with pytest.raises(SalesforceAPIError, match="Failed to describe"):
            describe_object("Account", client=mock_client)
