"""SObject CRUD operations."""

import logging
from typing import Any, Dict, List, Optional

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError as SimpleSalesforceError

from sf_utils.client import get_client
from sf_utils.exceptions import SalesforceAPIError, SalesforceAuthError, SalesforceRateLimitError
from sf_utils.retry import RetryConfig, DEFAULT_RETRY_CONFIG, with_retry

logger = logging.getLogger(__name__)


def _handle_salesforce_exception(e: SimpleSalesforceError, context: str) -> None:
    """Convert simple-salesforce exceptions to sf_utils exceptions.

    Args:
        e: Exception from simple-salesforce.
        context: Context string for error message.

    Raises:
        SalesforceAuthError: For authentication/authorization failures.
        SalesforceRateLimitError: For rate limit errors.
        SalesforceAPIError: For other API errors.
    """
    error_str = str(e)
    status_code = getattr(e, 'status', None)

    # Check for authentication errors
    if status_code in (401, 403) or "INVALID_SESSION_ID" in error_str:
        logger.error("Authentication error during %s: %s", context, error_str)
        raise SalesforceAuthError(
            message=error_str,
            status_code=status_code
        ) from e

    # Check for rate limit errors
    if status_code == 429 or "REQUEST_LIMIT_EXCEEDED" in error_str:
        retry_after = None
        api_usage = None
        if hasattr(e, 'headers'):
            headers = e.headers or {}
            if 'Retry-After' in headers:
                try:
                    retry_after = int(headers['Retry-After'])
                except (ValueError, TypeError):
                    pass
            if 'Sforce-Limit-Info' in headers:
                api_usage = headers['Sforce-Limit-Info']

        logger.warning("Rate limit exceeded during %s: %s", context, error_str)
        raise SalesforceRateLimitError(
            message=error_str,
            status_code=status_code or 429,
            retry_after=retry_after,
            api_usage=api_usage
        ) from e

    # Generic API error
    logger.error("API error during %s: %s", context, error_str)
    raise SalesforceAPIError(
        message=error_str,
        status_code=status_code or 500
    ) from e


def get_record(
    sobject_type: str,
    record_id: str,
    fields: Optional[List[str]] = None,
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce get() accepts record_id and optional fields
            if fields:
                result = sobject.get(record_id, fields=fields)
            else:
                result = sobject.get(record_id)

            if result is None:
                raise SalesforceAPIError(
                    message=f"Failed to retrieve {sobject_type} record {record_id}",
                    status_code=500
                )

            return result

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"get_record({sobject_type})")

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
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce create() returns {'id': '...', 'success': True, 'errors': []}
            result = sobject.create(data)

            if result is None:
                raise SalesforceAPIError(
                    message=f"Failed to create {sobject_type} record",
                    status_code=500
                )

            if not result.get("success"):
                errors = result.get("errors", [])
                error_msg = "; ".join(str(e) for e in errors) if errors else "Unknown error"
                raise SalesforceAPIError(
                    message=f"Failed to create {sobject_type} record: {error_msg}",
                    status_code=400
                )

            record_id = result.get("id")
            logger.debug("Created %s record: %s", sobject_type, record_id)

            return record_id

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"create_record({sobject_type})")

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
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce update() returns HTTP status code (204 for success)
            result = sobject.update(record_id, data)

            # Result is the HTTP status code (204 = No Content = Success)
            if result is None or result >= 400:
                raise SalesforceAPIError(
                    message=f"Failed to update {sobject_type} record {record_id}",
                    status_code=result or 500
                )

            logger.debug("Updated %s record: %s", sobject_type, record_id)
            return True

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"update_record({sobject_type})")

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
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce upsert() returns:
            # - Created: {'id': '...', 'success': True, 'created': True, 'errors': []}
            # - Updated: {'id': '...', 'success': True, 'created': False, 'errors': []}
            # Or HTTP status code on some versions
            result = sobject.upsert(
                f"{external_id_field}/{external_id_value}",
                data
            )

            # Handle both dict response and HTTP status code
            if isinstance(result, int):
                # HTTP status code response (201 = created, 204 = updated)
                created = result == 201
                # For status code response, we need to fetch the record to get ID
                # This is a fallback; normally simple-salesforce returns a dict
                record_id = None
            else:
                # Dict response
                if result is None:
                    raise SalesforceAPIError(
                        message=f"Failed to upsert {sobject_type} record",
                        status_code=500
                    )

                if not result.get("success", True):
                    errors = result.get("errors", [])
                    error_msg = "; ".join(str(e) for e in errors) if errors else "Unknown error"
                    raise SalesforceAPIError(
                        message=f"Failed to upsert {sobject_type} record: {error_msg}",
                        status_code=400
                    )

                record_id = result.get("id")
                created = result.get("created", False)

            response = {
                "id": record_id,
                "created": created,
            }

            logger.debug("Upserted %s record: %s (created=%s)", sobject_type, record_id, created)
            return response

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"upsert_record({sobject_type})")

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
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce delete() returns HTTP status code (204 for success)
            result = sobject.delete(record_id)

            # Result is the HTTP status code (204 = No Content = Success)
            if result is None or result >= 400:
                raise SalesforceAPIError(
                    message=f"Failed to delete {sobject_type} record {record_id}",
                    status_code=result or 500
                )

            logger.debug("Deleted %s record: %s", sobject_type, record_id)
            return True

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"delete_record({sobject_type})")

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
    client: Optional[Salesforce] = None,
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

        try:
            # Get SObject handler from client
            sobject = getattr(client, sobject_type)

            # simple-salesforce describe() returns describe metadata dict
            result = sobject.describe()

            if result is None:
                raise SalesforceAPIError(
                    message=f"Failed to describe {sobject_type}",
                    status_code=500
                )

            return result

        except SimpleSalesforceError as e:
            _handle_salesforce_exception(e, f"describe_object({sobject_type})")

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
