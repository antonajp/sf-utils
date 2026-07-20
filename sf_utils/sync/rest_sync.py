"""Date-chunked query execution for Salesforce REST API sync operations."""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Generator, List, Optional

from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError
from sf_utils.query import query_all
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG

logger = logging.getLogger(__name__)


class ChunkInterval(Enum):
    """Time interval for chunking date ranges in query_chunked().

    Attributes:
        HOURLY: Split date ranges into 1-hour chunks.
        DAILY: Split date ranges into 1-day chunks.
    """
    HOURLY = "hourly"
    DAILY = "daily"


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
    client: Optional[Client] = None,
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
