"""Tests for sync configuration module.

Tests cover:
- load_sync_config() happy path with valid multi-job configs
- Default value application (chunk_size=daily, mode=auto, enabled=true)
- Disabled job filtering (default and include_disabled=True)
- Validation of required fields (object_name, soql_file, date_field)
- Error handling (file not found, invalid YAML, empty file)
- SyncJobConfig dataclass field access and defaults
- Edge cases and boundary conditions
"""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest
import yaml

from sf_utils.sync.config import load_sync_config, SyncJobConfig


@contextmanager
def mock_config_file(yaml_content: str, file_exists: bool = True):
    """Context manager to mock YAML config file reading.

    Args:
        yaml_content: YAML string content to return when file is read.
        file_exists: Whether Path.exists() should return True. Defaults to True.

    Yields:
        None
    """
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        with patch.object(Path, "exists", return_value=file_exists):
            with patch.object(Path, "is_dir", return_value=False):
                with patch("sf_utils.sync.config.logger"):
                    yield


class TestSyncJobConfig:
    """Tests for SyncJobConfig dataclass."""

    def test_all_fields_accessible(self):
        """Should allow access to all fields."""
        config = SyncJobConfig(
            object_name="Account",
            soql_file="soql/account.soql",
            date_field="LastModifiedDate",
            chunk_size="weekly",
            mode="bulk",
            enabled=False,
        )

        assert config.object_name == "Account"
        assert config.soql_file == "soql/account.soql"
        assert config.date_field == "LastModifiedDate"
        assert config.chunk_size == "weekly"
        assert config.mode == "bulk"
        assert config.enabled is False

    def test_default_values_applied(self):
        """Should apply default values for optional fields."""
        config = SyncJobConfig(
            object_name="Account",
            soql_file="soql/account.soql",
            date_field="LastModifiedDate",
        )

        assert config.chunk_size == "daily"
        assert config.mode == "auto"
        assert config.enabled is True

    def test_partial_defaults_override(self):
        """Should allow partial override of defaults."""
        config = SyncJobConfig(
            object_name="Contact",
            soql_file="soql/contact.soql",
            date_field="CreatedDate",
            mode="rest",  # Override mode only
        )

        assert config.mode == "rest"
        assert config.chunk_size == "daily"  # Default
        assert config.enabled is True  # Default

    def test_validates_chunk_size(self):
        """Should raise ValueError for invalid chunk_size."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="Account",
                soql_file="soql/account.soql",
                date_field="LastModifiedDate",
                chunk_size="invalid",
            )

        assert "chunk_size" in str(exc_info.value)

    def test_validates_mode(self):
        """Should raise ValueError for invalid mode."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="Account",
                soql_file="soql/account.soql",
                date_field="LastModifiedDate",
                mode="invalid",
            )

        assert "mode" in str(exc_info.value)

    def test_validates_empty_object_name(self):
        """Should raise ValueError for empty object_name."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="",
                soql_file="soql/account.soql",
                date_field="LastModifiedDate",
            )

        assert "object_name" in str(exc_info.value)

    def test_validates_empty_soql_file(self):
        """Should raise ValueError for empty soql_file."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="Account",
                soql_file="",
                date_field="LastModifiedDate",
            )

        assert "soql_file" in str(exc_info.value)

    def test_validates_empty_date_field(self):
        """Should raise ValueError for empty date_field."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="Account",
                soql_file="soql/account.soql",
                date_field="",
            )

        assert "date_field" in str(exc_info.value)

    def test_validates_enabled_is_boolean(self):
        """Should raise ValueError for non-boolean enabled value."""
        with pytest.raises(ValueError) as exc_info:
            SyncJobConfig(
                object_name="Account",
                soql_file="soql/account.soql",
                date_field="LastModifiedDate",
                enabled="yes",  # String instead of boolean
            )

        assert "enabled" in str(exc_info.value).lower()


class TestLoadSyncConfigHappyPath:
    """Tests for load_sync_config() happy path scenarios."""

    def test_loads_valid_multi_job_config(self):
        """Should load and return list of SyncJobConfig from valid YAML."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    chunk_size: daily
    mode: auto
    enabled: true

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
    chunk_size: weekly
    mode: bulk
    enabled: true
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert isinstance(result, list)
        assert len(result) == 2

        # First job
        assert result[0].object_name == "Account"
        assert result[0].soql_file == "soql/account.soql"
        assert result[0].date_field == "LastModifiedDate"
        assert result[0].chunk_size == "daily"
        assert result[0].mode == "auto"
        assert result[0].enabled is True

        # Second job
        assert result[1].object_name == "Contact"
        assert result[1].soql_file == "soql/contact.soql"
        assert result[1].date_field == "CreatedDate"
        assert result[1].chunk_size == "weekly"
        assert result[1].mode == "bulk"
        assert result[1].enabled is True

    def test_applies_defaults_correctly(self):
        """Should apply default values for missing optional fields."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    # chunk_size, mode, enabled omitted - should use defaults
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert len(result) == 1
        assert result[0].chunk_size == "daily"  # Default
        assert result[0].mode == "auto"  # Default
        assert result[0].enabled is True  # Default

    def test_filters_disabled_jobs_by_default(self):
        """Should exclude jobs with enabled=false by default."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    enabled: true

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
    enabled: false

  - object_name: Opportunity
    soql_file: soql/opportunity.soql
    date_field: LastModifiedDate
    enabled: true
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        # Should only return enabled jobs
        assert len(result) == 2
        assert result[0].object_name == "Account"
        assert result[1].object_name == "Opportunity"

    def test_includes_disabled_jobs_when_requested(self):
        """Should include disabled jobs when include_disabled=True."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    enabled: true

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
    enabled: false
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml", include_disabled=True)

        # Should return all jobs
        assert len(result) == 2
        assert result[0].object_name == "Account"
        assert result[0].enabled is True
        assert result[1].object_name == "Contact"
        assert result[1].enabled is False

    def test_handles_yaml_boolean_variants(self):
        """Should correctly parse YAML boolean values (true/false, yes/no)."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    enabled: true

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
    enabled: yes

  - object_name: Lead
    soql_file: soql/lead.soql
    date_field: LastModifiedDate
    enabled: false
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml", include_disabled=True)

        assert len(result) == 3
        assert result[0].enabled is True  # true
        assert result[1].enabled is True  # yes
        assert result[2].enabled is False  # false

    def test_logs_config_load_summary(self):
        """Should log INFO message with count of loaded syncs."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
"""

        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "is_dir", return_value=False):
                    with patch("sf_utils.sync.config.logger") as mock_logger:
                        load_sync_config("test.yaml")

        # Verify INFO logging occurred
        assert mock_logger.info.called
        # Check that log message contains count
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("2" in call for call in log_calls)


class TestLoadSyncConfigValidation:
    """Tests for load_sync_config() validation."""

    def test_missing_object_name_raises_valueerror(self):
        """Should raise ValueError when object_name is missing."""
        yaml_content = """
syncs:
  - soql_file: soql/account.soql
    date_field: LastModifiedDate
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        assert "object_name" in str(exc_info.value).lower()

    def test_missing_soql_file_raises_valueerror(self):
        """Should raise ValueError when soql_file is missing."""
        yaml_content = """
syncs:
  - object_name: Account
    date_field: LastModifiedDate
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        assert "soql_file" in str(exc_info.value).lower()

    def test_missing_date_field_raises_valueerror(self):
        """Should raise ValueError when date_field is missing."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        assert "date_field" in str(exc_info.value).lower()

    def test_empty_syncs_list_returns_empty_list(self):
        """Should return empty list when syncs list is empty."""
        yaml_content = """
syncs: []
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert result == []

    def test_validation_error_includes_job_index(self):
        """Should include job index in validation error message for debugging."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate

  - object_name: Contact
    # Missing soql_file - should reference job index 1
    date_field: CreatedDate
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        error_msg = str(exc_info.value).lower()
        assert "soql_file" in error_msg
        # Should mention which job failed (index 1)
        assert "1" in error_msg


class TestLoadSyncConfigErrorHandling:
    """Tests for load_sync_config() error handling."""

    def test_file_not_found_raises_with_clear_message(self):
        """Should raise FileNotFoundError with clear message for missing file."""
        with mock_config_file("", file_exists=False):
            with pytest.raises(FileNotFoundError) as exc_info:
                load_sync_config("nonexistent.yaml")

        error_msg = str(exc_info.value)
        assert "nonexistent.yaml" in error_msg or "not found" in error_msg.lower()

    def test_directory_instead_of_file_raises_valueerror(self):
        """Should raise ValueError when path points to a directory."""
        with patch("builtins.open"):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "is_dir", return_value=True):
                    with patch("sf_utils.sync.config.logger"):
                        with pytest.raises(ValueError) as exc_info:
                            load_sync_config("test_dir")

        error_msg = str(exc_info.value).lower()
        assert "directory" in error_msg

    def test_invalid_yaml_syntax_raises_yamlerror(self):
        """Should raise yaml.YAMLError for invalid YAML syntax."""
        invalid_yaml = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    invalid_indentation
  should_not_be_here
"""

        with mock_config_file(invalid_yaml):
            with pytest.raises(yaml.YAMLError):
                load_sync_config("test.yaml")

    def test_empty_file_raises_valueerror(self):
        """Should raise ValueError for empty file."""
        with mock_config_file(""):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        assert "empty" in str(exc_info.value).lower()

    def test_non_dict_root_raises_valueerror(self):
        """Should raise ValueError when YAML root is not a dict."""
        yaml_content = """
- this
- is
- a
- list
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        error_msg = str(exc_info.value).lower()
        assert "dict" in error_msg or "mapping" in error_msg

    def test_missing_syncs_key_raises_valueerror(self):
        """Should raise ValueError when 'syncs' key is missing."""
        yaml_content = """
other_key: value
jobs:
  - something
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        assert "syncs" in str(exc_info.value).lower()

    def test_non_list_syncs_raises_valueerror(self):
        """Should raise ValueError when 'syncs' value is not a list."""
        yaml_content = """
syncs:
  object_name: Account
  soql_file: soql/account.soql
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        error_msg = str(exc_info.value).lower()
        assert "list" in error_msg or "syncs" in error_msg


class TestLoadSyncConfigEdgeCases:
    """Tests for load_sync_config() edge cases and boundary conditions."""

    def test_handles_single_job_config(self):
        """Should correctly handle config with single sync job."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert len(result) == 1
        assert result[0].object_name == "Account"

    def test_handles_all_disabled_jobs(self):
        """Should return empty list when all jobs are disabled."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    enabled: false

  - object_name: Contact
    soql_file: soql/contact.soql
    date_field: CreatedDate
    enabled: false
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert result == []

    def test_rejects_extra_unknown_fields(self):
        """Should raise ValueError for unknown fields in sync job config."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    custom_field: some_value
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        # Should raise error about unexpected keyword argument
        error_msg = str(exc_info.value).lower()
        assert "unexpected" in error_msg or "custom_field" in error_msg

    def test_utf8_encoding_for_special_characters(self):
        """Should handle UTF-8 encoded YAML with special characters."""
        yaml_content = """
syncs:
  - object_name: CustomObject__c
    soql_file: soql/custom_file.soql
    date_field: LastModifiedDate
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert len(result) == 1
        assert result[0].object_name == "CustomObject__c"

    def test_whitespace_in_values_preserved(self):
        """Should preserve whitespace in quoted field values."""
        yaml_content = """
syncs:
  - object_name: "  Account  "
    soql_file: "soql/account.soql  "
    date_field: "LastModifiedDate"
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        # YAML preserves quoted whitespace
        assert result[0].object_name == "  Account  "
        assert result[0].soql_file == "soql/account.soql  "

    def test_numeric_string_values_handled_correctly(self):
        """Should handle numeric-looking strings without type conversion."""
        yaml_content = """
syncs:
  - object_name: "123Account"
    soql_file: "456.soql"
    date_field: "789Date"
"""

        with mock_config_file(yaml_content):
            result = load_sync_config("test.yaml")

        assert result[0].object_name == "123Account"
        assert isinstance(result[0].object_name, str)

    def test_all_valid_chunk_sizes(self):
        """Should accept all valid chunk_size values."""
        valid_sizes = ["hourly", "daily", "weekly", "monthly", "none"]

        for size in valid_sizes:
            yaml_content = f"""
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    chunk_size: {size}
"""

            with mock_config_file(yaml_content):
                result = load_sync_config("test.yaml")

            assert result[0].chunk_size == size

    def test_all_valid_modes(self):
        """Should accept all valid mode values."""
        valid_modes = ["auto", "rest", "bulk"]

        for mode in valid_modes:
            yaml_content = f"""
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
    mode: {mode}
"""

            with mock_config_file(yaml_content):
                result = load_sync_config("test.yaml")

            assert result[0].mode == mode

    def test_sync_job_dict_not_mapping_raises_valueerror(self):
        """Should raise ValueError when sync job is not a dict/mapping."""
        yaml_content = """
syncs:
  - Account
"""

        with mock_config_file(yaml_content):
            with pytest.raises(ValueError) as exc_info:
                load_sync_config("test.yaml")

        error_msg = str(exc_info.value).lower()
        assert "mapping" in error_msg or "dict" in error_msg

    def test_credential_warning_logged(self):
        """Should log warning when credential-like values detected in config."""
        yaml_content = """
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
# Note: password should not be in config
password: secret123
"""

        with patch("builtins.open", mock_open(read_data=yaml_content)):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "is_dir", return_value=False):
                    with patch("sf_utils.sync.config.logger") as mock_logger:
                        result = load_sync_config("test.yaml")

        # Should have logged a warning about credentials
        assert mock_logger.warning.called
        warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
        assert any("password" in call.lower() or "credential" in call.lower() for call in warning_calls)
