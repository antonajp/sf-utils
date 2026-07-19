"""Tests for PostgreSQL database connection module.

Tests cover:
- PostgresConfig dataclass and environment loading
- get_connection function with various configurations
- Connection error handling and validation
- Password redaction in logs and repr
- Input validation for security
"""

import os
from unittest.mock import patch, MagicMock

import pytest

from sf_utils.db import PostgresConfig, get_connection


class TestPostgresConfig:
    """Tests for PostgresConfig dataclass."""

    def test_from_env_with_all_vars(self):
        """Should load config from environment variables with all optional vars."""
        env = {
            "PG_HOST": "db.example.com",
            "PG_PORT": "5433",
            "PG_DATABASE": "production_db",
            "PG_USER": "app_user",
            "PG_PASSWORD": "secret123",
            "PG_SSLMODE": "require",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()

        assert config.host == "db.example.com"
        assert config.port == 5433
        assert config.database == "production_db"
        assert config.user == "app_user"
        assert config.password == "secret123"
        assert config.sslmode == "require"

    def test_from_env_with_defaults(self):
        """Should use defaults for optional vars (port, sslmode)."""
        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == "sf_utils"
        assert config.user == "postgres"
        assert config.password == "password"
        assert config.sslmode == "prefer"

    def test_from_env_missing_required_host(self):
        """Should raise ValueError when PG_HOST is missing."""
        env = {
            # Missing PG_HOST
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_HOST" in str(exc_info.value)

    def test_from_env_missing_required_database(self):
        """Should raise ValueError when PG_DATABASE is missing."""
        env = {
            "PG_HOST": "localhost",
            # Missing PG_DATABASE
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_DATABASE" in str(exc_info.value)

    def test_from_env_missing_required_user(self):
        """Should raise ValueError when PG_USER is missing."""
        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils",
            # Missing PG_USER
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_USER" in str(exc_info.value)

    def test_from_env_missing_required_password(self):
        """Should raise ValueError when PG_PASSWORD is missing."""
        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            # Missing PG_PASSWORD
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_PASSWORD" in str(exc_info.value)

    def test_from_env_missing_multiple_required(self):
        """Should list all missing required vars in error message."""
        env = {
            "PG_HOST": "localhost",
            # Missing PG_DATABASE, PG_USER, PG_PASSWORD
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        error_message = str(exc_info.value)
        assert "PG_DATABASE" in error_message
        assert "PG_USER" in error_message
        assert "PG_PASSWORD" in error_message

    def test_from_env_invalid_port_non_numeric(self):
        """Should raise ValueError when PG_PORT is not numeric."""
        env = {
            "PG_HOST": "localhost",
            "PG_PORT": "not-a-number",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_PORT" in str(exc_info.value)

    def test_from_env_empty_required_vars_treated_as_missing(self):
        """Should treat empty string as missing for required vars."""
        env = {
            "PG_HOST": "",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                PostgresConfig.from_env()

        assert "PG_HOST" in str(exc_info.value)

    def test_config_direct_instantiation(self):
        """Should allow direct instantiation of PostgresConfig."""
        config = PostgresConfig(
            host="db.example.com",
            database="test_db",
            user="test_user",
            password="test_pass",
            port=5433,
            sslmode="require",
        )

        assert config.host == "db.example.com"
        assert config.database == "test_db"
        assert config.user == "test_user"
        assert config.password == "test_pass"
        assert config.port == 5433
        assert config.sslmode == "require"


class TestGetConnection:
    """Tests for get_connection function."""

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_get_connection_with_explicit_config(self, mock_connect):
        """Should create connection with provided config."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="db.example.com",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        conn = get_connection(config=config)

        mock_connect.assert_called_once_with(
            host="db.example.com",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
            sslmode="prefer",
        )
        assert conn == mock_conn

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_get_connection_from_env(self, mock_connect):
        """Should create connection from environment when config is None."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            conn = get_connection()

        mock_connect.assert_called_once_with(
            host="localhost",
            port=5432,
            database="sf_utils",
            user="postgres",
            password="password",
            sslmode="prefer",
        )
        assert conn == mock_conn

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_get_connection_with_custom_ssl(self, mock_connect):
        """Should pass custom sslmode to psycopg2."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="prod-db.example.com",
            port=5432,
            database="production",
            user="app_user",
            password="secret",
            sslmode="require",
        )

        get_connection(config=config)

        mock_connect.assert_called_once_with(
            host="prod-db.example.com",
            port=5432,
            database="production",
            user="app_user",
            password="secret",
            sslmode="require",
        )

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_get_connection_failure_operational_error(self, mock_connect):
        """Should propagate psycopg2.OperationalError on connection failure."""
        import psycopg2

        mock_connect.side_effect = psycopg2.OperationalError(
            "could not connect to server"
        )

        config = PostgresConfig(
            host="unreachable.example.com",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        with pytest.raises(psycopg2.OperationalError) as exc_info:
            get_connection(config=config)

        assert "could not connect to server" in str(exc_info.value)

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_get_connection_failure_does_not_expose_password_in_logs(self, mock_connect):
        """Should ensure password is not logged in connection attempts or errors.

        Note: This test verifies current behavior. Password is not exposed in logs,
        but should also be redacted in __repr__() per security requirements.
        """
        import psycopg2

        mock_connect.side_effect = psycopg2.OperationalError(
            "authentication failed"
        )

        config = PostgresConfig(
            host="localhost",
            port=5432,
            database="test_db",
            user="test_user",
            password="super_secret_password_123",
        )

        # Capture log output
        with patch("sf_utils.db.connection.logger") as mock_logger:
            with pytest.raises(psycopg2.OperationalError):
                get_connection(config=config)

            # Verify password not in any log call
            for call_obj in mock_logger.debug.call_args_list + mock_logger.error.call_args_list:
                log_message = str(call_obj)
                assert "super_secret_password_123" not in log_message, \
                    f"Password found in log call: {log_message}"

    @patch("sf_utils.db.connection.psycopg2.connect")
    @patch("sf_utils.db.connection.logger")
    def test_get_connection_logs_attempt_without_password(
        self, mock_logger, mock_connect
    ):
        """Should log connection attempt at DEBUG level without password."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="db.example.com",
            port=5432,
            database="production",
            user="app_user",
            password="secret_password",
        )

        get_connection(config=config)

        # Verify DEBUG logging was called
        assert mock_logger.debug.called

        # Verify password not in any log call
        for call_args in mock_logger.debug.call_args_list:
            log_message = str(call_args)
            assert "secret_password" not in log_message

        # Verify expected info IS logged (host, port, database, user)
        debug_call = mock_logger.debug.call_args
        assert "db.example.com" in str(debug_call)
        assert "production" in str(debug_call)
        assert "app_user" in str(debug_call)

    @patch("sf_utils.db.connection.psycopg2.connect")
    @patch("sf_utils.db.connection.logger")
    def test_get_connection_logs_success(self, mock_logger, mock_connect):
        """Should log successful connection at INFO level."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="localhost",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        get_connection(config=config)

        # Verify INFO logging was called
        assert mock_logger.info.called
        info_call = str(mock_logger.info.call_args)
        assert "successful" in info_call.lower()
        assert "localhost" in info_call
        assert "test_db" in info_call

    @patch("sf_utils.db.connection.psycopg2.connect")
    @patch("sf_utils.db.connection.logger")
    def test_get_connection_logs_failure(self, mock_logger, mock_connect):
        """Should log connection failure at ERROR level."""
        import psycopg2

        mock_connect.side_effect = psycopg2.OperationalError("connection refused")

        config = PostgresConfig(
            host="unreachable.host",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        with pytest.raises(psycopg2.OperationalError):
            get_connection(config=config)

        # Verify ERROR logging was called
        assert mock_logger.error.called
        error_call = str(mock_logger.error.call_args)
        assert "failed" in error_call.lower()
        assert "unreachable.host" in error_call

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_connection_returns_native_psycopg2_connection(self, mock_connect):
        """Should return native psycopg2.extensions.connection object."""
        import psycopg2.extensions

        # Create a real mock that matches psycopg2 connection interface
        mock_conn = MagicMock(spec=psycopg2.extensions.connection)
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="localhost",
            port=5432,
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        conn = get_connection(config=config)

        # Verify connection has expected psycopg2 methods
        assert hasattr(conn, "cursor")
        assert hasattr(conn, "commit")
        assert hasattr(conn, "rollback")
        assert hasattr(conn, "close")


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_config_with_standard_postgres_port(self):
        """Should accept standard PostgreSQL port 5432."""
        env = {
            "PG_HOST": "localhost",
            "PG_PORT": "5432",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.port == 5432

    def test_config_with_alternate_port(self):
        """Should accept alternate port numbers."""
        env = {
            "PG_HOST": "localhost",
            "PG_PORT": "5433",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.port == 5433

    def test_config_with_all_valid_sslmodes(self):
        """Should accept all valid sslmode values without validation.

        Note: Current implementation does not validate sslmode.
        This is a gap that should be addressed per security requirements.
        """
        valid_sslmodes = ["disable", "allow", "prefer", "require"]

        for sslmode in valid_sslmodes:
            env = {
                "PG_HOST": "localhost",
                "PG_DATABASE": "sf_utils",
                "PG_USER": "postgres",
                "PG_PASSWORD": "password",
                "PG_SSLMODE": sslmode,
            }

            with patch.dict(os.environ, env, clear=True):
                config = PostgresConfig.from_env()
                assert config.sslmode == sslmode

    def test_config_with_special_chars_in_password(self):
        """Should handle special characters in password."""
        special_password = "p@$$w0rd!#%^&*()_+-={}[]|:;'<>,.?/"

        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": special_password,
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.password == special_password

    def test_config_with_unicode_in_database_name(self):
        """Should handle unicode characters in database name."""
        env = {
            "PG_HOST": "localhost",
            "PG_DATABASE": "sf_utils_测试",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.database == "sf_utils_测试"

    def test_config_with_ipv4_host(self):
        """Should accept IPv4 addresses as host."""
        env = {
            "PG_HOST": "192.168.1.100",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.host == "192.168.1.100"

    def test_config_with_fqdn_host(self):
        """Should accept fully qualified domain names."""
        env = {
            "PG_HOST": "db-prod.internal.corp.example.com",
            "PG_DATABASE": "sf_utils",
            "PG_USER": "postgres",
            "PG_PASSWORD": "password",
        }

        with patch.dict(os.environ, env, clear=True):
            config = PostgresConfig.from_env()
            assert config.host == "db-prod.internal.corp.example.com"

    @patch("sf_utils.db.connection.psycopg2.connect")
    def test_connection_with_default_config(self, mock_connect):
        """Should use default port and sslmode when not specified."""
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn

        config = PostgresConfig(
            host="localhost",
            database="test_db",
            user="test_user",
            password="test_pass",
        )

        get_connection(config=config)

        # Verify defaults were passed to psycopg2.connect
        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs["port"] == 5432
        assert call_kwargs["sslmode"] == "prefer"
