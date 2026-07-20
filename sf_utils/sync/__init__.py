"""Sync utilities for Salesforce data synchronization."""

import logging
from enum import Enum
from typing import Optional

from psycopg2 import extensions
from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.query import query
from sf_utils.sync.bulk_sync import create_bulk_query_job, poll_bulk_job, get_bulk_results, sync_records_bulk
from sf_utils.sync.rest_sync import ChunkInterval, query_chunked, sync_records, SyncResult
from sf_utils.sync.soql_loader import load_soql, render_soql, validate_soql
from sf_utils.sync.state import (
    SyncStateRow,
    ensure_sync_state_table,
    get_sync_state,
    update_sync_state,
)
from sf_utils.sync.config import SyncJobConfig, load_sync_config

logger = logging.getLogger(__name__)


class SyncMode(Enum):
    """API mode selection for sync operations.

    Attributes:
        REST: Use REST API (sync_records()) for all queries.
        BULK: Use Bulk API 2.0 (sync_records_bulk()) for all queries.
        AUTO: Automatically choose REST or BULK based on record count threshold.
    """
    REST = "rest"
    BULK = "bulk"
    AUTO = "auto"


def sync(
    soql: str,
    object_name: str,
    *,
    mode: SyncMode = SyncMode.AUTO,
    threshold: int = 10000,
    date_field: str = "LastModifiedDate",
    batch_size: int = 1000,
    poll_interval: float = 5.0,
    timeout: float = 600.0,
    client: Optional[Client] = None,
    db_conn: Optional[extensions.connection] = None,
) -> SyncResult:
    """Execute a sync with automatic or explicit API mode selection.

    Orchestrates Salesforce sync operations with intelligent API selection. In AUTO mode,
    queries the total record count and automatically selects REST API for small datasets
    or Bulk API 2.0 for large datasets based on the threshold. Explicit mode selection
    bypasses the count query.

    Args:
        soql: SOQL query string. Must include Id field and the date_field in SELECT.
        object_name: Salesforce object name for sync state tracking (e.g., 'Account').
        mode: API mode selection strategy. Defaults to SyncMode.AUTO.
            - SyncMode.AUTO: Query record count and auto-select (REST if < threshold, BULK if >= threshold).
            - SyncMode.REST: Force REST API (sync_records()).
            - SyncMode.BULK: Force Bulk API 2.0 (sync_records_bulk()).
        threshold: Record count threshold for AUTO mode. Defaults to 10000.
            If record count < threshold, uses REST. If >= threshold, uses BULK.
        date_field: Date/datetime field for incremental sync. Defaults to 'LastModifiedDate'.
        batch_size: Number of records to process per batch (BULK mode only). Defaults to 1000.
        poll_interval: Initial polling interval in seconds (BULK mode only). Defaults to 5.0.
        timeout: Maximum time to poll job in seconds (BULK mode only). Defaults to 600.0 (10 minutes).
        client: Authenticated Salesforce client. Creates one if not provided.
        db_conn: Active psycopg2 connection. Creates one if not provided.

    Returns:
        SyncResult with sync statistics.

    Raises:
        ValueError: If inputs are invalid.
        SalesforceAuthError: If authentication fails (401/403).
        SalesforceAPIError: If count query or sync operation fails.
        psycopg2.Error: On database errors.

    Example:
        >>> from sf_utils.sync import sync, SyncMode
        >>>
        >>> # Auto-select API mode based on record count
        >>> result = sync(
        ...     soql="SELECT Id, Name, LastModifiedDate FROM Account",
        ...     object_name="Account",
        ...     mode=SyncMode.AUTO,
        ...     threshold=10000  # Use REST if < 10k records, BULK if >= 10k
        ... )
        >>> print(f"Fetched: {result.records_fetched}, Mode: {result.sync_mode}")
        >>>
        >>> # Force REST API mode
        >>> result = sync(
        ...     soql="SELECT Id, Name FROM Contact",
        ...     object_name="Contact",
        ...     mode=SyncMode.REST
        ... )
        >>>
        >>> # Force Bulk API mode
        >>> result = sync(
        ...     soql="SELECT Id, Amount FROM Opportunity",
        ...     object_name="Opportunity",
        ...     mode=SyncMode.BULK,
        ...     timeout=1800.0  # 30 minutes for large datasets
        ... )
        >>>
        >>> # Custom threshold for AUTO mode
        >>> result = sync(
        ...     soql="SELECT Id, Status__c FROM CustomObject__c",
        ...     object_name="CustomObject__c",
        ...     mode=SyncMode.AUTO,
        ...     threshold=5000  # Lower threshold for custom objects
        ... )

    Notes:
        - AUTO mode adds one COUNT() query before executing the sync
        - COUNT() query uses the object_name to determine record count
        - REST mode is more efficient for small datasets (< 10k records typically)
        - BULK mode is required for large datasets (> 10k records)
        - batch_size, poll_interval, and timeout only apply to BULK mode
    """
    logger.debug(
        "sync() called: object_name=%s mode=%s threshold=%d",
        object_name,
        mode.value,
        threshold,
    )

    # Initialize client if not provided
    if client is None:
        logger.debug("Creating Salesforce client from environment")
        client = get_client()

    # Determine which API mode to use
    selected_mode = mode

    if mode == SyncMode.AUTO:
        # Query Salesforce for record count
        logger.debug("AUTO mode: querying record count for %s", object_name)
        count_soql = f"SELECT COUNT() FROM {object_name}"

        try:
            # Use query() from sf_utils.query to get count
            result = query(count_soql, client=client)
            # COUNT() returns as expr0 in the first (and only) record
            record_count = result[0].get("expr0", 0) if result else 0
            logger.debug("Record count for %s: %d", object_name, record_count)

            # Select mode based on threshold
            if record_count < threshold:
                selected_mode = SyncMode.REST
                logger.info(
                    "Auto-selecting API mode: REST (count=%d < threshold=%d)",
                    record_count,
                    threshold,
                )
            else:
                selected_mode = SyncMode.BULK
                logger.info(
                    "Auto-selecting API mode: BULK (count=%d >= threshold=%d)",
                    record_count,
                    threshold,
                )

        except Exception as e:
            # If count query fails, log error and default to REST
            logger.warning(
                "COUNT() query failed for %s: %s - defaulting to REST mode",
                object_name,
                str(e),
            )
            selected_mode = SyncMode.REST

    # Execute sync with selected mode
    if selected_mode == SyncMode.REST:
        logger.info("Executing REST API sync for %s", object_name)
        return sync_records(
            soql=soql,
            object_name=object_name,
            date_field=date_field,
            client=client,
            db_conn=db_conn,
        )
    else:  # selected_mode == SyncMode.BULK
        logger.info("Executing Bulk API 2.0 sync for %s", object_name)
        return sync_records_bulk(
            soql=soql,
            object_name=object_name,
            date_field=date_field,
            batch_size=batch_size,
            poll_interval=poll_interval,
            timeout=timeout,
            client=client,
            db_conn=db_conn,
        )


__all__ = [
    "create_bulk_query_job",
    "poll_bulk_job",
    "get_bulk_results",
    "sync_records_bulk",
    "ChunkInterval",
    "query_chunked",
    "sync_records",
    "SyncResult",
    "load_soql",
    "render_soql",
    "validate_soql",
    "SyncStateRow",
    "ensure_sync_state_table",
    "get_sync_state",
    "update_sync_state",
    "sync",
    "SyncMode",
    "SyncJobConfig",
    "load_sync_config",
]
