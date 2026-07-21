"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, execute_query, get_connection
from sf_utils.db.schema import create_table_from_describe, create_table_from_query, upsert_records
from sf_utils.db.types import (
    ALLOWED_POSTGRES_TYPES,
    SALESFORCE_TYPE_TO_POSTGRES,
    get_postgres_type,
    validate_postgres_type,
)

__all__ = [
    "ALLOWED_POSTGRES_TYPES",
    "PostgresConfig",
    "SALESFORCE_TYPE_TO_POSTGRES",
    "create_table_from_describe",
    "create_table_from_query",
    "execute_query",
    "get_connection",
    "get_postgres_type",
    "upsert_records",
    "validate_postgres_type",
]
