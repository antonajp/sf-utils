"""PostgreSQL database utilities for Salesforce data sync."""

from sf_utils.db.connection import PostgresConfig, get_connection

__all__ = [
    "PostgresConfig",
    "get_connection",
]
