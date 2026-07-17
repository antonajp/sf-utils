"""SOQL query utilities."""

import logging
from typing import Any, Dict, List, Optional

from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError
from sf_utils.retry import raise_for_status, RetryConfig, DEFAULT_RETRY_CONFIG, with_retry

logger = logging.getLogger(__name__)


def query(
    soql: str,
    client: Optional[Client] = None,
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

        response = client.query(soql)

        if response is None:
            logger.error("Query returned None")
            raise SalesforceAPIError(
                message="Query failed - no response from Salesforce",
                status_code=500
            )

        # SalesforcePy returns tuple (response_body, status_code)
        body, status = response if isinstance(response, tuple) else (response, 200)

        # Raises typed exceptions for error status codes
        # NOTE: headers=None because SalesforcePy 2.2.1 does not expose HTTP response headers.
        # Rate limit detection works via error code parsing (REQUEST_LIMIT_EXCEEDED) instead.
        raise_for_status(body, status, headers=None)

        records = body.get("records", [])
        logger.debug("Query returned %d records", len(records))

        return records

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
    client: Optional[Client] = None,
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

        response = client.query(soql)

        if response is None:
            logger.error("Query returned None")
            raise SalesforceAPIError(
                message="Query failed - no response from Salesforce",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 200)

        # Raises typed exceptions for error status codes
        raise_for_status(body, status)

        records = body.get("records", [])
        all_records.extend(records)

        # Handle pagination via nextRecordsUrl
        while not body.get("done", True) and body.get("nextRecordsUrl"):
            next_url = body["nextRecordsUrl"]
            logger.debug("Fetching next page: %s", next_url)

            response = client.query_more(next_url)

            if response is None:
                logger.warning("Pagination query_more returned None at %s", next_url)
                break

            body, status = response if isinstance(response, tuple) else (response, 200)

            # Raise exceptions for error status codes to trigger retry
            # NOTE: headers=None because SalesforcePy 2.2.1 does not expose HTTP response headers.
            raise_for_status(body, status, headers=None)

            records = body.get("records", [])
            all_records.extend(records)

        logger.debug("Query returned total of %d records", len(all_records))

        return all_records

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
