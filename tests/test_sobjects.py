"""Tests for sobjects CRUD operations with retry behavior."""

from unittest.mock import Mock, patch

import pytest

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


class TestGetRecordRetryBehavior:
    """Tests for get_record() with retry logic."""

    def test_get_record_success(self):
        """Should retrieve record successfully."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.query.return_value = ({"Id": "001", "Name": "Test"}, 200)
        mock_client.sobjects.return_value = mock_sobjects

        record = get_record("Account", "001", client=mock_client)

        assert record["Id"] == "001"
        assert mock_sobjects.query.call_count == 1

    @patch('time.sleep')
    def test_get_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_query(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"Id": "001", "Name": "Test"}, 200)

        mock_sobjects.query = mock_query
        mock_client.sobjects.return_value = mock_sobjects

        record = get_record("Account", "001", client=mock_client)

        assert record["Id"] == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_get_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.query.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            get_record("Account", "001", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobjects.query.call_count == 1

    def test_get_record_with_fields(self):
        """Should pass fields parameter correctly."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.query.return_value = ({"Id": "001", "Name": "Test"}, 200)
        mock_client.sobjects.return_value = mock_sobjects

        record = get_record("Account", "001", fields=["Id", "Name"], client=mock_client)

        assert record["Id"] == "001"
        mock_sobjects.query.assert_called_once_with(fields="Id,Name")


class TestCreateRecordRetryBehavior:
    """Tests for create_record() with retry logic."""

    def test_create_record_success(self):
        """Should create record successfully."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.insert.return_value = ({"id": "001", "success": True}, 201)
        mock_client.sobjects.return_value = mock_sobjects

        record_id = create_record("Account", {"Name": "Test"}, client=mock_client)

        assert record_id == "001"
        assert mock_sobjects.insert.call_count == 1

    @patch('time.sleep')
    def test_create_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_insert(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"id": "001", "success": True}, 201)

        mock_sobjects.insert = mock_insert
        mock_client.sobjects.return_value = mock_sobjects

        record_id = create_record("Account", {"Name": "Test"}, client=mock_client)

        assert record_id == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_create_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.insert.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            create_record("Account", {"Name": "Test"}, client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobjects.insert.call_count == 1

    def test_create_record_auth_error_not_retried(self):
        """Should not retry authentication errors."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.insert.return_value = ({"message": "Unauthorized"}, 401)
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAuthError):
            create_record("Account", {"Name": "Test"}, client=mock_client)

        assert mock_sobjects.insert.call_count == 1


class TestUpdateRecordRetryBehavior:
    """Tests for update_record() with retry logic."""

    def test_update_record_success(self):
        """Should update record successfully."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.update.return_value = ({}, 204)
        mock_client.sobjects.return_value = mock_sobjects

        result = update_record("Account", "001", {"Name": "Updated"}, client=mock_client)

        assert result is True
        assert mock_sobjects.update.call_count == 1

    @patch('time.sleep')
    def test_update_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_update(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({}, 204)

        mock_sobjects.update = mock_update
        mock_client.sobjects.return_value = mock_sobjects

        result = update_record("Account", "001", {"Name": "Updated"}, client=mock_client)

        assert result is True
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_update_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.update.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            update_record("Account", "001", {"Name": "Test"}, client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobjects.update.call_count == 1


class TestUpsertRecordRetryBehavior:
    """Tests for upsert_record() with retry logic."""

    def test_upsert_record_creates_new(self):
        """Should upsert and create new record."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.upsert.return_value = ({"id": "001", "success": True}, 201)
        mock_client.sobjects.return_value = mock_sobjects

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert result["created"] is True
        assert mock_sobjects.upsert.call_count == 1

    def test_upsert_record_updates_existing(self):
        """Should upsert and update existing record."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.upsert.return_value = ({"id": "001", "success": True}, 200)
        mock_client.sobjects.return_value = mock_sobjects

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert result["created"] is False
        assert mock_sobjects.upsert.call_count == 1

    @patch('time.sleep')
    def test_upsert_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_upsert(data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"id": "001", "success": True}, 201)

        mock_sobjects.upsert = mock_upsert
        mock_client.sobjects.return_value = mock_sobjects

        result = upsert_record("Account", "ExtId__c", "EXT001", {"Name": "Test"}, client=mock_client)

        assert result["id"] == "001"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_upsert_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.upsert.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            upsert_record(
                "Account", "ExtId__c", "EXT001", {"Name": "Test"},
                client=mock_client, retry_config=NO_RETRY_CONFIG
            )

        assert mock_sobjects.upsert.call_count == 1


class TestDeleteRecordRetryBehavior:
    """Tests for delete_record() with retry logic."""

    def test_delete_record_success(self):
        """Should delete record successfully."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.delete.return_value = ({}, 204)
        mock_client.sobjects.return_value = mock_sobjects

        result = delete_record("Account", "001", client=mock_client)

        assert result is True
        assert mock_sobjects.delete.call_count == 1

    @patch('time.sleep')
    def test_delete_record_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_delete():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({}, 204)

        mock_sobjects.delete = mock_delete
        mock_client.sobjects.return_value = mock_sobjects

        result = delete_record("Account", "001", client=mock_client)

        assert result is True
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_delete_record_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.delete.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            delete_record("Account", "001", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobjects.delete.call_count == 1


class TestDescribeObjectRetryBehavior:
    """Tests for describe_object() with retry logic."""

    def test_describe_object_success(self):
        """Should describe object successfully."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.describe.return_value = ({"name": "Account", "fields": []}, 200)
        mock_client.sobjects.return_value = mock_sobjects

        result = describe_object("Account", client=mock_client)

        assert result["name"] == "Account"
        assert mock_sobjects.describe.call_count == 1

    @patch('time.sleep')
    def test_describe_object_retries_on_rate_limit(self, mock_sleep):
        """Should retry on rate limit."""
        mock_client = Mock()
        mock_sobjects = Mock()

        call_count = 0
        def mock_describe():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ([{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}], 429)
            return ({"name": "Account", "fields": []}, 200)

        mock_sobjects.describe = mock_describe
        mock_client.sobjects.return_value = mock_sobjects

        result = describe_object("Account", client=mock_client)

        assert result["name"] == "Account"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    def test_describe_object_no_retry_config(self):
        """Should not retry with NO_RETRY_CONFIG."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.describe.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceRateLimitError):
            describe_object("Account", client=mock_client, retry_config=NO_RETRY_CONFIG)

        assert mock_sobjects.describe.call_count == 1

    @patch('time.sleep')
    def test_describe_object_custom_retry_config(self, mock_sleep):
        """Should use custom retry configuration."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.describe.return_value = (
            [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Rate limit"}],
            429
        )
        mock_client.sobjects.return_value = mock_sobjects

        custom_config = RetryConfig(max_retries=2, initial_backoff=0.1, jitter=0.0)

        with pytest.raises(SalesforceRateLimitError):
            describe_object("Account", client=mock_client, retry_config=custom_config)

        # Should call 3 times (initial + 2 retries)
        assert mock_sobjects.describe.call_count == 3
        assert mock_sleep.call_count == 2


class TestSObjectExceptionHandling:
    """Tests for exception handling across all sobject functions."""

    def test_get_record_raises_auth_error_on_403(self):
        """Should raise SalesforceAuthError on 403 Forbidden."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.query.return_value = (
            [{"message": "Insufficient privileges", "errorCode": "INSUFFICIENT_ACCESS"}],
            403
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAuthError) as exc_info:
            get_record("Account", "001", client=mock_client)

        assert exc_info.value.status_code == 403

    def test_create_record_raises_api_error_on_400(self):
        """Should raise SalesforceAPIError on 400 client errors."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.insert.return_value = (
            [{"message": "Required field missing", "errorCode": "REQUIRED_FIELD_MISSING"}],
            400
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_record("Account", {}, client=mock_client)

        assert exc_info.value.status_code == 400

    def test_update_record_raises_api_error_on_404(self):
        """Should raise SalesforceAPIError on 404 not found."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.update.return_value = (
            [{"message": "Record not found", "errorCode": "NOT_FOUND"}],
            404
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError) as exc_info:
            update_record("Account", "001", {"Name": "Test"}, client=mock_client)

        assert exc_info.value.status_code == 404

    def test_delete_record_raises_api_error_on_404(self):
        """Should raise SalesforceAPIError on 404 not found."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.delete.return_value = (
            [{"message": "Record not found", "errorCode": "NOT_FOUND"}],
            404
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError) as exc_info:
            delete_record("Account", "001", client=mock_client)

        assert exc_info.value.status_code == 404

    @patch('time.sleep')
    def test_describe_object_raises_api_error_on_500(self, mock_sleep):
        """Should retry and raise SalesforceAPIError on persistent 500 errors."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.describe.return_value = (
            [{"message": "Internal server error"}],
            500
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError) as exc_info:
            describe_object("Account", client=mock_client)

        assert exc_info.value.status_code == 500
        # Should retry (DEFAULT_RETRY_CONFIG has max_retries=3, so 4 total calls)
        assert mock_sobjects.describe.call_count == 4

    def test_upsert_record_raises_auth_error_on_401(self):
        """Should raise SalesforceAuthError on 401 unauthorized."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.upsert.return_value = (
            [{"message": "Session expired", "errorCode": "INVALID_SESSION_ID"}],
            401
        )
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAuthError) as exc_info:
            upsert_record("Account", "External_Id__c", "ext-123", {"Name": "Test"}, client=mock_client)

        assert exc_info.value.status_code == 401


class TestSObjectNoneResponses:
    """Tests for handling None responses."""

    def test_get_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.query.return_value = None
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError, match="Failed to retrieve"):
            get_record("Account", "001", client=mock_client)

    def test_create_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.insert.return_value = None
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError, match="Failed to create"):
            create_record("Account", {"Name": "Test"}, client=mock_client)

    def test_update_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.update.return_value = None
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError, match="Failed to update"):
            update_record("Account", "001", {"Name": "Test"}, client=mock_client)

    def test_delete_record_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.delete.return_value = None
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError, match="Failed to delete"):
            delete_record("Account", "001", client=mock_client)

    def test_describe_object_none_response(self):
        """Should raise SalesforceAPIError when response is None."""
        mock_client = Mock()
        mock_sobjects = Mock()
        mock_sobjects.describe.return_value = None
        mock_client.sobjects.return_value = mock_sobjects

        with pytest.raises(SalesforceAPIError, match="Failed to describe"):
            describe_object("Account", client=mock_client)
