"""SOQL query parsing utilities for schema inference."""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Whitelist of valid aggregate function names (security)
VALID_AGGREGATE_FUNCTIONS = {"count", "sum", "avg", "min", "max"}

# Maximum expression length to prevent ReDoS attacks
MAX_EXPRESSION_LENGTH = 1000

# Maximum SOQL query length to prevent ReDoS attacks on SELECT parsing
MAX_SOQL_LENGTH = 100000  # 100KB - well above typical SOQL limits


@dataclass
class ColumnSpec:
    """Column specification extracted from SOQL SELECT clause.

    Attributes:
        name: Sanitized column name for PostgreSQL.
        alias: Original alias if specified in SOQL (e.g., "total" from "COUNT(Id) AS total").
        aggregate_function: Aggregate function name if present (e.g., "count", "sum").
        original_expression: Original SOQL expression before sanitization.
    """

    name: str
    alias: Optional[str] = None
    aggregate_function: Optional[str] = None
    original_expression: str = ""


def _sanitize_column_name(name: str) -> str:
    """Sanitize column name for PostgreSQL.

    Converts to lowercase and replaces spaces/special chars with underscores.

    Args:
        name: Column name to sanitize.

    Returns:
        Sanitized column name safe for PostgreSQL.

    Example:
        >>> _sanitize_column_name("Billing City")
        'billing_city'
        >>> _sanitize_column_name("Account.Name")
        'account_name'
    """
    logger.debug("Sanitizing column name: %s", name)
    # Convert to lowercase
    sanitized = name.lower()
    # Replace special chars (spaces, dots, etc.) with underscores
    # Preserve existing underscores (important for Salesforce __c, __r suffixes)
    sanitized = re.sub(r"[^a-z0-9_]", "_", sanitized)
    # Remove leading/trailing underscores only
    sanitized = sanitized.strip("_")
    logger.debug("Sanitized column name: %s -> %s", name, sanitized)
    return sanitized


def _extract_alias(field: str) -> str:
    """Extract alias from SOQL field expression.

    Handles "Field AS Alias" patterns and relationship traversals.

    Args:
        field: SOQL field expression (may contain AS alias).

    Returns:
        Column name (alias if present, otherwise sanitized field name).

    Example:
        >>> _extract_alias("Account.Name AS AccountName")
        'accountname'
        >>> _extract_alias("Account.Name")
        'account_name'
        >>> _extract_alias("Id")
        'id'
    """
    logger.debug("Extracting alias from field: %s", field)
    field = field.strip()

    # Check for AS alias pattern (case-insensitive)
    as_pattern = r"\s+AS\s+(\w+)\s*$"
    as_match = re.search(as_pattern, field, re.IGNORECASE)
    if as_match:
        alias = as_match.group(1)
        logger.debug("Found AS alias: %s -> %s", field, alias)
        return _sanitize_column_name(alias)

    # No alias, sanitize the field name (handles dots for relationships)
    return _sanitize_column_name(field)


def _parse_select_columns(soql: str) -> List[str]:
    """Parse SELECT columns from SOQL query.

    Extracts column names from SELECT clause, handling:
    - Simple fields: Id, Name
    - Relationship traversals: Account.Name
    - Aliases: Field AS Alias

    Args:
        soql: SOQL query string.

    Returns:
        List of sanitized column names.

    Raises:
        ValueError: If SOQL has no SELECT clause, is malformed, or exceeds length limit.

    Example:
        >>> _parse_select_columns("SELECT Id, Name FROM Account")
        ['id', 'name']
        >>> _parse_select_columns("SELECT Account.Name AS AccountName FROM Contact")
        ['accountname']
    """
    # ReDoS protection: limit SOQL length before regex parsing
    if len(soql) > MAX_SOQL_LENGTH:
        error_msg = f"SOQL query exceeds maximum length ({MAX_SOQL_LENGTH} chars)"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    logger.debug("Parsing SELECT columns from SOQL: %s", soql)

    # Find SELECT clause using regex
    select_pattern = r"SELECT\s+(.+?)\s+FROM"
    match = re.search(select_pattern, soql, re.IGNORECASE | re.DOTALL)

    if not match:
        error_msg = "SOQL query must contain a SELECT ... FROM clause"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    select_clause = match.group(1)
    logger.debug("Extracted SELECT clause: %s", select_clause)

    # Split on commas and extract aliases
    fields = [field.strip() for field in select_clause.split(",") if field.strip()]
    columns = [_extract_alias(field) for field in fields]

    if not columns:
        error_msg = "SELECT clause must contain at least one field"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    logger.info("Parsed %d columns from SOQL: %s", len(columns), columns)
    return columns


def _detect_aggregate_function(expression: str) -> Optional[str]:
    """Detect aggregate function in SOQL expression.

    Uses whitelist validation for security (prevents injection attacks).
    Limits expression length to prevent ReDoS.

    Args:
        expression: SOQL field expression (e.g., "COUNT(Id)", "SUM(Amount)").

    Returns:
        Aggregate function name (lowercase) if detected, None otherwise.

    Example:
        >>> _detect_aggregate_function("COUNT(Id)")
        'count'
        >>> _detect_aggregate_function("SUM(Amount)")
        'sum'
        >>> _detect_aggregate_function("Name")
        None
    """
    # Prevent ReDoS by limiting expression length
    if len(expression) > MAX_EXPRESSION_LENGTH:
        logger.warning(
            "Expression exceeds max length (%d), skipping aggregate detection: %s...",
            MAX_EXPRESSION_LENGTH,
            expression[:100],
        )
        return None

    expression = expression.strip()
    logger.debug("Detecting aggregate function in expression: %s", expression)

    # Pattern: FUNCTION_NAME(...)
    # Whitelist validation: function name must be in VALID_AGGREGATE_FUNCTIONS
    pattern = r"^(\w+)\s*\("
    match = re.match(pattern, expression, re.IGNORECASE)

    if match:
        func_name = match.group(1).lower()
        if func_name in VALID_AGGREGATE_FUNCTIONS:
            logger.debug("Detected aggregate function: %s in expression: %s", func_name, expression)
            return func_name
        else:
            logger.debug(
                "Function '%s' not in whitelist %s, skipping: %s",
                func_name,
                VALID_AGGREGATE_FUNCTIONS,
                expression,
            )
            return None

    logger.debug("No aggregate function detected in expression: %s", expression)
    return None


def _parse_select_columns_with_types(soql: str) -> List[ColumnSpec]:
    """Parse SELECT columns with aggregate function detection.

    Extracts column specifications from SELECT clause, including:
    - Sanitized column name
    - Original alias (if specified)
    - Aggregate function (if detected)
    - Original expression

    Args:
        soql: SOQL query string.

    Returns:
        List of ColumnSpec objects with metadata.

    Raises:
        ValueError: If SOQL has no SELECT clause, is malformed, or exceeds length limit.

    Example:
        >>> specs = _parse_select_columns_with_types(
        ...     "SELECT Id, COUNT(Id) AS total, SUM(Amount) FROM Account"
        ... )
        >>> specs[0].name
        'id'
        >>> specs[1].name
        'total'
        >>> specs[1].aggregate_function
        'count'
    """
    # ReDoS protection: limit SOQL length before regex parsing
    if len(soql) > MAX_SOQL_LENGTH:
        error_msg = f"SOQL query exceeds maximum length ({MAX_SOQL_LENGTH} chars)"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    logger.debug("Parsing SELECT columns with type inference: %s", soql)

    # Find SELECT clause using regex
    select_pattern = r"SELECT\s+(.+?)\s+FROM"
    match = re.search(select_pattern, soql, re.IGNORECASE | re.DOTALL)

    if not match:
        error_msg = "SOQL query must contain a SELECT ... FROM clause"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    select_clause = match.group(1)
    logger.debug("Extracted SELECT clause: %s", select_clause)

    # Split on commas
    fields = [field.strip() for field in select_clause.split(",") if field.strip()]

    if not fields:
        error_msg = "SELECT clause must contain at least one field"
        logger.error("Invalid SOQL: %s", error_msg)
        raise ValueError(error_msg)

    column_specs = []

    for field in fields:
        original_expression = field

        # Check for AS alias
        as_pattern = r"\s+AS\s+(\w+)\s*$"
        as_match = re.search(as_pattern, field, re.IGNORECASE)

        if as_match:
            alias = as_match.group(1)
            # Extract expression before AS keyword
            expression = re.sub(as_pattern, "", field, flags=re.IGNORECASE).strip()
        else:
            alias = None
            expression = field

        # Detect aggregate function
        aggregate_func = _detect_aggregate_function(expression)

        # Determine column name
        if alias:
            # Use alias if provided
            column_name = _sanitize_column_name(alias)
        elif aggregate_func:
            # For non-aliased aggregates, Salesforce uses exprN pattern
            # We'll use a placeholder here - caller should handle expr numbering
            column_name = f"{aggregate_func}_expr"
        else:
            # Regular field - sanitize
            column_name = _sanitize_column_name(expression)

        spec = ColumnSpec(
            name=column_name,
            alias=alias,
            aggregate_function=aggregate_func,
            original_expression=original_expression,
        )

        column_specs.append(spec)

        logger.debug(
            "Parsed column spec: name=%s, alias=%s, aggregate=%s, expr=%s",
            spec.name,
            spec.alias,
            spec.aggregate_function,
            spec.original_expression,
        )

    logger.info("Parsed %d column specs from SOQL", len(column_specs))
    return column_specs
