"""Tests for Migration module (sf_utils/migration.py).

This module tests the Attachment to ContentDocument migration functionality:
- Configuration loading from properties files
- Apex code generation for different tiers
- Cursor management for resumable migrations
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_client():
    """Create a mock Salesforce client (simple-salesforce API)."""
    client = Mock()
    client.sf_instance = "example.my.salesforce.com"
    client.sf_version = "61.0"
    client.session_id = "00Dxx0000001234!ABCdefghijklmnopQRSTuvwxyz"
    client.proxies = None
    return client


@pytest.fixture
def sample_config_content():
    """Sample migration.properties file content for attachment migration."""
    return """# Migration Configuration
poll_interval_seconds=10

tier1.min_size=0
tier1.max_size=1048576
tier1.batch_size=10
tier1.batch_class=AttachmentToFilesConversionBatch

tier2.min_size=1048576
tier2.max_size=3145728
tier2.batch_size=3
tier2.batch_class=AttachmentToFilesConversionBatch

tier3.min_size=3145728
tier3.max_size=6291456
tier3.batch_size=1
tier3.batch_class=AttachmentToFilesConversionBatch

tier4.min_size=6291456
tier4.batch_size=1
tier4.batch_class=SingleAttachmentToFilesConversionBatch
"""


@pytest.fixture
def tmp_config_file(tmp_path, sample_config_content):
    """Create a temporary config file."""
    config_file = tmp_path / "migration.properties"
    config_file.write_text(sample_config_content)
    return config_file


class TestLoadMigrationConfigSuccess:
    """Tests for successful config loading."""

    def test_load_migration_config_success(self, tmp_config_file):
        """Should load all config values from properties file."""
        from sf_utils.migration import load_migration_config

        config = load_migration_config(tmp_config_file)

        assert len(config.tiers) == 4
        assert config.poll_interval_seconds == 10

    def test_load_migration_config_tier_values(self, tmp_config_file):
        """Should parse tier values correctly."""
        from sf_utils.migration import load_migration_config

        config = load_migration_config(tmp_config_file)

        # Tier 1
        tier1 = config.tiers[0]
        assert tier1.name == "tier1"
        assert tier1.min_size == 0
        assert tier1.max_size == 1048576
        assert tier1.batch_size == 10
        assert tier1.batch_class == "AttachmentToFilesConversionBatch"

        # Tier 4
        tier4 = config.tiers[3]
        assert tier4.name == "tier4"
        assert tier4.min_size == 6291456
        assert tier4.max_size is None  # No upper bound
        assert tier4.batch_size == 1
        assert tier4.batch_class == "SingleAttachmentToFilesConversionBatch"

    def test_load_migration_config_handles_whitespace(self, tmp_path):
        """Should strip whitespace from values."""
        from sf_utils.migration import load_migration_config

        config_content = """poll_interval_seconds = 15

tier1.min_size = 0
tier1.max_size = 1000
tier1.batch_size = 5
tier1.batch_class = AttachmentToFilesConversionBatch
"""
        config_file = tmp_path / "migration.properties"
        config_file.write_text(config_content)

        config = load_migration_config(config_file)

        assert config.poll_interval_seconds == 15
        assert config.tiers[0].batch_size == 5

    def test_load_migration_config_ignores_comments(self, tmp_path):
        """Should ignore lines starting with # or blank lines."""
        from sf_utils.migration import load_migration_config

        config_content = """# This is a comment
poll_interval_seconds=10

# Another comment
tier1.min_size=0
tier1.max_size=1000
tier1.batch_size=5
tier1.batch_class=AttachmentToFilesConversionBatch
"""
        config_file = tmp_path / "migration.properties"
        config_file.write_text(config_content)

        config = load_migration_config(config_file)

        assert len(config.tiers) == 1


class TestLoadMigrationConfigMissingFile:
    """Tests for missing configuration file handling."""

    def test_load_migration_config_missing_file(self, tmp_path):
        """Should raise FileNotFoundError for missing config file."""
        from sf_utils.migration import load_migration_config

        missing_file = tmp_path / "nonexistent.properties"

        with pytest.raises(FileNotFoundError) as exc_info:
            load_migration_config(missing_file)

        assert "nonexistent.properties" in str(exc_info.value)

    def test_load_migration_config_missing_required_field(self, tmp_path):
        """Should raise ValueError for missing required fields."""
        from sf_utils.migration import load_migration_config

        # Missing batch_class
        config_content = """tier1.min_size=0
tier1.max_size=1000
tier1.batch_size=5
"""
        config_file = tmp_path / "migration.properties"
        config_file.write_text(config_content)

        with pytest.raises(ValueError) as exc_info:
            load_migration_config(config_file)

        assert "batch_class" in str(exc_info.value).lower()


class TestLoadMigrationConfigDefaults:
    """Tests for default configuration values."""

    def test_load_migration_config_default_poll_interval(self, tmp_path):
        """Should apply default poll_interval_seconds of 10."""
        from sf_utils.migration import load_migration_config

        config_content = """tier1.min_size=0
tier1.max_size=1000
tier1.batch_size=5
tier1.batch_class=AttachmentToFilesConversionBatch
"""
        config_file = tmp_path / "migration.properties"
        config_file.write_text(config_content)

        config = load_migration_config(config_file)

        assert config.poll_interval_seconds == 10  # Default


class TestBuildApexCode:
    """Tests for Apex code generation."""

    def test_build_apex_code_tier1(self):
        """Should generate valid Apex code for tier 1 migration."""
        from sf_utils.migration import build_apex_code, TierConfig

        tier = TierConfig(
            name="tier1",
            min_size=0,
            max_size=1048576,
            batch_size=10,
            batch_class="AttachmentToFilesConversionBatch"
        )
        hour_start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        hour_end = datetime(2020, 1, 1, 1, 0, 0, tzinfo=timezone.utc)

        apex_code = build_apex_code(tier, hour_start, hour_end)

        # Verify Apex structure
        assert "BodyLength >= 0" in apex_code
        assert "BodyLength < 1048576" in apex_code
        assert "2020-01-01T00:00:00Z" in apex_code
        assert "2020-01-01T01:00:00Z" in apex_code
        assert "Database.executeBatch" in apex_code
        assert "AttachmentToFilesConversionBatch" in apex_code
        assert ", 10)" in apex_code  # batch_size

    def test_build_apex_code_tier4(self):
        """Should generate Apex code using SingleAttachment class for tier 4."""
        from sf_utils.migration import build_apex_code, TierConfig

        tier = TierConfig(
            name="tier4",
            min_size=6291456,
            max_size=None,  # No upper bound
            batch_size=1,
            batch_class="SingleAttachmentToFilesConversionBatch"
        )
        hour_start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        hour_end = datetime(2020, 1, 1, 1, 0, 0, tzinfo=timezone.utc)

        apex_code = build_apex_code(tier, hour_start, hour_end)

        # Tier 4 uses SingleAttachment class
        assert "SingleAttachmentToFilesConversionBatch" in apex_code
        assert "BodyLength >= 6291456" in apex_code
        assert "BodyLength <" not in apex_code  # No upper bound
        assert ", 1)" in apex_code  # batch_size

    def test_build_apex_code_requires_timezone_aware_dates(self):
        """Should raise ValueError for naive datetime objects."""
        from sf_utils.migration import build_apex_code, TierConfig

        tier = TierConfig(
            name="tier1",
            min_size=0,
            max_size=1000,
            batch_size=10,
            batch_class="AttachmentToFilesConversionBatch"
        )
        hour_start = datetime(2020, 1, 1, 0, 0, 0)  # Naive
        hour_end = datetime(2020, 1, 1, 1, 0, 0)  # Naive

        with pytest.raises(ValueError) as exc_info:
            build_apex_code(tier, hour_start, hour_end)

        assert "timezone" in str(exc_info.value).lower()


class TestSaveCursor:
    """Tests for cursor file writing."""

    def test_save_cursor(self, tmp_path):
        """Should write cursor data to JSON file."""
        from sf_utils.migration import save_cursor

        cursor_file = tmp_path / "cursor.json"
        cursor_data = {
            "last_completed_hour": "2020-06-15T12:00:00Z",
            "next_hour": "2020-06-15T13:00:00Z",
            "total_hours_processed": 100,
            "total_batches_executed": 400
        }

        save_cursor(cursor_file, cursor_data)

        assert cursor_file.exists()
        saved_data = json.loads(cursor_file.read_text())
        assert saved_data["last_completed_hour"] == "2020-06-15T12:00:00Z"
        assert saved_data["total_hours_processed"] == 100

    def test_save_cursor_overwrites_existing(self, tmp_path):
        """Should overwrite existing cursor file."""
        from sf_utils.migration import save_cursor

        cursor_file = tmp_path / "cursor.json"

        # First save
        save_cursor(cursor_file, {"last_completed_hour": "2020-01-01T00:00:00Z"})

        # Second save (overwrite)
        save_cursor(cursor_file, {"last_completed_hour": "2020-06-01T00:00:00Z"})

        saved_data = json.loads(cursor_file.read_text())
        assert saved_data["last_completed_hour"] == "2020-06-01T00:00:00Z"


class TestLoadCursor:
    """Tests for cursor file reading."""

    def test_load_cursor(self, tmp_path):
        """Should load cursor data from JSON file."""
        from sf_utils.migration import load_cursor

        cursor_file = tmp_path / "cursor.json"
        cursor_data = {
            "last_completed_hour": "2020-06-15T12:00:00Z",
            "next_hour": "2020-06-15T13:00:00Z",
            "total_hours_processed": 100,
            "total_batches_executed": 400
        }
        cursor_file.write_text(json.dumps(cursor_data))

        loaded = load_cursor(cursor_file)

        assert loaded["last_completed_hour"] == "2020-06-15T12:00:00Z"
        assert loaded["total_hours_processed"] == 100

    def test_load_cursor_missing_returns_empty_dict(self, tmp_path):
        """Should return empty dict for missing cursor file."""
        from sf_utils.migration import load_cursor

        missing_file = tmp_path / "nonexistent_cursor.json"

        result = load_cursor(missing_file)

        assert result == {}


class TestTierConfigValidation:
    """Tests for TierConfig dataclass validation."""

    def test_tier_config_valid(self):
        """Should create TierConfig with valid values."""
        from sf_utils.migration import TierConfig

        tier = TierConfig(
            name="tier1",
            min_size=0,
            max_size=1000,
            batch_size=10,
            batch_class="AttachmentToFilesConversionBatch"
        )

        assert tier.name == "tier1"
        assert tier.batch_size == 10

    def test_tier_config_invalid_batch_size(self):
        """Should raise ValueError for invalid batch_size."""
        from sf_utils.migration import TierConfig

        with pytest.raises(ValueError) as exc_info:
            TierConfig(
                name="tier1",
                min_size=0,
                max_size=1000,
                batch_size=0,  # Invalid
                batch_class="AttachmentToFilesConversionBatch"
            )

        assert "batch_size" in str(exc_info.value).lower()

    def test_tier_config_empty_batch_class(self):
        """Should raise ValueError for empty batch_class."""
        from sf_utils.migration import TierConfig

        with pytest.raises(ValueError) as exc_info:
            TierConfig(
                name="tier1",
                min_size=0,
                max_size=1000,
                batch_size=10,
                batch_class=""  # Invalid
            )

        assert "batch_class" in str(exc_info.value).lower()

    def test_tier_config_invalid_batch_class(self):
        """Should raise ValueError for batch_class not in whitelist."""
        from sf_utils.migration import TierConfig

        with pytest.raises(ValueError) as exc_info:
            TierConfig(
                name="tier1",
                min_size=0,
                max_size=1000,
                batch_size=10,
                batch_class="MaliciousBatch"  # Not in whitelist
            )

        assert "must be one of" in str(exc_info.value).lower()


class TestMigrationConfigValidation:
    """Tests for MigrationConfig dataclass validation."""

    def test_migration_config_valid(self):
        """Should create MigrationConfig with valid values."""
        from sf_utils.migration import MigrationConfig, TierConfig

        tier = TierConfig(
            name="tier1",
            min_size=0,
            max_size=1000,
            batch_size=10,
            batch_class="AttachmentToFilesConversionBatch"
        )
        config = MigrationConfig(tiers=[tier], poll_interval_seconds=10)

        assert len(config.tiers) == 1
        assert config.poll_interval_seconds == 10

    def test_migration_config_empty_tiers(self):
        """Should raise ValueError for empty tiers list."""
        from sf_utils.migration import MigrationConfig

        with pytest.raises(ValueError) as exc_info:
            MigrationConfig(tiers=[], poll_interval_seconds=10)

        assert "tier" in str(exc_info.value).lower()

    def test_migration_config_invalid_poll_interval(self):
        """Should raise ValueError for invalid poll_interval_seconds."""
        from sf_utils.migration import MigrationConfig, TierConfig

        tier = TierConfig(
            name="tier1",
            min_size=0,
            max_size=1000,
            batch_size=10,
            batch_class="AttachmentToFilesConversionBatch"
        )

        with pytest.raises(ValueError) as exc_info:
            MigrationConfig(tiers=[tier], poll_interval_seconds=0)

        assert "poll_interval" in str(exc_info.value).lower()


class TestMigrationResultValidation:
    """Tests for MigrationResult dataclass validation."""

    def test_migration_result_valid(self):
        """Should create MigrationResult with valid values."""
        from sf_utils.migration import MigrationResult

        now = datetime.now(timezone.utc)
        result = MigrationResult(
            total_hours_processed=100,
            total_batches_executed=400,
            start_time=now,
            end_time=now,
            status="completed"
        )

        assert result.total_hours_processed == 100
        assert result.status == "completed"

    def test_migration_result_invalid_status(self):
        """Should raise ValueError for invalid status."""
        from sf_utils.migration import MigrationResult

        now = datetime.now(timezone.utc)

        with pytest.raises(ValueError) as exc_info:
            MigrationResult(
                total_hours_processed=100,
                total_batches_executed=400,
                start_time=now,
                end_time=now,
                status="invalid_status"  # Invalid
            )

        assert "status" in str(exc_info.value).lower()


class TestRunMigration:
    """Tests for run_migration function."""

    def test_run_migration_requires_timezone_aware_date(self, tmp_config_file, mock_client):
        """Should raise ValueError for naive start_date."""
        from sf_utils.migration import run_migration

        naive_date = datetime(2020, 1, 1)  # Naive

        with pytest.raises(ValueError) as exc_info:
            run_migration(
                start_date=naive_date,
                config_path=tmp_config_file,
                client=mock_client
            )

        assert "timezone" in str(exc_info.value).lower()

    @patch('sf_utils.migration._execute_and_poll_batch')
    def test_run_migration_completes_successfully(
        self,
        mock_execute_and_poll,
        tmp_config_file,
        mock_client
    ):
        """Should process hours and complete successfully."""
        from sf_utils.migration import run_migration

        # Mock successful execution
        mock_execute_and_poll.return_value = {"Status": "Completed", "NumberOfErrors": 0}

        # Run for 1 hour (start to end within same hour)
        start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        with patch('sf_utils.migration.datetime') as mock_datetime:
            # Set "now" to just past the start hour
            mock_datetime.now.return_value = datetime(2020, 1, 1, 1, 0, 1, tzinfo=timezone.utc)
            mock_datetime.fromisoformat = datetime.fromisoformat

            result = run_migration(
                start_date=start,
                config_path=tmp_config_file,
                client=mock_client,
                verbose=False
            )

        assert result.status == "completed"
        # The migration processes each hour with 4 tiers
        assert result.total_hours_processed >= 1
        assert result.total_batches_executed >= 4
