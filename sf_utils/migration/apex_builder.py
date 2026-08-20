"""Anonymous Apex code generation for attachment migration.

Builds SOQL queries and Apex batch execution code with security validation
to prevent SOQL injection attacks.
"""

import logging
import re
from datetime import datetime

from .config import TierConfig

logger = logging.getLogger(__name__)

# Salesforce sObject naming pattern: starts with letter, alphanumeric + underscore, max 40 chars
SOBJECT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


def validate_sobject_name(sobject_name: str) -> None:
    """Validate sObject API name to prevent SOQL injection.

    Args:
        sobject_name: The sObject API name to validate.

    Raises:
        ValueError: If name doesn't match Salesforce sObject naming rules.

    Example:
        >>> validate_sobject_name("Progress_Note__c")  # OK
        >>> validate_sobject_name("Account")  # OK
        >>> validate_sobject_name("'; DROP TABLE--")  # Raises ValueError
    """
    if not SOBJECT_PATTERN.match(sobject_name):
        logger.warning("SECURITY: Rejected invalid sObject name: %s", sobject_name)
        raise ValueError(
            f"Invalid sObject name: {sobject_name}. "
            f"Must start with letter, contain only alphanumeric/underscore, max 40 chars."
        )
    logger.debug("Validated sObject name: %s", sobject_name)


def build_apex_code(
    sobject_type: str,
    tier: TierConfig,
    hour_start: datetime,
    hour_end: datetime,
) -> str:
    """Build Anonymous Apex code to execute a batch for one tier and hour window.

    Args:
        sobject_type: The sObject API name (e.g., Progress_Note__c, Account).
        tier: Tier configuration with size bounds and batch settings.
        hour_start: Start of the hour window (UTC, timezone-aware).
        hour_end: End of the hour window (UTC, timezone-aware).

    Returns:
        Anonymous Apex code string ready for execution.

    Raises:
        ValueError: If datetime objects are not timezone-aware or sObject name is invalid.

    Example:
        >>> from datetime import datetime, timezone
        >>> tier = TierConfig(
        ...     name="tier1",
        ...     min_size=0,
        ...     max_size=1000000,
        ...     batch_size=200,
        ...     batch_class="AttachmentToFilesConversionBatch"
        ... )
        >>> apex = build_apex_code(
        ...     "Progress_Note__c",
        ...     tier,
        ...     datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
        ...     datetime(2023, 6, 1, 1, 0, tzinfo=timezone.utc)
        ... )
    """
    # Security: Validate sObject name first
    validate_sobject_name(sobject_type)

    if hour_start.tzinfo is None or hour_end.tzinfo is None:
        raise ValueError("hour_start and hour_end must be timezone-aware")

    start_iso = hour_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = hour_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Security: Validate ISO 8601 format to prevent SOQL injection
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    if not re.match(iso_pattern, start_iso) or not re.match(iso_pattern, end_iso):
        raise ValueError(f"Invalid ISO 8601 format: {start_iso}, {end_iso}")

    logger.debug(
        "Building Apex for sobject=%s tier=%s hour=%s to %s",
        sobject_type,
        tier.name,
        start_iso,
        end_iso,
    )

    # Build BodyLength filter
    size_conditions = []
    if tier.min_size is not None:
        size_conditions.append(f"BodyLength >= {tier.min_size}")
    if tier.max_size is not None:
        size_conditions.append(f"BodyLength < {tier.max_size}")
    size_filter = " AND ".join(size_conditions) if size_conditions else "BodyLength >= 0"

    # Build SOQL query with parameterized sObject type
    query = (
        f"SELECT Id, Name, Body, ParentId, Description, OwnerId, CreatedDate, "
        f"CreatedById, LastModifiedById, LastModifiedDate FROM Attachment "
        f"WHERE {size_filter} AND Parent.Id IN (SELECT Id FROM {sobject_type} "
        f"WHERE CreatedDate >= {start_iso} AND CreatedDate < {end_iso})"
    )

    # Strip __c suffix for Apex constructor parameter.
    # The batch class constructor typically expects the "label" form (e.g., "Progress_Note"
    # instead of "Progress_Note__c"). This matches how the original hardcoded implementation
    # passed 'Progress_Note' to the batch class.
    sobject_label = sobject_type.replace("__c", "")

    apex_code = (
        f"String query = '{query}';\n"
        f"Id batchId = Database.executeBatch(new {tier.batch_class}(query, '{sobject_label}'), {tier.batch_size});\n"
        f"System.debug('Started batch: ' + batchId);"
    )
    logger.debug("Generated Apex code (%d chars)", len(apex_code))
    return apex_code
