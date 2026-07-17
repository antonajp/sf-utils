"""SOQL query utilities."""

import logging
from typing import Any, Dict, List, Optional

from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError
from sf_utils.retry import raise_for_status

logger = logging.getLogger(__name__)


def query(
    soql: str,
    client: Optional[Client] = None,
) -> List[Dict[str, Any]]:
    """Execute a SOQL query and return the first batch of results.

    Automatically raises typed exceptions for API errors.

    Args:
        soql: SOQL query string.
        client: Authenticated Salesforce client. Creates one if not provided.

    Returns:
        List of record dictionaries.

    Raises:
        SalesforceAuthError: If authentication fails.
        SalesforceRateLimitError: If rate limit is exceeded.
        SalesforceAPIError: If query fails with other errors.
    """
    if client is None:
        client = get_client()

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
    raise_for_status(body, status)

    records = body.get("records", [])
    logger.debug("Query returned %d records", len(records))

    return records


def query_all(
    soql: str,
    client: Optional[Client] = None,
) -> List[Dict[str, Any]]:
    """Execute a SOQL query and return ALL results, handling pagination.

    Automatically raises typed exceptions for API errors.

    Args:
        soql: SOQL query string.
        client: Authenticated Salesforce client. Creates one if not provided.

    Returns:
        List of all record dictionaries across all pages.

    Raises:
        SalesforceAuthError: If authentication fails.
        SalesforceRateLimitError: If rate limit is exceeded.
        SalesforceAPIError: If query fails with other errors.
    """
    if client is None:
        client = get_client()

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
            break

        body, status = response if isinstance(response, tuple) else (response, 200)

        if status >= 400:
            logger.warning("Pagination failed at %s with status %d", next_url, status)
            break

        records = body.get("records", [])
        all_records.extend(records)

    logger.debug("Query returned total of %d records", len(all_records))

    return all_records
