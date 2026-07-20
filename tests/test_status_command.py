"""Tests for sf-sync status command."""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from sf_utils.cli import cli
from sf_utils.sync.config import SyncJobConfig
from sf_utils.sync.state import SyncStateRow


class TestStatusCommand:
    """Tests for sf-sync status command."""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    @pytest.fixture
    def config_file(self, tmp_path):
        """Create test config file."""
        config = tmp_path / "sync_config.yaml"
        config.write_text("""
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
  - object_name: Contact
    soql_file: contact.soql
    date_field: LastModifiedDate
  - object_name: Opportunity
    soql_file: opportunity.soql
    date_field: LastModifiedDate
""")
        return config

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    @patch("sf_utils.cli_status.ensure_sync_state_table")
    @patch("sf_utils.cli_status.get_sync_state")
    @patch("sf_utils.cli_status._get_record_count")
    def test_status_table_output(
        self,
        mock_get_record_count,
        mock_get_sync_state,
        mock_ensure_table,
        mock_get_conn,
        mock_load_config,
        runner,
        config_file,
    ):
        """Status command displays table with sync status."""
        # Mock config
        mock_load_config.return_value = [
            SyncJobConfig(object_name="Account", soql_file="account.soql", date_field="LastModifiedDate"),
            SyncJobConfig(object_name="Contact", soql_file="contact.soql", date_field="LastModifiedDate"),
            SyncJobConfig(object_name="Opportunity", soql_file="opportunity.soql", date_field="LastModifiedDate"),
        ]

        # Mock database connection
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        # Mock sync state results
        mock_get_sync_state.side_effect = [
            SyncStateRow(
                object_name="Account",
                last_sync_timestamp=datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc),
            ),
            SyncStateRow(
                object_name="Contact",
                last_sync_timestamp=datetime(2024, 1, 15, 10, 15, 0, tzinfo=timezone.utc),
            ),
            None,  # Opportunity never synced
        ]

        # Mock record counts
        mock_get_record_count.side_effect = [1234, 5678, 0]

        # Run CLI status command
        result = runner.invoke(cli, ["status", "--config", str(config_file)])

        # Assertions
        assert result.exit_code == 0
        assert "Object Name" in result.output
        assert "Last Sync Time" in result.output
        assert "Record Count" in result.output
        assert "Status" in result.output

        # Check data rows
        assert "Account" in result.output
        assert "1,234" in result.output  # Comma formatting
        assert "Contact" in result.output
        assert "5,678" in result.output
        assert "Opportunity" in result.output
        assert "OK" in result.output  # Has records
        assert "Never" in result.output  # Never synced

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    @patch("sf_utils.cli_status.ensure_sync_state_table")
    @patch("sf_utils.cli_status.get_sync_state")
    @patch("sf_utils.cli_status._get_record_count")
    def test_status_json_output(
        self,
        mock_get_record_count,
        mock_get_sync_state,
        mock_ensure_table,
        mock_get_conn,
        mock_load_config,
        runner,
        config_file,
    ):
        """Status command with --json flag returns valid JSON."""
        # Mock config
        mock_load_config.return_value = [
            SyncJobConfig(object_name="Account", soql_file="account.soql", date_field="LastModifiedDate"),
            SyncJobConfig(object_name="Contact", soql_file="contact.soql", date_field="LastModifiedDate"),
        ]

        # Mock database connection
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        # Mock sync state results
        mock_get_sync_state.side_effect = [
            SyncStateRow(
                object_name="Account",
                last_sync_timestamp=datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc),
            ),
            None,  # Contact never synced
        ]

        # Mock record counts
        mock_get_record_count.side_effect = [1234, 0]

        # Run CLI status command with --json flag
        result = runner.invoke(cli, ["status", "--config", str(config_file), "--json"])

        # Assertions
        assert result.exit_code == 0

        # Parse JSON output
        output_data = json.loads(result.output)

        # Verify JSON structure
        assert "status" in output_data
        assert output_data["status"] == "success"
        assert "objects" in output_data
        assert len(output_data["objects"]) == 2

        # Check Account object
        account = output_data["objects"][0]
        assert account["name"] == "Account"
        assert account["last_sync"] == "2024-01-15T10:30:45+00:00"
        assert account["record_count"] == 1234
        assert account["status"] == "OK"

        # Check Contact object (never synced)
        contact = output_data["objects"][1]
        assert contact["name"] == "Contact"
        assert contact["last_sync"] is None
        assert contact["record_count"] == 0
        assert contact["status"] == "Never"

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    @patch("sf_utils.cli_status.ensure_sync_state_table")
    @patch("sf_utils.cli_status.get_sync_state")
    @patch("sf_utils.cli_status._get_record_count")
    def test_status_sorting_oldest_first(
        self,
        mock_get_record_count,
        mock_get_sync_state,
        mock_ensure_table,
        mock_get_conn,
        mock_load_config,
        runner,
        config_file,
    ):
        """Objects are sorted by last sync time (oldest first, never synced last)."""
        # Mock config
        mock_load_config.return_value = [
            SyncJobConfig(object_name="Contact", soql_file="contact.soql", date_field="LastModifiedDate"),
            SyncJobConfig(object_name="Account", soql_file="account.soql", date_field="LastModifiedDate"),
            SyncJobConfig(object_name="Opportunity", soql_file="opportunity.soql", date_field="LastModifiedDate"),
        ]

        # Mock database connection
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        # Mock sync state results (unsorted)
        mock_get_sync_state.side_effect = [
            SyncStateRow(
                object_name="Contact",
                last_sync_timestamp=datetime(2024, 1, 15, 10, 30, 45, tzinfo=timezone.utc),  # Latest
            ),
            SyncStateRow(
                object_name="Account",
                last_sync_timestamp=datetime(2024, 1, 14, 8, 0, 0, tzinfo=timezone.utc),  # Oldest
            ),
            None,  # Opportunity never synced (should be last)
        ]

        # Mock record counts
        mock_get_record_count.side_effect = [100, 200, 0]

        # Run CLI status command
        result = runner.invoke(cli, ["status", "--config", str(config_file)])

        # Assertions
        assert result.exit_code == 0

        # Verify sorting: Account (oldest) < Contact < Opportunity (never)
        output_lines = result.output.split("\n")
        account_idx = next(i for i, line in enumerate(output_lines) if "Account" in line and "Object Name" not in line)
        contact_idx = next(i for i, line in enumerate(output_lines) if "Contact" in line)
        opportunity_idx = next(i for i, line in enumerate(output_lines) if "Opportunity" in line)

        assert account_idx < contact_idx < opportunity_idx

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    def test_status_database_connection_failure(
        self,
        mock_get_conn,
        mock_load_config,
        runner,
        config_file,
    ):
        """Status command handles database connection failure gracefully."""
        import psycopg2

        # Mock config
        mock_load_config.return_value = [
            SyncJobConfig(object_name="Account", soql_file="account.soql", date_field="LastModifiedDate"),
        ]

        # Mock connection failure
        mock_get_conn.side_effect = psycopg2.OperationalError("could not connect to server")

        # Run CLI status command
        result = runner.invoke(cli, ["status", "--config", str(config_file)])

        # Should show error message
        assert result.exit_code == 1
        assert "ERROR" in result.output
        assert "PostgreSQL" in result.output or "database" in result.output.lower()

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    @patch("sf_utils.cli_status.ensure_sync_state_table")
    @patch("sf_utils.cli_status.get_sync_state")
    @patch("sf_utils.cli_status._get_record_count")
    def test_status_failed_sync_no_records(
        self,
        mock_get_record_count,
        mock_get_sync_state,
        mock_ensure_table,
        mock_get_conn,
        mock_load_config,
        runner,
        config_file,
    ):
        """Sync state exists but no records shows Failed status."""
        # Mock config
        mock_load_config.return_value = [
            SyncJobConfig(object_name="Lead", soql_file="lead.soql", date_field="LastModifiedDate"),
        ]

        # Mock database connection
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        # Mock sync state exists but table has no records
        mock_get_sync_state.return_value = SyncStateRow(
            object_name="Lead",
            last_sync_timestamp=datetime(2024, 1, 14, 8, 0, 0, tzinfo=timezone.utc),
        )

        # Mock record count is 0
        mock_get_record_count.return_value = 0

        # Run CLI status command
        result = runner.invoke(cli, ["status", "--config", str(config_file)])

        # Assertions
        assert result.exit_code == 0
        assert "Lead" in result.output
        assert "Failed" in result.output  # Sync completed but no records

    @patch("sf_utils.cli_status.load_sync_config")
    @patch("sf_utils.cli_status.get_connection")
    @patch("sf_utils.cli_status.ensure_sync_state_table")
    def test_status_empty_config(
        self,
        mock_ensure_table,
        mock_get_conn,
        mock_load_config,
        runner,
        tmp_path,
    ):
        """Status with no configured objects shows appropriate message."""
        # Mock empty config
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text("syncs: []")

        mock_load_config.return_value = []

        # Mock database connection
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        # Run CLI status command
        result = runner.invoke(cli, ["status", "--config", str(config_file)])

        # Should show empty message
        assert result.exit_code == 0
        assert "No sync jobs configured" in result.output
