"""Tests for exception handling and credential sanitization."""

import pytest

from sf_utils.exceptions import (
    SalesforceError,
    SalesforceRateLimitError,
    SalesforceAuthError,
    SalesforceAPIError,
    _sanitize_value,
)


class TestSanitizeValue:
    """Tests for credential sanitization."""

    def test_sanitize_dict_with_sensitive_fields(self):
        """Should redact sensitive fields in dictionaries."""
        data = {
            'access_token': 'secret_token_123',
            'username': 'user@example.com',
            'password': 'secret_pass',
            'client_secret': 'secret_client',
        }
        sanitized = _sanitize_value(data)

        assert sanitized['access_token'] == '***REDACTED***'
        assert sanitized['password'] == '***REDACTED***'
        assert sanitized['client_secret'] == '***REDACTED***'
        assert sanitized['username'] == 'user@example.com'  # Not sensitive

    def test_sanitize_nested_dict(self):
        """Should recursively sanitize nested structures."""
        data = {
            'user': {
                'name': 'John',
                'session_id': 'session_abc123',
            },
            'auth': {
                'refresh_token': 'refresh_xyz789',
            }
        }
        sanitized = _sanitize_value(data)

        assert sanitized['user']['name'] == 'John'
        assert sanitized['user']['session_id'] == '***REDACTED***'
        assert sanitized['auth']['refresh_token'] == '***REDACTED***'

    def test_sanitize_list_of_dicts(self):
        """Should sanitize lists containing dictionaries."""
        data = [
            {'access_token': 'token1'},
            {'access_token': 'token2'},
        ]
        sanitized = _sanitize_value(data)

        assert sanitized[0]['access_token'] == '***REDACTED***'
        assert sanitized[1]['access_token'] == '***REDACTED***'

    def test_sanitize_bearer_token_in_string(self):
        """Should redact Bearer tokens from strings."""
        auth_header = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        sanitized = _sanitize_value(auth_header)

        assert 'Bearer ***REDACTED***' in sanitized
        assert 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' not in sanitized

    def test_sanitize_session_id_in_string(self):
        """Should redact session IDs from strings."""
        response = 'session_id: 00D8c000000abcd!ARsAQFGHIJKLMNOP'
        sanitized = _sanitize_value(response)

        assert '***REDACTED***' in sanitized
        assert '00D8c000000abcd!ARsAQFGHIJKLMNOP' not in sanitized

    def test_sanitize_non_dict_values(self):
        """Should handle non-dict values without errors."""
        assert _sanitize_value(123) == 123
        assert _sanitize_value(None) is None
        assert _sanitize_value("plain text") == "plain text"


class TestSalesforceError:
    """Tests for base SalesforceError exception."""

    def test_basic_error(self):
        """Should create error with message only."""
        error = SalesforceError("Something went wrong")

        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"
        assert error.status_code is None
        assert error.response_body is None

    def test_error_with_status_code(self):
        """Should include status code in error message."""
        error = SalesforceError("Bad request", status_code=400)

        assert "Bad request" in str(error)
        assert "Status: 400" in str(error)
        assert error.status_code == 400

    def test_error_sanitizes_response_body(self):
        """Should sanitize sensitive data in response body."""
        response_body = {
            'error': 'Invalid credentials',
            'access_token': 'secret_token',
            'username': 'user@example.com',
        }
        error = SalesforceError("Auth failed", response_body=response_body)

        assert error.response_body['access_token'] == '***REDACTED***'
        assert error.response_body['username'] == 'user@example.com'
        assert error.response_body['error'] == 'Invalid credentials'


class TestSalesforceRateLimitError:
    """Tests for SalesforceRateLimitError."""

    def test_rate_limit_error_basic(self):
        """Should create rate limit error with default values."""
        error = SalesforceRateLimitError("Too many requests")

        assert error.message == "Too many requests"
        assert error.status_code == 429
        assert error.retry_after is None
        assert error.api_usage is None

    def test_rate_limit_error_with_retry_after(self):
        """Should store retry_after value."""
        error = SalesforceRateLimitError(
            "Rate limit exceeded",
            retry_after=60,
            api_usage="5000/15000"
        )

        assert error.retry_after == 60
        assert error.api_usage == "5000/15000"

    def test_rate_limit_error_no_side_effects(self):
        """Exception constructor should not have side effects (logging moved to raise_for_status)."""
        # Creating exception should not log - logging happens in raise_for_status()
        error = SalesforceRateLimitError(
            "Rate limit",
            retry_after=30,
            api_usage="14000/15000"
        )
        # Just verify the exception was created correctly
        assert error.retry_after == 30
        assert error.api_usage == "14000/15000"


class TestSalesforceAuthError:
    """Tests for SalesforceAuthError."""

    def test_auth_error_401(self):
        """Should create auth error for 401."""
        error = SalesforceAuthError("Unauthorized", status_code=401)

        assert error.message == "Unauthorized"
        assert error.status_code == 401

    def test_auth_error_403(self):
        """Should create auth error for 403."""
        error = SalesforceAuthError("Forbidden", status_code=403)

        assert error.message == "Forbidden"
        assert error.status_code == 403

    def test_auth_error_optional_status_code(self):
        """Status code should be optional for login failures without HTTP response."""
        error = SalesforceAuthError("Login failed - check credentials")

        assert error.message == "Login failed - check credentials"
        assert error.status_code is None


class TestSalesforceAPIError:
    """Tests for SalesforceAPIError."""

    def test_api_error_basic(self):
        """Should create API error with status code."""
        error = SalesforceAPIError("Bad request", status_code=400)

        assert error.message == "Bad request"
        assert error.status_code == 400

    def test_api_error_with_response_body(self):
        """Should store and sanitize response body."""
        response = {
            'errorCode': 'INVALID_FIELD',
            'message': 'Field does not exist',
            'password': 'secret',
        }
        error = SalesforceAPIError("Invalid field", status_code=400, response_body=response)

        assert error.response_body['errorCode'] == 'INVALID_FIELD'
        assert error.response_body['password'] == '***REDACTED***'

    def test_api_error_no_side_effects(self):
        """Exception constructor should not have side effects (logging moved to raise_for_status)."""
        # Creating exception should not log - logging happens in raise_for_status()
        error = SalesforceAPIError("Server error", status_code=500)
        # Just verify the exception was created correctly
        assert error.message == "Server error"
        assert error.status_code == 500
