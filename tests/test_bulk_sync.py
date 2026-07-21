"""Tests for Bulk API 2.0 job creation in sync.bulk_sync module."""

import logging
from unittest.mock import Mock, MagicMock, patch

import pytest
import requests

from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, SalesforceRateLimitError
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, NO_RETRY_CONFIG
from sf_utils.sync.bulk_sync import create_bulk_query_job, poll_bulk_job


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before each test."""
    import sf_utils.retry
    with patch.object(sf_utils.retry, '_consecutive_failures', 0):
        yield


@pytest.fixture
def mock_client():
    """Create a mock Salesforce client (simple-salesforce API)."""
    client = Mock()
    # simple-salesforce uses sf_instance and sf_version (without 'v' prefix)
    client.sf_instance = "example.my.salesforce.com"
    client.sf_version = "61.0"  # simple-salesforce stores version without 'v' prefix
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
        mock_client.sf_instance = "example.my.salesforce.com"
        mock_client.sf_version = "61.0"
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
        custom_client.sf_instance = "custom.my.salesforce.com"
        custom_client.sf_version = "v60.0"
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
        # Test with different version (simple-salesforce stores without 'v' prefix)
        mock_client.sf_version = "60.0"

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
    def test_sandbox_sf_instance(self, mock_post, mock_client):
        """Should work with sandbox instance URLs."""
        # Sandbox URL
        mock_client.sf_instance = "example--sandbox.my.salesforce.com"

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


# ============================================================================
# Poll Bulk Job Tests
# ============================================================================


class TestPollBulkJobSuccess:
    """Tests for successful job polling scenarios."""

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_immediate_complete(self, mock_get, mock_sleep, mock_client):
        """Should return immediately if job is already JobComplete."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete",
            "object": "Account",
            "numberOfRecordsProcessed": 1000,
            "retries": 0,
            "totalProcessingTime": 5000
        }
        mock_get.return_value = mock_response

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            timeout=60.0,
            poll_interval=5.0
        )

        # Should return job metadata
        assert result["id"] == "750xx0000004567AAA"
        assert result["state"] == "JobComplete"
        assert result["numberOfRecordsProcessed"] == 1000

        # Should only poll once (already complete)
        assert mock_get.call_count == 1
        # Should not sleep (already complete)
        assert mock_sleep.call_count == 0

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_in_progress_then_complete(self, mock_get, mock_sleep, mock_client):
        """Should poll until job transitions to JobComplete."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First call: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 0
                }
            elif call_count == 2:
                # Second call: still InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 500
                }
            else:
                # Third call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            timeout=60.0,
            poll_interval=2.0
        )

        # Should eventually return complete job
        assert result["state"] == "JobComplete"
        assert result["numberOfRecordsProcessed"] == 1000

        # Should poll 3 times total
        assert call_count == 3
        # Should sleep 2 times (between polls)
        assert mock_sleep.call_count == 2

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_returns_full_metadata(self, mock_get, mock_sleep, mock_client):
        """Should return complete job metadata including all fields."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete",
            "object": "Account",
            "operation": "query",
            "numberOfRecordsProcessed": 5000,
            "retries": 0,
            "totalProcessingTime": 12000,
            "apiVersion": 61.0,
            "concurrencyMode": "Parallel",
            "contentType": "CSV",
            "createdById": "005xx000001X8UzAAK",
            "createdDate": "2024-07-19T12:00:00.000+0000",
            "systemModstamp": "2024-07-19T12:00:05.000+0000"
        }
        mock_get.return_value = mock_response

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client
        )

        # Verify all metadata fields present
        assert result["id"] == "750xx0000004567AAA"
        assert result["state"] == "JobComplete"
        assert result["object"] == "Account"
        assert result["numberOfRecordsProcessed"] == 5000
        assert result["retries"] == 0
        assert result["totalProcessingTime"] == 12000
        assert result["apiVersion"] == 61.0


class TestPollBulkJobStatusHandling:
    """Tests for different job status transitions."""

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_handles_upload_complete_status(self, mock_get, mock_sleep, mock_client):
        """Should continue polling when status is UploadComplete."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First call: UploadComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "UploadComplete",
                    "numberOfRecordsProcessed": 0
                }
            else:
                # Second call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=1.0
        )

        # Should eventually complete
        assert result["state"] == "JobComplete"
        assert call_count == 2

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_handles_in_progress_status(self, mock_get, mock_sleep, mock_client):
        """Should continue polling when status is InProgress."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count <= 3:
                # First 3 calls: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": call_count * 100
                }
            else:
                # Final call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=1.0
        )

        # Should poll until complete
        assert result["state"] == "JobComplete"
        assert call_count == 4

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_handles_job_complete_status(self, mock_get, mock_sleep, mock_client):
        """Should return immediately when status is JobComplete."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete",
            "numberOfRecordsProcessed": 1000
        }
        mock_get.return_value = mock_response

        result = poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client
        )

        # Should return immediately
        assert result["state"] == "JobComplete"
        assert mock_get.call_count == 1
        assert mock_sleep.call_count == 0


class TestPollBulkJobErrors:
    """Tests for error handling during polling."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_failed_raises_api_error(self, mock_get, mock_client):
        """Should raise SalesforceAPIError when job state is Failed."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "Failed",
            "errorMessage": "Query execution failed"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client
            )

        assert exc_info.value.status_code == 200
        assert "Failed" in str(exc_info.value)

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_aborted_raises_api_error(self, mock_get, mock_client):
        """Should raise SalesforceAPIError when job state is Aborted."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "Aborted",
            "errorMessage": "Job aborted by user"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client
            )

        assert "Aborted" in str(exc_info.value)

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.time.monotonic')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_timeout_raises_error(self, mock_get, mock_monotonic, mock_sleep, mock_client):
        """Should raise SalesforceAPIError when timeout is exceeded."""
        # Simulate time passing
        start_time = 1000.0
        mock_monotonic.side_effect = [
            start_time,      # Initial time
            start_time + 10, # After first poll
            start_time + 20, # After second poll
            start_time + 35, # After third poll (exceeds 30s timeout)
        ]

        # Job stays in InProgress
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "InProgress",
            "numberOfRecordsProcessed": 0
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client,
                timeout=30.0,
                poll_interval=5.0
            )

        # Verify timeout error message
        error_message = str(exc_info.value).lower()
        assert "did not complete within" in error_message or "timeout" in error_message

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_api_error_during_poll(self, mock_get, mock_sleep, mock_client):
        """Should raise SalesforceAPIError on HTTP error during polling."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "message": "Internal server error"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client
            )

        assert exc_info.value.status_code == 500

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_auth_error_401(self, mock_get, mock_client):
        """Should raise SalesforceAuthError on 401 response."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "errorCode": "INVALID_SESSION_ID",
            "message": "Session expired"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAuthError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client
            )

        assert exc_info.value.status_code == 401


class TestPollBulkJobBackoff:
    """Tests for exponential backoff behavior."""

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_uses_exponential_backoff(self, mock_get, mock_sleep, mock_client):
        """Should increase sleep interval exponentially with backoff multiplier."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count <= 3:
                # First 3 calls: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 0
                }
            else:
                # Final call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=2.0,
            max_poll_interval=20.0,
            backoff_multiplier=2.0
        )

        # Verify sleep calls use exponential backoff
        assert mock_sleep.call_count == 3
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]

        # First sleep: 2.0
        # Second sleep: 2.0 * 2.0 = 4.0
        # Third sleep: 4.0 * 2.0 = 8.0
        assert sleep_calls[0] == 2.0
        assert sleep_calls[1] == 4.0
        assert sleep_calls[2] == 8.0

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_respects_max_interval(self, mock_get, mock_sleep, mock_client):
        """Should cap sleep interval at max_poll_interval."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count <= 5:
                # First 5 calls: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 0
                }
            else:
                # Final call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=5.0,
            max_poll_interval=10.0,
            backoff_multiplier=2.0
        )

        # Verify sleep intervals are capped
        sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]

        # First sleep: 5.0
        # Second sleep: 10.0 (capped from 5.0 * 2.0)
        # Third sleep: 10.0 (capped)
        # Fourth sleep: 10.0 (capped)
        # Fifth sleep: 10.0 (capped)
        assert sleep_calls[0] == 5.0
        assert all(interval == 10.0 for interval in sleep_calls[1:])

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_respects_initial_interval(self, mock_get, mock_sleep, mock_client):
        """Should start with poll_interval on first sleep."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First call: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 0
                }
            else:
                # Second call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=3.0
        )

        # Verify first sleep uses poll_interval
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] == 3.0


class TestPollBulkJobConfig:
    """Tests for configuration parameter handling."""

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.time.monotonic')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_custom_timeout(self, mock_get, mock_monotonic, mock_sleep, mock_client):
        """Should honor custom timeout parameter."""
        # Simulate time passing beyond custom timeout
        start_time = 1000.0
        mock_monotonic.side_effect = [
            start_time,      # Initial time
            start_time + 15, # After first poll (exceeds 10s timeout)
        ]

        # Job stays in InProgress
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "InProgress",
            "numberOfRecordsProcessed": 0
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_bulk_job(
                job_id="750xx0000004567AAA",
                client=mock_client,
                timeout=10.0  # Custom short timeout
            )

        # Verify timeout error message
        error_message = str(exc_info.value).lower()
        assert "did not complete within" in error_message or "timeout" in error_message

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_poll_custom_interval(self, mock_get, mock_sleep, mock_client):
        """Should honor custom poll_interval parameter."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First call: InProgress
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "InProgress",
                    "numberOfRecordsProcessed": 0
                }
            else:
                # Second call: JobComplete
                mock_response.json.return_value = {
                    "id": "750xx0000004567AAA",
                    "state": "JobComplete",
                    "numberOfRecordsProcessed": 1000
                }

            return mock_response

        mock_get.side_effect = mock_get_fn

        poll_bulk_job(
            job_id="750xx0000004567AAA",
            client=mock_client,
            poll_interval=7.0  # Custom interval
        )

        # Verify custom interval used
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args[0][0] == 7.0

    @patch('sf_utils.sync.bulk_sync.time.sleep')
    @patch('sf_utils.sync.bulk_sync.requests.get')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_poll_creates_client_if_none(self, mock_get_client, mock_get, mock_sleep):
        """Should create client from environment if not provided."""
        # Create mock client
        mock_client = Mock()
        mock_client.sf_instance = "example.my.salesforce.com"
        mock_client.sf_version = "61.0"
        mock_client.session_id = "00Dxx0000001234!ABC"
        mock_client.proxies = None

        mock_get_client.return_value = mock_client

        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete",
            "numberOfRecordsProcessed": 1000
        }
        mock_get.return_value = mock_response

        result = poll_bulk_job(
            job_id="750xx0000004567AAA"
            # client=None (implicit)
        )

        # Verify get_client was called
        mock_get_client.assert_called_once()
        assert result["state"] == "JobComplete"


# ============================================================================
# Sync Records Bulk Tests
# ============================================================================


class TestSyncRecordsBulkSuccess:
    """Tests for successful sync_records_bulk() execution."""

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_successful_sync_returns_result(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should execute full sync flow and return SyncResult."""
        # Mock client
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock DB connection
        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn

        # Mock job creation
        mock_create_job.return_value = "750xx0000004567AAA"

        # Mock polling
        mock_poll.return_value = {
            "id": "750xx0000004567AAA",
            "state": "JobComplete",
            "numberRecordsProcessed": 1000
        }

        # Mock results (2 batches)
        batch1 = [{"Id": "001xxx", "Name": "Account1"}] * 500
        batch2 = [{"Id": "002xxx", "Name": "Account2"}] * 500
        mock_get_results.return_value = iter([batch1, batch2])

        # Mock sync state (no previous sync)
        mock_get_state.return_value = None

        # Mock upsert - called once per batch, returns (inserted, updated) per batch
        # Batch 1: 400 inserted, 100 updated
        # Batch 2: 400 inserted, 100 updated
        # Total: 800 inserted, 200 updated
        mock_upsert.side_effect = [(400, 100), (400, 100)]

        from sf_utils.sync.bulk_sync import sync_records_bulk

        result = sync_records_bulk(
            soql="SELECT Id, Name FROM Account",
            object_name="Account"
        )

        # Verify result
        assert result.object_name == "Account"
        assert result.records_fetched == 1000
        assert result.records_inserted == 800
        assert result.records_updated == 200
        assert result.sync_mode == "incremental"
        assert result.date_field == "LastModifiedDate"

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_returns_sync_result_with_correct_counts(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should return correct record counts from upsert operation."""
        # Mock all dependencies
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_create_job.return_value = "750xx0000004567AAA"
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 150}

        batch = [{"Id": f"00{i}xxx", "Name": f"Rec{i}"} for i in range(150)]
        mock_get_results.return_value = iter([batch])
        mock_get_state.return_value = None

        # Upsert returns specific counts
        mock_upsert.return_value = (100, 50)  # 100 inserted, 50 updated

        from sf_utils.sync.bulk_sync import sync_records_bulk

        result = sync_records_bulk(
            soql="SELECT Id, Name FROM Contact",
            object_name="Contact"
        )

        # Verify counts match upsert return
        assert result.records_fetched == 150
        assert result.records_inserted == 100
        assert result.records_updated == 50

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_updates_watermark_on_success(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should call update_sync_state() after successful upsert."""
        # Mock all dependencies
        mock_get_client.return_value = Mock()
        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn
        mock_create_job.return_value = "750xx0000004567AAA"
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 10}

        batch = [{"Id": f"00{i}xxx", "Name": f"Rec{i}"} for i in range(10)]
        mock_get_results.return_value = iter([batch])
        mock_get_state.return_value = None
        mock_upsert.return_value = (8, 2)

        from sf_utils.sync.bulk_sync import sync_records_bulk

        result = sync_records_bulk(
            soql="SELECT Id, Name FROM Lead",
            object_name="Lead"
        )

        # Verify update_sync_state was called
        mock_update_state.assert_called_once()
        call_args = mock_update_state.call_args[1]
        assert call_args["object_name"] == "Lead"
        assert call_args["db_conn"] == mock_conn
        assert "timestamp" in call_args

        # Verify commit was called
        mock_conn.commit.assert_called_once()


class TestSyncRecordsBulkWatermark:
    """Tests for watermark injection and filtering."""

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_injects_watermark_into_soql(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should inject watermark filter when previous sync exists."""
        from datetime import datetime, timezone
        from sf_utils.sync.state import SyncStateRow

        # Mock all dependencies
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 0}
        mock_get_results.return_value = iter([])

        # Mock sync state with previous watermark
        previous_watermark = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_get_state.return_value = SyncStateRow(
            object_name="Account",
            last_sync_timestamp=previous_watermark,
            sync_mode="incremental"
        )

        mock_upsert.return_value = (0, 0)

        from sf_utils.sync.bulk_sync import sync_records_bulk

        sync_records_bulk(
            soql="SELECT Id, Name FROM Account WHERE Industry = 'Technology'",
            object_name="Account"
        )

        # Verify create_bulk_query_job was called with modified SOQL
        mock_create_job.assert_called_once()
        call_args = mock_create_job.call_args[1]
        modified_soql = call_args["soql_query"]

        # Verify watermark filter was injected
        assert "LastModifiedDate >= 2024-01-01T00:00:00Z" in modified_soql
        # Verify original WHERE clause preserved
        assert "Industry = 'Technology'" in modified_soql

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_no_watermark_for_new_sync(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should not inject watermark when no previous sync exists."""
        # Mock all dependencies
        mock_get_client.return_value = Mock()
        mock_get_conn.return_value = Mock()
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 0}
        mock_get_results.return_value = iter([])

        # No previous sync state
        mock_get_state.return_value = None
        mock_upsert.return_value = (0, 0)

        from sf_utils.sync.bulk_sync import sync_records_bulk

        original_soql = "SELECT Id, Name FROM Contact"
        sync_records_bulk(
            soql=original_soql,
            object_name="Contact"
        )

        # Verify create_bulk_query_job was called with unmodified SOQL
        mock_create_job.assert_called_once()
        call_args = mock_create_job.call_args[1]
        modified_soql = call_args["soql_query"]

        # SOQL should be unchanged (no watermark filter)
        assert modified_soql == original_soql

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_does_not_update_watermark_on_failure(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should rollback and not update watermark when upsert fails."""
        # Mock all dependencies
        mock_get_client.return_value = Mock()
        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn
        mock_create_job.return_value = "750xx0000004567AAA"
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 10}

        batch = [{"Id": f"00{i}xxx", "Name": f"Rec{i}"} for i in range(10)]
        mock_get_results.return_value = iter([batch])
        mock_get_state.return_value = None

        # Upsert raises error
        mock_upsert.side_effect = Exception("Database error")

        from sf_utils.sync.bulk_sync import sync_records_bulk

        with pytest.raises(Exception, match="Database error"):
            sync_records_bulk(
                soql="SELECT Id, Name FROM Opportunity",
                object_name="Opportunity"
            )

        # Verify watermark was NOT updated
        mock_update_state.assert_not_called()

        # Verify rollback was called
        mock_conn.rollback.assert_called_once()


class TestSyncRecordsBulkIntegration:
    """Tests for full orchestration flow."""

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_orchestrates_full_flow(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should call all component functions in correct order."""
        # Mock all dependencies
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn

        mock_create_job.return_value = "750xx0000004567AAA"
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 100}

        batch = [{"Id": f"00{i}xxx", "Name": f"Rec{i}"} for i in range(100)]
        mock_get_results.return_value = iter([batch])
        mock_get_state.return_value = None
        mock_upsert.return_value = (80, 20)

        from sf_utils.sync.bulk_sync import sync_records_bulk

        result = sync_records_bulk(
            soql="SELECT Id, Name FROM Account",
            object_name="Account"
        )

        # Verify call sequence
        # 1. Ensure sync state table exists
        mock_ensure_table.assert_called_once_with(mock_conn)

        # 2. Get sync state
        mock_get_state.assert_called_once_with(object_name="Account", db_conn=mock_conn)

        # 3. Create bulk job
        mock_create_job.assert_called_once()
        assert mock_create_job.call_args[1]["sobject_type"] == "Account"
        assert mock_create_job.call_args[1]["client"] == mock_client

        # 4. Poll job
        mock_poll.assert_called_once()
        assert mock_poll.call_args[1]["job_id"] == "750xx0000004567AAA"
        assert mock_poll.call_args[1]["client"] == mock_client

        # 5. Get results
        mock_get_results.assert_called_once()
        assert mock_get_results.call_args[1]["job_id"] == "750xx0000004567AAA"
        assert mock_get_results.call_args[1]["client"] == mock_client

        # 6. Create table
        mock_create_table.assert_called_once()
        assert mock_create_table.call_args[1]["table_name"] == "sf_account"
        assert mock_create_table.call_args[1]["db_conn"] == mock_conn

        # 7. Upsert records
        mock_upsert.assert_called_once()
        assert mock_upsert.call_args[1]["table_name"] == "sf_account"
        assert len(mock_upsert.call_args[1]["records"]) == 100
        assert mock_upsert.call_args[1]["connection"] == mock_conn

        # 8. Update sync state
        mock_update_state.assert_called_once()
        assert mock_update_state.call_args[1]["object_name"] == "Account"

        # 9. Commit
        mock_conn.commit.assert_called_once()

    @patch('sf_utils.sync.bulk_sync.upsert_records')
    @patch('sf_utils.sync.bulk_sync.create_table_from_query')
    @patch('sf_utils.sync.bulk_sync.update_sync_state')
    @patch('sf_utils.sync.bulk_sync.get_sync_state')
    @patch('sf_utils.sync.bulk_sync.ensure_sync_state_table')
    @patch('sf_utils.sync.bulk_sync.get_connection')
    @patch('sf_utils.sync.bulk_sync.get_bulk_results')
    @patch('sf_utils.sync.bulk_sync.poll_bulk_job')
    @patch('sf_utils.sync.bulk_sync.create_bulk_query_job')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_creates_client_and_db_if_none(
        self,
        mock_get_client,
        mock_create_job,
        mock_poll,
        mock_get_results,
        mock_get_conn,
        mock_ensure_table,
        mock_get_state,
        mock_update_state,
        mock_create_table,
        mock_upsert
    ):
        """Should create client and DB connection when not provided."""
        # Mock all dependencies
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        mock_conn = Mock()
        mock_get_conn.return_value = mock_conn

        mock_create_job.return_value = "750xx0000004567AAA"
        mock_poll.return_value = {"id": "750xx0000004567AAA", "state": "JobComplete", "numberRecordsProcessed": 0}
        mock_get_results.return_value = iter([])
        mock_get_state.return_value = None
        mock_upsert.return_value = (0, 0)

        from sf_utils.sync.bulk_sync import sync_records_bulk

        # Call without client or db_conn
        result = sync_records_bulk(
            soql="SELECT Id, Name FROM Case",
            object_name="Case"
        )

        # Verify get_client was called
        mock_get_client.assert_called_once()

        # Verify get_connection was called
        mock_get_conn.assert_called_once()

        # Verify connection was closed at end (owns_db_conn=True)
        mock_conn.close.assert_called_once()
# ============================================================================
# Get Bulk Results Tests
# ============================================================================


class TestGetBulkResultsSuccess:
    """Tests for successful result retrieval scenarios."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_yields_records_as_dicts(self, mock_get, mock_client):
        """Should yield records as dictionaries with CSV headers as keys."""
        # Mock CSV response with headers and data
        csv_data = "Id,Name,Industry\n001xxx,Acme,Technology\n002xxx,Globex,Manufacturing"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        # iter_lines returns iterator of strings (decode_unicode=True)
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Collect all batches
        all_records = []
        for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
            all_records.extend(batch)

        # Verify records parsed correctly
        assert len(all_records) == 2
        assert all_records[0] == {"Id": "001xxx", "Name": "Acme", "Industry": "Technology"}
        assert all_records[1] == {"Id": "002xxx", "Name": "Globex", "Industry": "Manufacturing"}

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_batches_records_correctly(self, mock_get, mock_client):
        """Should honor batch_size parameter and yield correct batch sizes."""
        # Create CSV with 5 records
        csv_lines = ["Id,Name"] + [f"00{i}xxx,Account{i}" for i in range(5)]
        csv_data = "\n".join(csv_lines)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Collect batches with batch_size=2
        batches = list(get_bulk_results("750xx0000004567AAA", client=mock_client, batch_size=2))

        # Verify batch counts: 2, 2, 1
        assert len(batches) == 3
        assert len(batches[0]) == 2
        assert len(batches[1]) == 2
        assert len(batches[2]) == 1

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_yields_final_partial_batch(self, mock_get, mock_client):
        """Should yield final batch even if smaller than batch_size."""
        # Create CSV with 7 records (batch_size=3 → 3, 3, 1)
        csv_lines = ["Id,Name"] + [f"00{i}xxx,Account{i}" for i in range(7)]
        csv_data = "\n".join(csv_lines)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        batches = list(get_bulk_results("750xx0000004567AAA", client=mock_client, batch_size=3))

        # Verify last batch has 1 record
        assert len(batches) == 3
        assert len(batches[0]) == 3
        assert len(batches[1]) == 3
        assert len(batches[2]) == 1  # Final partial batch


class TestGetBulkResultsCSVParsing:
    """Tests for CSV parsing behavior."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_parses_csv_headers_as_keys(self, mock_get, mock_client):
        """Should use CSV header row as dictionary keys."""
        csv_data = "Id,Name,CreatedDate,Amount__c\n001xxx,Test,2024-01-01,1000.50"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        batches = list(get_bulk_results("750xx0000004567AAA", client=mock_client))
        record = batches[0][0]

        # Verify all headers became keys
        assert "Id" in record
        assert "Name" in record
        assert "CreatedDate" in record
        assert "Amount__c" in record
        assert record["Amount__c"] == "1000.50"

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_handles_empty_csv(self, mock_get, mock_client):
        """Should handle CSV with headers but no data records."""
        csv_data = "Id,Name,Industry"  # Headers only, no data

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        batches = list(get_bulk_results("750xx0000004567AAA", client=mock_client))

        # Should yield no batches (no records)
        assert len(batches) == 0

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_malformed_row_logged_and_skipped(self, mock_get, mock_client, caplog):
        """Should handle malformed CSV rows gracefully (csv.DictReader fills with None)."""
        # Row 2 has too few columns (malformed) - DictReader will fill missing fields with None
        csv_data = "Id,Name,Industry\n001xxx,Acme,Technology\n002xxx\n003xxx,Test,Retail"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        with caplog.at_level(logging.WARNING):
            all_records = []
            for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
                all_records.extend(batch)

        # DictReader handles short rows by filling missing fields with None
        assert len(all_records) == 3
        assert all_records[0]["Id"] == "001xxx"
        assert all_records[1]["Id"] == "002xxx"
        assert all_records[1]["Name"] is None  # Missing field filled with None
        assert all_records[2]["Id"] == "003xxx"


class TestGetBulkResultsErrors:
    """Tests for error handling."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_api_error_404_raises(self, mock_get, mock_client):
        """Should raise SalesforceAPIError on 404 (job not found)."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {}
        mock_response.json.return_value = {
            "errorCode": "NOT_FOUND",
            "message": "Job not found"
        }
        mock_response.text = "Job not found"
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        with pytest.raises(SalesforceAPIError) as exc_info:
            # Consume generator to trigger HTTP request
            list(get_bulk_results("750xx0000004567AAA", client=mock_client))

        assert exc_info.value.status_code == 404

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_api_error_401_raises_auth_error(self, mock_get, mock_client):
        """Should raise SalesforceAuthError on 401 (unauthorized)."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.headers = {}
        mock_response.json.return_value = {
            "errorCode": "INVALID_SESSION_ID",
            "message": "Session expired or invalid"
        }
        mock_response.text = "Session expired"
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        with pytest.raises(SalesforceAuthError) as exc_info:
            list(get_bulk_results("750xx0000004567AAA", client=mock_client))

        assert exc_info.value.status_code == 401

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_network_error_handled(self, mock_get, mock_client):
        """Should raise appropriate error on network failure."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Network unreachable")

        from sf_utils.sync.bulk_sync import get_bulk_results

        with pytest.raises(Exception):  # Will be requests.exceptions.ConnectionError or wrapped error
            list(get_bulk_results("750xx0000004567AAA", client=mock_client))


class TestGetBulkResultsConfig:
    """Tests for configuration parameter handling."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_custom_batch_size_honored(self, mock_get, mock_client):
        """Should honor custom batch_size parameter."""
        # Create CSV with 10 records
        csv_lines = ["Id,Name"] + [f"00{i}xxx,Account{i}" for i in range(10)]
        csv_data = "\n".join(csv_lines)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        batches = list(get_bulk_results("750xx0000004567AAA", client=mock_client, batch_size=4))

        # With batch_size=4 and 10 records: 4, 4, 2
        assert len(batches) == 3
        assert len(batches[0]) == 4
        assert len(batches[1]) == 4
        assert len(batches[2]) == 2

    @patch('sf_utils.sync.bulk_sync.requests.get')
    @patch('sf_utils.sync.bulk_sync.get_client')
    def test_creates_client_if_none(self, mock_get_client, mock_get):
        """Should create client from environment if not provided."""
        # Create mock client
        mock_client = Mock()
        mock_client.sf_instance = "example.my.salesforce.com"
        mock_client.sf_version = "61.0"
        mock_client.session_id = "00Dxx0000001234!ABC"
        mock_client.proxies = None

        mock_get_client.return_value = mock_client

        # Mock CSV response
        csv_data = "Id,Name\n001xxx,Test"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Call without client parameter
        list(get_bulk_results("750xx0000004567AAA"))

        # Verify get_client was called
        mock_get_client.assert_called_once()


class TestGetBulkResultsPagination:
    """Tests for pagination using Sforce-Locator header."""

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_fetches_multiple_pages_with_locator(self, mock_get, mock_client):
        """Should fetch all pages using Sforce-Locator header."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First page: 2 records, locator for next page
                csv_data = "Id,Name\n001xxx,Page1Record1\n002xxx,Page1Record2"
                mock_response.headers = {'Sforce-Locator': 'ABC123'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            elif call_count == 2:
                # Second page: 2 records, locator for next page
                csv_data = "Id,Name\n003xxx,Page2Record1\n004xxx,Page2Record2"
                mock_response.headers = {'Sforce-Locator': 'DEF456'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            else:
                # Third page: 1 record, no more pages
                csv_data = "Id,Name\n005xxx,Page3Record1"
                mock_response.headers = {'Sforce-Locator': 'null'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))

            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Collect all records
        all_records = []
        for batch in get_bulk_results("750xx0000004567AAA", client=mock_client, batch_size=10):
            all_records.extend(batch)

        # Should fetch all 3 pages
        assert call_count == 3
        assert len(all_records) == 5
        assert all_records[0]["Name"] == "Page1Record1"
        assert all_records[2]["Name"] == "Page2Record1"
        assert all_records[4]["Name"] == "Page3Record1"

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_stops_pagination_when_locator_null(self, mock_get, mock_client):
        """Should stop fetching pages when Sforce-Locator is 'null'."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First page with locator
                csv_data = "Id,Name\n001xxx,Record1"
                mock_response.headers = {'Sforce-Locator': 'ABC123'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            else:
                # Second page, locator is null - no more pages
                csv_data = "Id,Name\n002xxx,Record2"
                mock_response.headers = {'Sforce-Locator': 'null'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))

            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        all_records = []
        for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
            all_records.extend(batch)

        # Should stop after 2 pages
        assert call_count == 2
        assert len(all_records) == 2

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_stops_pagination_when_locator_absent(self, mock_get, mock_client):
        """Should stop fetching pages when Sforce-Locator header is missing."""
        mock_response = Mock()
        mock_response.status_code = 200
        # No Sforce-Locator header
        mock_response.headers = {}
        csv_data = "Id,Name\n001xxx,Record1"
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        all_records = []
        for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
            all_records.extend(batch)

        # Should only make 1 request (no locator)
        assert mock_get.call_count == 1
        assert len(all_records) == 1

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_includes_locator_in_url_query_param(self, mock_get, mock_client):
        """Should include locator as URL query parameter in subsequent requests."""
        call_count = 0

        def mock_get_fn(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                # First page - no locator in URL
                assert "?locator=" not in url
                csv_data = "Id,Name\n001xxx,Record1"
                mock_response.headers = {'Sforce-Locator': 'ABC123XYZ'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            else:
                # Second page - locator should be in URL
                assert "?locator=ABC123XYZ" in url
                csv_data = "Id,Name\n002xxx,Record2"
                mock_response.headers = {'Sforce-Locator': 'null'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))

            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        list(get_bulk_results("750xx0000004567AAA", client=mock_client))

        # Verify both requests were made
        assert call_count == 2

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_validates_locator_format(self, mock_get, mock_client):
        """Should validate locator format and raise error for invalid characters."""
        mock_response = Mock()
        mock_response.status_code = 200
        # Locator with invalid characters (potential injection)
        mock_response.headers = {'Sforce-Locator': 'ABC123; DROP TABLE users;'}
        csv_data = "Id,Name\n001xxx,Record1"
        mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
        mock_get.return_value = mock_response

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Should raise error on invalid locator format
        with pytest.raises(SalesforceAPIError) as exc_info:
            for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
                pass

        assert "Invalid Sforce-Locator format" in str(exc_info.value)

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_max_pages_limit_prevents_infinite_loop(self, mock_get, mock_client):
        """Should raise error if max_pages limit is exceeded."""
        def mock_get_fn(*args, **kwargs):
            mock_response = Mock()
            mock_response.status_code = 200
            # Always return a locator (simulating infinite pagination)
            mock_response.headers = {'Sforce-Locator': 'INFINITE'}
            csv_data = "Id,Name\n001xxx,Record"
            mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        # Set low max_pages for testing
        with pytest.raises(SalesforceAPIError) as exc_info:
            for batch in get_bulk_results("750xx0000004567AAA", client=mock_client, max_pages=3):
                pass

        assert "Max pages limit" in str(exc_info.value)
        assert mock_get.call_count == 3

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_logs_csv_headers_at_debug_level_once(self, mock_get, mock_client, caplog):
        """Should log CSV headers at DEBUG level only once (after first page)."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                csv_data = "Id,Name,Industry\n001xxx,Test,Tech"
                mock_response.headers = {'Sforce-Locator': 'ABC'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            else:
                csv_data = "Id,Name,Industry\n002xxx,Test2,Retail"
                mock_response.headers = {'Sforce-Locator': 'null'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))

            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        with caplog.at_level(logging.DEBUG):
            for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
                pass

        # Should log headers exactly once
        header_logs = [r for r in caplog.records if "CSV headers" in r.message]
        assert len(header_logs) == 1
        assert "['Id', 'Name', 'Industry']" in header_logs[0].message

    @patch('sf_utils.sync.bulk_sync.requests.get')
    def test_logs_first_row_metadata_without_pii(self, mock_get, mock_client, caplog):
        """Should log first row metadata (column count only) without actual data."""
        call_count = 0

        def mock_get_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            mock_response = Mock()
            mock_response.status_code = 200

            if call_count == 1:
                csv_data = "Id,Name,Email\n001xxx,John Doe,john@example.com"
                mock_response.headers = {'Sforce-Locator': 'ABC'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))
            else:
                csv_data = "Id,Name,Email\n002xxx,Jane Smith,jane@example.com"
                mock_response.headers = {'Sforce-Locator': 'null'}
                mock_response.iter_lines.return_value = iter(csv_data.split('\n'))

            return mock_response

        mock_get.side_effect = mock_get_fn

        from sf_utils.sync.bulk_sync import get_bulk_results

        with caplog.at_level(logging.DEBUG):
            for batch in get_bulk_results("750xx0000004567AAA", client=mock_client):
                pass

        # Should log first row metadata for each page
        metadata_logs = [r for r in caplog.records if "First row metadata" in r.message]
        assert len(metadata_logs) == 2

        # Should include column count
        assert "column_count=3" in metadata_logs[0].message

        # Should NOT include actual row data (PII)
        assert "John Doe" not in caplog.text
        assert "john@example.com" not in caplog.text
        assert "Jane Smith" not in caplog.text
