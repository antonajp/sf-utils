"""Tests for Tooling API module (sf_utils/tooling.py).

This module tests the Salesforce Tooling API functionality for:
- Execute Anonymous Apex
- Async job polling
- Rate limit handling
"""

import time
from unittest.mock import Mock, patch

import pytest

from sf_utils.exceptions import (
    SalesforceAPIError,
    SalesforceAuthError,
)


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


class TestExecuteAnonymousSuccess:
    """Tests for successful Execute Anonymous Apex operations."""

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_success(self, mock_get, mock_client):
        """Should return success result when Apex compiles and executes successfully."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": True,
            "compileProblem": None,
            "exceptionMessage": None,
            "exceptionStackTrace": None,
            "line": -1,
            "column": -1
        }
        mock_get.return_value = mock_response

        result = execute_anonymous(
            client=mock_client,
            apex_code="System.debug('Hello World');"
        )

        assert result["success"] is True
        assert result["compiled"] is True
        assert result["compileProblem"] is None
        assert result["exceptionMessage"] is None

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_correct_endpoint(self, mock_get, mock_client):
        """Should send GET request to correct Tooling API endpoint."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": True,
            "compileProblem": None,
            "exceptionMessage": None
        }
        mock_get.return_value = mock_response

        execute_anonymous(
            client=mock_client,
            apex_code="System.debug('test');"
        )

        # Verify correct endpoint
        call_args = mock_get.call_args
        url = call_args[0][0]
        expected_base = "https://example.my.salesforce.com/services/data/v61.0/tooling/executeAnonymous"
        assert url == expected_base

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_passes_apex_in_params(self, mock_get, mock_client):
        """Should pass the Apex code in the params."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": True,
            "compileProblem": None,
            "exceptionMessage": None
        }
        mock_get.return_value = mock_response

        apex_code = "String s = 'Hello World';"
        execute_anonymous(client=mock_client, apex_code=apex_code)

        # Verify Apex code was passed in params
        call_args = mock_get.call_args
        params = call_args[1].get('params', {})
        assert 'anonymousBody' in params
        assert params['anonymousBody'] == apex_code

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_uses_authorization_header(self, mock_get, mock_client):
        """Should include Bearer token in Authorization header."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": True,
            "compileProblem": None,
            "exceptionMessage": None
        }
        mock_get.return_value = mock_response

        execute_anonymous(
            client=mock_client,
            apex_code="System.debug('test');"
        )

        # Verify Authorization header
        call_args = mock_get.call_args
        headers = call_args[1].get('headers', {})
        assert 'Authorization' in headers
        assert headers['Authorization'].startswith('Bearer ')
        assert mock_client.session_id in headers['Authorization']


class TestExecuteAnonymousCompileError:
    """Tests for Apex compilation errors."""

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_compile_error(self, mock_get, mock_client):
        """Should return compile error details when Apex fails to compile."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": False,
            "success": False,
            "compileProblem": "Unexpected token 'for'.",
            "exceptionMessage": None,
            "exceptionStackTrace": None,
            "line": 5,
            "column": 10
        }
        mock_get.return_value = mock_response

        result = execute_anonymous(
            client=mock_client,
            apex_code="for for for"  # Invalid syntax
        )

        assert result["compiled"] is False
        assert result["success"] is False
        assert "Unexpected token" in result["compileProblem"]
        assert result["line"] == 5
        assert result["column"] == 10


class TestExecuteAnonymousRuntimeError:
    """Tests for Apex runtime errors."""

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_runtime_error(self, mock_get, mock_client):
        """Should return runtime exception details when Apex throws exception."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": False,
            "compileProblem": None,
            "exceptionMessage": "System.NullPointerException: Attempt to de-reference a null object",
            "exceptionStackTrace": "AnonymousBlock: line 3, column 1",
            "line": 3,
            "column": 1
        }
        mock_get.return_value = mock_response

        result = execute_anonymous(
            client=mock_client,
            apex_code="Account a; String name = a.Name;"  # NullPointerException
        )

        assert result["compiled"] is True
        assert result["success"] is False
        assert "NullPointerException" in result["exceptionMessage"]
        assert result["exceptionStackTrace"] is not None


class TestExecuteAnonymousHTTPErrors:
    """Tests for HTTP error handling in Execute Anonymous."""

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_401_raises_auth_error(self, mock_get, mock_client):
        """Should raise SalesforceAuthError on 401 response."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.json.return_value = {
            "errorCode": "INVALID_SESSION_ID",
            "message": "Session expired or invalid"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAuthError) as exc_info:
            execute_anonymous(
                client=mock_client,
                apex_code="System.debug('test');"
            )

        assert exc_info.value.status_code == 401

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_403_raises_auth_error(self, mock_get, mock_client):
        """Should raise SalesforceAuthError on 403 response."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "errorCode": "INSUFFICIENT_ACCESS",
            "message": "No access to execute anonymous Apex"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAuthError) as exc_info:
            execute_anonymous(
                client=mock_client,
                apex_code="System.debug('test');"
            )

        assert exc_info.value.status_code == 403

    @patch('sf_utils.tooling.requests.get')
    def test_execute_anonymous_500_raises_api_error(self, mock_get, mock_client):
        """Should raise SalesforceAPIError on 500 response."""
        from sf_utils.tooling import execute_anonymous

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "message": "Internal server error"
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            execute_anonymous(
                client=mock_client,
                apex_code="System.debug('test');"
            )

        assert exc_info.value.status_code == 500

    def test_execute_anonymous_empty_code_raises_value_error(self, mock_client):
        """Should raise ValueError for empty apex_code."""
        from sf_utils.tooling import execute_anonymous

        with pytest.raises(ValueError) as exc_info:
            execute_anonymous(client=mock_client, apex_code="")

        assert "empty" in str(exc_info.value).lower()


class TestPollAsyncJobCompleted:
    """Tests for async job polling - successful completion."""

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_completed(self, mock_get, mock_sleep, mock_client):
        """Should return success when job completes successfully."""
        from sf_utils.tooling import poll_async_job

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Completed",
                "NumberOfErrors": 0,
                "JobItemsProcessed": 100,
                "TotalJobItems": 100,
                "ExtendedStatus": None
            }]
        }
        mock_get.return_value = mock_response

        result = poll_async_job(
            client=mock_client,
            job_id="7071a00000XXXXXX",
            poll_interval=1,
            timeout=60
        )

        assert result["Status"] == "Completed"
        assert result["NumberOfErrors"] == 0

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.time.monotonic')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_waits_for_completion(self, mock_get, mock_monotonic, mock_sleep, mock_client):
        """Should poll multiple times until job completes."""
        from sf_utils.tooling import poll_async_job

        # Simulate time progression
        mock_monotonic.side_effect = [0, 0, 5, 10, 15]

        # First call: Queued, Second call: Processing, Third call: Completed
        responses = [
            {"totalSize": 1, "done": True, "records": [{"Id": "7071a00000XXXXXX", "Status": "Queued", "NumberOfErrors": 0, "JobItemsProcessed": 0, "TotalJobItems": 100, "ExtendedStatus": None}]},
            {"totalSize": 1, "done": True, "records": [{"Id": "7071a00000XXXXXX", "Status": "Processing", "NumberOfErrors": 0, "JobItemsProcessed": 50, "TotalJobItems": 100, "ExtendedStatus": None}]},
            {"totalSize": 1, "done": True, "records": [{"Id": "7071a00000XXXXXX", "Status": "Completed", "NumberOfErrors": 0, "JobItemsProcessed": 100, "TotalJobItems": 100, "ExtendedStatus": None}]}
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = responses
        mock_get.return_value = mock_response

        result = poll_async_job(
            client=mock_client,
            job_id="7071a00000XXXXXX",
            poll_interval=5,
            timeout=60
        )

        assert result["Status"] == "Completed"
        assert mock_get.call_count == 3


class TestPollAsyncJobFailed:
    """Tests for async job polling - failure scenarios."""

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_failed_raises_error(self, mock_get, mock_sleep, mock_client):
        """Should raise SalesforceAPIError when job fails."""
        from sf_utils.tooling import poll_async_job

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Failed",
                "NumberOfErrors": 5,
                "JobItemsProcessed": 95,
                "TotalJobItems": 100,
                "ExtendedStatus": "Apex heap size too large: 12100000"
            }]
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_async_job(
                client=mock_client,
                job_id="7071a00000XXXXXX",
                poll_interval=1,
                timeout=60
            )

        assert "Failed" in str(exc_info.value)

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_aborted_raises_error(self, mock_get, mock_sleep, mock_client):
        """Should raise SalesforceAPIError when job is aborted."""
        from sf_utils.tooling import poll_async_job

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Aborted",
                "NumberOfErrors": 0,
                "JobItemsProcessed": 50,
                "TotalJobItems": 100,
                "ExtendedStatus": "Job was cancelled by user"
            }]
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_async_job(
                client=mock_client,
                job_id="7071a00000XXXXXX",
                poll_interval=1,
                timeout=60
            )

        assert "Aborted" in str(exc_info.value)


class TestPollAsyncJobTimeout:
    """Tests for async job polling - timeout scenarios."""

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.time.monotonic')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_timeout(self, mock_get, mock_monotonic, mock_sleep, mock_client):
        """Should raise SalesforceAPIError when polling exceeds timeout."""
        from sf_utils.tooling import poll_async_job

        # Simulate time progression: first call at 0, then exceeds timeout
        mock_monotonic.side_effect = [0, 6]  # Starts at 0, next check at 6 seconds > 5 timeout

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Processing",  # Never completes
                "NumberOfErrors": 0,
                "JobItemsProcessed": 50,
                "TotalJobItems": 100,
                "ExtendedStatus": None
            }]
        }
        mock_get.return_value = mock_response

        with pytest.raises(SalesforceAPIError) as exc_info:
            poll_async_job(
                client=mock_client,
                job_id="7071a00000XXXXXX",
                poll_interval=2,
                timeout=5
            )

        assert "timeout" in str(exc_info.value).lower() or "did not complete" in str(exc_info.value).lower()


class TestPollAsyncJobRateLimit:
    """Tests for rate limit handling during async job polling."""

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.time.monotonic')
    @patch('sf_utils.tooling.requests.get')
    def test_poll_async_job_rate_limit_retries(self, mock_get, mock_monotonic, mock_sleep, mock_client):
        """Should handle 429 response with backoff and retry."""
        from sf_utils.tooling import poll_async_job

        # Simulate time progression
        mock_monotonic.side_effect = [0, 0, 5, 10]

        # First call: 429, Second call: success
        mock_response_429 = Mock()
        mock_response_429.status_code = 429
        mock_response_429.headers = {'Retry-After': '2'}
        mock_response_429.json.return_value = {
            "errorCode": "REQUEST_LIMIT_EXCEEDED",
            "message": "Request limit exceeded"
        }

        mock_response_200 = Mock()
        mock_response_200.status_code = 200
        mock_response_200.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Completed",
                "NumberOfErrors": 0,
                "JobItemsProcessed": 100,
                "TotalJobItems": 100,
                "ExtendedStatus": None
            }]
        }

        mock_get.side_effect = [mock_response_429, mock_response_200]

        result = poll_async_job(
            client=mock_client,
            job_id="7071a00000XXXXXX",
            poll_interval=2,
            timeout=60
        )

        assert result["Status"] == "Completed"
        # Should have retried after backoff
        assert mock_get.call_count == 2


class TestPollAsyncJobValidation:
    """Tests for poll_async_job input validation."""

    def test_poll_async_job_empty_job_id_raises_error(self, mock_client):
        """Should raise ValueError for empty job_id."""
        from sf_utils.tooling import poll_async_job

        with pytest.raises(ValueError) as exc_info:
            poll_async_job(client=mock_client, job_id="")

        assert "empty" in str(exc_info.value).lower()

    def test_poll_async_job_invalid_poll_interval_raises_error(self, mock_client):
        """Should raise ValueError for non-positive poll_interval."""
        from sf_utils.tooling import poll_async_job

        with pytest.raises(ValueError) as exc_info:
            poll_async_job(client=mock_client, job_id="7071a00000XXXXXX", poll_interval=0)

        assert "positive" in str(exc_info.value).lower()

    def test_poll_async_job_invalid_timeout_raises_error(self, mock_client):
        """Should raise ValueError for non-positive timeout."""
        from sf_utils.tooling import poll_async_job

        with pytest.raises(ValueError) as exc_info:
            poll_async_job(client=mock_client, job_id="7071a00000XXXXXX", timeout=0)

        assert "positive" in str(exc_info.value).lower()


class TestClientHandling:
    """Tests for client creation and handling."""

    @patch('sf_utils.tooling.requests.get')
    @patch('sf_utils.tooling.get_client')
    def test_execute_anonymous_creates_client_if_not_provided(self, mock_get_client, mock_get):
        """Should create client from env if not provided."""
        from sf_utils.tooling import execute_anonymous

        # Create mock client
        mock_client = Mock()
        mock_client.sf_instance = "example.my.salesforce.com"
        mock_client.sf_version = "61.0"
        mock_client.session_id = "00Dxx0000001234!ABC"
        mock_client.proxies = None
        mock_get_client.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "compiled": True,
            "success": True,
            "compileProblem": None,
            "exceptionMessage": None
        }
        mock_get.return_value = mock_response

        execute_anonymous(apex_code="System.debug('test');")

        mock_get_client.assert_called_once()

    @patch('sf_utils.tooling.time.sleep')
    @patch('sf_utils.tooling.requests.get')
    @patch('sf_utils.tooling.get_client')
    def test_poll_async_job_creates_client_if_not_provided(self, mock_get_client, mock_get, mock_sleep):
        """Should create client from env if not provided for polling."""
        from sf_utils.tooling import poll_async_job

        mock_client = Mock()
        mock_client.sf_instance = "example.my.salesforce.com"
        mock_client.sf_version = "61.0"
        mock_client.session_id = "00Dxx0000001234!ABC"
        mock_client.proxies = None
        mock_get_client.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "totalSize": 1,
            "done": True,
            "records": [{
                "Id": "7071a00000XXXXXX",
                "Status": "Completed",
                "NumberOfErrors": 0,
                "JobItemsProcessed": 100,
                "TotalJobItems": 100,
                "ExtendedStatus": None
            }]
        }
        mock_get.return_value = mock_response

        poll_async_job(
            job_id="7071a00000XXXXXX",
            poll_interval=1,
            timeout=60
        )

        mock_get_client.assert_called_once()
