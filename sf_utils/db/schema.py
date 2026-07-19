"""PostgreSQL schema management for Salesforce data sync."""

import logging
import re
from typing import List

import psycopg2
from psycopg2 import extensions, sql

logger = logging.getLogger(__name__)


def _sanitize_column_name(name: str) -> str:
    """Sanitize column name for PostgreSQL.

    Converts to lowercase and replaces spaces/special chars with underscores.

    Args:
        name: Column name to sanitize.

    Returns:
        Sanitized column name safe for PostgreSQL.

    Example:
        >>> _sanitize_column_name("Billing City")
        'billing_city'
        >>> _sanitize_column_name("Account.Name")
        'account_name'
    """
    logger.debug("Sanitizing column name: %s", name)
    # Convert to lowercase
    sanitized = name.lower()
    # Replace special chars (spaces, dots, etc.) with underscores
    sanitized = re.sub(r"[^a-z0-9_]", "_", sanitized)
    # Remove consecutive underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip("_")
    logger.debug("Sanitized column name: %s -> %s", name, sanitized)
    return sanitized


def _extract_alias(field: str) -> str:
    """Extract alias from SOQL field expression.

    Handles "Field AS Alias" patterns and relationship traversals.

    Args:
        field: SOQL field expression (may contain AS alias).

    Returns:
        Column name (alias if present, otherwise sanitized field name).

    Example:
        >>> _extract_alias("Account.Name AS AccountName")
        'accountname'
        >>> _extract_alias("Account.Name")
        'account_name'
        >>> _extract_alias("Id")
        'id'
    """
    logger.debug("Extracting alias from field: %s", field)
    field = field.strip()

    # Check for AS alias pattern (case-insensitive)
    as_pattern = r"\s+AS\s+(\w+)\s*$"
    as_match = re.search(as_pattern, field, re.IGNORECASE)
    if as_match:
        alias = as_match.group(1)
        logger.debug("Found AS alias: %s -> %s", field, alias)
        return _sanitize_column_name(alias)

    # No alias, sanitize the field name (handles dots for relationships)
    return _sanitize_column_name(field)


def _parse_select_columns(soql: str) -> List[str]:
    """Parse SELECT columns from SOQL query.

    Extracts column names from SELECT clause, handling:
    - Simple fields: Id, Name
    - Relationship traversals: Account.Name
    - Aliases: Field AS Alias

    Args:
        soql: SOQL query string.

    Returns:
        List of sanitized column names.

    Raises:
        ValueError: If SOQL has no SELECT clause or is malformed.

    Example:
        >>> _parse_select_columns("SELECT Id, Name FROM Account")
        ['id', 'name']
        >>> _parse_select_columns("SELECT Account.Name AS AccountName FROM Contact")
        ['accountname']
    """
    logger.debug("Parsing SELECT columns from SOQL: %s", soql)

    # Find SELECT clause using regex
    select_pattern = r"SELECT\s+(.+?)\s+FROM"
    match = re.search(select_pattern, soql, re.IGNORECASE | re.DOTALL)

    if not match:
        error_msg = "SOQL query must contain a SELECT ... FROM clause"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    select_clause = match.group(1)
    logger.debug("Extracted SELECT clause: %s", select_clause)

    # Split on commas and extract aliases
    fields = [field.strip() for field in select_clause.split(",") if field.strip()]
    columns = [_extract_alias(field) for field in fields]

    if not columns:
        error_msg = "SELECT clause must contain at least one field"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    logger.info("Parsed %d columns from SOQL: %s", len(columns), columns)
    return columns


def create_table_from_query(
    table_name: str,
    soql_query: str,
    db_conn: extensions.connection,
    if_not_exists: bool = True,
) -> bool:
    """Create a PostgreSQL table from SOQL SELECT columns.

    Args:
        table_name: PostgreSQL table name (e.g., 'sf_account').
        soql_query: SOQL query to extract columns from.
        db_conn: psycopg2 database connection.
        if_not_exists: If True, use CREATE TABLE IF NOT EXISTS. Default True.

    Returns:
        True if table was created, False if it already existed.

    Raises:
        ValueError: If SOQL query is malformed or has no SELECT clause.
        psycopg2.Error: For database errors.

    Example:
        >>> from sf_utils.db import get_connection, create_table_from_query
        >>> conn = get_connection()
        >>> created = create_table_from_query(
        ...     table_name="sf_account",
        ...     soql_query="SELECT Id, Name, BillingCity FROM Account",
        ...     db_conn=conn,
        ...     if_not_exists=True
        ... )
        >>> # Creates: CREATE TABLE IF NOT EXISTS sf_account (
        >>> #   id TEXT PRIMARY KEY,
        >>> #   name TEXT,
        >>> #   billingcity TEXT
        >>> # )
    """
    logger.debug(
        "create_table_from_query: table_name=%s if_not_exists=%s soql=%s",
        table_name,
        if_not_exists,
        soql_query,
    )

    # Parse columns from SOQL
    columns = _parse_select_columns(soql_query)

    # Check if Id column exists (required for PRIMARY KEY)
    if "id" not in columns:
        error_msg = "SOQL query must include Id field for primary key"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    # Build CREATE TABLE statement using psycopg2.sql for security
    # Id column is PRIMARY KEY, all others are TEXT
    column_defs = []
    for col in columns:
        if col == "id":
            column_defs.append(
                sql.SQL("{} TEXT PRIMARY KEY").format(sql.Identifier(col))
            )
        else:
            column_defs.append(
                sql.SQL("{} TEXT").format(sql.Identifier(col))
            )

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
        logger.debug("Executing CREATE TABLE for table: %s with %d columns", table_name, len(columns))
        cursor.execute(create_stmt)
        db_conn.commit()

        # Check if table was actually created (rowcount doesn't work for DDL)
        # Query pg_catalog to check if table existed before
        cursor.execute(
            sql.SQL("SELECT to_regclass({})").format(sql.Literal(table_name))
        )
        table_exists = cursor.fetchone()[0] is not None

        if table_exists:
            logger.info(
                "Table created successfully: %s with %d columns: %s",
                table_name,
                len(columns),
                columns,
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
