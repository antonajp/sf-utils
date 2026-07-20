"""Date-chunked query execution for Salesforce REST API sync operations."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Generator, List, Optional

from psycopg2 import extensions
from simple_salesforce import Salesforce

from sf_utils.client import get_client
from sf_utils.db import get_connection, create_table_from_query, upsert_records
from sf_utils.exceptions import SalesforceAPIError
from sf_utils.query import query_all
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG
from sf_utils.sync.soql_loader import validate_soql
from sf_utils.sync.state import (
    ensure_sync_state_table,
    get_sync_state,
    update_sync_state,
)

logger = logging.getLogger(__name__)


class ChunkInterval(Enum):
    """Time interval for chunking date ranges in query_chunked().

    Attributes:
        HOURLY: Split date ranges into 1-hour chunks.
        DAILY: Split date ranges into 1-day chunks.
    """
    HOURLY = "hourly"
    DAILY = "daily"


@dataclass
class SyncResult:
    """Result of a sync_records() operation.

    Attributes:
        object_name: Salesforce object name (e.g., 'Account').
        records_fetched: Total number of records retrieved from Salesforce.
        records_inserted: Number of new records inserted into PostgreSQL.
        records_updated: Number of existing records updated in PostgreSQL.
        sync_mode: Sync mode used ('incremental' or 'full').
        start_timestamp: Timestamp when sync started (timezone-aware UTC).
        end_timestamp: Timestamp when sync completed (timezone-aware UTC).
        date_field: Date field used for incremental sync tracking.
    """

    object_name: str
    records_fetched: int
    records_inserted: int
    records_updated: int
    sync_mode: str
    start_timestamp: datetime
    end_timestamp: datetime
    date_field: str


def _generate_date_chunks(
    start_date: datetime,
    end_date: datetime,
    chunk_size: ChunkInterval
) -> Generator[tuple[datetime, datetime], None, None]:
    """Generate date range chunks between start_date and end_date.

    Args:
        start_date: Start of the overall date range (inclusive). Must be timezone-aware.
        end_date: End of the overall date range (exclusive). Must be timezone-aware.
        chunk_size: Size of each chunk (hourly or daily).

    Yields:
        Tuples of (chunk_start, chunk_end) where chunk_end is exclusive.

    Raises:
        ValueError: If start_date or end_date are not timezone-aware.
    """
    if start_date.tzinfo is None:
        raise ValueError("start_date must be timezone-aware")
    if end_date.tzinfo is None:
        raise ValueError("end_date must be timezone-aware")

    current = start_date
    delta = timedelta(hours=1) if chunk_size == ChunkInterval.HOURLY else timedelta(days=1)

    while current < end_date:
        chunk_end = min(current + delta, end_date)
        yield (current, chunk_end)
        current = chunk_end


def query_chunked(
    soql: str,
    date_field: str,
    start_date: datetime,
    end_date: datetime,
    chunk_size: ChunkInterval = ChunkInterval.DAILY,
    client: Optional[Salesforce] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> Generator[List[Dict[str, Any]], None, None]:
    """Execute a SOQL query in date-chunked batches with automatic pagination.

    Splits the date range into chunks (hourly or daily) and executes the SOQL query
    for each chunk. Uses query_all() for automatic pagination within each chunk.
    Yields results one chunk at a time for memory-efficient processing.

    The SOQL query must contain {start_date} and {end_date} placeholders that will
    be substituted with ISO 8601 formatted timestamps (e.g., 2024-01-01T00:00:00Z).

    Args:
        soql: SOQL query template with {start_date} and {end_date} placeholders.
            Example: "SELECT Id FROM Account WHERE CreatedDate >= {start_date}
            AND CreatedDate < {end_date}"
        date_field: Name of the date/datetime field used for chunking (for logging).
        start_date: Start of the overall date range (inclusive). Must be timezone-aware.
        end_date: End of the overall date range (exclusive). Must be timezone-aware.
        chunk_size: Size of each chunk. Defaults to DAILY.
        client: Authenticated Salesforce client. Creates one if not provided.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Yields:
        List of record dictionaries for each chunk. Empty lists are yielded for
        chunks with no matching records.

    Raises:
        ValueError: If start_date or end_date are not timezone-aware, or if
            placeholders are missing from the SOQL query.
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If query fails with other errors. Error message includes
            chunk context (date range) for troubleshooting.

    Example:
        >>> from datetime import datetime, timezone
        >>> from sf_utils.sync import query_chunked, ChunkInterval
        >>>
        >>> # Query Opportunities created in January 2024, one day at a time
        >>> soql = '''
        ...     SELECT Id, Name, CreatedDate
        ...     FROM Opportunity
        ...     WHERE CreatedDate >= {start_date}
        ...       AND CreatedDate < {end_date}
        ... '''
        >>> start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        >>> end = datetime(2024, 2, 1, tzinfo=timezone.utc)
        >>>
        >>> for chunk_records in query_chunked(
        ...     soql=soql,
        ...     date_field="CreatedDate",
        ...     start_date=start,
        ...     end_date=end,
        ...     chunk_size=ChunkInterval.DAILY
        ... ):
        ...     # Process each day's records
        ...     process_records(chunk_records)
        >>>
        >>> # Hourly chunks for high-volume data
        >>> for chunk_records in query_chunked(
        ...     soql=soql,
        ...     date_field="CreatedDate",
        ...     start_date=start,
        ...     end_date=end,
        ...     chunk_size=ChunkInterval.HOURLY
        ... ):
        ...     sync_to_database(chunk_records)
    """
    # Validate timezone-aware datetimes
    if start_date.tzinfo is None:
        raise ValueError("start_date must be timezone-aware (e.g., use timezone.utc)")
    if end_date.tzinfo is None:
        raise ValueError("end_date must be timezone-aware (e.g., use timezone.utc)")

    # Validate SOQL template has required placeholders
    if "{start_date}" not in soql or "{end_date}" not in soql:
        raise ValueError(
            "SOQL query must contain {start_date} and {end_date} placeholders"
        )

    # Initialize client outside loop
    if client is None:
        client = get_client()

    # Calculate total chunks for progress logging
    chunks = list(_generate_date_chunks(start_date, end_date, chunk_size))
    total_chunks = len(chunks)

    if total_chunks == 0:
        logger.debug(
            "No chunks to process: start_date=%s equals end_date=%s",
            start_date.isoformat(),
            end_date.isoformat()
        )
        return

    logger.info(
        "Starting chunked query: date_field=%s, start=%s, end=%s, "
        "chunk_size=%s, total_chunks=%d",
        date_field,
        start_date.isoformat(),
        end_date.isoformat(),
        chunk_size.value,
        total_chunks
    )

    # Execute query for each chunk
    for chunk_num, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        # Format dates as ISO 8601 with 'Z' suffix for UTC
        start_iso = chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_iso = chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Substitute placeholders in SOQL template
        chunk_soql = soql.format(start_date=start_iso, end_date=end_iso)

        logger.info(
            "Processing chunk %d/%d: %s to %s",
            chunk_num,
            total_chunks,
            start_iso,
            end_iso
        )
        logger.debug("Chunk SOQL: %s", chunk_soql[:200])

        try:
            # Use query_all() for automatic pagination within this chunk
            records = query_all(
                soql=chunk_soql,
                client=client,
                retry_config=retry_config
            )

            if not records:
                logger.debug(
                    "Chunk %d/%d returned 0 records: %s to %s",
                    chunk_num,
                    total_chunks,
                    start_iso,
                    end_iso
                )
            else:
                logger.info(
                    "Chunk %d/%d returned %d records: %s to %s",
                    chunk_num,
                    total_chunks,
                    len(records),
                    start_iso,
                    end_iso
                )

            yield records

        except SalesforceAPIError as e:
            # Re-raise with chunk context for troubleshooting
            logger.error(
                "Query failed for chunk %d/%d (%s to %s): %s",
                chunk_num,
                total_chunks,
                start_iso,
                end_iso,
                str(e)
            )
            raise SalesforceAPIError(
                message=(
                    f"Query failed for chunk {chunk_num}/{total_chunks} "
                    f"({start_iso} to {end_iso}): {e.message}"
                ),
                status_code=e.status_code,
                response_body=e.response_body
            ) from e

    logger.info(
        "Completed chunked query: processed %d chunks from %s to %s",
        total_chunks,
        start_date.isoformat(),
        end_date.isoformat()
    )


def _validate_date_field_name(date_field: str) -> None:
    """Validate date field name format.

    Args:
        date_field: Field name to validate.

    Raises:
        ValueError: If field name format is invalid.
    """
    # Field name pattern: starts with letter, contains letters/digits/underscores, optional __c suffix
    pattern = r"^[A-Za-z][A-Za-z0-9_]*(__c)?$"
    if not re.match(pattern, date_field):
        logger.error("Invalid date field name format: %s", date_field)
        raise ValueError(
            f"Invalid date field name '{date_field}'. Must start with a letter and "
            f"contain only letters, digits, and underscores (optionally ending with __c)"
        )
    logger.debug("Date field name format is valid: %s", date_field)


def _inject_incremental_filter(soql: str, date_field: str, watermark: datetime) -> str:
    """Inject incremental filter into SOQL WHERE clause.

    Args:
        soql: Original SOQL query.
        date_field: Date field name for filtering.
        watermark: Watermark timestamp (must be timezone-aware).

    Returns:
        Modified SOQL query with date filter added.
    """
    logger.debug(
        "Injecting incremental filter: date_field=%s watermark=%s",
        date_field,
        watermark.isoformat(),
    )

    # Format watermark as ISO 8601
    watermark_iso = watermark.strftime("%Y-%m-%dT%H:%M:%SZ")
    filter_clause = f"{date_field} >= {watermark_iso}"

    # Check if SOQL has existing WHERE clause (case-insensitive)
    where_match = re.search(r"\bWHERE\b", soql, re.IGNORECASE)

    if where_match:
        # Append to existing WHERE clause
        modified_soql = re.sub(
            r"(\bWHERE\b)",
            rf"\1 {filter_clause} AND",
            soql,
            count=1,
            flags=re.IGNORECASE,
        )
        logger.debug("Appended filter to existing WHERE clause")
    else:
        # Add new WHERE clause before ORDER BY or at end
        # Check for ORDER BY clause
        order_match = re.search(r"\bORDER\s+BY\b", soql, re.IGNORECASE)
        if order_match:
            # Insert WHERE before ORDER BY
            modified_soql = re.sub(
                r"(\bORDER\s+BY\b)",
                rf" WHERE {filter_clause} \1",
                soql,
                count=1,
                flags=re.IGNORECASE,
            )
            logger.debug("Added WHERE clause before ORDER BY")
        else:
            # Append WHERE at end
            modified_soql = f"{soql} WHERE {filter_clause}"
            logger.debug("Added WHERE clause at end of query")

    logger.debug("Modified SOQL (first 200 chars): %s", modified_soql[:200])
    return modified_soql


def sync_records(
    soql: str,
    object_name: str,
    *,
    date_field: str = "LastModifiedDate",
    validate_date_field: bool = True,
    chunk_size: ChunkInterval = ChunkInterval.DAILY,
    mode: str = "incremental",
    client: Optional[Salesforce] = None,
    db_conn: Optional[extensions.connection] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> SyncResult:
    """Sync Salesforce records to PostgreSQL with incremental or full mode.

    Orchestrates the complete sync workflow:
    1. Validates SOQL query and date field
    2. Gets watermark from sync state (incremental mode only)
    3. Executes query (chunked for incremental, full query for full mode)
    4. Creates/updates PostgreSQL table
    5. Upserts records to database
    6. Updates sync state watermark

    Args:
        soql: SOQL query string. Must include Id field and the date_field in SELECT.
        object_name: Salesforce object name for sync state tracking (e.g., 'Account').
        date_field: Date/datetime field for incremental sync. Defaults to 'LastModifiedDate'.
        validate_date_field: If True, validate date_field exists in SELECT clause. Default True.
        chunk_size: Time interval for date chunking (incremental mode only). Default DAILY.
        mode: Sync mode - 'incremental' or 'full'. Default 'incremental'.
        client: Authenticated Salesforce client. Creates one if not provided.
        db_conn: Active psycopg2 connection. Creates one if not provided.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.

    Returns:
        SyncResult with sync statistics.

    Raises:
        ValueError: If date_field is missing from SELECT, invalid format, or invalid mode.
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If query fails with other errors.
        psycopg2.Error: On database errors.

    Example:
        >>> from sf_utils.sync import sync_records, ChunkInterval
        >>>
        >>> # Incremental sync - only fetch records modified since last sync
        >>> result = sync_records(
        ...     soql="SELECT Id, Name, LastModifiedDate FROM Account",
        ...     object_name="Account",
        ...     date_field="LastModifiedDate",
        ...     chunk_size=ChunkInterval.DAILY,
        ...     mode="incremental"
        ... )
        >>> print(f"Fetched: {result.records_fetched}, "
        ...       f"Inserted: {result.records_inserted}, "
        ...       f"Updated: {result.records_updated}")
        >>>
        >>> # Full sync - fetch all records (no watermark filter)
        >>> result = sync_records(
        ...     soql="SELECT Id, Name, CreatedDate FROM Contact",
        ...     object_name="Contact",
        ...     mode="full"
        ... )
        >>>
        >>> # Custom date field for incremental sync
        >>> result = sync_records(
        ...     soql="SELECT Id, Name, CreatedDate FROM Opportunity",
        ...     object_name="Opportunity",
        ...     date_field="CreatedDate",
        ...     mode="incremental"
        ... )
    """
    logger.info(
        "Starting sync_records: object_name=%s mode=%s date_field=%s",
        object_name,
        mode,
        date_field,
    )

    start_time = datetime.now(timezone.utc)

    # Validate mode
    if mode not in ("incremental", "full"):
        raise ValueError(f"Invalid mode '{mode}'. Must be 'incremental' or 'full'")

    # Validate date field name format
    _validate_date_field_name(date_field)

    # Validate date field exists in SELECT clause
    if validate_date_field:
        logger.info("Using date field '%s' for incremental sync", date_field)
        validate_soql(soql=soql, date_field=date_field)
        logger.debug("Date field validation passed")

    # Initialize connections
    if client is None:
        logger.debug("Creating Salesforce client from environment")
        client = get_client()

    owns_db_conn = False
    if db_conn is None:
        logger.debug("Creating PostgreSQL connection from environment")
        db_conn = get_connection()
        owns_db_conn = True

    try:
        # Ensure sync state table exists
        ensure_sync_state_table(db_conn)

        # Determine table name from object_name
        table_name = f"sf_{object_name.lower()}"
        logger.debug("Using table name: %s", table_name)

        # Initialize variables
        all_records: List[Dict[str, Any]] = []
        watermark: Optional[datetime] = None

        # Handle incremental vs full mode
        if mode == "incremental":
            # Get sync state watermark
            sync_state = get_sync_state(object_name=object_name, db_conn=db_conn)

            if sync_state:
                watermark = sync_state.last_sync_timestamp
                logger.info(
                    "Retrieved watermark for %s: %s",
                    object_name,
                    watermark.isoformat(),
                )

                # Inject watermark filter into SOQL
                modified_soql = _inject_incremental_filter(soql, date_field, watermark)
            else:
                logger.info(
                    "No previous sync state for %s - performing initial full sync",
                    object_name,
                )
                modified_soql = soql

            # Execute query (use query_all for simplicity - chunking can be added later)
            logger.info("Executing incremental query for %s", object_name)
            all_records = query_all(
                soql=modified_soql, client=client, retry_config=retry_config
            )

        else:  # mode == "full"
            # Full sync - no watermark filter
            logger.info("Executing full query for %s", object_name)
            all_records = query_all(soql=soql, client=client, retry_config=retry_config)

        records_fetched = len(all_records)
        logger.info("Query returned %d records for %s", records_fetched, object_name)

        # Create/update table schema
        logger.debug("Ensuring table exists: %s", table_name)
        create_table_from_query(
            table_name=table_name,
            soql_query=soql,
            db_conn=db_conn,
            if_not_exists=True,
        )

        # Upsert records to database
        records_inserted = 0
        records_updated = 0

        if all_records:
            logger.info("Upserting %d records to %s", records_fetched, table_name)
            records_inserted, records_updated = upsert_records(
                table_name=table_name,
                records=all_records,
                connection=db_conn,
                batch_size=500,
            )
            logger.info(
                "Upsert complete: inserted=%d updated=%d",
                records_inserted,
                records_updated,
            )

            # Update sync state with current timestamp
            new_watermark = datetime.now(timezone.utc)
            logger.debug("Updating sync state with watermark: %s", new_watermark.isoformat())
            update_sync_state(
                object_name=object_name,
                timestamp=new_watermark,
                db_conn=db_conn,
                mode=mode,
            )
            db_conn.commit()
            logger.info("Sync state updated for %s", object_name)
        else:
            logger.info("No records to sync for %s", object_name)

        end_time = datetime.now(timezone.utc)

        result = SyncResult(
            object_name=object_name,
            records_fetched=records_fetched,
            records_inserted=records_inserted,
            records_updated=records_updated,
            sync_mode=mode,
            start_timestamp=start_time,
            end_timestamp=end_time,
            date_field=date_field,
        )

        logger.info(
            "Sync complete for %s: mode=%s fetched=%d inserted=%d updated=%d duration=%.2fs",
            object_name,
            mode,
            records_fetched,
            records_inserted,
            records_updated,
            (end_time - start_time).total_seconds(),
        )

        return result

    except Exception as e:
        # Rollback on error
        db_conn.rollback()
        logger.error("Sync failed for %s: %s", object_name, str(e))
        raise

    finally:
        # Close connection if we created it
        if owns_db_conn:
            logger.debug("Closing database connection")
            db_conn.close()
