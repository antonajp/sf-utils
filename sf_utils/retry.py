"""Retry logic with exponential backoff for Salesforce API calls.

This module provides configurable retry behavior with circuit breaker protection,
jitter to prevent thundering herd, and API usage monitoring.
"""

import functools
import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

from .exceptions import (
    SalesforceAuthError,
    SalesforceRateLimitError,
    SalesforceAPIError,
    SalesforceError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RetryConfig",
    "APIUsageInfo",
    "with_retry",
    "raise_for_status",
    "DEFAULT_RETRY_CONFIG",
    "BATCH_RETRY_CONFIG",
    "NO_RETRY_CONFIG",
]

# Type variable for decorated functions
F = TypeVar('F', bound=Callable[..., Any])

# Circuit breaker: track consecutive failures across all decorated functions
# Thread-safe implementation using a lock
_circuit_breaker_lock = threading.Lock()
_consecutive_failures = 0
_circuit_breaker_threshold = 10


@dataclass
class RetryConfig:
    """Configuration for retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        initial_backoff: Initial backoff time in seconds.
        max_backoff: Maximum backoff time in seconds.
        jitter: Jitter factor (0.0-1.0) to add randomness to backoff.
        backoff_multiplier: Multiplier for exponential backoff.
    """
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    jitter: float = 0.1
    backoff_multiplier: float = 2.0

    def __post_init__(self):
        """Validate configuration values."""
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.initial_backoff <= 0:
            raise ValueError("initial_backoff must be > 0")
        if self.max_backoff < self.initial_backoff:
            raise ValueError("max_backoff must be >= initial_backoff")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("jitter must be between 0.0 and 1.0")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")


# Preset configurations
DEFAULT_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    initial_backoff=1.0,
    max_backoff=60.0,
    jitter=0.1,
    backoff_multiplier=2.0,
)

BATCH_RETRY_CONFIG = RetryConfig(
    max_retries=5,
    initial_backoff=1.0,
    max_backoff=300.0,
    jitter=0.1,
    backoff_multiplier=2.0,
)

NO_RETRY_CONFIG = RetryConfig(
    max_retries=0,
    initial_backoff=1.0,
    max_backoff=1.0,
    jitter=0.0,
    backoff_multiplier=1.0,
)


@dataclass
class APIUsageInfo:
    """Parsed API usage information from Sforce-Limit-Info header.

    Attributes:
        used: Number of API calls used in the current window.
        total: Total API calls allowed in the current window.
        percentage: Percentage of API quota used (0-100).
    """
    used: int
    total: int
    percentage: float

    @classmethod
    def from_header(cls, header_value: Optional[str]) -> Optional['APIUsageInfo']:
        """Parse API usage from Sforce-Limit-Info header.

        Args:
            header_value: Header value like "api-usage=5000/15000".

        Returns:
            APIUsageInfo instance, or None if header is missing/invalid.

        Example:
            >>> info = APIUsageInfo.from_header("api-usage=5000/15000")
            >>> info.used
            5000
            >>> info.total
            15000
            >>> info.percentage
            33.33
        """
        if not header_value:
            return None

        # Match pattern: api-usage=USED/TOTAL (not per-app-api-usage)
        # Use (?:^|[\s;]) to ensure we match "api-usage" at start or after whitespace/semicolon
        match = re.search(r'(?:^|[\s;])api-usage=(\d+)/(\d+)', header_value)
        if not match:
            logger.debug(f"Could not parse API usage from header: {header_value}")
            return None

        used = int(match.group(1))
        total = int(match.group(2))
        percentage = (used / total * 100) if total > 0 else 0.0

        return cls(used=used, total=total, percentage=percentage)


def _calculate_backoff(attempt: int, config: RetryConfig, retry_after: Optional[int] = None) -> float:
    """Calculate backoff time with exponential growth and jitter.

    Args:
        attempt: Current retry attempt number (0-indexed).
        config: Retry configuration.
        retry_after: Retry-After header value in seconds (takes precedence).

    Returns:
        Backoff time in seconds.
    """
    if retry_after is not None and retry_after > 0:
        # Respect Retry-After header, but still apply max_backoff cap
        base_backoff = min(retry_after, config.max_backoff)
    else:
        # Exponential backoff: initial * (multiplier ^ attempt)
        base_backoff = min(
            config.initial_backoff * (config.backoff_multiplier ** attempt),
            config.max_backoff
        )

    # Add jitter to prevent thundering herd
    jitter_range = base_backoff * config.jitter
    jitter_amount = random.uniform(-jitter_range, jitter_range)
    backoff = max(0, base_backoff + jitter_amount)

    logger.debug(
        f"Calculated backoff: {backoff:.2f}s (attempt={attempt}, "
        f"base={base_backoff:.2f}s, jitter={jitter_amount:.2f}s)"
    )

    return backoff


def _check_circuit_breaker():
    """Check if circuit breaker has tripped.

    Thread-safe check using a lock.

    Raises:
        SalesforceError: If too many consecutive failures have occurred.
    """
    global _consecutive_failures

    with _circuit_breaker_lock:
        if _consecutive_failures >= _circuit_breaker_threshold:
            logger.error(
                f"Circuit breaker tripped: {_consecutive_failures} consecutive failures. "
                "Stopping retries to prevent cascading failures."
            )
            raise SalesforceError(
                f"Circuit breaker open: {_consecutive_failures} consecutive failures across all API calls"
            )


def _reset_circuit_breaker():
    """Reset circuit breaker after successful call.

    Thread-safe reset using a lock.
    """
    global _consecutive_failures

    with _circuit_breaker_lock:
        if _consecutive_failures > 0:
            logger.info(f"Resetting circuit breaker (was {_consecutive_failures} failures)")
            _consecutive_failures = 0


def _increment_circuit_breaker():
    """Increment circuit breaker failure counter.

    Thread-safe increment using a lock.
    """
    global _consecutive_failures

    with _circuit_breaker_lock:
        _consecutive_failures += 1
        logger.debug(f"Circuit breaker failures: {_consecutive_failures}/{_circuit_breaker_threshold}")


def raise_for_status(body: Any, status: int, headers: Optional[Dict[str, str]] = None) -> None:
    """Raise appropriate exception for non-2xx status codes.

    This is the centralized error parsing function that converts HTTP responses
    into typed exceptions. Used primarily by bulk_sync.py for raw HTTP responses.

    Note: query.py and sobjects.py now use simple-salesforce's exception handling
    directly, which provides proper header access for rate limit information.

    Args:
        body: Response body from Salesforce API.
        status: HTTP status code.
        headers: Response headers (optional, used for Retry-After and API usage).
            With simple-salesforce, headers are available on exceptions.

    Raises:
        SalesforceAuthError: For 401/403 status codes.
        SalesforceRateLimitError: For 429 or REQUEST_LIMIT_EXCEEDED errors.
        SalesforceAPIError: For other 4xx/5xx errors.

    Example:
        >>> # Used by bulk_sync.py for raw HTTP responses
        >>> response = requests.get(url, headers=headers)
        >>> raise_for_status(response.json(), response.status_code, dict(response.headers))
    """
    if status < 400:
        return  # Success

    headers = headers or {}

    # Extract error message from response body
    # Salesforce typically returns [{"message": "...", "errorCode": "..."}]
    if isinstance(body, list) and len(body) > 0 and isinstance(body[0], dict):
        error_message = body[0].get('message', 'Unknown error')
        error_code = body[0].get('errorCode', '')
    elif isinstance(body, dict):
        error_message = body.get('message', body.get('error_description', 'Unknown error'))
        error_code = body.get('errorCode', body.get('error', ''))
    else:
        error_message = str(body) if body else 'Unknown error'
        error_code = ''

    # Authentication/authorization errors (do NOT retry)
    if status in (401, 403):
        logger.error(f"Authentication/authorization failed: {error_message}")
        raise SalesforceAuthError(
            message=error_message,
            status_code=status,
            response_body=body if isinstance(body, dict) else None
        )

    # Rate limit errors
    if status == 429 or error_code == 'REQUEST_LIMIT_EXCEEDED':
        retry_after = None
        if 'Retry-After' in headers:
            try:
                retry_after = int(headers['Retry-After'])
            except (ValueError, TypeError):
                logger.warning(f"Invalid Retry-After header: {headers['Retry-After']}")

        api_usage = None
        if 'Sforce-Limit-Info' in headers:
            api_usage = headers['Sforce-Limit-Info']

        logger.warning(
            f"Rate limit exceeded. retry_after={retry_after}s, api_usage={api_usage}"
        )
        raise SalesforceRateLimitError(
            message=error_message,
            status_code=status,
            response_body=body if isinstance(body, dict) else None,
            retry_after=retry_after,
            api_usage=api_usage
        )

    # Generic API error
    logger.error(f"Salesforce API error: {error_message}, status={status}")
    raise SalesforceAPIError(
        message=error_message,
        status_code=status,
        response_body=body if isinstance(body, dict) else None
    )


def with_retry(config: RetryConfig = DEFAULT_RETRY_CONFIG) -> Callable[[F], F]:
    """Decorator to add retry logic with exponential backoff to Salesforce API calls.

    Automatically retries on SalesforceRateLimitError, respects Retry-After headers,
    does NOT retry SalesforceAuthError, and implements circuit breaker protection.

    Args:
        config: Retry configuration (defaults to DEFAULT_RETRY_CONFIG).

    Returns:
        Decorator function.

    Example:
        >>> @with_retry(BATCH_RETRY_CONFIG)
        ... def query_accounts(client):
        ...     body, status = client.query("SELECT Id FROM Account")
        ...     raise_for_status(body, status)
        ...     return body
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0

            while attempt <= config.max_retries:
                try:
                    # Check circuit breaker before attempting call
                    _check_circuit_breaker()

                    # Execute the function
                    result = func(*args, **kwargs)

                    # Success - reset circuit breaker
                    _reset_circuit_breaker()

                    return result

                except SalesforceAuthError:
                    # Auth errors should NEVER be retried
                    _increment_circuit_breaker()
                    logger.error("Authentication error - not retrying")
                    raise

                except SalesforceRateLimitError as e:
                    _increment_circuit_breaker()

                    # Check API usage and warn if high
                    if e.api_usage:
                        usage_info = APIUsageInfo.from_header(e.api_usage)
                        if usage_info and usage_info.percentage >= 80:
                            logger.warning(
                                f"API usage critical: {usage_info.percentage:.1f}% "
                                f"({usage_info.used}/{usage_info.total})"
                            )

                    # Retry if attempts remain
                    if attempt < config.max_retries:
                        backoff = _calculate_backoff(attempt, config, e.retry_after)
                        logger.warning(
                            f"Rate limit hit, retrying in {backoff:.2f}s "
                            f"(attempt {attempt + 1}/{config.max_retries})"
                        )
                        time.sleep(backoff)
                        attempt += 1
                    else:
                        logger.error(f"Max retries ({config.max_retries}) exceeded for rate limit")
                        raise

                except (SalesforceAPIError, SalesforceError) as e:
                    # Other Salesforce errors might be transient (500, 503, etc.)
                    _increment_circuit_breaker()

                    # Retry 5xx errors, but not 4xx (except rate limits, handled above)
                    if hasattr(e, 'status_code') and e.status_code is not None and e.status_code >= 500:
                        if attempt < config.max_retries:
                            backoff = _calculate_backoff(attempt, config)
                            logger.warning(
                                f"Server error {e.status_code}, retrying in {backoff:.2f}s "
                                f"(attempt {attempt + 1}/{config.max_retries})"
                            )
                            time.sleep(backoff)
                            attempt += 1
                        else:
                            logger.error(f"Max retries ({config.max_retries}) exceeded")
                            raise
                    else:
                        # 4xx errors (except rate limits) should not be retried
                        logger.error(f"Client error {e.status_code if hasattr(e, 'status_code') else 'unknown'} - not retrying")
                        raise

            # Should never reach here, but just in case
            raise SalesforceError(f"Unexpected retry loop exit after {attempt} attempts")

        return wrapper
    return decorator
