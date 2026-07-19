"""SOQL query loader for loading queries from .soql files."""

import logging
import os
import re
import stat
from datetime import date, datetime
from pathlib import Path
from string import Template
from typing import Dict, Optional, Union

logger = logging.getLogger(__name__)


def load_soql(file_path: Union[str, Path]) -> str:
    """Load SOQL query content from a file.

    Args:
        file_path: Path to .soql file (string or pathlib.Path).

    Returns:
        SOQL query string (with whitespace trimmed).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If file is empty or not a .soql file.

    Example:
        >>> soql = load_soql("queries/accounts.soql")
        >>> print(soql)
        SELECT Id, Name FROM Account
    """
    # Convert to Path for cross-platform compatibility
    path = Path(file_path)

    logger.debug("Loading SOQL query from file: %s", path)

    # Validate file extension
    if path.suffix.lower() != ".soql":
        logger.error("Invalid file extension: %s (expected .soql)", path.suffix)
        raise ValueError(f"File must have .soql extension, got: {path.suffix}")

    # Check file exists
    if not path.exists():
        logger.error("SOQL file not found: %s", path)
        raise FileNotFoundError(f"SOQL file not found: {path}")

    # Check file is not a directory
    if path.is_dir():
        logger.error("Path is a directory, not a file: %s", path)
        raise ValueError(f"Path is a directory, not a file: {path}")

    # Read and validate content
    content = path.read_text(encoding="utf-8").strip()

    if not content:
        logger.error("SOQL file is empty: %s", path)
        raise ValueError(f"SOQL file is empty: {path}")

    logger.info("Successfully loaded SOQL query from: %s", path)
    logger.debug("Query length: %d characters", len(content))

    return content


def render_soql(
    template_content: str,
    params: Dict[str, Union[str, int, float, bool, datetime, date, None]],
    *,
    strict: bool = True,
) -> str:
    """Render SOQL template by substituting variables with provided parameters.

    Args:
        template_content: SOQL template with ${variable} placeholders
        params: Dictionary mapping variable names to values
        strict: If True (default), raise KeyError for missing variables.
                If False, leave unmatched placeholders unchanged.

    Returns:
        Rendered SOQL query string

    Raises:
        KeyError: If strict=True and required variables are missing
        TypeError: If parameter value is not a supported type

    Example:
        >>> from datetime import datetime
        >>> template = "SELECT Id FROM Account WHERE CreatedDate >= ${start_date}"
        >>> params = {"start_date": datetime(2024, 1, 1)}
        >>> soql = render_soql(template, params)
        >>> print(soql)
        SELECT Id FROM Account WHERE CreatedDate >= 2024-01-01T00:00:00Z
    """
    logger.debug("Rendering SOQL template with %d parameters", len(params))
    logger.debug("Strict mode: %s", strict)

    # Convert parameters to SOQL-compatible string representations
    converted_params = {}
    for key, value in params.items():
        logger.debug("Processing parameter: %s = %s (%s)", key, value, type(value).__name__)

        if value is None:
            converted_params[key] = "null"
        elif isinstance(value, bool):
            # Must check bool before int (bool is subclass of int in Python)
            converted_params[key] = "true" if value else "false"
        elif isinstance(value, datetime):
            # ISO8601 format with Z suffix for UTC
            converted_params[key] = value.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif isinstance(value, date):
            # Date only format
            converted_params[key] = value.strftime("%Y-%m-%d")
        elif isinstance(value, (int, float, str)):
            converted_params[key] = str(value)
        else:
            logger.error("Unsupported parameter type: %s for key: %s", type(value).__name__, key)
            raise TypeError(
                f"Unsupported parameter type: {type(value).__name__} for key '{key}'. "
                f"Supported types: str, int, float, bool, datetime, date, None"
            )

    # Use string.Template for substitution
    template = Template(template_content)

    if strict:
        # substitute() raises KeyError for missing variables
        try:
            result = template.substitute(converted_params)
            logger.info("Successfully rendered SOQL template (strict mode)")
            logger.debug("Rendered query length: %d characters", len(result))
            return result
        except KeyError as e:
            logger.error("Missing required parameter: %s", e)
            raise
    else:
        # safe_substitute() leaves unmatched placeholders unchanged
        result = template.safe_substitute(converted_params)
        logger.info("Successfully rendered SOQL template (non-strict mode)")
        logger.debug("Rendered query length: %d characters", len(result))
        return result


# Dangerous SQL keywords that should not appear in SOQL queries
DANGEROUS_KEYWORDS = re.compile(
    r'\b(DROP|DELETE|TRUNCATE|INSERT|UPDATE)\b',
    re.IGNORECASE
)


def validate_soql(
    soql: str,
    date_field: str,
    file_path: Optional[str] = None
) -> None:
    """Validate SOQL query for syntax and security issues.

    Performs static validation checks:
    - Ensures query contains SELECT and FROM keywords
    - Verifies date field exists in SELECT clause
    - Checks for dangerous SQL keywords (DROP, DELETE, etc.)
    - Optionally warns if file has world-writable permissions

    Args:
        soql: SOQL query string to validate.
        date_field: Date field name that must exist in SELECT clause.
        file_path: Optional file path for permission check.

    Raises:
        ValueError: If validation fails with specific error message.

    Example:
        >>> validate_soql("SELECT Id, CreatedDate FROM Account", "CreatedDate")
        >>> # Passes silently
        >>>
        >>> validate_soql("SELECT Id FROM Account", "CreatedDate")
        ... # Raises: ValueError: Date field 'CreatedDate' not found in SELECT clause
    """
    logger.debug("Validating SOQL query (date_field: %s)", date_field)

    # Check for dangerous keywords FIRST (security check)
    dangerous_match = DANGEROUS_KEYWORDS.search(soql)
    if dangerous_match:
        keyword = dangerous_match.group(1).upper()
        logger.error("Dangerous SQL keyword found: %s", keyword)
        raise ValueError(
            f"Dangerous SQL keyword '{keyword}' not allowed in SOQL queries"
        )

    # Check for SELECT keyword
    if not re.search(r'\bSELECT\b', soql, re.IGNORECASE):
        logger.error("SOQL query missing SELECT keyword")
        raise ValueError("SOQL query must contain SELECT keyword")

    # Check for FROM keyword
    if not re.search(r'\bFROM\b', soql, re.IGNORECASE):
        logger.error("SOQL query missing FROM keyword")
        raise ValueError("SOQL query must contain FROM keyword")

    # Extract SELECT clause to check for date field
    # Match from SELECT to FROM (non-greedy, case-insensitive)
    select_match = re.search(
        r'\bSELECT\s+(.*?)\s+FROM\b',
        soql,
        re.IGNORECASE | re.DOTALL
    )

    if not select_match:
        logger.error("Could not parse SELECT clause from SOQL query")
        raise ValueError("Could not parse SELECT clause from SOQL query")

    select_clause = select_match.group(1)
    logger.debug("Parsed SELECT clause: %s", select_clause.replace('\n', ' '))

    # Check if date field is in the SELECT clause
    # Use word boundary to match exact field names (case-insensitive)
    date_field_pattern = re.compile(
        r'\b' + re.escape(date_field) + r'\b',
        re.IGNORECASE
    )

    if not date_field_pattern.search(select_clause):
        logger.error("Date field '%s' not found in SELECT clause", date_field)
        raise ValueError(
            f"Date field '{date_field}' not found in SELECT clause"
        )

    logger.debug("Date field '%s' found in SELECT clause", date_field)

    # Check file permissions if file_path provided
    if file_path:
        try:
            file_stat = os.stat(file_path)
            if file_stat.st_mode & stat.S_IWOTH:
                logger.warning(
                    "SOQL file has world-writable permissions: %s",
                    file_path
                )
        except OSError as e:
            # Log but don't fail - permission check is advisory only
            logger.debug("Could not check file permissions for %s: %s", file_path, e)

    logger.debug("SOQL validation passed")

