"""PostgreSQL schema management for Salesforce data sync."""

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import psycopg2
from psycopg2 import extensions, sql
from psycopg2.extras import execute_values
from simple_salesforce import Salesforce

from sf_utils.db.parser import (
    _extract_alias,
    _parse_select_columns,
    _parse_select_columns_with_types,
    _sanitize_column_name,
)
from sf_utils.db.types import (
    SALESFORCE_TYPE_TO_POSTGRES,
    get_postgres_type,
    infer_aggregate_type,
)
from sf_utils.sobjects import describe_object

logger = logging.getLogger(__name__)


def _get_existing_columns(table_name: str, db_conn: extensions.connection) -> List[str]:
    """Get existing column names from a PostgreSQL table."""
    cursor = db_conn.cursor()
    try:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        columns = [row[0] for row in cursor.fetchall()]
        logger.debug("Existing columns for %s: %s", table_name, columns)
        return columns
    finally:
        cursor.close()


def _add_missing_columns(
    table_name: str,
    required_columns: List[str],
    existing_columns: List[str],
    db_conn: extensions.connection,
) -> List[str]:
    """Add missing columns to an existing table."""
    # Find missing columns (case-insensitive comparison)
    existing_set = {col.lower() for col in existing_columns}
    missing = [col for col in required_columns if col.lower() not in existing_set]

    if not missing:
        logger.debug("No missing columns for table %s", table_name)
        return []

    logger.info(
        "Adding %d missing column(s) to %s: %s",
        len(missing),
        table_name,
        missing,
    )

    cursor = db_conn.cursor()
    try:
        for col in missing:
            # All columns are TEXT type for flexibility
            alter_stmt = sql.SQL("ALTER TABLE {} ADD COLUMN {} TEXT").format(
                sql.Identifier(table_name),
                sql.Identifier(col),
            )
            logger.debug("Executing: ALTER TABLE %s ADD COLUMN %s TEXT", table_name, col)
            cursor.execute(alter_stmt)

        db_conn.commit()
        logger.info("Successfully added %d column(s) to %s", len(missing), table_name)
        return missing

    except psycopg2.Error as e:
        db_conn.rollback()
        logger.error("Failed to add columns to %s: %s", table_name, str(e))
        raise
    finally:
        cursor.close()


def create_table_from_query(
    table_name: str,
    soql_query: str,
    db_conn: extensions.connection,
    *,
    if_not_exists: bool = True,
    infer_aggregate_types: bool = True,
    type_overrides: Optional[Dict[str, str]] = None,
) -> bool:
    """Create a PostgreSQL table from SOQL SELECT columns.

    If the table already exists, adds any missing columns from the SOQL query.
    This handles schema evolution when new fields are added to the SOQL.

    When infer_aggregate_types=True, detects aggregate functions (COUNT, SUM, AVG)
    and uses appropriate numeric types (BIGINT for COUNT, NUMERIC for SUM/AVG)
    instead of TEXT.

    Args:
        table_name: PostgreSQL table name (e.g., 'sf_account').
        soql_query: SOQL query to extract columns from.
        db_conn: psycopg2 database connection.
        if_not_exists: If True, use CREATE TABLE IF NOT EXISTS. Default True.
        infer_aggregate_types: If True, infer numeric types for aggregate functions.
            Default True. When enabled, COUNT columns use BIGINT, SUM/AVG use NUMERIC.
        type_overrides: Optional dict mapping column names to PostgreSQL types.
            Overrides inferred types for specific columns.

    Returns:
        True if table was created, False if it already existed.

    Raises:
        ValueError: If SOQL query is malformed or has no SELECT clause.
        psycopg2.Error: For database errors.

    Example:
        >>> # Basic usage - all TEXT columns
        >>> created = create_table_from_query(
        ...     table_name="sf_account",
        ...     soql_query="SELECT Id, Name FROM Account",
        ...     db_conn=conn
        ... )
        >>> # Aggregate query with type inference (total=BIGINT, revenue=NUMERIC)
        >>> created = create_table_from_query(
        ...     table_name="sf_stats",
        ...     soql_query="SELECT Id, COUNT(Id) AS total, SUM(Amount) AS revenue FROM Account",
        ...     db_conn=conn,
        ...     infer_aggregate_types=True
        ... )
    """
    logger.debug(
        "create_table_from_query: table_name=%s if_not_exists=%s infer_aggregate_types=%s soql=%s",
        table_name,
        if_not_exists,
        infer_aggregate_types,
        soql_query,
    )

    # Parse columns from SOQL with type inference if enabled
    if infer_aggregate_types:
        column_specs = _parse_select_columns_with_types(soql_query)
        columns = [spec.name for spec in column_specs]
    else:
        columns = _parse_select_columns(soql_query)
        column_specs = None

    # Check if Id column exists (required for PRIMARY KEY)
    if "id" not in columns:
        error_msg = "SOQL query must include Id field for primary key"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    # Build column definitions with type inference
    column_defs = []
    for i, col in enumerate(columns):
        col_type = "TEXT"  # Default

        # Apply type overrides or infer from aggregates
        if type_overrides and col in type_overrides:
            col_type = type_overrides[col]
            logger.debug("Type override for %s: %s", col, col_type)
        elif infer_aggregate_types and column_specs:
            spec = column_specs[i]
            if spec.aggregate_function:
                inferred_type = infer_aggregate_type(spec.original_expression)
                if inferred_type:
                    col_type = inferred_type
                    logger.debug("Inferred %s for %s (%s)", col_type, col, spec.aggregate_function)

        # Build column definition (id gets PRIMARY KEY constraint)
        col_def = sql.SQL("{} {}{}").format(
            sql.Identifier(col),
            sql.SQL(col_type),
            sql.SQL(" PRIMARY KEY" if col == "id" else ""),
        )
        column_defs.append(col_def)

    create_stmt = sql.SQL(
        "CREATE TABLE {if_not_exists} {table} ({columns})"
    ).format(
        if_not_exists=sql.SQL("IF NOT EXISTS" if if_not_exists else ""),
        table=sql.Identifier(table_name),
        columns=sql.SQL(", ").join(column_defs),
    )

    # Check if table already exists before attempting CREATE
    existing_columns = _get_existing_columns(table_name, db_conn)
    table_existed = len(existing_columns) > 0

    if table_existed:
        logger.debug("Table %s already exists with %d columns", table_name, len(existing_columns))
        # Add any missing columns from the SOQL query
        added = _add_missing_columns(table_name, columns, existing_columns, db_conn)
        if added:
            logger.info(
                "Table %s: added %d new column(s): %s",
                table_name,
                len(added),
                added,
            )
        return False  # Table was not newly created

    # Execute CREATE TABLE for new table
    cursor = db_conn.cursor()
    try:
        logger.debug("Executing CREATE TABLE for table: %s with %d columns", table_name, len(columns))
        cursor.execute(create_stmt)
        db_conn.commit()

        logger.info(
            "Table created successfully: %s with %d columns: %s",
            table_name,
            len(columns),
            columns,
        )
        return True  # Table was newly created

    except psycopg2.Error as e:
        db_conn.rollback()
        logger.error(
            "Failed to create table %s: %s",
            table_name,
            str(e),
        )
        raise
    finally:
        cursor.close()


def upsert_records(
    table_name: str,
    records: List[Dict[str, Any]],
    connection: extensions.connection,
    *,
    batch_size: int = 500,
) -> Tuple[int, int]:
    """Upsert records to PostgreSQL table.

    Inserts new records or updates existing ones based on the 'id' field.
    Uses PostgreSQL's INSERT ... ON CONFLICT DO UPDATE for efficient upserts.

    Args:
        table_name: Name of the target table.
        records: List of record dictionaries to upsert. Each record must contain an 'id' key.
        connection: Active psycopg2 connection.
        batch_size: Number of records per batch (default 500).

    Returns:
        Tuple of (inserted_count, updated_count).

    Raises:
        psycopg2.DatabaseError: On SQL errors with table name and record count context.
        ValueError: On invalid input (empty table name, non-list records, empty records).

    Example:
        >>> from sf_utils.db import get_connection, upsert_records
        >>> conn = get_connection()
        >>> inserted, updated = upsert_records(
        ...     table_name="sf_account",
        ...     records=[
        ...         {"id": "001abc", "name": "Acme Corp", "status": "active"},
        ...         {"id": "002def", "name": "Globex", "status": "inactive"},
        ...     ],
        ...     connection=conn,
        ...     batch_size=500
        ... )
        >>> print(f"Inserted: {inserted}, Updated: {updated}")
    """
    logger.debug(
        "upsert_records: table_name=%s record_count=%d batch_size=%d",
        table_name,
        len(records),
        batch_size,
    )

    # Validate inputs
    if not table_name or not isinstance(table_name, str):
        error_msg = "table_name must be a non-empty string"
        logger.error("Invalid input: %s", error_msg)
        raise ValueError(error_msg)

    if not isinstance(records, list):
        error_msg = "records must be a list"
        logger.error("Invalid input: %s", error_msg)
        raise ValueError(error_msg)

    if not records:
        logger.info("No records to upsert for table: %s", table_name)
        return (0, 0)

    # Validate all records have 'id' field
    if not all("id" in record for record in records):
        error_msg = "All records must contain an 'id' field"
        logger.error("Invalid input: %s", error_msg)
        raise ValueError(error_msg)

    total_records = len(records)
    total_inserted = 0
    total_updated = 0
    cursor = connection.cursor()

    try:
        # Extract column names from first record
        # All records must have the same structure
        columns = list(records[0].keys())
        logger.debug("Upserting %d columns: %s", len(columns), columns)

        # Build column identifiers
        column_identifiers = [sql.Identifier(col) for col in columns]

        # Build SET clause for UPDATE (exclude 'id' since it's the conflict target)
        update_columns = [col for col in columns if col != "id"]
        set_clause = sql.SQL(", ").join(
            [
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(col), sql.Identifier(col)
                )
                for col in update_columns
            ]
        )

        # Process records in batches
        for batch_start in range(0, total_records, batch_size):
            batch_end = min(batch_start + batch_size, total_records)
            batch = records[batch_start:batch_end]

            # Convert records to tuples in column order
            values = [tuple(record.get(col) for col in columns) for record in batch]

            # Build UPSERT query with manual placeholders
            # Use xmax = 0 in RETURNING to distinguish inserts (xmax=0) from updates (xmax>0)

            # Build placeholder template for VALUES clause: (%s, %s, %s, ...)
            value_template = sql.SQL("({})").format(
                sql.SQL(", ").join(sql.Placeholder() for _ in columns)
            )

            # Build full VALUES clause with placeholders for all records
            values_clause = sql.SQL(", ").join([value_template] * len(batch))

            # Build complete UPSERT query
            upsert_query = sql.SQL(
                "INSERT INTO {table} ({columns}) VALUES {values} ON CONFLICT (id) DO UPDATE SET {set_clause} RETURNING (xmax = 0) AS inserted"
            ).format(
                table=sql.Identifier(table_name),
                columns=sql.SQL(", ").join(column_identifiers),
                values=values_clause,
                set_clause=set_clause,
            )

            # Execute batch upsert
            logger.debug(
                "Executing batch upsert: records %d-%d of %d",
                batch_start + 1,
                batch_end,
                total_records,
            )

            # Flatten values for parameterized query
            flat_values = [val for record_tuple in values for val in record_tuple]

            # Execute with parameterized values
            cursor.execute(upsert_query, flat_values)

            # Count inserts vs updates using xmax
            results = cursor.fetchall()
            batch_inserted = sum(1 for (is_insert,) in results if is_insert)
            batch_updated = len(results) - batch_inserted

            total_inserted += batch_inserted
            total_updated += batch_updated

            # Commit per batch
            connection.commit()

            logger.info(
                "Upserted %d/%d records to %s (inserted: %d, updated: %d)",
                batch_end,
                total_records,
                table_name,
                batch_inserted,
                batch_updated,
            )

        logger.info(
            "Upsert complete for %s: %d inserted, %d updated (total: %d)",
            table_name,
            total_inserted,
            total_updated,
            total_records,
        )
        return (total_inserted, total_updated)

    except psycopg2.Error as e:
        connection.rollback()
        logger.error(
            "Failed to upsert %d records to table %s: %s",
            total_records,
            table_name,
            str(e),
        )
        raise psycopg2.DatabaseError(
            f"Failed to upsert {total_records} records to table {table_name}: {e}"
        ) from e
    finally:
        cursor.close()


def create_table_from_describe(
    table_name: str,
    sobject_type: str,
    fields: List[str],
    *,
    if_not_exists: bool = True,
    type_mapper: Optional[Callable[[str], Optional[str]]] = None,
    client: Optional[Salesforce] = None,
    db_conn: extensions.connection,
) -> bool:
    """Create a PostgreSQL table with typed columns from Salesforce describe() metadata.

    Uses the Salesforce describe() API to get field type information and maps
    Salesforce types to appropriate PostgreSQL types for proper date filtering,
    numeric aggregations, and boolean logic.

    Args:
        table_name: PostgreSQL table name (e.g., 'sf_account').
        sobject_type: Salesforce object type (e.g., 'Account', 'Contact').
        fields: List of Salesforce field API names to include (e.g., ['Id', 'Name', 'AnnualRevenue']).
        if_not_exists: If True, use CREATE TABLE IF NOT EXISTS. Default True.
        type_mapper: Optional custom type mapper function. Takes Salesforce type string,
            returns PostgreSQL type string or None to use default mapping.
        client: Authenticated Salesforce client. If None, creates from environment.
        db_conn: psycopg2 database connection.

    Returns:
        True if table was created, False if it already existed.

    Raises:
        ValueError: If fields list is empty or missing 'Id' field.
        SalesforceAPIError: If describe() API call fails.
        psycopg2.Error: For database errors.
    """
    logger.debug(
        "create_table_from_describe: table_name=%s sobject_type=%s fields=%s if_not_exists=%s",
        table_name,
        sobject_type,
        fields,
        if_not_exists,
    )

    # Validate fields list
    if not fields:
        error_msg = "fields list must not be empty"
        logger.error("Invalid input: %s", error_msg)
        raise ValueError(error_msg)

    # Normalize field names to lowercase for comparison
    fields_lower = [f.lower() for f in fields]

    if "id" not in fields_lower:
        error_msg = "fields list must include 'Id' field for primary key"
        logger.error("Invalid input: %s", error_msg)
        raise ValueError(error_msg)

    # Get describe metadata from Salesforce
    logger.debug("Calling describe_object for %s", sobject_type)
    describe_result = describe_object(sobject_type, client=client)

    # Build field name to type mapping from describe result
    field_metadata: Dict[str, str] = {}
    for field_info in describe_result.get("fields", []):
        field_name = field_info.get("name", "").lower()
        field_type = field_info.get("type", "")
        if field_name:
            field_metadata[field_name] = field_type
            logger.debug(
                "Field metadata: %s -> type=%s",
                field_name,
                field_type,
            )

    # Build column definitions
    column_defs = []
    columns_created = []

    for field in fields:
        field_lower = field.lower()
        sanitized_name = _sanitize_column_name(field)

        # Get Salesforce type from describe metadata
        sf_type = field_metadata.get(field_lower, "")

        if not sf_type:
            logger.warning(
                "Field '%s' not found in describe() result for %s, defaulting to TEXT",
                field,
                sobject_type,
            )
            pg_type = "TEXT"
        else:
            pg_type = get_postgres_type(sf_type, type_mapper)

        logger.debug(
            "Column: %s (field=%s, sf_type=%s, pg_type=%s)",
            sanitized_name,
            field,
            sf_type,
            pg_type,
        )

        # Build column definition with PRIMARY KEY for id
        if sanitized_name == "id":
            column_defs.append(
                sql.SQL("{} {} PRIMARY KEY").format(
                    sql.Identifier(sanitized_name),
                    sql.SQL(pg_type),
                )
            )
        else:
            column_defs.append(
                sql.SQL("{} {}").format(
                    sql.Identifier(sanitized_name),
                    sql.SQL(pg_type),
                )
            )

        columns_created.append(sanitized_name)

    # Build CREATE TABLE statement
    create_stmt = sql.SQL(
        "CREATE TABLE {if_not_exists} {table} ({columns})"
    ).format(
        if_not_exists=sql.SQL("IF NOT EXISTS" if if_not_exists else ""),
        table=sql.Identifier(table_name),
        columns=sql.SQL(", ").join(column_defs),
    )

    # Execute CREATE TABLE
    cursor = db_conn.cursor()
    try:
        logger.debug(
            "Executing CREATE TABLE for %s with %d typed columns",
            table_name,
            len(columns_created),
        )
        cursor.execute(create_stmt)
        db_conn.commit()

        # Check if table exists to determine if it was created or already existed
        cursor.execute(
            sql.SQL("SELECT to_regclass({})").format(sql.Literal(table_name))
        )
        table_exists = cursor.fetchone()[0] is not None

        if table_exists:
            logger.info(
                "Table created successfully: %s with %d typed columns: %s",
                table_name,
                len(columns_created),
                columns_created,
            )
            return True
        else:
            logger.info("Table already existed: %s", table_name)
            return False

    except psycopg2.Error as e:
        db_conn.rollback()
        logger.error(
            "Failed to create table %s: %s",
            table_name,
            str(e),
        )
        raise
    finally:
        cursor.close()
