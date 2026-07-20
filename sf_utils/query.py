"""SOQL query utilities."""

import logging
from typing import Any, Dict, List, Optional

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError as SimpleSalesforceError

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, SalesforceRateLimitError
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, with_retry

logger = logging.getLogger(__name__)


def _handle_salesforce_exception(e: SimpleSalesforceError, context: str = "query") -> None:
    """Convert simple-salesforce exceptions to sf_utils exceptions.

    Args:
        e: Exception from simple-salesforce.
        context: Context string for error message.

    Raises:
        SalesforceAuthError: For authentication/authorization failures.
        SalesforceRateLimitError: For rate limit errors.
        SalesforceAPIError: For other API errors.
    """
    error_str = str(e)
    status_code = getattr(e, 'status', None)

    # Check for authentication errors
    if status_code in (401, 403) or "INVALID_SESSION_ID" in error_str:
        logger.error("Authentication error during %s: %s", context, error_str)
        raise SalesforceAuthError(
            message=error_str,
            status_code=status_code
        ) from e

    # Check for rate limit errors
    if status_code == 429 or "REQUEST_LIMIT_EXCEEDED" in error_str:
        # Extract retry_after from exception if available
        retry_after = None
        api_usage = None
        if hasattr(e, 'headers'):
            headers = e.headers or {}
            if 'Retry-After' in headers:
                try:
                    retry_after = int(headers['Retry-After'])
                except (ValueError, TypeError):
                    pass
            if 'Sforce-Limit-Info' in headers:
                api_usage = headers['Sforce-Limit-Info']

        logger.warning("Rate limit exceeded during %s: %s", context, error_str)
        raise SalesforceRateLimitError(
            message=error_str,
            status_code=status_code or 429,
            retry_after=retry_after,
            api_usage=api_usage
        ) from e

    # Generic API error
    logger.error("API error during %s: %s", context, error_str)
    raise SalesforceAPIError(
        message=error_str,
        status_code=status_code or 500
    ) from e


def query(
    soql: str,
    client: Optional[Salesforce] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> List[Dict[str, Any]]:
    """Execute a SOQL query and return the first batch of results.

    Automatically retries on rate limits with exponential backoff. Raises typed
    exceptions for API errors.

    Args:
        soql: SOQL query string.
        client: Authenticated Salesforce client. Creates one if not provided.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        List of record dictionaries.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If query fails with other errors.

    Example:
        >>> # Use default retry behavior
        >>> records = query("SELECT Id FROM Account")
        >>>
        >>> # Disable retries
        >>> from sf_utils.retry import NO_RETRY_CONFIG
        >>> records = query("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)
        >>>
        >>> # Custom retry config
        >>> custom_config = RetryConfig(max_retries=5, initial_backoff=2.0)
        >>> records = query("SELECT Id FROM Account", retry_config=custom_config)
    """
    # Initialize client outside inner function to avoid issues with nonlocal
    if client is None:
        client = get_client()

    def _query_impl():
        logger.debug("Executing SOQL query: %s", soql[:100])

        try:
            # simple-salesforce returns dict directly, not (body, status) tuple
            result = client.query(soql)

            if result is None:
                logger.error("Query returned None")
                raise SalesforceAPIError(
                    message="Query failed - no response from Salesforce",
                    status_code=500
                )

            records = result.get("records", [])
            logger.debug("Query returned %d records", len(records))

            return records

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, "query")

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_query_impl)()
    else:
        logger.debug("Retry disabled")
        return _query_impl()


def query_all(
    soql: str,
    client: Optional[Salesforce] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> List[Dict[str, Any]]:
    """Execute a SOQL query and return ALL results, handling pagination.

    Automatically retries on rate limits with exponential backoff. Raises typed
    exceptions for API errors.

    Args:
        soql: SOQL query string.
        client: Authenticated Salesforce client. Creates one if not provided.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        List of all record dictionaries across all pages.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If query fails with other errors.

    Example:
        >>> # Use default retry behavior
        >>> records = query_all("SELECT Id, Name FROM Account")
        >>>
        >>> # Disable retries
        >>> from sf_utils.retry import NO_RETRY_CONFIG
        >>> records = query_all("SELECT Id FROM Account", retry_config=NO_RETRY_CONFIG)
    """
    # Initialize client outside inner function to avoid issues with nonlocal
    if client is None:
        client = get_client()

    def _query_all_impl():
        logger.debug("Executing paginated SOQL query: %s", soql[:100])

        all_records: List[Dict[str, Any]] = []

        try:
            # simple-salesforce query_all() automatically handles pagination
            # and includes soft-deleted records (queryAll endpoint)
            # We use query() + query_more() for consistency with original behavior
            result = client.query(soql)

            if result is None:
                logger.error("Query returned None")
                raise SalesforceAPIError(
                    message="Query failed - no response from Salesforce",
                    status_code=500
                )

            records = result.get("records", [])
            all_records.extend(records)

            # Handle pagination via nextRecordsUrl
            while not result.get("done", True) and result.get("nextRecordsUrl"):
                next_url = result["nextRecordsUrl"]
                logger.debug("Fetching next page: %s", next_url)

                result = client.query_more(next_url, identifier_is_url=True)

                if result is None:
                    logger.warning("Pagination query_more returned None at %s", next_url)
                    break

                records = result.get("records", [])
                all_records.extend(records)

            logger.debug("Query returned total of %d records", len(all_records))

            return all_records

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, "query_all")

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_query_all_impl)()
    else:
        logger.debug("Retry disabled")
        return _query_all_impl()
