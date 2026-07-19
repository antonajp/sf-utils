"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, execute_query, get_connection
from sf_utils.db.schema import create_table_from_query, upsert_records

__all__ = [
    "PostgresConfig",
    "execute_query",
    "get_connection",
    "create_table_from_query",
    "upsert_records",
]
