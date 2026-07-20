"""Cross-platform CLI for Salesforce sync operations.

This module provides the sf-sync command-line interface for executing
Salesforce data synchronization jobs using Click.
"""

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click

from sf_utils.client import get_client
from sf_utils.sync import SyncMode, sync
from sf_utils.sync.config import SyncJobConfig, load_sync_config
from sf_utils.sync.rest_sync import SyncResult
from sf_utils.sync.soql_loader import load_soql

logger = logging.getLogger(__name__)


def _configure_logging(verbose: bool) -> None:
    """Configure logging level based on verbose flag.

    Args:
        verbose: If True, enable DEBUG logging. Otherwise, use INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.debug("Logging configured: level=%s", logging.getLevelName(level))


def _validate_arguments(object_name: Optional[str], sync_all: bool) -> None:
    """Validate mutually exclusive arguments.

    Args:
        object_name: Single object name to sync.
        sync_all: Flag to sync all enabled objects.

    Raises:
        click.UsageError: If both or neither argument is provided.
    """
    if object_name and sync_all:
        logger.error("Cannot specify both OBJECT_NAME and --all")
        raise click.UsageError("Cannot specify both OBJECT_NAME and --all")

    if not object_name and not sync_all:
        logger.error("Must specify either OBJECT_NAME or --all")
        raise click.UsageError("Must specify either OBJECT_NAME or --all")

    logger.debug("Arguments validated: object_name=%s sync_all=%s", object_name, sync_all)


def _load_sync_job_config(
    config_path: Path,
    object_name: str,
) -> SyncJobConfig:
    """Load a specific sync job configuration from YAML config file.

    Args:
        config_path: Path to YAML configuration file.
        object_name: Salesforce object name to find in config.

    Returns:
        SyncJobConfig for the specified object.

    Raises:
        click.ClickException: If object not found or config is invalid.
    """
    logger.debug("Loading sync config for object: %s from %s", object_name, config_path)

    try:
        # Load all configs (including disabled)
        configs = load_sync_config(config_path, include_disabled=True)
        logger.debug("Loaded %d sync job(s) from config", len(configs))

    except FileNotFoundError as e:
        logger.error("Config file not found: %s", e)
        raise click.ClickException(str(e)) from e
    except (ValueError, Exception) as e:
        logger.error("Failed to load config: %s", e)
        raise click.ClickException(f"Failed to load config: {e}") from e

    # Find matching object
    for config in configs:
        if config.object_name == object_name:
            logger.debug("Found sync config for %s: %s", object_name, config)

            # Warn if disabled
            if not config.enabled:
                logger.warning("Sync job for %s is disabled in config", object_name)
                click.echo(
                    f"WARNING: Sync job for {object_name} is disabled in config",
                    err=True,
                )

            return config

    # Object not found in config
    logger.error("Object %s not found in config file %s", object_name, config_path)
    available_objects = [c.object_name for c in configs]
    raise click.ClickException(
        f"Object '{object_name}' not found in config file: {config_path}\n"
        f"Available objects: {', '.join(available_objects)}"
    )


def _execute_sync(
    job_config: SyncJobConfig,
    mode: str,
    dry_run: bool,
) -> SyncResult:
    """Execute a single sync job.

    Args:
        job_config: Sync job configuration.
        mode: API mode selection ('rest', 'bulk', 'auto').
        dry_run: If True, preview without executing.

    Returns:
        SyncResult with sync statistics.

    Raises:
        click.ClickException: If sync fails.
    """
    object_name = job_config.object_name
    logger.debug(
        "Executing sync: object=%s mode=%s dry_run=%s",
        object_name,
        mode,
        dry_run,
    )

    # Dry run - just preview
    if dry_run:
        logger.info("DRY RUN: Would sync %s using mode=%s", object_name, mode)
        click.echo("DRY RUN - Preview only (no changes will be made)")
        click.echo(f"Object: {object_name}")
        click.echo(f"Mode: {mode}")
        click.echo(f"SOQL file: {job_config.soql_file}")
        click.echo(f"Date field: {job_config.date_field}")
        click.echo(f"Chunk size: {job_config.chunk_size}")
        click.echo()
        click.echo("Run without --dry-run to execute sync")

        # Return empty result for dry run
        now = datetime.now(timezone.utc)
        return SyncResult(
            object_name=object_name,
            records_fetched=0,
            records_inserted=0,
            records_updated=0,
            sync_mode=mode,
            start_timestamp=now,
            end_timestamp=now,
            date_field=job_config.date_field,
        )

    # Create Salesforce client (auto-detects JWT vs password auth)
    try:
        logger.debug("Creating Salesforce client (auto-detecting auth method)")
        client = get_client()
    except ValueError as e:
        logger.error("Missing Salesforce credentials: %s", e)
        raise click.ClickException(
            f"Missing Salesforce credentials: {e}\n"
            f"For JWT auth: SF_USERNAME, SF_CLIENT_ID, SF_PRIVATE_KEY_PATH\n"
            f"For password auth: SF_USERNAME, SF_PASSWORD, SF_CLIENT_ID, SF_CLIENT_SECRET"
        ) from e
    except Exception as e:
        logger.error("Failed to create Salesforce client: %s", e)
        raise click.ClickException(f"Failed to authenticate with Salesforce: {e}") from e

    # Load SOQL query from file
    try:
        logger.debug("Loading SOQL from file: %s", job_config.soql_file)
        soql_path = Path(job_config.soql_file)

        if not soql_path.exists():
            logger.error("SOQL file not found: %s", soql_path)
            raise click.ClickException(
                f"SOQL file not found: {soql_path}\n"
                f"Expected path: {soql_path.resolve()}"
            )

        # Load SOQL template
        soql = load_soql(soql_path)
        logger.debug("Loaded SOQL query (%d characters)", len(soql))

    except Exception as e:
        logger.error("Failed to load SOQL file: %s", e)
        raise click.ClickException(f"Failed to load SOQL file: {e}") from e

    # Convert mode string to SyncMode enum
    try:
        sync_mode = SyncMode(mode)
    except ValueError:
        logger.error("Invalid mode: %s", mode)
        raise click.ClickException(f"Invalid mode: {mode}")

    # Execute sync
    try:
        logger.info(
            "Starting sync: object=%s mode=%s",
            object_name,
            sync_mode.value,
        )

        start_time = time.time()

        result = sync(
            soql=soql,
            object_name=object_name,
            mode=sync_mode,
            date_field=job_config.date_field,
            client=client,
        )

        duration = time.time() - start_time

        logger.info(
            "Sync completed: object=%s records=%d duration=%.1fs",
            object_name,
            result.records_fetched,
            duration,
        )

        # Store duration in result for output
        result._duration = duration  # Store for output formatting

        return result

    except Exception as e:
        logger.error("Sync failed for %s: %s", object_name, e)
        raise click.ClickException(f"Failed to sync {object_name}: {e}") from e


def _print_sync_summary(object_name: str, result: SyncResult, dry_run: bool) -> None:
    """Print sync summary to stdout.

    Args:
        object_name: Salesforce object name.
        result: Sync result statistics.
        dry_run: Whether this was a dry run.
    """
    if dry_run:
        return  # Already printed dry run message in _execute_sync

    click.echo("\nSync Summary")
    click.echo("============")
    click.echo(f"Object: {object_name}")
    click.echo(f"Records: {result.records_fetched:,}")

    # Get duration if stored
    duration = getattr(result, "_duration", None)
    if duration is not None:
        click.echo(f"Duration: {duration:.1f}s")

    click.echo(f"Mode: {result.sync_mode}")
    click.echo("Status: SUCCESS")


@click.group()
def cli() -> None:
    """Salesforce sync CLI.

    Sync Salesforce data to local PostgreSQL database using REST or Bulk API.
    """
    pass


@cli.command("sync")
@click.argument("object_name", required=False)
@click.option(
    "--all",
    "sync_all",
    is_flag=True,
    help="Sync all enabled objects from config file",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview sync without executing (no changes will be made)",
)
@click.option(
    "--mode",
    type=click.Choice(["rest", "bulk", "auto"], case_sensitive=False),
    default="auto",
    help="API mode selection: rest (REST API), bulk (Bulk API 2.0), auto (auto-detect). Defaults to auto.",
)
@click.option(
    "--config",
    type=click.Path(path_type=Path),
    default="sync_config.yaml",
    help="Path to YAML configuration file. Defaults to sync_config.yaml.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable debug logging for detailed output",
)
def sync_cmd(
    object_name: Optional[str],
    sync_all: bool,
    dry_run: bool,
    mode: str,
    config: Path,
    verbose: bool,
) -> None:
    """Sync Salesforce data to local PostgreSQL database.

    Execute sync jobs defined in YAML configuration file. Sync a single object
    by name or sync all enabled objects from the config file.

    Examples:

        # Sync a single object using auto mode
        sf-sync sync Account

        # Sync all enabled objects from config
        sf-sync sync --all

        # Preview sync without executing
        sf-sync sync --dry-run Account

        # Force specific sync mode
        sf-sync sync --mode bulk Account
        sf-sync sync --mode rest Contact

        # Use custom config file
        sf-sync sync --config ./my_config.yaml Account

        # Enable debug logging
        sf-sync sync --verbose Account

    Credentials are loaded from environment variables:
        JWT auth: SF_USERNAME, SF_CLIENT_ID, SF_PRIVATE_KEY_PATH
        Password auth: SF_USERNAME, SF_PASSWORD, SF_CLIENT_ID, SF_CLIENT_SECRET
        Optional: SF_SANDBOX (default: false), SF_API_VERSION (default: v61.0)

    Exit codes:
        0 - Success
        1 - Failure (authentication, sync error, invalid config)
    """
    # Configure logging
    _configure_logging(verbose)

    logger.debug(
        "CLI invoked: object_name=%s sync_all=%s dry_run=%s mode=%s config=%s verbose=%s",
        object_name,
        sync_all,
        dry_run,
        mode,
        config,
        verbose,
    )

    try:
        # Validate arguments
        _validate_arguments(object_name, sync_all)

        # Single object sync
        if object_name:
            logger.info("Single object sync: %s", object_name)

            # Load sync job config
            job_config = _load_sync_job_config(config, object_name)

            # Execute sync
            result = _execute_sync(job_config, mode, dry_run)

            # Print summary
            _print_sync_summary(object_name, result, dry_run)

            # Success
            sys.exit(0)

        # Sync all enabled objects
        elif sync_all:
            logger.info("Syncing all enabled objects from config: %s", config)

            # Load all enabled sync jobs
            try:
                configs = load_sync_config(config, include_disabled=False)
            except FileNotFoundError as e:
                logger.error("Config file not found: %s", e)
                click.echo(f"ERROR: {e}", err=True)
                sys.exit(1)
            except Exception as e:
                logger.error("Failed to load config: %s", e)
                click.echo(f"ERROR: Failed to load config: {e}", err=True)
                sys.exit(1)

            if not configs:
                logger.warning("No enabled sync jobs found in config")
                click.echo("No enabled sync jobs found in config", err=True)
                sys.exit(1)

            logger.info("Found %d enabled sync job(s)", len(configs))
            click.echo(f"Syncing {len(configs)} enabled object(s)...")

            # Execute each sync
            success_count = 0
            failure_count = 0

            for job_config in configs:
                obj_name = job_config.object_name

                try:
                    logger.info("Syncing object: %s", obj_name)
                    click.echo(f"\n--- {obj_name} ---")

                    result = _execute_sync(job_config, mode, dry_run)
                    _print_sync_summary(obj_name, result, dry_run)

                    success_count += 1

                except click.ClickException as e:
                    logger.error("Failed to sync %s: %s", obj_name, e.message)
                    click.echo(f"ERROR: {e.message}", err=True)
                    failure_count += 1
                    continue

            # Final summary
            click.echo(f"\n{'='*50}")
            click.echo(f"Total: {len(configs)} object(s)")
            click.echo(f"Success: {success_count}")
            click.echo(f"Failed: {failure_count}")

            # Exit with error if any failures
            if failure_count > 0:
                sys.exit(1)
            else:
                sys.exit(0)

    except click.ClickException as e:
        # Click exceptions are already logged
        click.echo(f"ERROR: {e.message}", err=True)
        sys.exit(1)

    except Exception as e:
        # Unexpected errors
        logger.exception("Unexpected error: %s", e)
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)


# Register status command from separate module
from sf_utils.cli_status import register_status_command
register_status_command(cli)


if __name__ == "__main__":
    cli()
