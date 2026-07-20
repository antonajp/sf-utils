"""Unit tests for CLI module."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner

from sf_utils.cli import main, _validate_arguments, _configure_logging
from sf_utils.sync.config import SyncJobConfig
from sf_utils.sync.rest_sync import SyncResult


class TestValidateArguments:
    """Test argument validation logic."""

    def test_both_object_name_and_sync_all_raises(self):
        """Test that providing both object_name and --all raises error."""
        from click import UsageError

        with pytest.raises(UsageError, match="Cannot specify both"):
            _validate_arguments(object_name="Account", sync_all=True)

    def test_neither_object_name_nor_sync_all_raises(self):
        """Test that providing neither object_name nor --all raises error."""
        from click import UsageError

        with pytest.raises(UsageError, match="Must specify either"):
            _validate_arguments(object_name=None, sync_all=False)

    def test_only_object_name_succeeds(self):
        """Test that providing only object_name succeeds."""
        # Should not raise
        _validate_arguments(object_name="Account", sync_all=False)

    def test_only_sync_all_succeeds(self):
        """Test that providing only --all succeeds."""
        # Should not raise
        _validate_arguments(object_name=None, sync_all=True)


class TestConfigureLogging:
    """Test logging configuration."""

    @patch("sf_utils.cli.logging.basicConfig")
    def test_verbose_enables_debug_logging(self, mock_basic_config):
        """Test that --verbose enables DEBUG logging."""
        import logging

        _configure_logging(verbose=True)

        mock_basic_config.assert_called_once()
        call_kwargs = mock_basic_config.call_args[1]
        assert call_kwargs["level"] == logging.DEBUG

    @patch("sf_utils.cli.logging.basicConfig")
    def test_non_verbose_enables_info_logging(self, mock_basic_config):
        """Test that default (non-verbose) enables INFO logging."""
        import logging

        _configure_logging(verbose=False)

        mock_basic_config.assert_called_once()
        call_kwargs = mock_basic_config.call_args[1]
        assert call_kwargs["level"] == logging.INFO


class TestCLICommand:
    """Test CLI command execution."""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    def test_no_arguments_shows_error(self, runner, tmp_path):
        """Test that running with no arguments shows usage error."""
        # Create empty config file to avoid Click file validation error
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text("syncs: []")

        result = runner.invoke(main, ["--config", str(config_file)], catch_exceptions=False)

        # Click raises SystemExit(2) for usage errors, which becomes exit_code=2
        assert result.exit_code in [1, 2]
        assert "Must specify either OBJECT_NAME or --all" in result.output

    def test_both_object_and_all_shows_error(self, runner, tmp_path):
        """Test that providing both object name and --all shows error."""
        # Create empty config file to avoid Click file validation error
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text("syncs: []")

        result = runner.invoke(main, ["Account", "--all", "--config", str(config_file)], catch_exceptions=False)

        # Click raises SystemExit(2) for usage errors, which becomes exit_code=2
        assert result.exit_code in [1, 2]
        assert "Cannot specify both" in result.output

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_dry_run_mode_does_not_execute_sync(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """Test that --dry-run previews without executing."""
        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    mode: auto
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                mode="auto",
                enabled=True,
            )
        ]

        # Run with --dry-run
        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file), "--dry-run"],
        )

        # Assertions
        assert result.exit_code == 0
        assert "DRY RUN" in result.output
        assert "Preview only" in result.output

        # sync() should NOT be called in dry run mode
        mock_sync.assert_not_called()

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_single_object_sync_success(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """Test successful single object sync."""
        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    mode: auto
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                mode="auto",
                enabled=True,
            )
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Account"

        mock_config_from_env.return_value = Mock(
            username="test@example.com",
            password="password",
            client_id="client_id",
            client_secret="client_secret",
            sandbox=False,
            api_version="v61.0",
        )

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        now = datetime.now(timezone.utc)
        mock_result = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=50,
            records_updated=50,
            sync_mode="rest",
            start_timestamp=now,
            end_timestamp=now,
            date_field="LastModifiedDate",
        )
        mock_sync.return_value = mock_result

        # Run CLI
        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file)],
            catch_exceptions=False,
        )

        # Assertions
        assert result.exit_code == 0
        assert "Sync Summary" in result.output
        assert "Object: Account" in result.output
        assert "Records: 100" in result.output
        assert "Mode: rest" in result.output
        assert "Status: SUCCESS" in result.output

        # Verify sync was called
        mock_sync.assert_called_once()

    @patch("sf_utils.cli.load_sync_config")
    def test_config_file_not_found_shows_error(self, mock_load_sync_config, runner):
        """Test that missing config file shows helpful error."""
        mock_load_sync_config.side_effect = FileNotFoundError(
            "Config file not found: nonexistent.yaml"
        )

        result = runner.invoke(
            main,
            ["Account", "--config", "nonexistent.yaml"],
            catch_exceptions=False,
        )

        # Click file existence checks happen before our code runs
        # This test may fail at Click level (exit_code=2) before reaching our handler
        assert result.exit_code in [1, 2]
        assert "ERROR" in result.output or "does not exist" in result.output

    @patch("sf_utils.cli.load_sync_config")
    def test_object_not_in_config_shows_error(
        self, mock_load_sync_config, runner, tmp_path
    ):
        """Test that object not found in config shows helpful error."""
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Contact
    soql_file: contact.soql
    date_field: LastModifiedDate
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Contact",
                soql_file="contact.soql",
                date_field="LastModifiedDate",
            )
        ]

        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file)],
        )

        assert result.exit_code == 1
        assert "not found in config" in result.output
        assert "Available objects: Contact" in result.output

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_sync_all_executes_multiple_objects(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """Test that --all syncs all enabled objects."""
        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    enabled: true
  - object_name: Contact
    soql_file: contact.soql
    date_field: LastModifiedDate
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                enabled=True,
            ),
            SyncJobConfig(
                object_name="Contact",
                soql_file="contact.soql",
                date_field="LastModifiedDate",
                enabled=True,
            ),
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Object"

        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        now = datetime.now(timezone.utc)
        mock_result = SyncResult(
            object_name="Test",
            records_fetched=100,
            records_inserted=50,
            records_updated=50,
            sync_mode="rest",
            start_timestamp=now,
            end_timestamp=now,
            date_field="LastModifiedDate",
        )
        mock_sync.return_value = mock_result

        # Run CLI with --all
        result = runner.invoke(
            main,
            ["--all", "--config", str(config_file)],
            catch_exceptions=False,
        )

        # Assertions
        assert result.exit_code == 0
        assert "Syncing 2 enabled object(s)" in result.output
        assert "Account" in result.output
        assert "Contact" in result.output
        assert "Success: 2" in result.output

        # Verify sync was called twice
        assert mock_sync.call_count == 2


class TestModeOverride:
    """Tests for mode override flags: sf-sync --mode <mode> <object_name>"""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_mode_bulk_override(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """sf-sync --mode bulk Account should pass SyncMode.BULK to sync()."""
        from sf_utils.sync import SyncMode

        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    mode: auto
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                mode="auto",
                enabled=True,
            )
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Account"
        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        now = datetime.now(timezone.utc)
        mock_result = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=50,
            records_updated=50,
            sync_mode="bulk",
            start_timestamp=now,
            end_timestamp=now,
            date_field="LastModifiedDate",
        )
        mock_sync.return_value = mock_result

        # Run CLI with --mode bulk
        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file), "--mode", "bulk"],
            catch_exceptions=False,
        )

        # Assertions
        assert result.exit_code == 0

        # Verify sync() was called with BULK mode
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args[1]
        assert call_kwargs['mode'] == SyncMode.BULK

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_mode_rest_override(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """sf-sync --mode rest Account should pass SyncMode.REST to sync()."""
        from sf_utils.sync import SyncMode

        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    mode: bulk
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                mode="bulk",
                enabled=True,
            )
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Account"
        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        now = datetime.now(timezone.utc)
        mock_result = SyncResult(
            object_name="Account",
            records_fetched=100,
            records_inserted=50,
            records_updated=50,
            sync_mode="rest",
            start_timestamp=now,
            end_timestamp=now,
            date_field="LastModifiedDate",
        )
        mock_sync.return_value = mock_result

        # Run CLI with --mode rest (overrides config mode=bulk)
        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file), "--mode", "rest"],
            catch_exceptions=False,
        )

        # Assertions
        assert result.exit_code == 0

        # Verify sync() was called with REST mode
        mock_sync.assert_called_once()
        call_kwargs = mock_sync.call_args[1]
        assert call_kwargs['mode'] == SyncMode.REST


class TestOutputFormatAndExitCodes:
    """Tests for output format and exit codes."""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_success_output_includes_all_fields(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """Success output should include Object, Records, Duration, Mode, Status."""
        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Contact
    soql_file: contact.soql
    date_field: LastModifiedDate
    mode: rest
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Contact",
                soql_file="contact.soql",
                date_field="LastModifiedDate",
                mode="rest",
                enabled=True,
            )
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Contact"
        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        # Mock sync with known values
        now = datetime.now(timezone.utc)
        mock_result = SyncResult(
            object_name="Contact",
            records_fetched=532,
            records_inserted=500,
            records_updated=32,
            sync_mode="rest",
            start_timestamp=now,
            end_timestamp=now,
            date_field="LastModifiedDate",
        )
        mock_sync.return_value = mock_result

        # Run CLI
        result = runner.invoke(
            main,
            ["Contact", "--config", str(config_file)],
            catch_exceptions=False,
        )

        # Verify exit code
        assert result.exit_code == 0

        # Verify output contains required fields
        assert "Object: Contact" in result.output
        assert "Records: 532" in result.output
        assert "Duration:" in result.output
        assert "Mode: rest" in result.output
        assert "Status: SUCCESS" in result.output

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_sync_failure_returns_exit_code_1(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """Sync failure should return exit code 1 with error message."""
        from sf_utils.exceptions import SalesforceAPIError

        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    mode: auto
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                mode="auto",
                enabled=True,
            )
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Account"
        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        # Mock sync to raise exception
        mock_sync.side_effect = SalesforceAPIError(
            message="Invalid field: FakeField__c",
            status_code=400,
        )

        # Run CLI
        result = runner.invoke(
            main,
            ["Account", "--config", str(config_file)],
        )

        # Verify exit code is non-zero
        assert result.exit_code == 1

        # Verify error message in output
        assert "ERROR" in result.output
        assert "Invalid field" in result.output or "failed" in result.output.lower()


class TestHelpAndUsage:
    """Tests for help text and usage information."""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    def test_help_flag_shows_usage(self, runner):
        """sf-sync --help should display help text with usage information."""
        result = runner.invoke(main, ['--help'])

        # Verify exit code is 0 (help is not an error)
        assert result.exit_code == 0

        # Verify help text is displayed
        assert "Usage" in result.output or "usage" in result.output.lower()
        assert "--all" in result.output
        assert "--mode" in result.output
        assert "--config" in result.output
        assert "--verbose" in result.output
        assert "--dry-run" in result.output


class TestIntegrationScenarios:
    """Integration tests for realistic usage scenarios."""

    @pytest.fixture
    def runner(self):
        """Create Click test runner."""
        return CliRunner()

    @patch("sf_utils.cli.load_sync_config")
    @patch("sf_utils.cli.Path.exists")
    @patch("sf_utils.cli.load_soql")
    @patch("sf_utils.cli.get_client")
    @patch("sf_utils.cli.sync")
    @patch("sf_utils.cli.SalesforceConfig.from_env")
    def test_partial_failure_handling(
        self,
        mock_config_from_env,
        mock_sync,
        mock_get_client,
        mock_load_soql,
        mock_path_exists,
        mock_load_sync_config,
        runner,
        tmp_path,
    ):
        """When syncing multiple objects, partial failures should be handled gracefully."""
        # Setup mocks
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text(
            """
syncs:
  - object_name: Account
    soql_file: account.soql
    date_field: LastModifiedDate
    enabled: true
  - object_name: Contact
    soql_file: contact.soql
    date_field: LastModifiedDate
    enabled: true
"""
        )

        mock_load_sync_config.return_value = [
            SyncJobConfig(
                object_name="Account",
                soql_file="account.soql",
                date_field="LastModifiedDate",
                enabled=True,
            ),
            SyncJobConfig(
                object_name="Contact",
                soql_file="contact.soql",
                date_field="LastModifiedDate",
                enabled=True,
            ),
        ]

        mock_path_exists.return_value = True
        mock_load_soql.return_value = "SELECT Id, Name FROM Object"
        mock_config_from_env.return_value = Mock()
        mock_get_client.return_value = Mock()

        # First sync succeeds, second fails
        now = datetime.now(timezone.utc)
        mock_sync.side_effect = [
            SyncResult(
                object_name="Account",
                records_fetched=1000,
                records_inserted=1000,
                records_updated=0,
                sync_mode="rest",
                start_timestamp=now,
                end_timestamp=now,
                date_field="LastModifiedDate",
            ),
            Exception("Contact sync failed: Invalid field"),
        ]

        # Run CLI
        result = runner.invoke(
            main,
            ["--all", "--config", str(config_file)],
        )

        # Verify partial failure is reported
        assert "Account" in result.output
        assert "Contact" in result.output or "failed" in result.output.lower()

        # CLI should report the failure
        assert "Failed: 1" in result.output or "Error" in result.output
