"""Migration configuration and data structures.

Defines tier configurations, migration settings, and result structures for
the attachment-to-files migration process.
"""

import logging
import os
import re
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_allowed_batch_classes() -> frozenset:
    """Get the set of allowed batch class names.

    Can be extended via SF_CUSTOM_BATCH_CLASSES environment variable
    (comma-separated list of additional class names).

    Returns:
        Frozen set of allowed Apex batch class names.
    """
    base = {
        "AttachmentToFilesConversionBatch",
        "SingleAttachmentToFilesConversionBatch",
    }
    custom = os.environ.get("SF_CUSTOM_BATCH_CLASSES", "")
    if custom:
        for cls_name in custom.split(","):
            cls_name = cls_name.strip()
            if cls_name and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", cls_name):
                base.add(cls_name)
                logger.info("Added custom batch class to allowlist: %s", cls_name)
    return frozenset(base)


# Lazy evaluation of allowed batch classes
ALLOWED_BATCH_CLASSES = _get_allowed_batch_classes()


@dataclass
class TierConfig:
    """Configuration for a single migration tier.

    Attributes:
        name: Tier identifier (e.g., "tier1").
        min_size: Minimum BodyLength in bytes (inclusive). None means no lower bound.
        max_size: Maximum BodyLength in bytes (exclusive). None means no upper bound.
        batch_size: Number of records per batch execution.
        batch_class: Name of the Apex batch class to execute.
    """

    name: str
    min_size: Optional[int]
    max_size: Optional[int]
    batch_size: int
    batch_class: str

    def __post_init__(self) -> None:
        """Validate tier configuration."""
        if self.batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {self.batch_size}")
        if not self.batch_class:
            raise ValueError("batch_class must be a non-empty string")

        # Security: Validate batch_class is in whitelist
        if self.batch_class not in ALLOWED_BATCH_CLASSES:
            raise ValueError(
                f"batch_class must be one of {sorted(ALLOWED_BATCH_CLASSES)}, "
                f"got '{self.batch_class}'"
            )

        # Security: Validate batch_class contains only alphanumeric/underscore
        if not re.match(r"^[A-Za-z0-9_]+$", self.batch_class):
            raise ValueError(
                f"batch_class contains invalid characters: {self.batch_class}"
            )

        # Validate size bounds
        if self.min_size is not None and self.min_size < 0:
            raise ValueError(f"min_size must be >= 0, got {self.min_size}")
        if self.max_size is not None and self.max_size < 0:
            raise ValueError(f"max_size must be >= 0, got {self.max_size}")
        if (
            self.min_size is not None
            and self.max_size is not None
            and self.min_size >= self.max_size
        ):
            raise ValueError(
                f"min_size ({self.min_size}) must be < max_size ({self.max_size})"
            )

        logger.debug(
            "TierConfig: %s min=%s max=%s batch=%d class=%s",
            self.name,
            self.min_size,
            self.max_size,
            self.batch_size,
            self.batch_class,
        )


@dataclass
class MigrationConfig:
    """Configuration for the attachment migration process.

    Attributes:
        tiers: List of TierConfig objects defining batch processing tiers.
        poll_interval_seconds: Seconds between AsyncApexJob status polls.
    """

    tiers: List[TierConfig]
    poll_interval_seconds: int = 10

    def __post_init__(self) -> None:
        """Validate migration configuration."""
        if not self.tiers:
            raise ValueError("At least one tier must be configured")
        if self.poll_interval_seconds < 1:
            raise ValueError(
                f"poll_interval_seconds must be >= 1, got {self.poll_interval_seconds}"
            )
        logger.debug(
            "MigrationConfig: %d tiers, poll_interval=%ds",
            len(self.tiers),
            self.poll_interval_seconds,
        )


@dataclass
class MigrationResult:
    """Result of a migration run.

    Attributes:
        total_hours_processed: Number of hour windows processed.
        total_batches_executed: Total batch jobs executed across all tiers.
        start_time: Migration start timestamp (UTC).
        end_time: Migration end timestamp (UTC).
        status: Final status ('completed', 'failed', 'interrupted').
        error_message: Error details if status is 'failed' or 'interrupted'.
    """

    total_hours_processed: int
    total_batches_executed: int
    start_time: datetime
    end_time: datetime
    status: str  # 'completed', 'failed', 'interrupted'
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate result status."""
        valid_statuses = {"completed", "failed", "interrupted"}
        if self.status not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}, got {self.status}")


def load_migration_config(config_path: Path) -> MigrationConfig:
    """Load migration configuration from a properties file.

    Args:
        config_path: Path to the migration.properties file.

    Returns:
        MigrationConfig with tier configurations and polling settings.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If required properties are missing or invalid.

    Example:
        >>> config = load_migration_config(Path("migration.properties"))
        >>> print(f"Tiers: {len(config.tiers)}")
    """
    logger.info("Loading migration config from: %s", config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Migration config file not found: {config_path}")

    parser = ConfigParser()
    with open(config_path, "r", encoding="utf-8") as f:
        content = "[migration]\n" + f.read()
    parser.read_string(content)

    config_dict = dict(parser["migration"])
    logger.debug("Parsed config: %d entries", len(config_dict))

    poll_interval = int(config_dict.get("poll_interval_seconds", "10"))

    # Discover tier names
    tier_names = {
        k.split(".")[0] for k in config_dict if "." in k and k.startswith("tier")
    }
    logger.debug("Discovered tiers: %s", sorted(tier_names))

    tiers: List[TierConfig] = []
    for tier_name in sorted(tier_names):
        prefix = f"{tier_name}."
        min_size_str = config_dict.get(f"{prefix}min_size")
        max_size_str = config_dict.get(f"{prefix}max_size")
        batch_size_str = config_dict.get(f"{prefix}batch_size")
        batch_class = config_dict.get(f"{prefix}batch_class")

        if batch_size_str is None:
            raise ValueError(f"Missing required property: {prefix}batch_size")
        if batch_class is None:
            raise ValueError(f"Missing required property: {prefix}batch_class")

        # Handle max_size=-1 as "no upper bound"
        max_size_val = int(max_size_str) if max_size_str else None
        if max_size_val is not None and max_size_val < 0:
            max_size_val = None

        tiers.append(
            TierConfig(
                name=tier_name,
                min_size=int(min_size_str) if min_size_str else None,
                max_size=max_size_val,
                batch_size=int(batch_size_str),
                batch_class=batch_class.strip(),
            )
        )

    config = MigrationConfig(tiers=tiers, poll_interval_seconds=poll_interval)
    logger.info("Loaded migration config with %d tiers", len(tiers))
    return config
