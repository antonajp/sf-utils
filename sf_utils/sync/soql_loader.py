"""SOQL query loader for loading queries from .soql files."""

import logging
from pathlib import Path
from typing import Union

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
