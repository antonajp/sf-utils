"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, get_connection
from sf_utils.db.schema import create_table_from_query

__all__ = [
    "PostgresConfig",
    "get_connection",
    "create_table_from_query",
]
