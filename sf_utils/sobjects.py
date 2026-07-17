"""SObject CRUD operations."""

import logging
from typing import Any, Dict, List, Optional

from SalesforcePy.sfdc import Client

from sf_utils.client import get_client

logger = logging.getLogger(__name__)


def get_record(
    sobject_type: str,
    record_id: str,
    fields: Optional[List[str]] = None,
    client: Optional[Client] = None,
) -> Dict[str, Any]:
    """Retrieve a single record by ID.

    Args:
        sobject_type: Salesforce object type (e.g., 'Account', 'Contact').
        record_id: 15 or 18-character Salesforce record ID.
        fields: Optional list of fields to retrieve. If None, retrieves all.
        client: Authenticated Salesforce client.

    Returns:
        Record dictionary.

    Raises:
        Exception: If retrieval fails.
    """
    if client is None:
        client = get_client()

    logger.debug("Getting %s record: %s", sobject_type, record_id)

    sobjects = client.sobjects(sobject_type, record_id=record_id)

    params = {}
    if fields:
        params["fields"] = ",".join(fields)

    response = sobjects.query(**params) if params else sobjects.query()

    if response is None:
        raise Exception(f"Failed to retrieve {sobject_type} record {record_id}")

    body, status = response if isinstance(response, tuple) else (response, 200)

    if status >= 400:
        raise Exception(f"Get record failed with status {status}: {body}")

    return body


def create_record(
    sobject_type: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
) -> str:
    """Create a new record.

    Args:
        sobject_type: Salesforce object type.
        data: Field values for the new record.
        client: Authenticated Salesforce client.

    Returns:
        The ID of the created record.

    Raises:
        Exception: If creation fails.
    """
    if client is None:
        client = get_client()

    logger.debug("Creating %s record with data: %s", sobject_type, list(data.keys()))

    sobjects = client.sobjects(sobject_type)
    response = sobjects.insert(data)

    if response is None:
        raise Exception(f"Failed to create {sobject_type} record")

    body, status = response if isinstance(response, tuple) else (response, 201)

    if status >= 400:
        raise Exception(f"Create record failed with status {status}: {body}")

    record_id = body.get("id")
    logger.debug("Created %s record: %s", sobject_type, record_id)

    return record_id


def update_record(
    sobject_type: str,
    record_id: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
) -> bool:
    """Update an existing record.

    Args:
        sobject_type: Salesforce object type.
        record_id: ID of the record to update.
        data: Field values to update.
        client: Authenticated Salesforce client.

    Returns:
        True if update succeeded.

    Raises:
        Exception: If update fails.
    """
    if client is None:
        client = get_client()

    logger.debug("Updating %s record %s with: %s", sobject_type, record_id, list(data.keys()))

    sobjects = client.sobjects(sobject_type, record_id=record_id)
    response = sobjects.update(data)

    if response is None:
        raise Exception(f"Failed to update {sobject_type} record {record_id}")

    body, status = response if isinstance(response, tuple) else (response, 204)

    if status >= 400:
        raise Exception(f"Update record failed with status {status}: {body}")

    logger.debug("Updated %s record: %s", sobject_type, record_id)
    return True


def upsert_record(
    sobject_type: str,
    external_id_field: str,
    external_id_value: str,
    data: Dict[str, Any],
    client: Optional[Client] = None,
) -> Dict[str, Any]:
    """Upsert a record using an external ID field.

    Args:
        sobject_type: Salesforce object type.
        external_id_field: API name of the external ID field.
        external_id_value: Value of the external ID.
        data: Field values to insert/update.
        client: Authenticated Salesforce client.

    Returns:
        Dict with 'id' and 'created' (bool) keys.

    Raises:
        Exception: If upsert fails.
    """
    if client is None:
        client = get_client()

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
        raise Exception(f"Failed to upsert {sobject_type} record")

    body, status = response if isinstance(response, tuple) else (response, 200)

    if status >= 400:
        raise Exception(f"Upsert record failed with status {status}: {body}")

    created = status == 201
    result = {
        "id": body.get("id"),
        "created": created,
    }

    logger.debug("Upserted %s record: %s (created=%s)", sobject_type, result["id"], created)
    return result


def delete_record(
    sobject_type: str,
    record_id: str,
    client: Optional[Client] = None,
) -> bool:
    """Delete a record.

    Args:
        sobject_type: Salesforce object type.
        record_id: ID of the record to delete.
        client: Authenticated Salesforce client.

    Returns:
        True if deletion succeeded.

    Raises:
        Exception: If deletion fails.
    """
    if client is None:
        client = get_client()

    logger.debug("Deleting %s record: %s", sobject_type, record_id)

    sobjects = client.sobjects(sobject_type, record_id=record_id)
    response = sobjects.delete()

    if response is None:
        raise Exception(f"Failed to delete {sobject_type} record {record_id}")

    body, status = response if isinstance(response, tuple) else (response, 204)

    if status >= 400:
        raise Exception(f"Delete record failed with status {status}: {body}")

    logger.debug("Deleted %s record: %s", sobject_type, record_id)
    return True


def describe_object(
    sobject_type: str,
    client: Optional[Client] = None,
) -> Dict[str, Any]:
    """Get metadata description of an SObject type.

    Args:
        sobject_type: Salesforce object type.
        client: Authenticated Salesforce client.

    Returns:
        Object describe result with fields, relationships, etc.

    Raises:
        Exception: If describe fails.
    """
    if client is None:
        client = get_client()

    logger.debug("Describing %s", sobject_type)

    sobjects = client.sobjects(sobject_type)
    response = sobjects.describe()

    if response is None:
        raise Exception(f"Failed to describe {sobject_type}")

    body, status = response if isinstance(response, tuple) else (response, 200)

    if status >= 400:
        raise Exception(f"Describe failed with status {status}: {body}")

    return body
