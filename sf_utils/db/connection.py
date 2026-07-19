"""PostgreSQL connection management."""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv
import psycopg2
from psycopg2 import DatabaseError, extensions, sql

logger = logging.getLogger(__name__)


@dataclass
class PostgresConfig:
    """Configuration for PostgreSQL connection."""

    host: str
    database: str
    user: str
    password: str
    port: int = 5432
    sslmode: str = "prefer"

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        """Load configuration from environment variables.

        Expects: PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD
        Optional: PG_PORT (default 5432), PG_SSLMODE (default 'prefer')

        Returns:
            PostgresConfig instance populated from environment.

        Raises:
            ValueError: If required environment variables are missing.
        """
        load_dotenv()

        host = os.environ.get("PG_HOST")
        database = os.environ.get("PG_DATABASE")
        user = os.environ.get("PG_USER")
        password = os.environ.get("PG_PASSWORD")

        missing = []
        if not host:
            missing.append("PG_HOST")
        if not database:
            missing.append("PG_DATABASE")
        if not user:
            missing.append("PG_USER")
        if not password:
            missing.append("PG_PASSWORD")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        port_str = os.environ.get("PG_PORT", "5432")
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"PG_PORT must be an integer, got: {port_str}")

        sslmode = os.environ.get("PG_SSLMODE", "prefer")

        return cls(
            host=host,
            database=database,
            user=user,
            password=password,
            port=port,
            sslmode=sslmode,
        )


def get_connection(
    config: Optional[PostgresConfig] = None
) -> extensions.connection:
    """Get a PostgreSQL connection.

    Args:
        config: PostgresConfig instance. If None, creates from environment.

    Returns:
        psycopg2 database connection.

    Raises:
        ValueError: If required environment variables missing.
        psycopg2.OperationalError: If connection fails.

    Example:
        >>> from sf_utils.db.connection import get_connection
        >>> conn = get_connection()
        >>> cursor = conn.cursor()
        >>> cursor.execute("SELECT version()")
        >>> version = cursor.fetchone()
        >>> cursor.close()
        >>> conn.close()
    """
    if config is None:
        config = PostgresConfig.from_env()

    logger.debug(
        "Creating PostgreSQL connection to host=%s database=%s user=%s port=%d sslmode=%s",
        config.host,
        config.database,
        config.user,
        config.port,
        config.sslmode,
    )

    try:
        conn = psycopg2.connect(
            host=config.host,
            database=config.database,
            user=config.user,
            password=config.password,
            port=config.port,
            sslmode=config.sslmode,
        )
        logger.info(
            "PostgreSQL connection successful: host=%s database=%s",
            config.host,
            config.database,
        )
        return conn
    except psycopg2.OperationalError as e:
        logger.error(
            "PostgreSQL connection failed: host=%s database=%s user=%s - %s",
            config.host,
            config.database,
            config.user,
            str(e),
        )
        raise


def execute_query(
    conn: extensions.connection,
    query_template: str,
    identifiers: Optional[Dict[str, str]] = None,
    params: Optional[Tuple[Any, ...]] = None,
    fetch: bool = True,
) -> Union[List[Tuple[Any, ...]], int]:
    """Execute a parameterized SQL query safely.

    Uses psycopg2.sql.Identifier for table/column names and parameter
    substitution for values to prevent SQL injection.

    Args:
        conn: Active psycopg2 connection.
        query_template: SQL query with {name} placeholders for identifiers
            and %s for parameter values.
        identifiers: Dict mapping placeholder names to table/column names.
            These will be safely quoted as SQL identifiers.
        params: Tuple of parameter values for %s placeholders in query.
        fetch: If True, return rows (SELECT). If False, return affected count
            (INSERT/UPDATE/DELETE).

    Returns:
        List of row tuples if fetch=True, affected row count if fetch=False.

    Raises:
        DatabaseError: If query execution fails.
        ValueError: If identifiers dict keys don't match query placeholders.

    Example:
        >>> from sf_utils.db.connection import get_connection, execute_query
        >>> conn = get_connection()
        >>> # SELECT query - returns rows
        >>> rows = execute_query(
        ...     conn,
        ...     "SELECT {col1}, {col2} FROM {table} WHERE {col3} = %s",
        ...     identifiers={"col1": "id", "col2": "name", "table": "accounts", "col3": "status"},
        ...     params=("active",),
        ...     fetch=True
        ... )
        >>> # UPDATE query - returns affected count
        >>> count = execute_query(
        ...     conn,
        ...     "UPDATE {table} SET {col} = %s WHERE id = %s",
        ...     identifiers={"table": "accounts", "col": "status"},
        ...     params=("inactive", 123),
        ...     fetch=False
        ... )
        >>> conn.close()
    """
    if identifiers is None:
        identifiers = {}
    if params is None:
        params = ()

    logger.debug(
        "Preparing query with %d identifier(s) and %d parameter(s)",
        len(identifiers),
        len(params),
    )

    try:
        # Build safe SQL with properly quoted identifiers
        # Uses psycopg2.sql.Identifier to prevent SQL injection on table/column names
        query = sql.SQL(query_template).format(
            **{k: sql.Identifier(v) for k, v in identifiers.items()}
        )

        with conn.cursor() as cursor:
            # Log query structure but NEVER log parameter values (may contain sensitive data)
            # Use cursor for as_string() to avoid connection mock issues in tests
            try:
                logger.debug("Executing query: %s", query.as_string(cursor))
            except (TypeError, AttributeError):
                # as_string may fail with mock connections in tests
                logger.debug("Executing parameterized query with %d identifier(s)", len(identifiers))

            # Parameter substitution via psycopg2 prevents SQL injection on values
            cursor.execute(query, params)

            if fetch:
                rows = cursor.fetchall()
                logger.debug("Query returned %d row(s)", len(rows))
                return rows
            else:
                count = cursor.rowcount
                logger.debug("Query affected %d row(s)", count)
                return count

    except DatabaseError as e:
        logger.error("Query execution failed: %s", str(e))
        raise
    except KeyError as e:
        error_msg = f"Invalid identifier placeholder: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg) from e
