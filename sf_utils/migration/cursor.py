"""Cursor persistence for migration resumption.

Handles saving and loading migration state to JSON files for recovery
after failures or interruptions.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_default_cursor_path(sobject_type: str) -> Path:
    """Get the default cursor path for an sObject type.

    The sObject name is preserved in its original case to avoid cursor file
    collisions between objects with different casing (though Salesforce
    object names are case-insensitive, we preserve user input).

    Args:
        sobject_type: The sObject API name (e.g., Progress_Note__c).

    Returns:
        Path object for the cursor file.

    Example:
        >>> get_default_cursor_path("Progress_Note__c")
        Path('.Progress_Note__c_migration_cursor.json')
    """
    return Path(f".{sobject_type}_migration_cursor.json")


def save_cursor(cursor_path: Path, cursor_data: Dict[str, Any]) -> None:
    """Persist cursor state to JSON file for recovery.

    Args:
        cursor_path: Path to write the cursor JSON file.
        cursor_data: Dictionary with cursor state (last_completed_hour, etc.).
    """
    logger.info("Saving cursor to: %s", cursor_path)
    with open(cursor_path, "w", encoding="utf-8") as f:
        json.dump(cursor_data, f, indent=2, default=str)


def load_cursor(cursor_path: Path) -> Dict[str, Any]:
    """Load cursor state from JSON file for resumption.

    Args:
        cursor_path: Path to the cursor JSON file.

    Returns:
        Dictionary with cursor state, or empty dict if file doesn't exist.
    """
    if not cursor_path.exists():
        logger.info("No existing cursor file found at: %s", cursor_path)
        return {}
    with open(cursor_path, "r", encoding="utf-8") as f:
        cursor_data = json.load(f)
    logger.info(
        "Loaded cursor: last_completed=%s total_hours=%d",
        cursor_data.get("last_completed_hour"),
        cursor_data.get("total_hours_processed", 0),
    )
    return cursor_data
