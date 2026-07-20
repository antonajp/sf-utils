"""Tests for Bulk API 2.0 job creation in sync.bulk_sync module."""

import logging
from unittest.mock import Mock, MagicMock, patch

import pytest

from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, SalesforceRateLimitError
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, NO_RETRY_CONFIG
from sf_utils.sync.bulk_sync import create_bulk_query_job


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    import sf_utils.retry
    with patch.object(sf_utils.retry, '_consecutive_failures', 0):
        yield


@pytest.fixture
def mock_client():
    """Create a mock Salesforce client."""
    client = Mock()
    client.instance_url = "example.my.salesforce.com"
    client.client_api_version = "v61.0"
    client.session_id = "00Dxx0000001234!ABCdefghijklmnopQRSTuvwxyz"
    client.proxies = None

    return client


class TestCreateBulkQueryJobSuccess:
    """Tests for successful job creation."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_successful_job_creation_returns_job_id(self, mock_post, mock_client):
        """Should return job ID string on 200/201 response."""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "UploadComplete",
            "object": "Account",
            "operation": "query"
        }
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id, Name FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"
        assert mock_post.call_count == 1

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_post_request_sent_to_correct_endpoint(self, mock_post, mock_client):
        """POST request should be sent to /services/data/vXX.0/jobs/query."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        # Verify endpoint construction
        expected_url = "https://example.my.salesforce.com/services/data/v61.0/jobs/query"
        actual_url = mock_post.call_args[0][0]

        assert actual_url == expected_url

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_request_body_contains_required_fields(self, mock_post, mock_client):
        """Request body should contain operation, query, contentType."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        soql = "SELECT Id, Name FROM Account WHERE CreatedDate > 2024-01-01"

        create_bulk_query_job(
            sobject_type="Account",
            soql_query=soql,
            client=mock_client
        )

        # Verify request body
        call_kwargs = mock_post.call_args[1]
        request_body = call_kwargs['json']

        assert request_body['operation'] == 'query'
        assert request_body['query'] == soql
        assert request_body['contentType'] == 'CSV'

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_job_id_logged_at_info_level(self, mock_post, mock_client, caplog):
        """Job ID should be logged at INFO level on success."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        with caplog.at_level(logging.INFO):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        # Verify logging
        assert any("750xx0000004567AAA" in record.message for record in caplog.records)
        assert any(record.levelname == "INFO" for record in caplog.records)

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_accepts_200_status_code(self, mock_post, mock_client):
        """Should accept 200 status code (not just 201)."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "750xx0000004567BBB", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567BBB"


class TestCreateBulkQueryJobErrorHandling:
    """Tests for error handling."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_invalid_soql_400_raises_api_error(self, mock_post, mock_client):
        """Invalid SOQL should raise SalesforceAPIError."""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "errorCode": "MALFORMED_QUERY",
            "message": "Invalid SOQL query syntax"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="INVALID SOQL",
                client=mock_client
            )

        assert exc_info.value.status_code == 400
        assert "Invalid SOQL" in str(exc_info.value) or "MALFORMED_QUERY" in str(exc_info.value)

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_auth_failure_401_raises_auth_error(self, mock_post, mock_client):
        """401 Unauthorized should raise SalesforceAuthError."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "errorCode": "INVALID_SESSION_ID",
            "message": "Session expired or invalid"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAuthError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        assert exc_info.value.status_code == 401

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_forbidden_403_raises_auth_error(self, mock_post, mock_client):
        """403 Forbidden should raise SalesforceAuthError."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "errorCode": "INSUFFICIENT_ACCESS",
            "message": "Insufficient privileges to create bulk job"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAuthError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        assert exc_info.value.status_code == 403

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_rate_limit_429_raises_api_error(self, mock_post, mock_client):
        """429 Too Many Requests should raise SalesforceAPIError (Bulk API uses different error handling)."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.json.return_value = {
            "errorCode": "REQUEST_LIMIT_EXCEEDED",
            "message": "Concurrent bulk job limit exceeded"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client,
                retry_config=NO_RETRY_CONFIG
            )

        assert exc_info.value.status_code == 429

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_server_error_500_raises_api_error(self, mock_post, mock_client):
        """500 Internal Server Error should raise SalesforceAPIError."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "message": "Internal server error"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client,
                retry_config=NO_RETRY_CONFIG
            )

        assert exc_info.value.status_code == 500

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_malformed_response_missing_id_raises_error(self, mock_post, mock_client):
        """Response missing 'id' field should raise SalesforceAPIError."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "state": "UploadComplete"
            # Missing 'id' field!
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        assert "missing 'id'" in str(exc_info.value).lower()

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_json_decode_error_returns_text_fallback(self, mock_post, mock_client):
        """Non-JSON response should still be handled (error field created)."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client,
                retry_config=NO_RETRY_CONFIG
            )


class TestCreateBulkQueryJobClientHandling:
    """Tests for client creation and handling."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_client_created_if_not_provided(self, mock_get_client, mock_post):
        """Client should be created from env if not provided."""
        # Create mock client
        mock_client = Mock()
        mock_client.instance_url = "example.my.salesforce.com"
        mock_client.client_api_version = "v61.0"
        mock_client.session_id = "00Dxx0000001234!ABC"
        mock_client.proxies = None

        mock_get_client.return_value = mock_client

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account"
            # client=None (implicit)
        )

        # Verify get_client was called
        mock_get_client.assert_called_once()
        assert job_id == "750xx0000004567AAA"

    @patch('sf_utils.sync.bulk_sync.requests.post')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_provided_client_is_used(self, mock_get_client, mock_post):
        """Provided client should be used instead of creating new one."""
        # Create custom client
        custom_client = Mock()
        custom_client.instance_url = "custom.my.salesforce.com"
        custom_client.client_api_version = "v60.0"
        custom_client.session_id = "00Dxx0000001234!ABC"
        custom_client.proxies = None

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=custom_client
        )

        # get_client should NOT be called
        mock_get_client.assert_not_called()

        # Verify custom client was used
        assert mock_post.call_count == 1
        actual_url = mock_post.call_args[0][0]
        assert "custom.my.salesforce.com" in actual_url


class TestCreateBulkQueryJobRetryBehavior:
    """Tests for retry behavior."""

    @patch('time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_retries_on_server_error_with_default_config(self, mock_post, mock_sleep, mock_client):
        """Should retry on 5xx server errors with DEFAULT_RETRY_CONFIG."""
        call_count = 0

        def mock_post_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            if call_count == 1:
                # First call fails with server error
                mock_response.status_code = 500
                mock_response.json.return_value = {"message": "Internal server error"}
            else:
                # Second call succeeds
                mock_response.status_code = 201
                mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}

            return mock_response

        mock_post.side_effect = mock_post_fn

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_no_retry_with_no_retry_config(self, mock_post, mock_client):
        """Should not retry when NO_RETRY_CONFIG is used."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "Internal server error"}
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAPIError):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client,
                retry_config=NO_RETRY_CONFIG
            )

        # Should only be called once (no retries)
        assert mock_post.call_count == 1

    @patch('time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_custom_retry_config_honored(self, mock_post, mock_sleep, mock_client):
        """Should respect custom retry configuration."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "Internal server error"}
        mock_post.return_value = mock_response

        custom_config = RetryConfig(max_retries=2, initial_backoff=0.1, jitter=0.0)

        with pytest.raises(SalesforceAPIError):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client,
                retry_config=custom_config
            )

        # Should call 3 times (initial + 2 retries)
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_auth_error_not_retried(self, mock_post, mock_client):
        """Authentication errors should NOT be retried."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "errorCode": "INVALID_SESSION_ID",
            "message": "Session expired"
        }
        mock_post.return_value = mock_response

        with pytest.raises(SalesforceAuthError):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        # Should only be called once (no retries for auth errors)
        assert mock_post.call_count == 1

    @patch('time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_server_error_500_retried(self, mock_post, mock_sleep, mock_client):
        """Server errors (5xx) should be retried."""
        call_count = 0

        def mock_post_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            if call_count == 1:
                # First call fails with server error
                mock_response.status_code = 500
                mock_response.json.return_value = {"message": "Internal server error"}
            else:
                # Second call succeeds
                mock_response.status_code = 201
                mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}

            return mock_response

        mock_post.side_effect = mock_post_fn

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"
        assert call_count == 2
        assert mock_sleep.call_count == 1


class TestCreateBulkQueryJobEdgeCases:
    """Tests for edge cases and special scenarios."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_empty_soql_query_raises_value_error(self, mock_post, mock_client):
        """Empty SOQL query should raise ValueError."""
        with pytest.raises(ValueError, match="soql_query cannot be empty"):
            create_bulk_query_job(
                sobject_type="Account",
                soql_query="",
                client=mock_client
            )

        # Should not make HTTP request
        assert mock_post.call_count == 0

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_empty_sobject_type_raises_value_error(self, mock_post, mock_client):
        """Empty sobject_type should raise ValueError."""
        with pytest.raises(ValueError, match="sobject_type cannot be empty"):
            create_bulk_query_job(
                sobject_type="",
                soql_query="SELECT Id FROM Account",
                client=mock_client
            )

        # Should not make HTTP request
        assert mock_post.call_count == 0

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_very_long_soql_query(self, mock_post, mock_client):
        """Should handle very long SOQL queries."""
        # Create a long SOQL query with many fields
        fields = ", ".join([f"Field{i}__c" for i in range(100)])
        long_soql = f"SELECT Id, {fields} FROM Account WHERE CreatedDate > 2024-01-01"

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query=long_soql,
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"

        # Verify the full query was sent
        request_body = mock_post.call_args[1]['json']
        assert request_body['query'] == long_soql

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_special_characters_in_soql(self, mock_post, mock_client):
        """Should handle special characters in SOQL query."""
        soql = "SELECT Id, Name FROM Account WHERE Name LIKE '%O\\'Reilly%'"

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query=soql,
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"

        # Verify query was sent correctly
        request_body = mock_post.call_args[1]['json']
        assert request_body['query'] == soql

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_different_api_versions(self, mock_post, mock_client):
        """Should use API version from client."""
        # Test with different version
        mock_client.client_api_version = "v60.0"

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        # Verify endpoint uses correct version
        actual_url = mock_post.call_args[0][0]
        assert "/services/data/v60.0/jobs/query" in actual_url

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_sandbox_instance_url(self, mock_post, mock_client):
        """Should work with sandbox instance URLs."""
        # Sandbox URL
        mock_client.instance_url = "example--sandbox.my.salesforce.com"

        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        # Verify sandbox URL used
        actual_url = mock_post.call_args[0][0]
        assert "example--sandbox.my.salesforce.com" in actual_url


class TestCreateBulkQueryJobResponseParsing:
    """Tests for response parsing and validation."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_response_with_additional_fields(self, mock_post, mock_client):
        """Should extract job ID even if response has extra fields."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "UploadComplete",
            "object": "Account",
            "createdById": "005xx000001X8UzAAK",
            "createdDate": "2024-07-19T12:00:00.000+0000",
            "systemModstamp": "2024-07-19T12:00:00.000+0000",
            "operation": "query",
            "contentType": "CSV",
            "apiVersion": 61.0,
            "lineEnding": "LF",
            "columnDelimiter": "COMMA"
        }
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_response_with_different_state(self, mock_post, mock_client):
        """Should accept response even with different state values."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete"  # Different state
        }
        mock_post.return_value = mock_response

        job_id = create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        assert job_id == "750xx0000004567AAA"


class TestCreateBulkQueryJobHeaders:
    """Tests for request headers."""

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_authorization_header_included(self, mock_post, mock_client):
        """Authorization header with Bearer token should be included."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        # Verify headers
        headers = mock_post.call_args[1]['headers']
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')
        assert mock_client.session_id in headers['Authorization']

    @patch('sf_utils.sync.bulk_sync.requests.post')
    def test_content_type_header_included(self, mock_post, mock_client):
        """Content-Type header should be application/json."""
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "750xx0000004567AAA", "state": "UploadComplete"}
        mock_post.return_value = mock_response

        create_bulk_query_job(
            sobject_type="Account",
            soql_query="SELECT Id FROM Account",
            client=mock_client
        )

        # Verify headers
        headers = mock_post.call_args[1]['headers']
        assert headers['Content-Type'] == 'application/json'
        assert headers['Accept'] == 'application/json'
