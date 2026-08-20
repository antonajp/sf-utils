"""Migration orchestration and execution.

Coordinates hour-by-hour attachment migration with cursor-based resumption,
tiered batch processing, and comprehensive error handling.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from simple_salesforce import Salesforce

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, _sanitize_value
from sf_utils.tooling import execute_anonymous, poll_async_job

from .apex_builder import build_apex_code, validate_sobject_name
from .config import MigrationConfig, MigrationResult, load_migration_config
from .cursor import get_default_cursor_path, load_cursor, save_cursor

logger = logging.getLogger(__name__)


def _execute_and_poll_batch(
    client: Salesforce,
    apex_code: str,
    poll_interval: int,
) -> Dict[str, Any]:
    """Execute Anonymous Apex and poll the resulting batch job.

    Uses the tooling module's execute_anonymous() and poll_async_job() functions.

    Args:
        client: Authenticated Salesforce client.
        apex_code: Anonymous Apex code to execute.
        poll_interval: Seconds between job status polls.

    Returns:
        Final job status dictionary.

    Raises:
        SalesforceAPIError: If Apex execution or batch job fails.
    """
    # Execute Anonymous Apex
    logger.debug("Executing Anonymous Apex (%d chars)", len(apex_code))
    result = execute_anonymous(apex_code=apex_code, client=client)
    logger.debug("Apex execution result: %s", _sanitize_value(result))

    if not result.get("success", False):
        error = (
            result.get("compileProblem")
            or result.get("exceptionMessage")
            or "Unknown error"
        )
        logger.error("Apex execution failed: %s", error)
        if result.get("exceptionStackTrace"):
            logger.error("Stack trace: %s", result["exceptionStackTrace"])
        raise SalesforceAPIError(
            message=f"Anonymous Apex failed: {error}",
            status_code=400,
            response_body=result,
        )

    # Query for the most recent batch job
    try:
        job_result = client.query(
            "SELECT Id, Status, CreatedDate, ApexClass.Name FROM AsyncApexJob "
            "WHERE JobType = 'BatchApex' ORDER BY CreatedDate DESC LIMIT 1"
        )
        if job_result.get("records"):
            batch_id = job_result["records"][0]["Id"]
            logger.info("Found batch job: %s", batch_id)

            # Poll until completion using tooling module
            # Default batch timeout of 1 hour (independent of poll_interval)
            DEFAULT_BATCH_TIMEOUT_SECONDS = 3600
            timeout = DEFAULT_BATCH_TIMEOUT_SECONDS
            job_info = poll_async_job(
                job_id=batch_id,
                poll_interval=poll_interval,
                timeout=timeout,
                client=client,
            )

            # Check for errors
            if job_info.get("NumberOfErrors", 0) > 0:
                raise SalesforceAPIError(
                    message=f"Batch job had errors: {job_info.get('ExtendedStatus', '')}",
                    status_code=500,
                    response_body=job_info,
                )
            return job_info
    except SalesforceAPIError:
        raise
    except Exception as e:
        logger.warning("Could not query/poll AsyncApexJob: %s", str(e))

    return result


def run_migration(
    sobject_type: str,
    start_date: datetime,
    config_path: Path,
    *,
    verbose: bool = False,
    cursor_path: Optional[Path] = None,
    resume: bool = False,
    client: Optional[Salesforce] = None,
) -> MigrationResult:
    """Run hour-by-hour attachment migration from start_date to now.

    Processes attachments in hourly windows, executing tiered batch jobs
    based on BodyLength. Supports cursor-based resumption on failure.

    Args:
        sobject_type: The sObject API name (e.g., Progress_Note__c, Account).
        start_date: Starting datetime (UTC, timezone-aware).
        config_path: Path to migration.properties configuration file.
        verbose: Enable verbose (DEBUG) logging.
        cursor_path: Path to cursor file. Defaults to .{sobject_type.lower()}_migration_cursor.json.
        resume: If True, resume from last saved cursor position.
        client: Authenticated Salesforce client. Creates one if not provided.

    Returns:
        MigrationResult with execution statistics.

    Raises:
        ValueError: If start_date is not timezone-aware or sObject name is invalid.
        FileNotFoundError: If config_path doesn't exist.

    Example:
        >>> from datetime import datetime, timezone
        >>> result = run_migration(
        ...     sobject_type="Progress_Note__c",
        ...     start_date=datetime(2023, 6, 1, tzinfo=timezone.utc),
        ...     config_path=Path("migration.properties"),
        ...     verbose=True
        ... )
        >>> print(f"Status: {result.status}")
    """
    # Security: Validate sObject name first
    validate_sobject_name(sobject_type)

    if start_date.tzinfo is None:
        raise ValueError("start_date must be timezone-aware (e.g., use timezone.utc)")

    if verbose:
        logging.getLogger(__name__).setLevel(logging.DEBUG)
        logging.getLogger("sf_utils.tooling").setLevel(logging.DEBUG)

    logger.info(
        "Starting attachment migration for %s from %s",
        sobject_type,
        start_date.isoformat(),
    )

    # Default cursor path is sObject-specific
    cursor_path = cursor_path or get_default_cursor_path(sobject_type)
    config = load_migration_config(config_path)

    if client is None:
        logger.debug("Creating Salesforce client from environment")
        client = get_client()

    # Initialize state
    total_hours_processed = 0
    total_batches_executed = 0
    start_time = datetime.now(timezone.utc)

    # Resume from cursor if requested
    if resume and cursor_path.exists():
        cursor = load_cursor(cursor_path)
        total_hours_processed = cursor.get("total_hours_processed", 0)
        total_batches_executed = cursor.get("total_batches_executed", 0)
        if next_hour_str := cursor.get("next_hour"):
            start_date = datetime.fromisoformat(next_hour_str.replace("Z", "+00:00"))
            logger.info("Resuming from cursor: %s", start_date.isoformat())

    # Generate hour windows
    end_date = datetime.now(timezone.utc)
    current_hour = start_date.replace(minute=0, second=0, microsecond=0)
    hour_windows: List[tuple] = []
    while current_hour < end_date:
        hour_windows.append((current_hour, current_hour + timedelta(hours=1)))
        current_hour += timedelta(hours=1)

    total_hours = len(hour_windows)
    logger.info("Processing %d hour windows for %s", total_hours, sobject_type)

    try:
        for hour_idx, (hour_start, hour_end) in enumerate(hour_windows):
            logger.info(
                "Processing hour %d/%d: %s to %s",
                hour_idx + 1,
                total_hours,
                hour_start.isoformat(),
                hour_end.isoformat(),
            )

            for tier in config.tiers:
                logger.info(
                    "Executing tier %s (size: %s-%s, batch_size=%d)",
                    tier.name,
                    tier.min_size or "0",
                    tier.max_size or "unlimited",
                    tier.batch_size,
                )

                apex_code = build_apex_code(sobject_type, tier, hour_start, hour_end)

                try:
                    _execute_and_poll_batch(
                        client, apex_code, config.poll_interval_seconds
                    )
                    total_batches_executed += 1
                    logger.info(
                        "Tier %s completed for hour %s", tier.name, hour_start.isoformat()
                    )

                except SalesforceAPIError as e:
                    logger.error(
                        "Migration failed at hour %s tier %s: %s",
                        hour_start.isoformat(),
                        tier.name,
                        str(e),
                    )
                    cursor = {
                        "last_completed_hour": (
                            (hour_start - timedelta(hours=1)).isoformat() + "Z"
                            if hour_idx > 0
                            else None
                        ),
                        "next_hour": hour_start.isoformat() + "Z",
                        "total_hours_processed": total_hours_processed,
                        "total_batches_executed": total_batches_executed,
                        "failed_tier": tier.name,
                        "error": str(e),
                    }
                    save_cursor(cursor_path, cursor)
                    recovery_cmd = (
                        f"To resume migration, run:\n"
                        f"  sf-sync migrate-attachments {sobject_type} --resume --config {config_path}"
                    )
                    logger.error(recovery_cmd)
                    print(f"\n{recovery_cmd}\n")
                    return MigrationResult(
                        total_hours_processed=total_hours_processed,
                        total_batches_executed=total_batches_executed,
                        start_time=start_time,
                        end_time=datetime.now(timezone.utc),
                        status="failed",
                        error_message=str(e),
                    )

            total_hours_processed += 1
            cursor = {
                "last_completed_hour": hour_start.isoformat() + "Z",
                "next_hour": hour_end.isoformat() + "Z",
                "total_hours_processed": total_hours_processed,
                "total_batches_executed": total_batches_executed,
            }
            save_cursor(cursor_path, cursor)
            logger.info(
                "Hour %d/%d completed. Total hours: %d, Total batches: %d",
                hour_idx + 1,
                total_hours,
                total_hours_processed,
                total_batches_executed,
            )

        logger.info(
            "Migration completed successfully for %s. Hours: %d, Batches: %d",
            sobject_type,
            total_hours_processed,
            total_batches_executed,
        )
        return MigrationResult(
            total_hours_processed=total_hours_processed,
            total_batches_executed=total_batches_executed,
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            status="completed",
        )

    except KeyboardInterrupt:
        logger.warning("Migration interrupted by user")
        cursor = {
            "last_completed_hour": (
                hour_windows[total_hours_processed - 1][0].isoformat() + "Z"
                if total_hours_processed > 0
                else None
            ),
            "next_hour": (
                hour_windows[total_hours_processed][0].isoformat() + "Z"
                if total_hours_processed < len(hour_windows)
                else None
            ),
            "total_hours_processed": total_hours_processed,
            "total_batches_executed": total_batches_executed,
            "interrupted": True,
        }
        save_cursor(cursor_path, cursor)
        return MigrationResult(
            total_hours_processed=total_hours_processed,
            total_batches_executed=total_batches_executed,
            start_time=start_time,
            end_time=datetime.now(timezone.utc),
            status="interrupted",
            error_message="Migration interrupted by user (Ctrl+C)",
        )
