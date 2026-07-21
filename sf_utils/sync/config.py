"""YAML configuration loader for sync jobs.

Provides data structures and functions for loading YAML-based sync job configurations.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union

import yaml

logger = logging.getLogger(__name__)


def _resolve_config_path(config_path: Union[str, Path]) -> Path:
    """Resolve config path with fallback to projects/ directory.

    Implements precedence order:
    1. Explicit path (if exists or is absolute)
    2. projects/{filename} (if exists)
    3. Original path (caller handles FileNotFoundError)

    Args:
        config_path: Path to config file (relative or absolute).

    Returns:
        Resolved Path object. May not exist - caller must handle FileNotFoundError.

    Raises:
        ValueError: If path is a symlink (security protection).
    """
    path = Path(config_path)

    # If path exists or is absolute, use it as-is
    if path.exists():
        # SECURITY: Reject symlinks to prevent reading arbitrary files
        if path.is_symlink():
            resolved = path.resolve()
            logger.warning(
                "SECURITY: Config path is a symlink: %s -> %s. Rejecting.",
                path, resolved
            )
            raise ValueError(f"Symlinks are not allowed for security reasons: {path}")
        logger.debug("Config path exists, using: %s", path)
        return path

    if path.is_absolute():
        logger.debug("Config path is absolute, using as-is: %s", path)
        return path

    # Try projects/ directory fallback
    projects_path = Path("projects") / path.name
    if projects_path.exists():
        # SECURITY: Reject symlinks in fallback path
        if projects_path.is_symlink():
            resolved = projects_path.resolve()
            logger.warning(
                "SECURITY: Config path is a symlink: %s -> %s. Rejecting.",
                projects_path, resolved
            )
            raise ValueError(f"Symlinks are not allowed for security reasons: {projects_path}")
        logger.debug(
            "Config not found at %s, using fallback: %s", path, projects_path
        )
        return projects_path

    # Return original path (caller handles FileNotFoundError)
    logger.debug("Config not found at %s or %s, returning original", path, projects_path)
    return path


@dataclass
class SyncJobConfig:
    """Configuration for a Salesforce sync job.

    Attributes:
        object_name: Salesforce object name (e.g., 'Account', 'Contact').
        soql_file: Path to .soql file containing the query template.
        date_field: Date/datetime field for incremental sync tracking.
        chunk_size: Time interval for chunking queries. Defaults to 'daily'.
            Supported values: 'hourly', 'daily', 'weekly', 'monthly', 'none'.
        mode: API mode selection. Defaults to 'auto'.
            Supported values: 'auto', 'rest', 'bulk'.
        enabled: Whether this sync job is enabled. Defaults to True.

    Example:
        >>> config = SyncJobConfig(
        ...     object_name="Account",
        ...     soql_file="soql/account.soql",
        ...     date_field="LastModifiedDate",
        ...     chunk_size="daily",
        ...     mode="auto",
        ...     enabled=True
        ... )
    """

    object_name: str
    soql_file: str
    date_field: str
    chunk_size: str = "daily"
    mode: str = "auto"
    enabled: bool = True

    def __post_init__(self):
        """Validate configuration values after initialization."""
        # Validate required fields are non-empty
        if not self.object_name or not isinstance(self.object_name, str):
            raise ValueError("object_name must be a non-empty string")

        if not self.soql_file or not isinstance(self.soql_file, str):
            raise ValueError("soql_file must be a non-empty string")

        if not self.date_field or not isinstance(self.date_field, str):
            raise ValueError("date_field must be a non-empty string")

        # Validate chunk_size
        valid_chunk_sizes = {"hourly", "daily", "weekly", "monthly", "none"}
        if self.chunk_size not in valid_chunk_sizes:
            raise ValueError(
                f"chunk_size must be one of {valid_chunk_sizes}, got: {self.chunk_size}"
            )

        # Validate mode
        valid_modes = {"auto", "rest", "bulk"}
        if self.mode not in valid_modes:
            raise ValueError(
                f"mode must be one of {valid_modes}, got: {self.mode}"
            )

        # Validate enabled is boolean
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")

        logger.debug(
            "SyncJobConfig validated: object_name=%s soql_file=%s date_field=%s chunk_size=%s mode=%s enabled=%s",
            self.object_name,
            self.soql_file,
            self.date_field,
            self.chunk_size,
            self.mode,
            self.enabled,
        )


def load_sync_config(
    config_path: Union[str, Path],
    include_disabled: bool = False,
) -> List[SyncJobConfig]:
    """Load sync job configurations from YAML file.

    Reads a YAML configuration file containing sync job definitions and returns
    a list of validated SyncJobConfig objects. By default, filters out disabled
    sync jobs unless explicitly requested.

    Args:
        config_path: Path to YAML configuration file (string or pathlib.Path).
        include_disabled: If True, include disabled sync jobs. Defaults to False.

    Returns:
        List of validated SyncJobConfig objects.

    Raises:
        FileNotFoundError: If config file does not exist with helpful message.
        yaml.YAMLError: If YAML syntax is invalid.
        ValueError: If required fields are missing or values are invalid.

    Example:
        >>> # Load enabled syncs only (default)
        >>> configs = load_sync_config("config/syncs.yaml")
        >>> for config in configs:
        ...     print(f"Sync: {config.object_name} - {config.mode}")
        Sync: Account - auto
        Sync: Contact - rest
        >>>
        >>> # Load all syncs including disabled
        >>> all_configs = load_sync_config("config/syncs.yaml", include_disabled=True)

    Example YAML file:
        ```yaml
        syncs:
          - object_name: Account
            soql_file: soql/account.soql
            date_field: LastModifiedDate
            chunk_size: daily
            mode: auto
            enabled: true

          - object_name: Contact
            soql_file: soql/contact.soql
            date_field: LastModifiedDate
            chunk_size: hourly
            mode: rest
            enabled: false
        ```

    Config Path Resolution:
        The config_path is resolved with fallback precedence:
        1. Explicit path (if exists or is absolute)
        2. projects/{filename} (if exists)
        3. Original path (raises FileNotFoundError)

        This allows placing sync_config.yaml in the gitignored projects/
        directory for user-specific configurations.

    Security Notes:
        - Uses yaml.safe_load() to prevent arbitrary code execution
        - Validates config_path to prevent path traversal attacks
        - Warns if credential-like values detected in config
    """
    # Resolve config path with fallback to projects/ directory
    original_path = Path(config_path)
    path = _resolve_config_path(config_path)

    logger.debug("Loading sync configuration from: %s", path)
    logger.debug("Original path requested: %s", original_path)
    logger.debug("Include disabled syncs: %s", include_disabled)

    # Validate path to prevent path traversal
    try:
        # Resolve to absolute path to check for suspicious patterns
        resolved_path = path.resolve()
        logger.debug("Resolved config path: %s", resolved_path)

        # SECURITY: Block path traversal attempts
        if ".." in path.parts or ".." in original_path.parts:
            logger.error("SECURITY: Path contains '..' component, rejecting: %s", path)
            raise ValueError(
                f"Path traversal detected: {config_path}\n"
                f"Config paths cannot contain '..' components for security."
            )

    except (OSError, RuntimeError) as e:
        logger.error("Invalid config path: %s - %s", path, e)
        raise ValueError(f"Invalid config path: {path}") from e

    # Check file exists
    if not path.exists():
        logger.error("Config file not found: %s", path)
        # Build error message showing locations checked
        projects_path = Path("projects") / original_path.name
        if original_path.is_absolute():
            # Absolute path was provided - only checked one location
            error_msg = (
                f"Sync configuration file not found: {path}\n"
                f"Please create a YAML config file with sync job definitions."
            )
        else:
            # Relative path - both locations were checked
            # _resolve_config_path checks: original first, then projects/ fallback
            error_msg = (
                f"Sync configuration file not found.\n"
                f"Checked locations:\n"
                f"  1. {original_path} (original path)\n"
                f"  2. projects/{original_path.name} (fallback)\n"
                f"Please create a YAML config file in one of these locations."
            )
        raise FileNotFoundError(error_msg)

    # Check file is not a directory
    if path.is_dir():
        logger.error("Config path is a directory, not a file: %s", path)
        raise ValueError(f"Config path is a directory, not a file: {path}")

    # Read and parse YAML file
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            logger.debug("Read config file (%d bytes)", len(content))

            # Security: Check for credential-like values in config
            _check_for_credentials(content, path)

            # Parse YAML using safe_load (prevents code execution)
            config_data = yaml.safe_load(content)

    except yaml.YAMLError as e:
        logger.error("Invalid YAML syntax in config file %s: %s", path, e)
        raise yaml.YAMLError(
            f"Invalid YAML syntax in {path}: {e}"
        ) from e
    except OSError as e:
        logger.error("Error reading config file %s: %s", path, e)
        raise OSError(f"Error reading config file {path}: {e}") from e

    # Validate structure
    if not config_data:
        logger.error("Config file is empty: %s", path)
        raise ValueError(f"Config file is empty: {path}")

    if not isinstance(config_data, dict):
        logger.error("Config file root must be a mapping (dict), got: %s", type(config_data).__name__)
        raise ValueError(
            f"Config file root must be a YAML mapping (dict), got: {type(config_data).__name__}"
        )

    if "syncs" not in config_data:
        logger.error("Config file missing 'syncs' key: %s", path)
        raise ValueError(
            f"Config file missing required 'syncs' key: {path}\n"
            f"Expected format:\n"
            f"syncs:\n"
            f"  - object_name: Account\n"
            f"    soql_file: soql/account.soql\n"
            f"    date_field: LastModifiedDate"
        )

    syncs_data = config_data["syncs"]

    if not isinstance(syncs_data, list):
        logger.error("'syncs' must be a list, got: %s", type(syncs_data).__name__)
        raise ValueError(f"'syncs' must be a list, got: {type(syncs_data).__name__}")

    if not syncs_data:
        logger.warning("Config file contains no sync jobs: %s", path)
        return []

    # Parse each sync job
    sync_configs = []
    for idx, sync_dict in enumerate(syncs_data):
        if not isinstance(sync_dict, dict):
            logger.error("Sync job %d must be a mapping (dict), got: %s", idx, type(sync_dict).__name__)
            raise ValueError(
                f"Sync job at index {idx} must be a YAML mapping (dict), "
                f"got: {type(sync_dict).__name__}"
            )

        # SECURITY: Only log safe fields (avoid credential leakage)
        logger.debug(
            "Processing sync job %d: object_name=%s mode=%s enabled=%s",
            idx,
            sync_dict.get("object_name"),
            sync_dict.get("mode"),
            sync_dict.get("enabled"),
        )

        # Validate required fields
        required_fields = {"object_name", "soql_file", "date_field"}
        missing_fields = required_fields - sync_dict.keys()

        if missing_fields:
            logger.error(
                "Sync job %d missing required fields: %s",
                idx,
                missing_fields,
            )
            raise ValueError(
                f"Sync job at index {idx} missing required fields: {missing_fields}\n"
                f"Required fields: object_name, soql_file, date_field\n"
                f"Got: {list(sync_dict.keys())}"
            )

        # Apply defaults for optional fields
        sync_dict.setdefault("chunk_size", "daily")
        sync_dict.setdefault("mode", "auto")
        sync_dict.setdefault("enabled", True)

        # Create SyncJobConfig (validation happens in __post_init__)
        try:
            config = SyncJobConfig(**sync_dict)
            sync_configs.append(config)
            logger.debug("Loaded sync job %d: %s", idx, config.object_name)

        except (ValueError, TypeError) as e:
            logger.error("Invalid sync job %d: %s", idx, e)
            raise ValueError(f"Invalid sync job at index {idx}: {e}") from e

    # Filter disabled syncs if requested
    if not include_disabled:
        original_count = len(sync_configs)
        sync_configs = [c for c in sync_configs if c.enabled]
        disabled_count = original_count - len(sync_configs)

        if disabled_count > 0:
            logger.info(
                "Filtered out %d disabled sync job(s), %d enabled",
                disabled_count,
                len(sync_configs),
            )

    logger.info(
        "Successfully loaded %d sync job(s) from %s",
        len(sync_configs),
        path,
    )

    return sync_configs


def _check_for_credentials(content: str, path: Path) -> None:
    """Check for credential-like values in config content and warn.

    Args:
        content: Raw YAML content string.
        path: Path to config file (for logging).
    """
    # Patterns that might indicate hardcoded credentials
    credential_patterns = [
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "client_secret",
    ]

    content_lower = content.lower()

    for pattern in credential_patterns:
        if pattern in content_lower:
            logger.warning(
                "Config file %s contains potential credential field: '%s'. "
                "Credentials should be stored in environment variables, not config files.",
                path,
                pattern,
            )
            # Only warn once, don't spam logs
            break
