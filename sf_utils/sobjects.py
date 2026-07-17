"""SObject CRUD operations."""

import logging
from typing import Any, Dict, List, Optional

from SalesforcePy.sfdc import Client

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError
from sf_utils.retry import raise_for_status, RetryConfig, DEFAULT_RETRY_CONFIG, with_retry

logger = logging.getLogger(__name__)


def get_record(
    sobject_type: str,
    record_id: str,
    fields: Optional[List[str]] = None,
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> Dict[str, Any]:
    """Retrieve a single record by ID.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type (e.g., 'Account', 'Contact').
        record_id: 15 or 18-character Salesforce record ID.
        fields: Optional list of fields to retrieve. If None, retrieves all.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        Record dictionary.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If retrieval fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _get_record_impl():
        logger.debug("Getting %s record: %s", sobject_type, record_id)

        sobjects = client.sobjects(sobject_type, record_id=record_id)

        params = {}
        if fields:
            params["fields"] = ",".join(fields)

        response = sobjects.query(**params) if params else sobjects.query()

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to retrieve {sobject_type} record {record_id}",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 200)

        raise_for_status(body, status)

        return body

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_get_record_impl)()
    else:
        logger.debug("Retry disabled")
        return _get_record_impl()


def create_record(
    sobject_type: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> str:
    """Create a new record.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type.
        data: Field values for the new record.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        The ID of the created record.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If creation fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _create_record_impl():
        logger.debug("Creating %s record with data: %s", sobject_type, list(data.keys()))

        sobjects = client.sobjects(sobject_type)
        response = sobjects.insert(data)

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to create {sobject_type} record",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 201)

        raise_for_status(body, status)

        record_id = body.get("id")
        logger.debug("Created %s record: %s", sobject_type, record_id)

        return record_id

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_create_record_impl)()
    else:
        logger.debug("Retry disabled")
        return _create_record_impl()


def update_record(
    sobject_type: str,
    record_id: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> bool:
    """Update an existing record.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type.
        record_id: ID of the record to update.
        data: Field values to update.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        True if update succeeded.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If update fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _update_record_impl():
        logger.debug("Updating %s record %s with: %s", sobject_type, record_id, list(data.keys()))

        sobjects = client.sobjects(sobject_type, record_id=record_id)
        response = sobjects.update(data)

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to update {sobject_type} record {record_id}",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 204)

        raise_for_status(body, status)

        logger.debug("Updated %s record: %s", sobject_type, record_id)
        return True

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_update_record_impl)()
    else:
        logger.debug("Retry disabled")
        return _update_record_impl()


def upsert_record(
    sobject_type: str,
    external_id_field: str,
    external_id_value: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> Dict[str, Any]:
    """Upsert a record using an external ID field.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type.
        external_id_field: API name of the external ID field.
        external_id_value: Value of the external ID.
        data: Field values to insert/update.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        Dict with 'id' and 'created' (bool) keys.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If upsert fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _upsert_record_impl():
        logger.debug(
            "Upserting %s with %s=%s",
            sobject_type,
            external_id_field,
            external_id_value
        )

        sobjects = client.sobjects(
            sobject_type,
            external_id_field=external_id_field,
            record_id=external_id_value,
        )
        response = sobjects.upsert(data)

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to upsert {sobject_type} record",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 200)

        raise_for_status(body, status)

        created = status == 201
        result = {
            "id": body.get("id"),
            "created": created,
        }

        logger.debug("Upserted %s record: %s (created=%s)", sobject_type, result["id"], created)
        return result

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_upsert_record_impl)()
    else:
        logger.debug("Retry disabled")
        return _upsert_record_impl()


def delete_record(
    sobject_type: str,
    record_id: str,
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> bool:
    """Delete a record.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type.
        record_id: ID of the record to delete.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        True if deletion succeeded.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If deletion fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _delete_record_impl():
        logger.debug("Deleting %s record: %s", sobject_type, record_id)

        sobjects = client.sobjects(sobject_type, record_id=record_id)
        response = sobjects.delete()

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to delete {sobject_type} record {record_id}",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 204)

        raise_for_status(body, status)

        logger.debug("Deleted %s record: %s", sobject_type, record_id)
        return True

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_delete_record_impl)()
    else:
        logger.debug("Retry disabled")
        return _delete_record_impl()


def describe_object(
    sobject_type: str,
    client: Optional[Client] = None,
    retry_config: Optional[RetryConfig] = DEFAULT_RETRY_CONFIG,
) -> Dict[str, Any]:
    """Get metadata description of an SObject type.

    Automatically retries on rate limits with exponential backoff.

    Args:
        sobject_type: Salesforce object type.
        client: Authenticated Salesforce client.
        retry_config: Retry configuration. Defaults to DEFAULT_RETRY_CONFIG.
            Pass NO_RETRY_CONFIG to disable retries.

    Returns:
        Object describe result with fields, relationships, etc.

    Raises:
        SalesforceAuthError: If authentication fails (not retried).
        SalesforceRateLimitError: If rate limit is exceeded and max retries exhausted.
        SalesforceAPIError: If describe fails.
    """
    # Initialize client outside inner function
    if client is None:
        client = get_client()

    def _describe_object_impl():
        logger.debug("Describing %s", sobject_type)

        sobjects = client.sobjects(sobject_type)
        response = sobjects.describe()

        if response is None:
            raise SalesforceAPIError(
                message=f"Failed to describe {sobject_type}",
                status_code=500
            )

        body, status = response if isinstance(response, tuple) else (response, 200)

        raise_for_status(body, status)

        return body

    # Apply retry logic if configured
    if retry_config and retry_config.max_retries > 0:
        logger.debug(
            "Retry enabled: max_retries=%d, initial_backoff=%.1fs",
            retry_config.max_retries,
            retry_config.initial_backoff
        )
        return with_retry(retry_config)(_describe_object_impl)()
    else:
        logger.debug("Retry disabled")
        return _describe_object_impl()
