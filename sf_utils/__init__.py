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
from sf_utils.exceptions import (
    SalesforceError,
    SalesforceRateLimitError,
    SalesforceAuthError,
    SalesforceAPIError,
)
from sf_utils.retry import (
    RetryConfig,
    APIUsageInfo,
    with_retry,
    raise_for_status,
    DEFAULT_RETRY_CONFIG,
    BATCH_RETRY_CONFIG,
    NO_RETRY_CONFIG,
)
from sf_utils.sync import load_soql, query_chunked, ChunkInterval

__version__ = "0.2.0"

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
    # Exceptions
    "SalesforceError",
    "SalesforceRateLimitError",
    "SalesforceAuthError",
    "SalesforceAPIError",
    # Retry logic
    "RetryConfig",
    "APIUsageInfo",
    "with_retry",
    "raise_for_status",
    "DEFAULT_RETRY_CONFIG",
    "BATCH_RETRY_CONFIG",
    "NO_RETRY_CONFIG",
    # Sync utilities
    "load_soql",
    "query_chunked",
    "ChunkInterval",
]
