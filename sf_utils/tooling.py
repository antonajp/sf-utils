"""Salesforce Tooling API operations.

This module provides functions for executing Anonymous Apex code and polling
asynchronous Apex jobs via the Salesforce Tooling API.

Functions:
    execute_anonymous: Execute Anonymous Apex code
    poll_async_job: Poll AsyncApexJob until completion

Example:
    >>> from sf_utils.tooling import execute_anonymous, poll_async_job
    >>>
    >>> # Execute Anonymous Apex
    >>> result = execute_anonymous(client, "System.debug('Hello World');")
    >>> if result['success']:
    ...     print("Apex executed successfully")
    >>>
    >>> # Poll an async job
    >>> job_result = poll_async_job(client, job_id)
    >>> print(f"Job status: {job_result['Status']}")
"""

import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import requests
from simple_salesforce import Salesforce

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, _sanitize_value

logger = logging.getLogger(__name__)

__all__ = [
    "execute_anonymous",
    "poll_async_job",
]


def execute_anonymous(
    apex_code: str,
    client: Optional[Salesforce] = None,
) -> Dict[str, Any]:
    """Execute Anonymous Apex code via the Salesforce Tooling API.

    Executes the provided Apex code synchronously using the Tooling API's
    executeAnonymous endpoint. The code is compiled and executed on the
    Salesforce server, with results returned immediately.

    Args:
        client: Authenticated Salesforce client. Creates one from environment
            if not provided.
        apex_code: Apex code to execute. Must be valid Apex syntax.

    Returns:
        Dictionary containing execution results:
        - compiled (bool): Whether the code compiled successfully
        - compileProblem (str|None): Compilation error message if any
        - success (bool): Whether execution completed without exceptions
        - exceptionMessage (str|None): Runtime exception message if any
        - exceptionStackTrace (str|None): Runtime exception stack trace if any
        - line (int|None): Line number of error if any
        - column (int|None): Column number of error if any

    Raises:
        ValueError: If apex_code is empty.
        SalesforceAuthError: If authentication fails (401/403).
        SalesforceAPIError: If the API request fails.

    Example:
        >>> from sf_utils.tooling import execute_anonymous
        >>> from sf_utils.client import get_client
        >>>
        >>> client = get_client()
        >>>
        >>> # Simple debug statement
        >>> result = execute_anonymous(client, "System.debug('Hello World');")
        >>> print(f"Success: {result['success']}")
        Success: True
        >>>
        >>> # Handle compilation errors
        >>> result = execute_anonymous(client, "invalid apex code")
        >>> if not result['compiled']:
        ...     print(f"Compile error: {result['compileProblem']}")
        >>>
        >>> # Handle runtime errors
        >>> result = execute_anonymous(client, "Integer x = 1/0;")
        >>> if not result['success']:
        ...     print(f"Runtime error: {result['exceptionMessage']}")

    Notes:
        - Maximum Apex code size is 65,535 characters
        - Code runs in system context with full permissions
        - Debug logs are written to the server, not returned in response
        - Rate limiting (429) is handled with automatic retry
    """
    # Validate inputs
    if not apex_code or not apex_code.strip():
        raise ValueError("apex_code cannot be empty")

    # Initialize client if not provided
    if client is None:
        logger.debug("Creating Salesforce client from environment")
        client = get_client()

    logger.debug(
        "Executing Anonymous Apex: code_length=%d, code_preview=%s",
        len(apex_code),
        apex_code[:100] + "..." if len(apex_code) > 100 else apex_code
    )

    # Construct Tooling API endpoint
    # Format: https://{instance}/services/data/v{version}/tooling/executeAnonymous?anonymousBody={encoded_apex}
    version = client.sf_version
    encoded_apex = quote(apex_code, safe='')
    url = f"https://{client.sf_instance}/services/data/v{version}/tooling/executeAnonymous"

    # Prepare headers
    headers = {
        "Authorization": f"Bearer {client.session_id}",
        "Accept": "application/json"
    }

    logger.debug("GET %s?anonymousBody=<apex_code>", url)

    # Handle rate limiting with retry
    max_retries = 3
    retry_count = 0
    base_wait = 5

    while True:
        try:
            response = requests.get(
                url,
                params={"anonymousBody": apex_code},
                headers=headers,
                proxies=client.proxies if hasattr(client, 'proxies') else None,
                timeout=120  # 2 minutes for Apex execution
            )
        except requests.exceptions.RequestException as e:
            logger.error("HTTP request failed during executeAnonymous: error=%s", str(e))
            raise SalesforceAPIError(
                message=f"Failed to execute Anonymous Apex: {str(e)}",
                status_code=None,
                response_body={"error": str(e)}
            )

        status_code = response.status_code

        # Handle rate limiting (429)
        if status_code == 429:
            retry_count += 1
            if retry_count > max_retries:
                logger.error(
                    "Rate limit exceeded after %d retries for executeAnonymous",
                    max_retries
                )
                raise SalesforceAPIError(
                    message="Rate limit exceeded for executeAnonymous after max retries",
                    status_code=429,
                    response_body={"error": "Rate limit exceeded"}
                )

            # Get Retry-After header or use exponential backoff
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    wait_time = int(retry_after)
                except ValueError:
                    wait_time = base_wait * (2 ** (retry_count - 1))
            else:
                wait_time = base_wait * (2 ** (retry_count - 1))

            logger.warning(
                "Rate limited (429), retry %d/%d, waiting %ds",
                retry_count,
                max_retries,
                wait_time
            )
            time.sleep(wait_time)
            continue

        # Parse response
        try:
            result = response.json()
            # Salesforce sometimes returns errors as a list
            if isinstance(result, list) and result:
                result = result[0]
        except Exception:
            result = {"error": response.text}

        logger.debug(
            "executeAnonymous response: status=%d, body=%s",
            status_code,
            str(_sanitize_value(result))[:200]
        )

        # Check for errors
        if status_code >= 400:
            # Handle authentication errors
            if status_code in (401, 403):
                error_message = result.get('message', 'Authentication failed')
                logger.error(
                    "Tooling API authentication failed: status=%d, message=%s",
                    status_code,
                    error_message
                )
                raise SalesforceAuthError(
                    message=error_message,
                    status_code=status_code,
                    response_body=result
                )

            # Handle other errors
            error_message = result.get('message', result.get('exceptionMessage', 'Unknown error'))
            logger.error(
                "executeAnonymous failed: status=%d, message=%s",
                status_code,
                error_message
            )
            raise SalesforceAPIError(
                message=f"Failed to execute Anonymous Apex: {error_message}",
                status_code=status_code,
                response_body=result
            )

        # Log execution result
        compiled = result.get('compiled', False)
        success = result.get('success', False)

        if not compiled:
            logger.warning(
                "Apex compilation failed: problem=%s, line=%s, column=%s",
                result.get('compileProblem'),
                result.get('line'),
                result.get('column')
            )
        elif not success:
            logger.warning(
                "Apex execution failed: exception=%s",
                result.get('exceptionMessage')
            )
        else:
            logger.info("Anonymous Apex executed successfully")

        return result


def poll_async_job(
    job_id: str,
    *,
    poll_interval: int = 10,
    timeout: int = 3600,
    client: Optional[Salesforce] = None,
) -> Dict[str, Any]:
    """Poll an AsyncApexJob until it reaches a terminal state.

    Queries the AsyncApexJob object at regular intervals until the job
    reaches a completed, failed, or aborted state. Used to monitor
    batch Apex jobs, scheduled jobs, and other asynchronous operations.

    Terminal states:
    - Completed: Job finished successfully
    - Failed: Job encountered an error
    - Aborted: Job was cancelled

    Non-terminal states (continue polling):
    - Queued: Job is waiting to run
    - Preparing: Job is preparing resources
    - Processing: Job is actively running
    - Holding: Job is paused

    Args:
        client: Authenticated Salesforce client. Creates one from environment
            if not provided.
        job_id: AsyncApexJob Id to poll (18-character Salesforce ID).
        poll_interval: Seconds between polls. Defaults to 10.
        timeout: Maximum seconds to wait for completion. Defaults to 3600 (1 hour).

    Returns:
        Dictionary containing final job state:
        - Id (str): Job ID
        - Status (str): Final status (Completed, Failed, Aborted)
        - NumberOfErrors (int): Number of batch errors
        - JobItemsProcessed (int): Number of items processed
        - TotalJobItems (int): Total items in job
        - ExtendedStatus (str|None): Additional status information

    Raises:
        ValueError: If job_id is empty or poll_interval/timeout invalid.
        SalesforceAuthError: If authentication fails.
        SalesforceAPIError: If job fails, is aborted, polling fails, or timeout exceeded.

    Example:
        >>> from sf_utils.tooling import poll_async_job
        >>> from sf_utils.client import get_client
        >>>
        >>> client = get_client()
        >>>
        >>> # Poll a batch job
        >>> job = poll_async_job(client, "7071x00000AbCdEfG", poll_interval=5, timeout=1800)
        >>> print(f"Status: {job['Status']}, Processed: {job['JobItemsProcessed']}/{job['TotalJobItems']}")
        Status: Completed, Processed: 100/100
        >>>
        >>> # Handle job errors
        >>> job = poll_async_job(client, job_id)
        >>> if job['NumberOfErrors'] > 0:
        ...     print(f"Job had {job['NumberOfErrors']} errors")
        ...     print(f"Extended status: {job['ExtendedStatus']}")

    Notes:
        - Uses monotonic time for reliable timeout tracking
        - Progress is logged at INFO level each poll
        - Rate limiting (429) is handled with automatic retry
        - Job query uses SOQL against the AsyncApexJob object
    """
    # Validate inputs
    if not job_id or not job_id.strip():
        raise ValueError("job_id cannot be empty")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    # Initialize client if not provided
    if client is None:
        logger.debug("Creating Salesforce client from environment")
        client = get_client()

    logger.debug(
        "Polling AsyncApexJob: job_id=%s, poll_interval=%ds, timeout=%ds",
        job_id,
        poll_interval,
        timeout
    )

    # Terminal states that exit the polling loop
    TERMINAL_STATES = {"Completed", "Failed", "Aborted"}

    # SOQL query for AsyncApexJob
    soql = (
        f"SELECT Id, Status, NumberOfErrors, JobItemsProcessed, TotalJobItems, ExtendedStatus "
        f"FROM AsyncApexJob WHERE Id = '{job_id}'"
    )

    # Construct query endpoint
    version = client.sf_version
    url = f"https://{client.sf_instance}/services/data/v{version}/query"

    # Prepare headers
    headers = {
        "Authorization": f"Bearer {client.session_id}",
        "Accept": "application/json"
    }

    # Polling loop
    start_time = time.monotonic()
    poll_count = 0

    while True:
        # Check timeout
        elapsed = time.monotonic() - start_time
        if elapsed >= timeout:
            logger.error(
                "AsyncApexJob polling timeout: job_id=%s, elapsed=%.1fs, timeout=%ds, polls=%d",
                job_id,
                elapsed,
                timeout,
                poll_count
            )
            raise SalesforceAPIError(
                message=f"AsyncApexJob {job_id} did not complete within {timeout}s (elapsed: {elapsed:.1f}s, polls: {poll_count})",
                status_code=None,
                response_body={"job_id": job_id, "elapsed": elapsed, "timeout": timeout}
            )

        poll_count += 1

        logger.debug(
            "Polling job status: job_id=%s, poll=%d, elapsed=%.1fs",
            job_id,
            poll_count,
            elapsed
        )

        try:
            response = requests.get(
                url,
                params={"q": soql},
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
            # Sleep and retry on network errors
            time.sleep(poll_interval)
            continue

        status_code = response.status_code

        # Handle rate limiting (429)
        if status_code == 429:
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    wait_time = int(retry_after)
                except ValueError:
                    wait_time = poll_interval * 2
            else:
                wait_time = poll_interval * 2

            logger.warning(
                "Rate limited (429) during job polling, waiting %ds",
                wait_time
            )
            time.sleep(wait_time)
            continue

        # Parse response
        try:
            query_result = response.json()
            if isinstance(query_result, list) and query_result:
                query_result = query_result[0]
        except Exception:
            query_result = {"error": response.text}

        logger.debug(
            "Job status response: job_id=%s, status=%d, body=%s",
            job_id,
            status_code,
            str(_sanitize_value(query_result))[:200]
        )

        # Check for errors
        if status_code >= 400:
            # Handle authentication errors
            if status_code in (401, 403):
                error_message = query_result.get('message', 'Authentication failed')
                logger.error(
                    "Tooling API authentication failed during polling: job_id=%s, status=%d, message=%s",
                    job_id,
                    status_code,
                    error_message
                )
                raise SalesforceAuthError(
                    message=error_message,
                    status_code=status_code,
                    response_body=query_result
                )

            # Handle other errors
            error_message = query_result.get('message', query_result.get('exceptionMessage', 'Unknown error'))
            logger.error(
                "AsyncApexJob query failed: job_id=%s, status=%d, message=%s",
                job_id,
                status_code,
                error_message
            )
            raise SalesforceAPIError(
                message=f"Failed to query AsyncApexJob: {error_message}",
                status_code=status_code,
                response_body=query_result
            )

        # Extract job record from query results
        records = query_result.get('records', [])
        if not records:
            logger.error("AsyncApexJob not found: job_id=%s", job_id)
            raise SalesforceAPIError(
                message=f"AsyncApexJob not found: {job_id}",
                status_code=status_code,
                response_body=query_result
            )

        job_info = records[0]

        # Remove Salesforce metadata
        job_info = {k: v for k, v in job_info.items() if not k.startswith('attributes')}

        status = job_info.get('Status')
        job_items_processed = job_info.get('JobItemsProcessed', 0)
        total_job_items = job_info.get('TotalJobItems', 0)
        number_of_errors = job_info.get('NumberOfErrors', 0)

        logger.info(
            "AsyncApexJob status: job_id=%s, status=%s, processed=%s/%s, errors=%s, poll=%d, elapsed=%.1fs",
            job_id,
            status,
            job_items_processed,
            total_job_items,
            number_of_errors,
            poll_count,
            elapsed
        )

        # Check for terminal states
        if status in TERMINAL_STATES:
            if status == "Completed":
                logger.info(
                    "AsyncApexJob completed: job_id=%s, processed=%s/%s, errors=%s, polls=%d, elapsed=%.1fs",
                    job_id,
                    job_items_processed,
                    total_job_items,
                    number_of_errors,
                    poll_count,
                    elapsed
                )
                return job_info
            else:
                # Job Failed or Aborted
                extended_status = job_info.get('ExtendedStatus', '')
                logger.error(
                    "AsyncApexJob %s: job_id=%s, errors=%s, extended_status=%s, polls=%d, elapsed=%.1fs",
                    status,
                    job_id,
                    number_of_errors,
                    extended_status,
                    poll_count,
                    elapsed
                )
                raise SalesforceAPIError(
                    message=f"AsyncApexJob {job_id} {status}: {extended_status or 'No details available'}",
                    status_code=status_code,
                    response_body=job_info
                )

        # Non-terminal state - sleep and continue polling
        logger.debug(
            "Job in non-terminal state: job_id=%s, status=%s, sleeping %ds",
            job_id,
            status,
            poll_interval
        )
        time.sleep(poll_interval)
