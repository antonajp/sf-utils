"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, execute_query, get_connection
from sf_utils.db.parser import (
    ColumnSpec,
    _extract_alias,
    _parse_select_columns,
    _parse_select_columns_with_types,
    _sanitize_column_name,
)
from sf_utils.db.schema import create_table_from_describe, create_table_from_query, upsert_records
from sf_utils.db.types import (
    AGGREGATE_FUNCTION_TYPES,
    ALLOWED_POSTGRES_TYPES,
    SALESFORCE_TYPE_TO_POSTGRES,
    get_postgres_type,
    infer_aggregate_type,
    validate_postgres_type,
)

__all__ = [
    # Connection
    "PostgresConfig",
    "execute_query",
    "get_connection",
    # Schema
    "create_table_from_describe",
    "create_table_from_query",
    "upsert_records",
    # Parser
    "ColumnSpec",
    "_extract_alias",
    "_parse_select_columns",
    "_parse_select_columns_with_types",
    "_sanitize_column_name",
    # Types
    "AGGREGATE_FUNCTION_TYPES",
    "ALLOWED_POSTGRES_TYPES",
    "SALESFORCE_TYPE_TO_POSTGRES",
    "get_postgres_type",
    "infer_aggregate_type",
    "validate_postgres_type",
]
