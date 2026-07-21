"""Salesforce to PostgreSQL type mapping utilities."""

import logging
import re
from typing import Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


# Whitelist of allowed base PostgreSQL types for type_mapper validation
# These are the only types that can be returned by custom type mappers
ALLOWED_POSTGRES_TYPES: Set[str] = {
    # Standard types from our mapping
    "TEXT",
    "INTEGER",
    "BIGINT",
    "NUMERIC",
    "BOOLEAN",
    "DATE",
    "TIME",
    "TIMESTAMP WITH TIME ZONE",
    "JSONB",
    # Additional common PostgreSQL types users might need
    "VARCHAR",
    "CHAR",
    "SMALLINT",
    "REAL",
    "DOUBLE PRECISION",
    "DECIMAL",
    "SERIAL",
    "BIGSERIAL",
    "UUID",
    "JSON",
    "BYTEA",
    "INTERVAL",
    "TIMESTAMPTZ",
    "TIMETZ",
}


# Salesforce type to PostgreSQL type mapping
# Based on Salesforce field types: https://developer.salesforce.com/docs/atlas.en-us.api.meta/api/field_types.htm
SALESFORCE_TYPE_TO_POSTGRES: Dict[str, str] = {
    # Text-based types
    "id": "TEXT",
    "reference": "TEXT",
    "string": "TEXT",
    "textarea": "TEXT",
    "picklist": "TEXT",
    "multipicklist": "TEXT",
    "email": "TEXT",
    "url": "TEXT",
    "phone": "TEXT",
    "encryptedstring": "TEXT",
    "combobox": "TEXT",
    "base64": "TEXT",
    "datacategorygroupreference": "TEXT",
    # Numeric types
    "int": "INTEGER",
    "double": "NUMERIC",
    "currency": "NUMERIC",
    "percent": "NUMERIC",
    "long": "BIGINT",
    # Boolean type
    "boolean": "BOOLEAN",
    # Date/time types
    "date": "DATE",
    "datetime": "TIMESTAMP WITH TIME ZONE",
    "time": "TIME",
    # Complex types stored as JSONB
    "location": "JSONB",
    "address": "JSONB",
    # Other types
    "anytype": "TEXT",
}


# Aggregate function to PostgreSQL type mapping
# Used when inferring types for aggregate query results
AGGREGATE_FUNCTION_TYPES: Dict[str, str] = {
    "count": "BIGINT",  # Use BIGINT to prevent overflow for large counts
    "sum": "NUMERIC",  # Use NUMERIC for arbitrary precision
    "avg": "NUMERIC",  # Use NUMERIC for arbitrary precision
    "min": "TEXT",  # Default to TEXT; inherit field type when available
    "max": "TEXT",  # Default to TEXT; inherit field type when available
}


def validate_postgres_type(pg_type: str) -> str:
    """Validate that a PostgreSQL type string is safe to use in SQL.

    Validates against a whitelist of allowed PostgreSQL types to prevent
    SQL injection when using custom type mappers.

    Args:
        pg_type: PostgreSQL type string to validate.

    Returns:
        The validated PostgreSQL type string (uppercase, normalized).

    Raises:
        ValueError: If pg_type is not a valid/allowed PostgreSQL type.

    Example:
        >>> validate_postgres_type("TEXT")
        'TEXT'
        >>> validate_postgres_type("varchar(255)")
        'VARCHAR(255)'
        >>> validate_postgres_type("TEXT); DROP TABLE users; --")
        ValueError: Invalid PostgreSQL type...
    """
    if not pg_type or not isinstance(pg_type, str):
        raise ValueError(f"Invalid PostgreSQL type: must be a non-empty string, got {type(pg_type)}")

    # Normalize to uppercase for comparison
    pg_type_upper = pg_type.upper().strip()

    # Check for SQL injection indicators
    dangerous_patterns = [";", "--", "/*", "*/", "'", '"', "\\"]
    for pattern in dangerous_patterns:
        if pattern in pg_type_upper:
            raise ValueError(
                f"Invalid PostgreSQL type '{pg_type}': contains dangerous character '{pattern}'"
            )

    # Extract base type (handle parameterized types like VARCHAR(255))
    base_type = pg_type_upper.split("(")[0].strip()

    if base_type not in ALLOWED_POSTGRES_TYPES:
        raise ValueError(
            f"Invalid PostgreSQL type '{pg_type}': base type '{base_type}' not in whitelist. "
            f"Allowed types: {sorted(ALLOWED_POSTGRES_TYPES)}"
        )

    # If parameterized (e.g., VARCHAR(255) or NUMERIC(10,2)), validate parameters
    if "(" in pg_type_upper:
        # Match TYPE(digits) or TYPE(digits,digits) with optional whitespace
        if not re.match(r"^[A-Z\s]+\(\s*\d+(\s*,\s*\d+)?\s*\)$", pg_type_upper):
            raise ValueError(
                f"Invalid PostgreSQL type '{pg_type}': malformed type parameters. "
                f"Expected format like VARCHAR(255) or NUMERIC(10,2)"
            )

    logger.debug("Validated PostgreSQL type: '%s' -> '%s'", pg_type, pg_type_upper)
    return pg_type_upper


def get_postgres_type(
    sf_type: str,
    type_mapper: Optional[Callable[[str], Optional[str]]] = None,
) -> str:
    """Map a Salesforce field type to PostgreSQL type.

    Looks up the Salesforce type in SALESFORCE_TYPE_TO_POSTGRES mapping.
    Unknown types default to TEXT with a WARNING log.

    Args:
        sf_type: Salesforce field type (e.g., 'string', 'double', 'datetime').
        type_mapper: Optional custom type mapper function. If provided and returns
            a non-None value, that value is validated and used instead of the
            default mapping. The returned type must be in ALLOWED_POSTGRES_TYPES.

    Returns:
        PostgreSQL type string (e.g., 'TEXT', 'NUMERIC', 'TIMESTAMP WITH TIME ZONE').

    Raises:
        ValueError: If custom type_mapper returns an invalid PostgreSQL type.

    Example:
        >>> get_postgres_type("string")
        'TEXT'
        >>> get_postgres_type("double")
        'NUMERIC'
        >>> get_postgres_type("datetime")
        'TIMESTAMP WITH TIME ZONE'
        >>> get_postgres_type("unknown_type")  # Returns 'TEXT' with WARNING
        'TEXT'
    """
    sf_type_lower = sf_type.lower()
    logger.debug("Mapping Salesforce type '%s' to PostgreSQL type", sf_type)

    # Try custom type mapper first if provided
    if type_mapper is not None:
        custom_type = type_mapper(sf_type_lower)
        if custom_type is not None:
            # SECURITY: Validate custom type to prevent SQL injection
            try:
                validated_type = validate_postgres_type(custom_type)
                logger.debug(
                    "Custom type mapper returned '%s' (validated as '%s') for Salesforce type '%s'",
                    custom_type,
                    validated_type,
                    sf_type,
                )
                return validated_type
            except ValueError as e:
                logger.error(
                    "Custom type mapper returned invalid PostgreSQL type: %s", str(e)
                )
                raise ValueError(
                    f"Custom type_mapper returned invalid PostgreSQL type for '{sf_type}': {e}"
                ) from e

    # Use default mapping
    pg_type = SALESFORCE_TYPE_TO_POSTGRES.get(sf_type_lower)

    if pg_type is not None:
        logger.debug(
            "Mapped Salesforce type '%s' -> PostgreSQL type '%s'",
            sf_type,
            pg_type,
        )
        return pg_type

    # Unknown type - default to TEXT with warning
    logger.warning(
        "Unknown Salesforce type '%s', defaulting to TEXT",
        sf_type,
    )
    return "TEXT"


def infer_aggregate_type(expression: str) -> Optional[str]:
    """Infer PostgreSQL type from SOQL aggregate function expression.

    Detects aggregate functions (COUNT, SUM, AVG, MIN, MAX) and returns
    the appropriate PostgreSQL type. Uses whitelist validation for security.

    Args:
        expression: SOQL expression (e.g., "COUNT(Id)", "SUM(Amount)").

    Returns:
        PostgreSQL type string if aggregate detected, None otherwise.

    Example:
        >>> infer_aggregate_type("COUNT(Id)")
        'BIGINT'
        >>> infer_aggregate_type("SUM(Amount)")
        'NUMERIC'
        >>> infer_aggregate_type("AVG(Revenue)")
        'NUMERIC'
        >>> infer_aggregate_type("Name")
        None
    """
    # Prevent ReDoS by limiting expression length
    if len(expression) > 1000:
        logger.debug(
            "Expression exceeds max length, skipping aggregate type inference: %s...",
            expression[:100],
        )
        return None

    expression = expression.strip()
    logger.debug("Inferring aggregate type for expression: %s", expression)

    # Pattern: FUNCTION_NAME(...)
    pattern = r"^(\w+)\s*\("
    match = re.match(pattern, expression, re.IGNORECASE)

    if match:
        func_name = match.group(1).lower()
        pg_type = AGGREGATE_FUNCTION_TYPES.get(func_name)

        if pg_type:
            logger.debug(
                "Inferred aggregate type: expression=%s, function=%s, type=%s",
                expression,
                func_name,
                pg_type,
            )
            return pg_type
        else:
            logger.debug(
                "Function '%s' not in AGGREGATE_FUNCTION_TYPES: %s",
                func_name,
                expression,
            )
            return None

    logger.debug("No aggregate function detected in expression: %s", expression)
    return None
