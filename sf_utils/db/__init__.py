"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, execute_query, get_connection
from sf_utils.db.schema import create_table_from_describe, create_table_from_query, upsert_records
from sf_utils.db.types import SALESFORCE_TYPE_TO_POSTGRES, get_postgres_type

__all__ = [
    "PostgresConfig",
    "execute_query",
    "get_connection",
    "create_table_from_describe",
    "create_table_from_query",
    "get_postgres_type",
    "SALESFORCE_TYPE_TO_POSTGRES",
    "upsert_records",
]
