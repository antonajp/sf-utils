"""PostgreSQL connection management."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
import psycopg2
from psycopg2 import extensions

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
