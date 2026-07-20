"""Status command implementation for sf-sync CLI.

This module provides the status subcommand for displaying sync status information.
"""

import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import click
import psycopg2
from psycopg2 import sql

from sf_utils.db import get_connection
from sf_utils.sync.config import load_sync_config
from sf_utils.sync.state import ensure_sync_state_table, get_sync_state

logger = logging.getLogger(__name__)


@dataclass
class SyncStatusRecord:
    """Sync status for a single object.

    Attributes:
        object_name: Salesforce object name.
        last_sync_time: Timestamp of last sync, None if never synced.
        record_count: Number of records in local table.
        status: Sync status: "OK", "Failed", or "Never".
    """

    object_name: str
    last_sync_time: Optional[datetime]
    record_count: int
    status: str


def _get_record_count(
    object_name: str,
    db_conn: psycopg2.extensions.connection,
) -> int:
    """Get record count for a synced object table.

    Args:
        object_name: Salesforce object name.
        db_conn: Active psycopg2 connection.

    Returns:
        Record count, or 0 if table doesn't exist.
    """
    table_name = f"sf_{object_name.lower()}"

    # Check if table exists using SQL composition
    check_query = sql.SQL(
        "SELECT EXISTS ("
        "  SELECT FROM information_schema.tables"
        "  WHERE table_schema = 'public'"
        "  AND table_name = %s"
        ")"
    )

    try:
        cursor = db_conn.cursor()
        cursor.execute(check_query, (table_name,))
        table_exists = cursor.fetchone()[0]

        if not table_exists:
            logger.debug("Table %s does not exist", table_name)
            return 0

        # Table exists - count records using SQL composition
        count_query = sql.SQL("SELECT COUNT(*) FROM {table}").format(
            table=sql.Identifier(table_name)
        )

        cursor.execute(count_query)
        count = cursor.fetchone()[0]
        logger.debug("Table %s has %d records", table_name, count)
        return count

    except psycopg2.Error as e:
        logger.warning("Failed to count records for %s: %s", table_name, e)
        return 0


def _get_sync_status_records(
    config_path: Path,
) -> List[SyncStatusRecord]:
    """Get sync status for all configured objects.

    Args:
        config_path: Path to sync configuration YAML file.

    Returns:
        List of SyncStatusRecord objects sorted by last_sync_time (oldest first).

    Raises:
        click.ClickException: If database connection fails.
    """
    logger.debug("Loading sync status for all configured objects")

    # Load all configured objects (including disabled)
    try:
        configs = load_sync_config(config_path, include_disabled=True)
    except FileNotFoundError as e:
        logger.error("Config file not found: %s", e)
        raise click.ClickException(str(e)) from e
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        raise click.ClickException(f"Failed to load config: {e}") from e

    if not configs:
        logger.warning("No sync jobs found in config")
        return []

    # Connect to database
    try:
        db_conn = get_connection()
    except ValueError as e:
        logger.error("Missing PostgreSQL credentials: %s", e)
        raise click.ClickException(
            f"Missing PostgreSQL credentials: {e}\n"
            f"Required environment variables: PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD"
        ) from e
    except psycopg2.OperationalError as e:
        logger.error("Failed to connect to PostgreSQL: %s", e)
        raise click.ClickException(f"Failed to connect to PostgreSQL: {e}") from e

    try:
        # Ensure sf_sync_state table exists
        ensure_sync_state_table(db_conn)

        # Build status records for each configured object
        status_records = []

        for job_config in configs:
            object_name = job_config.object_name

            # Get sync state
            sync_state = get_sync_state(object_name, db_conn)

            # Get record count
            record_count = _get_record_count(object_name, db_conn)

            # Determine status
            if sync_state is None:
                # Never synced
                status = "Never"
                last_sync_time = None
            elif record_count > 0:
                # Has records - assume OK
                status = "OK"
                last_sync_time = sync_state.last_sync_timestamp
            else:
                # Sync state exists but no records - assume failed
                status = "Failed"
                last_sync_time = sync_state.last_sync_timestamp

            status_records.append(
                SyncStatusRecord(
                    object_name=object_name,
                    last_sync_time=last_sync_time,
                    record_count=record_count,
                    status=status,
                )
            )

        # Sort by last_sync_time (oldest first, nulls last)
        status_records.sort(
            key=lambda r: (r.last_sync_time is None, r.last_sync_time or datetime.min)
        )

        logger.debug("Retrieved sync status for %d object(s)", len(status_records))
        return status_records

    finally:
        db_conn.close()


def _format_status_table(status_records: List[SyncStatusRecord]) -> str:
    """Format sync status as table.

    Args:
        status_records: List of sync status records.

    Returns:
        Formatted table string.
    """
    if not status_records:
        return "No sync jobs configured."

    # Calculate column widths
    max_object_name = max(len(r.object_name) for r in status_records)
    max_object_name = max(max_object_name, len("Object Name"))

    # Header
    lines = []
    lines.append(
        f"{'Object Name':<{max_object_name}}  "
        f"{'Last Sync Time':<20}  "
        f"{'Record Count':>12}  "
        f"{'Status':<10}"
    )
    lines.append(
        f"{'-' * max_object_name}  "
        f"{'-' * 20}  "
        f"{'-' * 12}  "
        f"{'-' * 10}"
    )

    # Rows
    for record in status_records:
        # Format timestamp
        if record.last_sync_time is None:
            time_str = "Never"
        else:
            time_str = record.last_sync_time.strftime("%Y-%m-%d %H:%M:%S")

        lines.append(
            f"{record.object_name:<{max_object_name}}  "
            f"{time_str:<20}  "
            f"{record.record_count:>12,}  "
            f"{record.status:<10}"
        )

    return "\n".join(lines)


def _format_status_json(status_records: List[SyncStatusRecord]) -> str:
    """Format sync status as JSON.

    Args:
        status_records: List of sync status records.

    Returns:
        JSON string.
    """
    # Convert to dict structure
    objects = []
    for record in status_records:
        obj_dict = {
            "name": record.object_name,
            "last_sync": (
                record.last_sync_time.isoformat() if record.last_sync_time else None
            ),
            "record_count": record.record_count,
            "status": record.status,
        }
        objects.append(obj_dict)

    result = {"status": "success", "objects": objects}

    return json.dumps(result, indent=2)


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


def register_status_command(cli_group: click.Group) -> None:
    """Register the status command with a CLI group.

    Args:
        cli_group: Click group to register the command with.
    """

    @cli_group.command()
    @click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Output status in JSON format",
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
    def status(
        json_output: bool,
        config: Path,
        verbose: bool,
    ) -> None:
        """Display sync status for configured objects.

        Shows the last sync time, record count, and status for each object
        defined in the sync configuration file.

        Examples:

            # Display status table
            sf-sync status

            # Output as JSON
            sf-sync status --json

            # Use custom config file
            sf-sync status --config ./my_config.yaml

            # Enable debug logging
            sf-sync status --verbose

        Status values:
            OK     - Last sync succeeded and records exist
            Failed - Last sync completed but no records
            Never  - Object has never been synced

        Exit codes:
            0 - Success (always, this is informational only)
        """
        # Configure logging
        _configure_logging(verbose)

        logger.debug(
            "Status command invoked: json_output=%s config=%s verbose=%s",
            json_output,
            config,
            verbose,
        )

        try:
            # Get sync status for all configured objects
            status_records = _get_sync_status_records(config)

            # Format and output
            if json_output:
                output = _format_status_json(status_records)
            else:
                output = _format_status_table(status_records)

            click.echo(output)

            # Always exit 0 (informational command)
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
