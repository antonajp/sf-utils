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

from sf_utils.sync.config import load_sync_config, SyncJobConfig, _resolve_config_path


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


class TestResolveConfigPath:
    """Tests for _resolve_config_path() fallback behavior."""

    def test_returns_path_if_exists(self, tmp_path):
        """Should return original path if file exists."""
        config_file = tmp_path / "sync_config.yaml"
        config_file.write_text("syncs: []")

        result = _resolve_config_path(config_file)

        assert result == config_file

    def test_returns_absolute_path_as_is(self, tmp_path):
        """Should return absolute path without checking projects/ fallback."""
        # Absolute path that doesn't exist
        abs_path = tmp_path / "nonexistent_config.yaml"

        result = _resolve_config_path(abs_path)

        assert result == abs_path
        assert result.is_absolute()

    def test_falls_back_to_projects_directory(self, tmp_path, monkeypatch):
        """Should fall back to projects/ directory if file exists there."""
        # Change working directory to tmp_path
        monkeypatch.chdir(tmp_path)

        # Create projects/ directory with config file
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        projects_config = projects_dir / "sync_config.yaml"
        projects_config.write_text("syncs: []")

        # Request sync_config.yaml (doesn't exist in root)
        result = _resolve_config_path("sync_config.yaml")

        assert result == Path("projects/sync_config.yaml")

    def test_returns_original_if_neither_exists(self, tmp_path, monkeypatch):
        """Should return original path if not found in either location."""
        monkeypatch.chdir(tmp_path)

        # Neither root nor projects/ has the config
        result = _resolve_config_path("sync_config.yaml")

        assert result == Path("sync_config.yaml")

    def test_prefers_root_over_projects(self, tmp_path, monkeypatch):
        """Should prefer root config over projects/ when both exist."""
        monkeypatch.chdir(tmp_path)

        # Create config in both root and projects/
        root_config = tmp_path / "sync_config.yaml"
        root_config.write_text("syncs: []  # root")

        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        projects_config = projects_dir / "sync_config.yaml"
        projects_config.write_text("syncs: []  # projects")

        result = _resolve_config_path("sync_config.yaml")

        # Should use root (exists check returns True for original path)
        assert result == Path("sync_config.yaml")


class TestLoadSyncConfigPathFallback:
    """Tests for load_sync_config() path fallback behavior."""

    def test_loads_from_projects_directory(self, tmp_path, monkeypatch):
        """Should load config from projects/ if root doesn't exist."""
        monkeypatch.chdir(tmp_path)

        # Create projects/ directory with config file
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        projects_config = projects_dir / "sync_config.yaml"
        projects_config.write_text("""
syncs:
  - object_name: Account
    soql_file: soql/account.soql
    date_field: LastModifiedDate
""")

        # Load using default path (root doesn't exist)
        result = load_sync_config("sync_config.yaml")

        assert len(result) == 1
        assert result[0].object_name == "Account"

    def test_loads_from_root_when_exists(self, tmp_path, monkeypatch):
        """Should load from root when it exists, ignoring projects/."""
        monkeypatch.chdir(tmp_path)

        # Create config in root
        root_config = tmp_path / "sync_config.yaml"
        root_config.write_text("""
syncs:
  - object_name: RootAccount
    soql_file: soql/root.soql
    date_field: LastModifiedDate
""")

        # Also create in projects/ (should be ignored)
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        projects_config = projects_dir / "sync_config.yaml"
        projects_config.write_text("""
syncs:
  - object_name: ProjectsAccount
    soql_file: soql/projects.soql
    date_field: LastModifiedDate
""")

        result = load_sync_config("sync_config.yaml")

        assert len(result) == 1
        assert result[0].object_name == "RootAccount"

    def test_explicit_config_bypasses_fallback(self, tmp_path, monkeypatch):
        """Should use explicit --config path without fallback."""
        monkeypatch.chdir(tmp_path)

        # Create custom config location
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        custom_config = custom_dir / "my_config.yaml"
        custom_config.write_text("""
syncs:
  - object_name: CustomAccount
    soql_file: soql/custom.soql
    date_field: LastModifiedDate
""")

        result = load_sync_config(custom_config)

        assert len(result) == 1
        assert result[0].object_name == "CustomAccount"

    def test_error_message_mentions_both_paths(self, tmp_path, monkeypatch):
        """Should mention both paths in error when config not found."""
        monkeypatch.chdir(tmp_path)

        # No config file exists anywhere
        with pytest.raises(FileNotFoundError) as exc_info:
            load_sync_config("sync_config.yaml")

        error_msg = str(exc_info.value)
        # Should mention both locations checked
        assert "projects" in error_msg.lower()
        assert "sync_config.yaml" in error_msg


class TestConfigPathSecurity:
    """Tests for security protections in config path handling."""

    def test_rejects_symlink_config_file(self, tmp_path, monkeypatch):
        """Should reject symlinks for security (prevents arbitrary file read)."""
        monkeypatch.chdir(tmp_path)

        # Create a target file
        target_file = tmp_path / "target.yaml"
        target_file.write_text("syncs: []")

        # Create symlink to target
        symlink = tmp_path / "sync_config.yaml"
        symlink.symlink_to(target_file)

        with pytest.raises(ValueError, match="Symlinks are not allowed"):
            _resolve_config_path("sync_config.yaml")

    def test_rejects_symlink_in_projects(self, tmp_path, monkeypatch):
        """Should reject symlinks in projects/ directory."""
        monkeypatch.chdir(tmp_path)

        # Create projects/ directory with symlink
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        target_file = tmp_path / "target.yaml"
        target_file.write_text("syncs: []")

        symlink = projects_dir / "sync_config.yaml"
        symlink.symlink_to(target_file)

        with pytest.raises(ValueError, match="Symlinks are not allowed"):
            _resolve_config_path("sync_config.yaml")

    def test_rejects_path_traversal_attempts(self, tmp_path, monkeypatch):
        """Should reject paths containing '..' components."""
        monkeypatch.chdir(tmp_path)

        # Create config file one level up
        parent_config = tmp_path.parent / "config.yaml"
        parent_config.write_text("syncs: []")

        try:
            with pytest.raises(ValueError, match="Path traversal detected"):
                load_sync_config("../config.yaml")
        finally:
            # Cleanup
            if parent_config.exists():
                parent_config.unlink()

    def test_rejects_nested_path_traversal(self, tmp_path, monkeypatch):
        """Should reject nested path traversal attempts."""
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="Path traversal detected"):
            load_sync_config("foo/../../../etc/passwd")
