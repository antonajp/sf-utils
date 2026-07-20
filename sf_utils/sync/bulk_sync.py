"""Bulk API 2.0 job creation for Salesforce data synchronization."""

import logging
from typing import Optional

import requests
from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, _sanitize_value
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, with_retry, raise_for_status

logger = logging.getLogger(__name__)


def create_bulk_query_job(
    sobject_type: str,
    soql_query: str,
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> str:
    """Create a Bulk API 2.0 query job.

    Creates an asynchronous query job using Salesforce's Bulk API 2.0. The job is
    created in UploadComplete state and can be polled for results using the returned
    job ID.

    Bulk API 2.0 is designed for large data volumes (up to 150 million records) and
    provides better performance than REST API for batch operations. Results are
    returned as CSV files.

    Args:
        sobject_type: Salesforce object type (e.g., "Account", "Contact").
            Used for logging and context only - the SOQL query determines the actual
            objects queried.
        soql_query: Complete SOQL query to execute (e.g., "SELECT Id, Name FROM Account").
            Must be valid SOQL syntax. Query is executed asynchronously.
        client: Authenticated Salesforce client. Creates one if not provided.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        Job ID (e.g., "750xx000000Xyzo") that can be used to poll job status and
        retrieve results.

    Raises:
        ValueError: If sobject_type or soql_query is empty.
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If job creation fails with other errors.

    Example:
        >>> from sf_utils.sync import create_bulk_query_job
        >>>
        >>> # Create a bulk query job for all Accounts
        >>> job_id = create_bulk_query_job(
        ...     sobject_type="Account",
        ...     soql_query="SELECT Id, Name, Industry FROM Account"
        ... )
        >>> print(f"Job created: {job_id}")
        Job created: 750xx000000XyzoAAC
        >>>
        >>> # With custom retry configuration
        >>> from sf_utils.retry import BATCH_RETRY_CONFIG
        >>> job_id = create_bulk_query_job(
        ...     sobject_type="Opportunity",
        ...     soql_query="SELECT Id, Amount FROM Opportunity WHERE Amount > 10000",
        ...     retry_config=BATCH_RETRY_CONFIG
        ... )

    Notes:
        - Bulk API 2.0 has different rate limits than REST API (see Salesforce docs)
        - Results must be downloaded separately after job completes
        - Jobs expire after 7 days
        - Maximum query size is 15,000 characters
    """
    # Validate inputs
    if not sobject_type or not sobject_type.strip():
        raise ValueError("sobject_type cannot be empty")
    if not soql_query or not soql_query.strip():
        raise ValueError("soql_query cannot be empty")

    # Initialize client if not provided
    if client is None:
        client = get_client()

    logger.debug(
        "Creating Bulk API 2.0 query job: sobject_type=%s, query=%s",
        sobject_type,
        soql_query[:100] + "..." if len(soql_query) > 100 else soql_query
    )

    # Execute with retry logic
    @with_retry(retry_config)
    def _create_job():
        # Construct Bulk API 2.0 endpoint
        # Format: https://{instance_url}/services/data/{version}/jobs/query
        api_version = client.client_api_version or "v61.0"
        # Remove 'v' prefix if present for URL construction
        version = api_version.lstrip('v')
        url = f"https://{client.instance_url}/services/data/v{version}/jobs/query"

        # Prepare request body
        request_body = {
            "operation": "query",
            "query": soql_query,
            "contentType": "CSV"
        }

        # Prepare headers
        headers = {
            "Authorization": f"Bearer {client.session_id}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        logger.debug(
            "POST %s, operation=query, contentType=CSV, query_length=%d",
            url,
            len(soql_query)
        )

        # Make HTTP request using requests library directly
        # SalesforcePy doesn't expose native Bulk API 2.0 support
        response = requests.post(
            url,
            json=request_body,
            headers=headers,
            proxies=client.proxies if hasattr(client, 'proxies') else None,
            timeout=30
        )

        # Parse response
        status_code = response.status_code
        try:
            response_body = response.json()
        except Exception:
            # If JSON parsing fails, use text as fallback
            response_body = {"error": response.text}

        # Sanitize response body before logging to prevent credential exposure
        logger.debug(
            "Bulk API 2.0 response: status=%d, body=%s",
            status_code,
            str(_sanitize_value(response_body))[:200]
        )

        # Check for errors using standard error handling
        # Note: Bulk API 2.0 returns different error formats than REST API,
        # but raise_for_status handles both
        if status_code >= 400:
            # Handle authentication errors
            if status_code in (401, 403):
                error_message = response_body.get('message', 'Authentication failed')
                logger.error(
                    "Bulk API 2.0 authentication failed: status=%d, message=%s",
                    status_code,
                    error_message
                )
                raise SalesforceAuthError(
                    message=error_message,
                    status_code=status_code,
                    response_body=response_body
                )

            # Handle other errors
            error_message = response_body.get('message', response_body.get('exceptionMessage', 'Unknown error'))
            logger.error(
                "Bulk API 2.0 job creation failed: status=%d, message=%s",
                status_code,
                error_message
            )
            raise SalesforceAPIError(
                message=f"Failed to create Bulk API 2.0 job: {error_message}",
                status_code=status_code,
                response_body=response_body
            )

        # Extract job ID from response
        job_id = response_body.get('id')
        if not job_id:
            logger.error("Bulk API 2.0 response missing job ID: %s", _sanitize_value(response_body))
            raise SalesforceAPIError(
                message="Bulk API 2.0 response missing 'id' field",
                status_code=status_code,
                response_body=response_body
            )

        job_state = response_body.get('state', 'Unknown')
        logger.info(
            "Bulk API 2.0 job created: job_id=%s, state=%s, sobject_type=%s",
            job_id,
            job_state,
            sobject_type
        )

        return job_id

    return _create_job()
