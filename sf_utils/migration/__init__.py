"""Attachment to ContentDocument migration orchestration.

Executes hour-by-hour batch migrations using Anonymous Apex to convert Salesforce
Attachments to ContentDocument/ContentVersion records. Supports tiered batch sizing
based on attachment body length and cursor-based resumption on failure.

Example:
    >>> from datetime import datetime, timezone
    >>> from pathlib import Path
    >>> from sf_utils.migration import run_migration
    >>>
    >>> result = run_migration(
    ...     sobject_type="Progress_Note__c",
    ...     start_date=datetime(2023, 6, 1, tzinfo=timezone.utc),
    ...     config_path=Path("migration.properties")
    ... )
    >>> print(f"Processed {result.total_hours_processed} hours")
"""

from .apex_builder import build_apex_code, validate_sobject_name
from .config import (
    MigrationConfig,
    MigrationResult,
    TierConfig,
    load_migration_config,
)
from .cursor import get_default_cursor_path, load_cursor, save_cursor
from .orchestrator import run_migration

__all__ = [
    "run_migration",
    "build_apex_code",
    "validate_sobject_name",
    "load_migration_config",
    "save_cursor",
    "load_cursor",
    "get_default_cursor_path",
    "MigrationConfig",
    "MigrationResult",
    "TierConfig",
]
