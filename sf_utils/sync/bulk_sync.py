"""Bulk API 2.0 job creation for Salesforce data synchronization."""

import csv
import logging
import time
from typing import Any, Dict, Iterator, List, Optional

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


def poll_bulk_job(
    job_id: str,
    client: Optional[Client] = None,
    timeout: float = 600.0,
    poll_interval: float = 5.0,
    max_poll_interval: float = 30.0,
    backoff_multiplier: float = 1.5,
) -> Dict[str, Any]:
    """Poll a Bulk API 2.0 query job until completion.

    Polls a Bulk API 2.0 job at regular intervals until it reaches a terminal state
    (JobComplete, Failed, or Aborted). Uses exponential backoff to reduce API load
    for long-running jobs.

    Terminal states:
    - JobComplete: Job finished successfully, results can be downloaded
    - Failed: Job failed, check errorMessage for details
    - Aborted: Job was aborted by user or system

    Non-terminal states (continue polling):
    - UploadComplete: Job created, not yet started processing
    - InProgress: Job is actively processing data

    Args:
        job_id: Bulk API 2.0 job ID (e.g., "750xx000000XyzoAAC").
        client: Authenticated Salesforce client. Creates one if not provided.
        timeout: Maximum time to poll in seconds. Defaults to 600 (10 minutes).
            Raises SalesforceAPIError if job doesn't complete within timeout.
        poll_interval: Initial polling interval in seconds. Defaults to 5.
            Increases with exponential backoff on each poll.
        max_poll_interval: Maximum polling interval in seconds. Defaults to 30.
            Caps the exponential backoff to prevent excessive delays.
        backoff_multiplier: Multiplier for exponential backoff. Defaults to 1.5.
            Each poll interval is multiplied by this value until max_poll_interval.

    Returns:
        Job metadata dict containing:
        - id: Job ID
        - state: Final state (should be "JobComplete")
        - object: Object type queried
        - createdDate: Job creation timestamp
        - systemModstamp: Last modification timestamp
        - numberRecordsProcessed: Total records processed
        - retries: Number of retries attempted
        - totalProcessingTime: Total processing time in milliseconds

    Raises:
        ValueError: If job_id is empty or timeout is invalid.
        SalesforceAuthError: If authentication fails (401/403).
        SalesforceAPIError: If job fails, is aborted, times out, or API returns error.

    Example:
        >>> from sf_utils.sync import create_bulk_query_job, poll_bulk_job
        >>>
        >>> # Create and poll a bulk query job
        >>> job_id = create_bulk_query_job(
        ...     sobject_type="Account",
        ...     soql_query="SELECT Id, Name FROM Account"
        ... )
        >>> job_info = poll_bulk_job(job_id)
        >>> print(f"Job complete: {job_info['numberRecordsProcessed']} records")
        Job complete: 15000 records
        >>>
        >>> # Custom polling configuration for large jobs
        >>> job_info = poll_bulk_job(
        ...     job_id=job_id,
        ...     timeout=1800.0,  # 30 minutes
        ...     poll_interval=10.0,  # Start at 10 seconds
        ...     max_poll_interval=60.0,  # Cap at 1 minute
        ...     backoff_multiplier=2.0  # Double each time
        ... )

    Notes:
        - Uses time.monotonic() for reliable timeout tracking across system clock changes
        - Polling interval starts at poll_interval and increases exponentially
        - Final interval is capped at max_poll_interval
        - Each HTTP request has a 30-second timeout
        - Job status checks do not consume Bulk API job limits
    """
    # Validate inputs
    if not job_id or not job_id.strip():
        raise ValueError("job_id cannot be empty")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    if max_poll_interval < poll_interval:
        raise ValueError("max_poll_interval must be >= poll_interval")
    if backoff_multiplier < 1.0:
        raise ValueError("backoff_multiplier must be >= 1.0")

    # Initialize client if not provided
    if client is None:
        client = get_client()

    logger.debug(
        "Polling Bulk API 2.0 job: job_id=%s, timeout=%.1fs, poll_interval=%.1fs, "
        "max_poll_interval=%.1fs, backoff_multiplier=%.1f",
        job_id,
        timeout,
        poll_interval,
        max_poll_interval,
        backoff_multiplier
    )

    # Construct Bulk API 2.0 endpoint for job status
    # Format: https://{instance_url}/services/data/{version}/jobs/query/{job_id}
    api_version = client.client_api_version or "v61.0"
    version = api_version.lstrip('v')
    url = f"https://{client.instance_url}/services/data/v{version}/jobs/query/{job_id}"

    # Prepare headers
    headers = {
        "Authorization": f"Bearer {client.session_id}",
        "Accept": "application/json"
    }

    # Terminal states that exit the polling loop
    TERMINAL_STATES = {"JobComplete", "Failed", "Aborted"}

    # Polling loop with exponential backoff
    start_time = time.monotonic()
    current_interval = poll_interval
    poll_count = 0

    while True:
        # Check timeout
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            logger.error(
                "Bulk job polling timeout: job_id=%s, elapsed=%.1fs, timeout=%.1fs, polls=%d",
                job_id,
                elapsed,
                timeout,
                poll_count
            )
            raise SalesforceAPIError(
                message=f"Bulk job {job_id} did not complete within {timeout}s (elapsed: {elapsed:.1f}s, polls: {poll_count})",
                status_code=None,
                response_body={"job_id": job_id, "elapsed": elapsed, "timeout": timeout}
            )

        poll_count += 1

        # GET job status
        logger.debug(
            "Polling job status: job_id=%s, poll=%d, elapsed=%.1fs, interval=%.1fs",
            job_id,
            poll_count,
            elapsed,
            current_interval
        )

        try:
            response = requests.get(
                url,
                headers=headers,
                proxies=client.proxies if hasattr(client, 'proxies') else None,
                timeout=30
            )
        except requests.exceptions.RequestException as e:
            logger.warning(
                "HTTP request failed during job polling: job_id=%s, poll=%d, error=%s",
                job_id,
                poll_count,
                str(e)
            )
            # Sleep before retry
            time.sleep(current_interval)
            current_interval = min(current_interval * backoff_multiplier, max_poll_interval)
            continue

        # Parse response
        status_code = response.status_code
        try:
            job_info = response.json()
        except Exception:
            # If JSON parsing fails, use text as fallback
            job_info = {"error": response.text}

        # Sanitize response body before logging
        logger.debug(
            "Job status response: job_id=%s, status=%d, body=%s",
            job_id,
            status_code,
            str(_sanitize_value(job_info))[:200]
        )

        # Check for errors
        if status_code >= 400:
            # Handle authentication errors
            if status_code in (401, 403):
                error_message = job_info.get('message', 'Authentication failed')
                logger.error(
                    "Bulk API 2.0 authentication failed during polling: job_id=%s, status=%d, message=%s",
                    job_id,
                    status_code,
                    error_message
                )
                raise SalesforceAuthError(
                    message=error_message,
                    status_code=status_code,
                    response_body=job_info
                )

            # Handle other errors
            error_message = job_info.get('message', job_info.get('exceptionMessage', 'Unknown error'))
            logger.error(
                "Bulk API 2.0 job status request failed: job_id=%s, status=%d, message=%s",
                job_id,
                status_code,
                error_message
            )
            raise SalesforceAPIError(
                message=f"Failed to get Bulk API 2.0 job status: {error_message}",
                status_code=status_code,
                response_body=job_info
            )

        # Extract job state
        state = job_info.get("state")
        if not state:
            logger.error(
                "Bulk API 2.0 response missing state: job_id=%s, body=%s",
                job_id,
                _sanitize_value(job_info)
            )
            raise SalesforceAPIError(
                message=f"Bulk API 2.0 response missing 'state' field for job {job_id}",
                status_code=status_code,
                response_body=job_info
            )

        logger.debug(
            "Job state: job_id=%s, state=%s, poll=%d, elapsed=%.1fs",
            job_id,
            state,
            poll_count,
            elapsed
        )

        # Check for terminal states
        if state in TERMINAL_STATES:
            if state == "JobComplete":
                records_processed = job_info.get('numberRecordsProcessed', 0)
                processing_time = job_info.get('totalProcessingTime', 0)
                logger.info(
                    "Bulk job completed: job_id=%s, records=%d, processing_time=%dms, polls=%d, elapsed=%.1fs",
                    job_id,
                    records_processed,
                    processing_time,
                    poll_count,
                    elapsed
                )
                return job_info
            else:
                # Job Failed or Aborted
                error_message = job_info.get('errorMessage', f'Job {state}')
                logger.error(
                    "Bulk job terminated: job_id=%s, state=%s, error=%s, polls=%d, elapsed=%.1fs",
                    job_id,
                    state,
                    error_message,
                    poll_count,
                    elapsed
                )
                raise SalesforceAPIError(
                    message=f"Bulk job {job_id} {state}: {error_message}",
                    status_code=status_code,
                    response_body=job_info
                )

        # Non-terminal state (UploadComplete, InProgress) - continue polling
        logger.debug(
            "Job in non-terminal state: job_id=%s, state=%s, sleeping %.1fs",
            job_id,
            state,
            current_interval
        )

        # Sleep with exponential backoff
        time.sleep(current_interval)
        current_interval = min(current_interval * backoff_multiplier, max_poll_interval)


def get_bulk_results(
    job_id: str,
    client: Optional[Client] = None,
    batch_size: int = 1000,
) -> Iterator[List[Dict[str, Any]]]:
    """Download and parse CSV results from a completed Bulk API 2.0 job.

    Retrieves query results from a completed Bulk API 2.0 job as CSV data and
    yields batches of records as dictionaries. Results are streamed to handle
    large datasets efficiently without loading all data into memory.

    The job must be in "JobComplete" state before calling this function. Use
    poll_bulk_job() to wait for job completion if needed.

    Args:
        job_id: Bulk API 2.0 job ID (e.g., "750xx000000XyzoAAC").
        client: Authenticated Salesforce client. Creates one if not provided.
        batch_size: Number of records to yield per batch. Defaults to 1000.
            Larger batches reduce overhead but consume more memory.

    Yields:
        Lists of dictionaries, where each dictionary represents one record with
        field names as keys. Each batch contains up to batch_size records.
        Final batch may contain fewer records.

    Raises:
        ValueError: If job_id is empty or batch_size is invalid.
        SalesforceAuthError: If authentication fails (401/403).
        SalesforceAPIError: If results download fails or HTTP error occurs.

    Example:
        >>> from sf_utils.sync import create_bulk_query_job, poll_bulk_job, get_bulk_results
        >>>
        >>> # Create and poll a bulk query job
        >>> job_id = create_bulk_query_job(
        ...     sobject_type="Account",
        ...     soql_query="SELECT Id, Name, Industry FROM Account"
        ... )
        >>> job_info = poll_bulk_job(job_id)
        >>>
        >>> # Download results in batches
        >>> total_records = 0
        >>> for batch in get_bulk_results(job_id, batch_size=500):
        ...     total_records += len(batch)
        ...     # Process batch (insert to DB, export to CSV, etc.)
        ...     process_batch(batch)
        >>> print(f"Processed {total_records} records")
        Processed 15000 records
        >>>
        >>> # Access individual records
        >>> for batch in get_bulk_results(job_id):
        ...     for record in batch:
        ...         print(f"{record['Id']}: {record['Name']}")
        001xx000003DHP0AAO: Acme Corporation
        001xx000003DHP1AAO: Global Industries

    Notes:
        - Results are returned as CSV and parsed into dictionaries
        - Uses streaming to handle large result sets (up to 150M records)
        - Malformed CSV rows are logged as WARNING and skipped
        - Progress is logged every batch (INFO level)
        - HTTP request timeout is 300 seconds (5 minutes)
        - Results must be downloaded within 7 days of job completion
        - CSV field values are returned as strings (no type conversion)
    """
    # Validate inputs
    if not job_id or not job_id.strip():
        raise ValueError("job_id cannot be empty")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    # Initialize client if not provided
    if client is None:
        client = get_client()

    logger.debug(
        "Downloading Bulk API 2.0 results: job_id=%s, batch_size=%d",
        job_id,
        batch_size
    )

    # Construct Bulk API 2.0 endpoint for results
    # Format: https://{instance_url}/services/data/{version}/jobs/query/{job_id}/results
    api_version = client.client_api_version or "v61.0"
    version = api_version.lstrip('v')
    url = f"https://{client.instance_url}/services/data/v{version}/jobs/query/{job_id}/results"

    # Prepare headers for CSV response
    headers = {
        "Authorization": f"Bearer {client.session_id}",
        "Accept": "text/csv"
    }

    logger.debug("GET %s, Accept=text/csv", url)

    # Stream response to handle large result sets
    try:
        response = requests.get(
            url,
            headers=headers,
            stream=True,
            proxies=client.proxies if hasattr(client, 'proxies') else None,
            timeout=300  # 5 minutes for large downloads
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            "HTTP request failed during results download: job_id=%s, error=%s",
            job_id,
            str(e)
        )
        raise SalesforceAPIError(
            message=f"Failed to download Bulk API 2.0 results: {str(e)}",
            status_code=None,
            response_body={"job_id": job_id, "error": str(e)}
        )

    # Check for HTTP errors
    status_code = response.status_code
    if status_code >= 400:
        # Try to parse error response (may be JSON or text)
        try:
            error_body = response.json()
            error_message = error_body.get('message', error_body.get('exceptionMessage', 'Unknown error'))
        except Exception:
            error_body = {"error": response.text}
            error_message = response.text or 'Unknown error'

        logger.debug(
            "Results download error response: job_id=%s, status=%d, body=%s",
            job_id,
            status_code,
            str(_sanitize_value(error_body))[:200]
        )

        # Handle authentication errors
        if status_code in (401, 403):
            logger.error(
                "Bulk API 2.0 authentication failed during results download: job_id=%s, status=%d, message=%s",
                job_id,
                status_code,
                error_message
            )
            raise SalesforceAuthError(
                message=error_message,
                status_code=status_code,
                response_body=error_body
            )

        # Handle other errors
        logger.error(
            "Bulk API 2.0 results download failed: job_id=%s, status=%d, message=%s",
            job_id,
            status_code,
            error_message
        )
        raise SalesforceAPIError(
            message=f"Failed to download Bulk API 2.0 results: {error_message}",
            status_code=status_code,
            response_body=error_body
        )

    # Extract total record count from headers if available
    # Salesforce may provide Sforce-NumberOfRecords header
    total_records = None
    if 'Sforce-NumberOfRecords' in response.headers:
        try:
            total_records = int(response.headers['Sforce-NumberOfRecords'])
            logger.debug(
                "Total records from headers: job_id=%s, total=%d",
                job_id,
                total_records
            )
        except (ValueError, TypeError):
            pass

    logger.info(
        "Starting CSV results stream: job_id=%s, total_records=%s",
        job_id,
        total_records if total_records is not None else "unknown"
    )

    # Process CSV in batches
    # Use iter_lines with decode_unicode for text processing
    lines = response.iter_lines(decode_unicode=True)

    # Create CSV DictReader to parse headers and rows
    try:
        reader = csv.DictReader(lines)
    except Exception as e:
        logger.error(
            "Failed to create CSV reader: job_id=%s, error=%s",
            job_id,
            str(e)
        )
        raise SalesforceAPIError(
            message=f"Failed to parse CSV results: {str(e)}",
            status_code=status_code,
            response_body={"job_id": job_id, "error": str(e)}
        )

    # Yield records in batches
    batch = []
    total_processed = 0
    row_num = 0

    for row in reader:
        row_num += 1

        # Validate row
        if not row:
            logger.warning(
                "Skipping empty row: job_id=%s, row_num=%d",
                job_id,
                row_num
            )
            continue

        try:
            # Add row to current batch
            batch.append(row)
            total_processed += 1

            # Yield batch when full
            if len(batch) >= batch_size:
                logger.info(
                    "Retrieved %d/%s records (batch of %d)",
                    total_processed,
                    total_records if total_records is not None else "?",
                    len(batch)
                )
                yield batch
                batch = []

        except Exception as e:
            logger.warning(
                "Malformed CSV row skipped: job_id=%s, row_num=%d, error=%s",
                job_id,
                row_num,
                str(e)
            )
            continue

    # Yield final partial batch if any
    if batch:
        logger.info(
            "Retrieved %d/%s records (final batch of %d)",
            total_processed,
            total_records if total_records is not None else "?",
            len(batch)
        )
        yield batch

    logger.info(
        "Bulk API 2.0 results download complete: job_id=%s, total_records=%d",
        job_id,
        total_processed
    )
