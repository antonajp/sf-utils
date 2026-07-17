"""Salesforce utility functions using SalesforcePy."""

from sf_utils.client import get_client, SalesforceConfig
from sf_utils.query import query, query_all
from sf_utils.sobjects import (
    get_record,
    create_record,
    update_record,
    upsert_record,
    delete_record,
    describe_object,
)

__version__ = "0.1.0"

__all__ = [
    "get_client",
    "SalesforceConfig",
    "query",
    "query_all",
    "get_record",
    "create_record",
    "update_record",
    "upsert_record",
    "delete_record",
    "describe_object",
]
