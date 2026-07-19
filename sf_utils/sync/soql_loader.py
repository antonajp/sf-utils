"""SOQL query loader for loading queries from .soql files."""

import logging
from datetime import date, datetime
from pathlib import Path
from string import Template
from typing import Dict, Union

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
