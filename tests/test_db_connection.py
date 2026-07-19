"""Tests for PostgreSQL database connection module.

Tests cover:
- PostgresConfig dataclass and environment loading
- get_connection function with various configurations
- Connection error handling and validation
- Password redaction in logs and repr
- Input validation for security
- execute_query function with parameterized queries
"""

import os
from unittest.mock import patch, MagicMock, call

import pytest
from psycopg2 import DatabaseError

from sf_utils.db import PostgresConfig, execute_query, get_connection


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


class TestExecuteQuery:
    """Tests for execute_query parameterized query function."""

    def test_select_query_returns_rows(self):
        """Should execute SELECT query and return rows when fetch=True."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "Account A", "active"),
            (2, "Account B", "inactive"),
        ]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col1}, {col2}, {col3} FROM {table} WHERE {status_col} = %s",
            identifiers={
                "col1": "id",
                "col2": "name",
                "col3": "status",
                "table": "accounts",
                "status_col": "status",
            },
            params=("active",),
            fetch=True,
        )

        assert len(rows) == 2
        assert rows[0] == (1, "Account A", "active")
        assert rows[1] == (2, "Account B", "inactive")
        mock_cursor.execute.assert_called_once()
        mock_cursor.fetchall.assert_called_once()

    def test_insert_query_returns_row_count(self):
        """Should execute INSERT query and return affected row count when fetch=False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        count = execute_query(
            mock_conn,
            "INSERT INTO {table} ({col1}, {col2}) VALUES (%s, %s)",
            identifiers={"table": "accounts", "col1": "name", "col2": "status"},
            params=("New Account", "active"),
            fetch=False,
        )

        assert count == 1
        mock_cursor.execute.assert_called_once()
        assert not mock_cursor.fetchall.called

    def test_update_query_returns_row_count(self):
        """Should execute UPDATE query and return affected row count when fetch=False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 3
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        count = execute_query(
            mock_conn,
            "UPDATE {table} SET {col} = %s WHERE {filter_col} = %s",
            identifiers={"table": "accounts", "col": "status", "filter_col": "type"},
            params=("inactive", "customer"),
            fetch=False,
        )

        assert count == 3
        mock_cursor.execute.assert_called_once()

    def test_delete_query_returns_row_count(self):
        """Should execute DELETE query and return affected row count when fetch=False."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 5
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        count = execute_query(
            mock_conn,
            "DELETE FROM {table} WHERE {col} = %s",
            identifiers={"table": "accounts", "col": "status"},
            params=("archived",),
            fetch=False,
        )

        assert count == 5
        mock_cursor.execute.assert_called_once()

    def test_query_with_no_identifiers(self):
        """Should execute query without identifiers (only params)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(42,)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT COUNT(*) FROM accounts WHERE status = %s",
            identifiers=None,
            params=("active",),
            fetch=True,
        )

        assert rows == [(42,)]
        mock_cursor.execute.assert_called_once()

    def test_query_with_no_params(self):
        """Should execute query without params (only identifiers)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1,), (2,), (3,)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col} FROM {table}",
            identifiers={"col": "id", "table": "accounts"},
            params=None,
            fetch=True,
        )

        assert len(rows) == 3
        mock_cursor.execute.assert_called_once()

    def test_query_with_no_identifiers_or_params(self):
        """Should execute simple query without identifiers or params."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(5,)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT COUNT(*) FROM accounts",
            fetch=True,
        )

        assert rows == [(5,)]
        mock_cursor.execute.assert_called_once()

    def test_query_with_multiple_params(self):
        """Should handle multiple parameter values."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "Account A"),
            (2, "Account B"),
        ]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col1}, {col2} FROM {table} WHERE {col3} = %s AND {col4} > %s",
            identifiers={
                "col1": "id",
                "col2": "name",
                "table": "accounts",
                "col3": "status",
                "col4": "created_date",
            },
            params=("active", "2024-01-01"),
            fetch=True,
        )

        assert len(rows) == 2
        # Verify params were passed correctly
        execute_call = mock_cursor.execute.call_args
        assert execute_call[0][1] == ("active", "2024-01-01")

    def test_query_uses_sql_identifier_for_safety(self):
        """Should use psycopg2.sql.Identifier to prevent SQL injection on table/column names."""
        from psycopg2 import sql

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # Try to inject SQL via identifier - should be safely quoted
        execute_query(
            mock_conn,
            "SELECT {col} FROM {table}",
            identifiers={"col": "id", "table": "accounts; DROP TABLE users--"},
            fetch=True,
        )

        # Verify query was constructed with SQL identifiers
        execute_call = mock_cursor.execute.call_args
        query_obj = execute_call[0][0]

        # psycopg2.sql.SQL objects are composable, not plain strings
        assert isinstance(query_obj, sql.Composable)

    def test_query_logs_structure_not_params(self):
        """Should log query structure but NEVER log parameter values (security)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        sensitive_password = "super_secret_password_123"

        with patch("sf_utils.db.connection.logger") as mock_logger:
            execute_query(
                mock_conn,
                "SELECT {col} FROM {table} WHERE password = %s",
                identifiers={"col": "id", "table": "users"},
                params=(sensitive_password,),
                fetch=True,
            )

            # Verify password not in any log call
            for call_obj in mock_logger.debug.call_args_list:
                log_message = str(call_obj)
                assert sensitive_password not in log_message, \
                    f"Sensitive parameter found in log: {log_message}"

    def test_query_raises_database_error_on_execution_failure(self):
        """Should raise DatabaseError when query execution fails."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = DatabaseError("syntax error")
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with pytest.raises(DatabaseError) as exc_info:
            execute_query(
                mock_conn,
                "INVALID SQL {table}",
                identifiers={"table": "accounts"},
                fetch=True,
            )

        assert "syntax error" in str(exc_info.value)

    def test_query_raises_value_error_on_missing_identifier(self):
        """Should raise ValueError when identifier placeholder is missing from dict."""
        mock_conn = MagicMock()

        with pytest.raises(ValueError) as exc_info:
            execute_query(
                mock_conn,
                "SELECT {col1}, {col2} FROM {table}",
                identifiers={"col1": "id", "table": "accounts"},  # Missing col2
                fetch=True,
            )

        assert "col2" in str(exc_info.value)

    def test_query_with_schema_qualified_table(self):
        """Should handle schema-qualified table names."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1,), (2,)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col} FROM {schema}.{table}",
            identifiers={"col": "id", "schema": "public", "table": "accounts"},
            fetch=True,
        )

        assert len(rows) == 2
        mock_cursor.execute.assert_called_once()

    def test_query_returns_empty_list_when_no_rows(self):
        """Should return empty list when SELECT returns no rows."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col} FROM {table} WHERE {filter_col} = %s",
            identifiers={"col": "id", "table": "accounts", "filter_col": "status"},
            params=("nonexistent",),
            fetch=True,
        )

        assert rows == []

    def test_query_returns_zero_when_no_rows_affected(self):
        """Should return 0 when UPDATE/DELETE affects no rows."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        count = execute_query(
            mock_conn,
            "UPDATE {table} SET {col} = %s WHERE {filter_col} = %s",
            identifiers={"table": "accounts", "col": "status", "filter_col": "id"},
            params=("inactive", 99999),
            fetch=False,
        )

        assert count == 0

    def test_query_with_complex_identifiers(self):
        """Should handle multiple identifiers in complex query."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(100, 50, 25)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            """
            SELECT
                COUNT({col1}) as total,
                COUNT(CASE WHEN {col2} = %s THEN 1 END) as active,
                COUNT(CASE WHEN {col2} = %s THEN 1 END) as inactive
            FROM {table}
            WHERE {col3} > %s
            """,
            identifiers={
                "col1": "id",
                "col2": "status",
                "col3": "created_date",
                "table": "accounts",
            },
            params=("active", "inactive", "2024-01-01"),
            fetch=True,
        )

        assert rows == [(100, 50, 25)]

    def test_query_logging_debug_level(self):
        """Should log at DEBUG level for query execution."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with patch("sf_utils.db.connection.logger") as mock_logger:
            execute_query(
                mock_conn,
                "SELECT {col} FROM {table}",
                identifiers={"col": "id", "table": "accounts"},
                fetch=True,
            )

            # Verify DEBUG logging was called
            assert mock_logger.debug.called
            # Should log: preparing, executing, and result count
            assert mock_logger.debug.call_count >= 3

    def test_query_closes_cursor_on_success(self):
        """Should properly close cursor using context manager on success."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        execute_query(
            mock_conn,
            "SELECT {col} FROM {table}",
            identifiers={"col": "id", "table": "accounts"},
            fetch=True,
        )

        # Verify context manager was used (cursor.__exit__ called)
        assert mock_conn.cursor.return_value.__exit__.called

    def test_query_closes_cursor_on_error(self):
        """Should properly close cursor using context manager even on error."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = DatabaseError("error")
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with pytest.raises(DatabaseError):
            execute_query(
                mock_conn,
                "SELECT {col} FROM {table}",
                identifiers={"col": "id", "table": "accounts"},
                fetch=True,
            )

        # Verify context manager __exit__ was called (cursor cleanup)
        assert mock_conn.cursor.return_value.__exit__.called

    def test_query_with_special_chars_in_params(self):
        """Should safely handle special characters in parameter values."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, "O'Reilly's Account")]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        # SQL injection attempt via parameter - should be safely escaped by psycopg2
        dangerous_value = "'; DROP TABLE users; --"

        rows = execute_query(
            mock_conn,
            "SELECT {col1}, {col2} FROM {table} WHERE {col2} = %s",
            identifiers={"col1": "id", "col2": "name", "table": "accounts"},
            params=(dangerous_value,),
            fetch=True,
        )

        # Verify params were passed to execute (psycopg2 will safely escape them)
        execute_call = mock_cursor.execute.call_args
        assert execute_call[0][1] == (dangerous_value,)

    def test_query_with_null_param(self):
        """Should handle NULL parameter values."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, None)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col1}, {col2} FROM {table} WHERE {col2} IS NULL OR {col2} = %s",
            identifiers={"col1": "id", "col2": "description", "table": "accounts"},
            params=(None,),
            fetch=True,
        )

        assert rows[0][1] is None

    def test_query_with_numeric_params(self):
        """Should handle numeric parameter values (int, float)."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(1, 100.50)]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        rows = execute_query(
            mock_conn,
            "SELECT {col1}, {col2} FROM {table} WHERE {col1} = %s AND {col2} > %s",
            identifiers={"col1": "id", "col2": "amount", "table": "transactions"},
            params=(123, 99.99),
            fetch=True,
        )

        execute_call = mock_cursor.execute.call_args
        assert execute_call[0][1] == (123, 99.99)
