"""Attachment to ContentDocument migration orchestration.

Executes hour-by-hour batch migrations using Anonymous Apex to convert Salesforce
Attachments to ContentDocument/ContentVersion records. Supports tiered batch sizing
based on attachment body length and cursor-based resumption on failure.

Example:
    >>> from datetime import datetime, timezone
    >>> from pathlib import Path
    >>> from sf_utils.migration import run_migration
    >>>
    >>> result = run_migration(
    ...     start_date=datetime(2023, 6, 1, tzinfo=timezone.utc),
    ...     config_path=Path("migration.properties")
    ... )
    >>> print(f"Processed {result.total_hours_processed} hours")
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from simple_salesforce import Salesforce

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, _sanitize_value
from sf_utils.tooling import execute_anonymous, poll_async_job

logger = logging.getLogger(__name__)

__all__ = [
    "run_migration",
    "load_migration_config",
    "build_apex_code",
    "save_cursor",
    "load_cursor",
    "MigrationConfig",
    "MigrationResult",
    "TierConfig",
]

DEFAULT_CURSOR_PATH = Path(".attachment_migration_cursor.json")

# Whitelist of allowed batch class names for security (prevents Apex injection)
ALLOWED_BATCH_CLASSES = frozenset({
    "AttachmentToFilesConversionBatch",
    "SingleAttachmentToFilesConversionBatch",
})


@dataclass
class TierConfig:
    """Configuration for a single migration tier.

    Attributes:
        name: Tier identifier (e.g., "tier1").
        min_size: Minimum BodyLength in bytes (inclusive). None means no lower bound.
        max_size: Maximum BodyLength in bytes (exclusive). None means no upper bound.
        batch_size: Number of records per batch execution.
        batch_class: Name of the Apex batch class to execute.
    """

    name: str
    min_size: Optional[int]
    max_size: Optional[int]
    batch_size: int
    batch_class: str

    def __post_init__(self) -> None:
        """Validate tier configuration."""
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if not self.batch_class:
            raise ValueError("batch_class must be a non-empty string")

        # Security: Validate batch_class is in whitelist
        if self.batch_class not in ALLOWED_BATCH_CLASSES:
            raise ValueError(
                f"batch_class must be one of {sorted(ALLOWED_BATCH_CLASSES)}, "
                f"got '{self.batch_class}'"
            )

        # Security: Validate batch_class contains only alphanumeric/underscore
        if not re.match(r"^[A-Za-z0-9_]+$", self.batch_class):
            raise ValueError(
                f"batch_class contains invalid characters: {self.batch_class}"
            )

        # Validate size bounds
        if self.min_size is not None and self.min_size < 0:
            raise ValueError(f"min_size must be >= 0, got {self.min_size}")
        if self.max_size is not None and self.max_size < 0:
            raise ValueError(f"max_size must be >= 0, got {self.max_size}")
        if (
            self.min_size is not None
            and self.max_size is not None
            and self.min_size >= self.max_size
        ):
            raise ValueError(
                f"min_size ({self.min_size}) must be < max_size ({self.max_size})"
            )

        logger.debug(
            "TierConfig: %s min=%s max=%s batch=%d class=%s",
            self.name,
            self.min_size,
            self.max_size,
            self.batch_size,
            self.batch_class,
        )


@dataclass
class MigrationConfig:
    """Configuration for the attachment migration process.

    Attributes:
        tiers: List of TierConfig objects defining batch processing tiers.
        poll_interval_seconds: Seconds between AsyncApexJob status polls.
    """

    tiers: List[TierConfig]
    poll_interval_seconds: int = 10

    def __post_init__(self) -> None:
        """Validate migration configuration."""
        if not self.tiers:
            raise ValueError("At least one tier must be configured")
        if self.poll_interval_seconds < 1:
            raise ValueError(
                f"poll_interval_seconds must be >= 1, got {self.poll_interval_seconds}"
            )
        logger.debug(
            "MigrationConfig: %d tiers, poll_interval=%ds",
            len(self.tiers),
            self.poll_interval_seconds,
        )


@dataclass
class MigrationResult:
    """Result of a migration run.

    Attributes:
        total_hours_processed: Number of hour windows processed.
        total_batches_executed: Total batch jobs executed across all tiers.
        start_time: Migration start timestamp (UTC).
        end_time: Migration end timestamp (UTC).
        status: Final status ('completed', 'failed', 'interrupted').
        error_message: Error details if status is 'failed' or 'interrupted'.
    """

    total_hours_processed: int
    total_batches_executed: int
    start_time: datetime
    end_time: datetime
    status: str  # 'completed', 'failed', 'interrupted'
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate result status."""
        valid_statuses = {"completed", "failed", "interrupted"}
        if self.status not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}, got {self.status}")


def load_migration_config(config_path: Path) -> MigrationConfig:
    """Load migration configuration from a properties file.

    Args:
        config_path: Path to the migration.properties file.

    Returns:
        MigrationConfig with tier configurations and polling settings.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If required properties are missing or invalid.

    Example:
        >>> config = load_migration_config(Path("migration.properties"))
        >>> print(f"Tiers: {len(config.tiers)}")
    """
    from configparser import ConfigParser

    logger.info("Loading migration config from: %s", config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Migration config file not found: {config_path}")

    parser = ConfigParser()
    with open(config_path, "r", encoding="utf-8") as f:
        content = "[migration]\n" + f.read()
    parser.read_string(content)

    config_dict = dict(parser["migration"])
    logger.debug("Parsed config: %d entries", len(config_dict))

    poll_interval = int(config_dict.get("poll_interval_seconds", "10"))

    # Discover tier names
    tier_names = {
        k.split(".")[0] for k in config_dict if "." in k and k.startswith("tier")
    }
    logger.debug("Discovered tiers: %s", sorted(tier_names))

    tiers: List[TierConfig] = []
    for tier_name in sorted(tier_names):
        prefix = f"{tier_name}."
        min_size_str = config_dict.get(f"{prefix}min_size")
        max_size_str = config_dict.get(f"{prefix}max_size")
        batch_size_str = config_dict.get(f"{prefix}batch_size")
        batch_class = config_dict.get(f"{prefix}batch_class")

        if batch_size_str is None:
            raise ValueError(f"Missing required property: {prefix}batch_size")
        if batch_class is None:
            raise ValueError(f"Missing required property: {prefix}batch_class")

        # Handle max_size=-1 as "no upper bound"
        max_size_val = int(max_size_str) if max_size_str else None
        if max_size_val is not None and max_size_val < 0:
            max_size_val = None

        tiers.append(
            TierConfig(
                name=tier_name,
                min_size=int(min_size_str) if min_size_str else None,
                max_size=max_size_val,
                batch_size=int(batch_size_str),
                batch_class=batch_class.strip(),
            )
        )

    config = MigrationConfig(tiers=tiers, poll_interval_seconds=poll_interval)
    logger.info("Loaded migration config with %d tiers", len(tiers))
    return config


def build_apex_code(tier: TierConfig, hour_start: datetime, hour_end: datetime) -> str:
    """Build Anonymous Apex code to execute a batch for one tier and hour window.

    Args:
        tier: Tier configuration with size bounds and batch settings.
        hour_start: Start of the hour window (UTC, timezone-aware).
        hour_end: End of the hour window (UTC, timezone-aware).

    Returns:
        Anonymous Apex code string ready for execution.

    Raises:
        ValueError: If datetime objects are not timezone-aware.
    """
    if hour_start.tzinfo is None or hour_end.tzinfo is None:
        raise ValueError("hour_start and hour_end must be timezone-aware")

    start_iso = hour_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = hour_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Security: Validate ISO 8601 format to prevent SOQL injection
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    if not re.match(iso_pattern, start_iso) or not re.match(iso_pattern, end_iso):
        raise ValueError(f"Invalid ISO 8601 format: {start_iso}, {end_iso}")

    logger.debug("Building Apex for tier=%s hour=%s to %s", tier.name, start_iso, end_iso)

    # Build BodyLength filter
    size_conditions = []
    if tier.min_size is not None:
        size_conditions.append(f"BodyLength >= {tier.min_size}")
    if tier.max_size is not None:
        size_conditions.append(f"BodyLength < {tier.max_size}")
    size_filter = " AND ".join(size_conditions) if size_conditions else "BodyLength >= 0"

    query = (
        f"SELECT Id, Name, Body, ParentId, Description, OwnerId, CreatedDate, "
        f"CreatedById, LastModifiedById, LastModifiedDate FROM Attachment "
        f"WHERE {size_filter} AND Parent.Id IN (SELECT Id FROM Progress_Note__c "
        f"WHERE CreatedDate >= {start_iso} AND CreatedDate < {end_iso})"
    )

    apex_code = (
        f"String query = '{query}';\n"
        f"Id batchId = Database.executeBatch(new {tier.batch_class}(query, 'Progress_Note'), {tier.batch_size});\n"
        f"System.debug('Started batch: ' + batchId);"
    )
    logger.debug("Generated Apex code (%d chars)", len(apex_code))
    return apex_code


def save_cursor(cursor_path: Path, cursor_data: Dict[str, Any]) -> None:
    """Persist cursor state to JSON file for recovery.

    Args:
        cursor_path: Path to write the cursor JSON file.
        cursor_data: Dictionary with cursor state (last_completed_hour, etc.).
    """
    logger.info("Saving cursor to: %s", cursor_path)
    with open(cursor_path, "w", encoding="utf-8") as f:
        json.dump(cursor_data, f, indent=2, default=str)


def load_cursor(cursor_path: Path) -> Dict[str, Any]:
    """Load cursor state from JSON file for resumption.

    Args:
        cursor_path: Path to the cursor JSON file.

    Returns:
        Dictionary with cursor state, or empty dict if file doesn't exist.
    """
    if not cursor_path.exists():
        logger.info("No existing cursor file found at: %s", cursor_path)
        return {}
    with open(cursor_path, "r", encoding="utf-8") as f:
        cursor_data = json.load(f)
    logger.info(
        "Loaded cursor: last_completed=%s total_hours=%d",
        cursor_data.get("last_completed_hour"),
        cursor_data.get("total_hours_processed", 0),
    )
    return cursor_data


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
            timeout = poll_interval * 360  # ~1 hour default
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
        start_date: Starting datetime (UTC, timezone-aware).
        config_path: Path to migration.properties configuration file.
        verbose: Enable verbose (DEBUG) logging.
        cursor_path: Path to cursor file. Defaults to .attachment_migration_cursor.json.
        resume: If True, resume from last saved cursor position.
        client: Authenticated Salesforce client. Creates one if not provided.

    Returns:
        MigrationResult with execution statistics.

    Raises:
        ValueError: If start_date is not timezone-aware.
        FileNotFoundError: If config_path doesn't exist.

    Example:
        >>> from datetime import datetime, timezone
        >>> result = run_migration(
        ...     start_date=datetime(2023, 6, 1, tzinfo=timezone.utc),
        ...     config_path=Path("migration.properties"),
        ...     verbose=True
        ... )
        >>> print(f"Status: {result.status}")
    """
    if start_date.tzinfo is None:
        raise ValueError("start_date must be timezone-aware (e.g., use timezone.utc)")

    if verbose:
        logging.getLogger(__name__).setLevel(logging.DEBUG)
        logging.getLogger("sf_utils.tooling").setLevel(logging.DEBUG)

    logger.info("Starting attachment migration from %s", start_date.isoformat())
    cursor_path = cursor_path or DEFAULT_CURSOR_PATH
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
    logger.info("Processing %d hour windows", total_hours)

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

                apex_code = build_apex_code(tier, hour_start, hour_end)

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
                        f"  sf-sync migrate-attachments --resume --config {config_path}"
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
            "Migration completed successfully. Hours: %d, Batches: %d",
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
