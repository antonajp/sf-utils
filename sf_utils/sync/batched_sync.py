"""Batched sync operations for Salesforce junction objects with platform restrictions.

This module provides specialized sync functions for Salesforce objects that cannot be
queried without specific filter criteria (e.g., ContentDocumentLink requires filtering
by ContentDocumentId or LinkedEntityId due to platform restrictions).

The batched sync approach reads IDs from a local PostgreSQL source table, batches them
to respect Salesforce SOQL limits, and executes multiple queries to sync the full dataset.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg2 import extensions
from simple_salesforce import Salesforce

from sf_utils.client import get_client
from sf_utils.db import get_connection, create_table_from_query, upsert_records
from sf_utils.db.schema import _sanitize_column_name
from sf_utils.query import query_all
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG
from sf_utils.sync.rest_sync import SyncResult

logger = logging.getLogger(__name__)

# Salesforce ID pattern: 15 or 18 alphanumeric characters
SALESFORCE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9]{15}$|^[a-zA-Z0-9]{18}$')

# Default SOQL template for ContentDocumentLink
DEFAULT_SOQL_TEMPLATE = """
SELECT Id, ShareType, Visibility, ContentDocumentId, LinkedEntityId
FROM ContentDocumentLink
WHERE ContentDocumentId IN ({id_list})
"""


def _validate_salesforce_id(id_value: str) -> bool:
    """Validate a single Salesforce ID format.

    Args:
        id_value: ID value to validate.

    Returns:
        True if id_value matches Salesforce ID format (15 or 18 alphanumeric chars).

    Example:
        >>> _validate_salesforce_id("001000000000001AAA")
        True
        >>> _validate_salesforce_id("invalid-id")
        False
        >>> _validate_salesforce_id("001000000000001")
        True
    """
    if not isinstance(id_value, str):
        logger.debug("ID validation failed: not a string (type=%s)", type(id_value).__name__)
        return False

    is_valid = SALESFORCE_ID_PATTERN.match(id_value) is not None
    if not is_valid:
        # Log length only - never log actual ID values for security
        logger.debug("ID validation failed: invalid format (length=%d)", len(id_value))

    return is_valid


def _validate_salesforce_ids(ids: List[str]) -> Tuple[List[str], List[str]]:
    """Validate a list of Salesforce IDs.

    Args:
        ids: List of ID values to validate.

    Returns:
        Tuple of (valid_ids, invalid_ids).

    Example:
        >>> valid, invalid = _validate_salesforce_ids(["001000000000001AAA", "invalid"])
        >>> len(valid), len(invalid)
        (1, 1)
    """
    valid_ids = []
    invalid_ids = []

    for id_value in ids:
        if _validate_salesforce_id(id_value):
            valid_ids.append(id_value)
        else:
            invalid_ids.append(id_value)

    logger.debug(
        "ID validation complete: valid=%d invalid=%d",
        len(valid_ids),
        len(invalid_ids),
    )

    return valid_ids, invalid_ids


def _batch_ids(ids: List[str], batch_size: int) -> List[List[str]]:
    """Split IDs into batches of batch_size.

    Args:
        ids: List of IDs to batch.
        batch_size: Maximum number of IDs per batch.

    Returns:
        List of batches, where each batch is a list of IDs.

    Example:
        >>> batches = _batch_ids(["id1", "id2", "id3"], batch_size=2)
        >>> len(batches)
        2
        >>> batches[0]
        ['id1', 'id2']
    """
    batches = []

    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        batches.append(batch)

    logger.debug(
        "Created %d batches from %d IDs (batch_size=%d)",
        len(batches),
        len(ids),
        batch_size,
    )

    return batches


def _construct_in_clause(ids: List[str]) -> str:
    """Construct properly quoted IN clause for SOQL.

    Args:
        ids: List of Salesforce IDs (already validated).

    Returns:
        IN clause string: ('id1', 'id2', 'id3', ...)

    Example:
        >>> _construct_in_clause(["001000000000001AAA", "001000000000002AAA"])
        "('001000000000001AAA', '001000000000002AAA')"
    """
    # Quote each ID and join with commas
    quoted_ids = [f"'{id_value}'" for id_value in ids]
    in_clause = f"({', '.join(quoted_ids)})"

    # Log count only - never log actual ID values for security
    logger.debug("Constructed IN clause with %d IDs", len(ids))

    return in_clause


def sync_content_document_links(
    source_table: str,
    *,
    id_column: str = "id",
    batch_size: int = 200,
    soql_template: Optional[str] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
    client: Optional[Salesforce] = None,
    db_conn: Optional[extensions.connection] = None,
) -> SyncResult:
    """Sync ContentDocumentLink records from Salesforce to PostgreSQL using batched queries.

    ContentDocumentLink is a Salesforce junction object that requires filtering by
    ContentDocumentId or LinkedEntityId (platform restriction). This function reads
    ContentDocumentIds from a local PostgreSQL table, batches them, and executes
    multiple SOQL queries to sync the full dataset.

    Workflow:
    1. Query PostgreSQL for distinct ContentDocumentIds from source_table
    2. Validate IDs (Salesforce format: 15/18 alphanumeric chars)
    3. Batch IDs into groups of batch_size (default 200, max 2000)
    4. Execute SOQL queries for each batch
    5. Create sf_contentdocumentlink table
    6. Upsert records to PostgreSQL

    Args:
        source_table: PostgreSQL table name to read ContentDocumentIds from.
            Must contain an ID column (default: 'id').
        id_column: Column name containing ContentDocumentIds. Defaults to 'id'.
        batch_size: Number of IDs per SOQL batch. Defaults to 200.
            Maximum is 2000 (Salesforce SOQL IN clause limit).
        soql_template: Custom SOQL template. Must contain {id_list} placeholder.
            Defaults to selecting Id, ShareType, Visibility, ContentDocumentId, LinkedEntityId.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.
        client: Authenticated Salesforce client. Creates one if not provided.
        db_conn: Active psycopg2 connection. Creates one if not provided.

    Returns:
        SyncResult with sync statistics.

    Raises:
        ValueError: If batch_size exceeds 2000, invalid IDs found, or source table empty.
        psycopg2.Error: On database errors.
        SalesforceAPIError: If SOQL query fails.

    Example:
        >>> from sf_utils.sync import sync_content_document_links
        >>>
        >>> # Sync ContentDocumentLinks for ContentDocuments in sf_contentdocument table
        >>> result = sync_content_document_links(
        ...     source_table="sf_contentdocument",
        ...     id_column="id",
        ...     batch_size=200
        ... )
        >>> print(f"Fetched: {result.records_fetched}, "
        ...       f"Inserted: {result.records_inserted}, "
        ...       f"Updated: {result.records_updated}")
        >>>
        >>> # Custom SOQL template
        >>> custom_soql = '''
        ... SELECT Id, ContentDocumentId, LinkedEntityId, ShareType
        ... FROM ContentDocumentLink
        ... WHERE ContentDocumentId IN ({id_list})
        ... '''
        >>> result = sync_content_document_links(
        ...     source_table="sf_contentdocument",
        ...     soql_template=custom_soql
        ... )
    """
    logger.info(
        "Starting sync_content_document_links: source_table=%s id_column=%s batch_size=%d",
        source_table,
        id_column,
        batch_size,
    )

    start_time = datetime.now(timezone.utc)

    # Validate batch_size
    if batch_size < 1 or batch_size > 2000:
        raise ValueError(
            f"batch_size must be between 1 and 2000 (Salesforce SOQL limit), got {batch_size}"
        )

    # Use default SOQL template if not provided
    if soql_template is None:
        soql_template = DEFAULT_SOQL_TEMPLATE.strip()
        logger.debug("Using default SOQL template")

    # Validate SOQL template has {id_list} placeholder
    if "{id_list}" not in soql_template:
        raise ValueError(
            "SOQL template must contain {id_list} placeholder for IN clause"
        )

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
        # Query PostgreSQL for distinct ContentDocumentIds
        logger.info("Querying distinct IDs from %s.%s", source_table, id_column)

        cursor = db_conn.cursor()
        try:
            # Use parameterized query for table name safety
            # Note: psycopg2 doesn't support identifiers in parameters, but we validate the name
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', source_table):
                raise ValueError(
                    f"Invalid table name '{source_table}'. Must contain only letters, "
                    f"digits, and underscores, starting with a letter."
                )

            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', id_column):
                raise ValueError(
                    f"Invalid column name '{id_column}'. Must contain only letters, "
                    f"digits, and underscores, starting with a letter."
                )

            # Safe to use format() since we validated the identifiers
            query_sql = f"""
                SELECT DISTINCT {id_column}
                FROM {source_table}
                WHERE {id_column} IS NOT NULL
                ORDER BY {id_column}
            """

            logger.debug("Executing: %s", query_sql)
            cursor.execute(query_sql)

            rows = cursor.fetchall()
            logger.debug("Query returned %d rows", len(rows))

        finally:
            cursor.close()

        # Extract IDs from query results
        all_ids = [row[0] for row in rows if row[0]]

        if not all_ids:
            logger.warning(
                "No IDs found in %s.%s - nothing to sync",
                source_table,
                id_column,
            )
            return SyncResult(
                object_name="ContentDocumentLink",
                records_fetched=0,
                records_inserted=0,
                records_updated=0,
                sync_mode="batched",
                start_timestamp=start_time,
                end_timestamp=datetime.now(timezone.utc),
                date_field=None,
            )

        logger.info("Found %d distinct IDs in source table", len(all_ids))

        # Validate IDs
        valid_ids, invalid_ids = _validate_salesforce_ids(all_ids)

        if invalid_ids:
            raise ValueError(
                f"Found {len(invalid_ids)} invalid Salesforce IDs in {source_table}.{id_column}. "
                f"IDs must be 15 or 18 alphanumeric characters."
            )

        logger.info("All %d IDs validated successfully", len(valid_ids))

        # Batch IDs
        batches = _batch_ids(valid_ids, batch_size)
        total_batches = len(batches)

        logger.info(
            "Split %d IDs into %d batches (batch_size=%d)",
            len(valid_ids),
            total_batches,
            batch_size,
        )

        # Execute queries for each batch
        all_records: List[Dict[str, Any]] = []

        for batch_num, batch_ids in enumerate(batches, start=1):
            logger.info(
                "Processing batch %d/%d: %d IDs",
                batch_num,
                total_batches,
                len(batch_ids),
            )

            # Construct IN clause
            in_clause = _construct_in_clause(batch_ids)

            # Substitute {id_list} in SOQL template
            batch_soql = soql_template.replace("{id_list}", in_clause)

            # Log batch execution without SOQL content (contains IDs)
            logger.debug(
                "Batch %d/%d: Executing SOQL query with %d IDs in IN clause",
                batch_num,
                total_batches,
                len(batch_ids),
            )

            # Execute query
            try:
                batch_records = query_all(
                    soql=batch_soql, client=client, retry_config=retry_config
                )

                logger.info(
                    "Batch %d/%d returned %d records",
                    batch_num,
                    total_batches,
                    len(batch_records),
                )

                all_records.extend(batch_records)

            except Exception as e:
                logger.error(
                    "Query failed for batch %d/%d: %s",
                    batch_num,
                    total_batches,
                    str(e),
                )
                raise

        records_fetched = len(all_records)
        logger.info(
            "Completed %d batches: fetched %d total records",
            total_batches,
            records_fetched,
        )

        # Create table from SOQL query
        table_name = "sf_contentdocumentlink"
        logger.debug("Ensuring table exists: %s", table_name)

        # Use the SOQL template (without {id_list} substitution) for schema creation
        # Replace {id_list} with a dummy value to make it valid SOQL
        schema_soql = soql_template.replace("{id_list}", "('dummy')")

        create_table_from_query(
            table_name=table_name,
            soql_query=schema_soql,
            db_conn=db_conn,
            if_not_exists=True,
        )

        # Upsert records to database
        records_inserted = 0
        records_updated = 0

        if all_records:
            # Normalize record keys using same sanitization as table creation
            normalized_records = [
                {_sanitize_column_name(k): v for k, v in record.items()}
                for record in all_records
            ]

            logger.info("Upserting %d records to %s", records_fetched, table_name)

            records_inserted, records_updated = upsert_records(
                table_name=table_name,
                records=normalized_records,
                connection=db_conn,
                batch_size=500,
            )

            logger.info(
                "Upsert complete: inserted=%d updated=%d",
                records_inserted,
                records_updated,
            )

            db_conn.commit()
        else:
            logger.info("No records to upsert")

        end_time = datetime.now(timezone.utc)

        result = SyncResult(
            object_name="ContentDocumentLink",
            records_fetched=records_fetched,
            records_inserted=records_inserted,
            records_updated=records_updated,
            sync_mode="batched",
            start_timestamp=start_time,
            end_timestamp=end_time,
            date_field=None,
        )

        logger.info(
            "Sync complete for ContentDocumentLink: fetched=%d inserted=%d updated=%d duration=%.2fs",
            records_fetched,
            records_inserted,
            records_updated,
            (end_time - start_time).total_seconds(),
        )

        return result

    except Exception as e:
        # Rollback on error
        db_conn.rollback()
        logger.error("Sync failed for ContentDocumentLink: %s", str(e))
        raise

    finally:
        # Close connection if we created it
        if owns_db_conn:
            logger.debug("Closing database connection")
            db_conn.close()
