"""Custom exceptions for Salesforce operations.

This module provides a hierarchy of exceptions for handling Salesforce API errors
with automatic credential sanitization for security.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "SalesforceError",
    "SalesforceRateLimitError",
    "SalesforceAuthError",
    "SalesforceAPIError",
]

# Sensitive fields that should be redacted from error messages
SENSITIVE_FIELDS = {
    'access_token',
    'refresh_token',
    'password',
    'client_secret',
    'session_id',
    'authorization',
}


def _sanitize_value(value: Any) -> Any:
    """Recursively sanitize sensitive data from values.

    Args:
        value: The value to sanitize (can be dict, list, str, or other).

    Returns:
        Sanitized value with sensitive data redacted.
    """
    if isinstance(value, dict):
        return {k: '***REDACTED***' if k.lower() in SENSITIVE_FIELDS else _sanitize_value(v)
                for k, v in value.items()}
    elif isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    elif isinstance(value, str):
        # Redact Bearer tokens in Authorization headers
        value = re.sub(r'Bearer\s+[A-Za-z0-9._-]+', 'Bearer ***REDACTED***', value, flags=re.IGNORECASE)
        # Redact session IDs (alphanumeric strings that look like session tokens)
        value = re.sub(r'(session[_-]?id["\s:=]+)[A-Za-z0-9!._-]{15,}', r'\1***REDACTED***', value, flags=re.IGNORECASE)
    return value


class SalesforceError(Exception):
    """Base exception for all Salesforce API errors.

    Automatically sanitizes credentials from error messages and response bodies.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code (if applicable).
        response_body: Sanitized response body from Salesforce.
    """

    def __init__(self, message: str, status_code: Optional[int] = None,
                 response_body: Optional[Dict[str, Any]] = None):
        """Initialize SalesforceError.

        Args:
            message: Error message.
            status_code: HTTP status code.
            response_body: Raw response body from Salesforce API.
        """
        self.message = message
        self.status_code = status_code
        self.response_body = _sanitize_value(response_body) if response_body else None

        # Construct safe error message
        error_parts = [message]
        if status_code:
            error_parts.append(f"Status: {status_code}")

        super().__init__(", ".join(error_parts))


class SalesforceRateLimitError(SalesforceError):
    """Exception raised when Salesforce API rate limit is exceeded.

    Raised for HTTP 429 (Too Many Requests) or when error code is REQUEST_LIMIT_EXCEEDED.

    Attributes:
        retry_after (Optional[int]): Seconds to wait before retrying (from Retry-After header).
        api_usage (Optional[str]): Current API usage info (e.g., "5000/15000").
    """

    retry_after: Optional[int]
    api_usage: Optional[str]

    def __init__(self, message: str, status_code: int = 429,
                 response_body: Optional[Dict[str, Any]] = None,
                 retry_after: Optional[int] = None,
                 api_usage: Optional[str] = None):
        """Initialize SalesforceRateLimitError.

        Args:
            message: Error message.
            status_code: HTTP status code (default 429).
            response_body: Raw response body from Salesforce API.
            retry_after: Seconds to wait before retrying.
            api_usage: API usage string (e.g., "5000/15000").
        """
        super().__init__(message, status_code, response_body)
        self.retry_after = retry_after
        self.api_usage = api_usage


class SalesforceAuthError(SalesforceError):
    """Exception raised for authentication/authorization failures.

    Raised for HTTP 401 (Unauthorized) or 403 (Forbidden).
    These errors should NOT be retried.
    """

    def __init__(self, message: str, status_code: Optional[int] = None,
                 response_body: Optional[Dict[str, Any]] = None):
        """Initialize SalesforceAuthError.

        Args:
            message: Error message.
            status_code: HTTP status code (401 or 403). Optional for login failures.
            response_body: Raw response body from Salesforce API.
        """
        super().__init__(message, status_code, response_body)


class SalesforceAPIError(SalesforceError):
    """Exception raised for general Salesforce API errors.

    Raised for 4xx/5xx errors that are not rate limits or auth errors.
    """

    def __init__(self, message: str, status_code: int,
                 response_body: Optional[Dict[str, Any]] = None):
        """Initialize SalesforceAPIError.

        Args:
            message: Error message.
            status_code: HTTP status code.
            response_body: Raw response body from Salesforce API.
        """
        super().__init__(message, status_code, response_body)
