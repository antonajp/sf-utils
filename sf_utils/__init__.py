"""Salesforce utility functions using simple-salesforce.

This library provides a high-level Python interface to Salesforce REST APIs
with automatic retry, rate limit handling, and JWT Bearer OAuth support.

Authentication Methods:
- Password OAuth flow (legacy, for non-MFA accounts)
- JWT Bearer OAuth flow (recommended, for MFA-enabled accounts)

The authentication method is auto-detected from environment variables:
- If SF_PRIVATE_KEY_PATH is set → JWT Bearer flow
- Otherwise → Password flow
"""

from sf_utils.client import get_client, get_client_from_token, SalesforceConfig, SalesforceJWTConfig
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
from sf_utils.sync import load_soql, query_chunked, ChunkInterval, SyncJobConfig, load_sync_config

__version__ = "0.3.0"

__all__ = [
    "get_client",
    "get_client_from_token",
    "SalesforceConfig",
    "SalesforceJWTConfig",
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
    "SyncJobConfig",
    "load_sync_config",
]
