"""Tests for client module."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest
from simple_salesforce.exceptions import SalesforceAuthenticationFailed

from sf_utils.client import SalesforceConfig, SalesforceJWTConfig, get_client, _detect_auth_method
from sf_utils.exceptions import SalesforceAuthError


class TestSalesforceConfig:
    """Tests for SalesforceConfig dataclass."""

    def test_from_env_with_all_vars(self):
        """Should load config from environment variables."""
        env = {
            "SF_USERNAME": "test@example.com",
            "SF_PASSWORD": "password123",
            "SF_CLIENT_ID": "client-id",
            "SF_CLIENT_SECRET": "client-secret",
            "SF_SANDBOX": "true",
            "SF_API_VERSION": "v60.0",
        }

        with patch.dict(os.environ, env, clear=True):
            config = SalesforceConfig.from_env()

        assert config.username == "test@example.com"
        assert config.password == "password123"
        assert config.client_id == "client-id"
        assert config.client_secret == "client-secret"
        assert config.sandbox is True
        assert config.api_version == "v60.0"

    def test_from_env_with_defaults(self):
        """Should use defaults for optional vars."""
        env = {
            "SF_USERNAME": "test@example.com",
            "SF_PASSWORD": "password123",
            "SF_CLIENT_ID": "client-id",
            "SF_CLIENT_SECRET": "client-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            config = SalesforceConfig.from_env()

        assert config.sandbox is False
        assert config.api_version == "v61.0"

    def test_from_env_missing_required(self):
        """Should raise ValueError when required vars missing."""
        env = {
            "SF_USERNAME": "test@example.com",
            # Missing password, client_id, client_secret
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                SalesforceConfig.from_env()

        assert "SF_PASSWORD" in str(exc_info.value)
        assert "SF_CLIENT_ID" in str(exc_info.value)
        assert "SF_CLIENT_SECRET" in str(exc_info.value)


class TestSalesforceJWTConfig:
    """Tests for SalesforceJWTConfig dataclass."""

    def test_from_env_with_all_vars(self, tmp_path):
        """Should load JWT config from environment variables."""
        # Create a temporary key file
        key_path = tmp_path / "server.key"
        key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----")

        env = {
            "SF_USERNAME": "test@example.com",
            "SF_CLIENT_ID": "client-id",
            "SF_PRIVATE_KEY_PATH": str(key_path),
            "SF_SANDBOX": "true",
            "SF_API_VERSION": "v60.0",
            "SF_PRIVATE_KEY_PASSPHRASE": "secret",
        }

        with patch.dict(os.environ, env, clear=True):
            config = SalesforceJWTConfig.from_env()

        assert config.username == "test@example.com"
        assert config.client_id == "client-id"
        assert config.private_key_path == key_path
        assert config.sandbox is True
        assert config.api_version == "v60.0"
        assert config.private_key_passphrase == "secret"

    def test_from_env_missing_private_key_path(self):
        """Should raise ValueError when private key path is missing."""
        env = {
            "SF_USERNAME": "test@example.com",
            "SF_CLIENT_ID": "client-id",
            # Missing SF_PRIVATE_KEY_PATH
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                SalesforceJWTConfig.from_env()

        assert "SF_PRIVATE_KEY_PATH" in str(exc_info.value)

    def test_from_env_nonexistent_key_file(self):
        """Should raise FileNotFoundError when key file doesn't exist."""
        env = {
            "SF_USERNAME": "test@example.com",
            "SF_CLIENT_ID": "client-id",
            "SF_PRIVATE_KEY_PATH": "/nonexistent/path/server.key",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(FileNotFoundError) as exc_info:
                SalesforceJWTConfig.from_env()

        assert "server.key" in str(exc_info.value)


class TestDetectAuthMethod:
    """Tests for auth method detection."""

    def test_detects_jwt_when_key_path_set(self):
        """Should detect JWT auth when SF_PRIVATE_KEY_PATH is set."""
        env = {
            "SF_PRIVATE_KEY_PATH": "/path/to/key.pem",
        }

        with patch.dict(os.environ, env, clear=True):
            assert _detect_auth_method() == "jwt"

    def test_detects_password_when_no_key_path(self):
        """Should detect password auth when SF_PRIVATE_KEY_PATH is not set."""
        env = {
            "SF_USERNAME": "test@example.com",
        }

        with patch.dict(os.environ, env, clear=True):
            assert _detect_auth_method() == "password"


class TestGetClient:
    """Tests for get_client function."""

    @patch("sf_utils.client.Salesforce")
    def test_creates_client_with_password_config(self, mock_salesforce_class):
        """Should create client with provided password config."""
        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client

        config = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
        )

        client = get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="test@example.com",
            password="password",
            consumer_key="id",
            consumer_secret="secret",
            domain="login",
            version="61.0",
        )
        assert client == mock_client

    @patch("sf_utils.client.Salesforce")
    def test_creates_sandbox_client(self, mock_salesforce_class):
        """Should use sandbox domain when sandbox=True."""
        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client

        config = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
            sandbox=True,
        )

        get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="test@example.com",
            password="password",
            consumer_key="id",
            consumer_secret="secret",
            domain="test",
            version="61.0",
        )

    @patch("sf_utils.client.Salesforce")
    def test_auth_failure_raises_salesforce_auth_error(self, mock_salesforce_class):
        """Should raise SalesforceAuthError when authentication fails."""
        mock_salesforce_class.side_effect = SalesforceAuthenticationFailed(
            code=401, auth_message="Invalid credentials"
        )

        config = SalesforceConfig(
            username="test@example.com",
            password="wrong",
            client_id="id",
            client_secret="secret",
        )

        with pytest.raises(SalesforceAuthError):
            get_client(config=config)

    @patch("sf_utils.client._load_private_key")
    @patch("sf_utils.client.Salesforce")
    def test_creates_jwt_client(self, mock_salesforce_class, mock_load_key, tmp_path):
        """Should create client with JWT config."""
        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client
        mock_load_key.return_value = "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"

        # Create a temporary key file
        key_path = tmp_path / "server.key"
        key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----")

        config = SalesforceJWTConfig(
            username="test@example.com",
            client_id="client-id",
            private_key_path=key_path,
        )

        client = get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="test@example.com",
            consumer_key="client-id",
            privatekey="-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
            domain="login",
            version="61.0",
        )
        assert client == mock_client

    @patch("sf_utils.client._load_private_key")
    @patch("sf_utils.client.Salesforce")
    def test_creates_jwt_sandbox_client(self, mock_salesforce_class, mock_load_key, tmp_path):
        """Should use test domain for JWT sandbox clients."""
        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client
        mock_load_key.return_value = "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"

        key_path = tmp_path / "server.key"
        key_path.write_text("-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----")

        config = SalesforceJWTConfig(
            username="test@example.com",
            client_id="client-id",
            private_key_path=key_path,
            sandbox=True,
        )

        get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="test@example.com",
            consumer_key="client-id",
            privatekey="-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
            domain="test",
            version="61.0",
        )

    @patch("sf_utils.client._detect_auth_method")
    @patch("sf_utils.client.SalesforceConfig.from_env")
    @patch("sf_utils.client.Salesforce")
    def test_auto_detects_password_auth(self, mock_salesforce_class, mock_config_from_env, mock_detect):
        """Should auto-detect password auth when no config provided."""
        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client
        mock_detect.return_value = "password"
        mock_config_from_env.return_value = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
        )

        client = get_client()

        mock_detect.assert_called_once()
        mock_config_from_env.assert_called_once()
        assert client == mock_client
