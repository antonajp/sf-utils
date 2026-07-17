"""Tests for client module."""

import os
from unittest.mock import patch, MagicMock

import pytest

from sf_utils.client import SalesforceConfig, get_client


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


class TestGetClient:
    """Tests for get_client function."""

    @patch("sf_utils.client.sfdc.client")
    def test_creates_client_with_config(self, mock_client_fn):
        """Should create client with provided config."""
        mock_client = MagicMock()
        mock_client.login.return_value = {"access_token": "token"}
        mock_client_fn.return_value = mock_client

        config = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
        )

        client = get_client(config=config)

        mock_client_fn.assert_called_once_with(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
            version="v61.0",
            login_url="login.salesforce.com",
        )
        mock_client.login.assert_called_once()
        assert client == mock_client

    @patch("sf_utils.client.sfdc.client")
    def test_creates_sandbox_client(self, mock_client_fn):
        """Should use sandbox login URL when sandbox=True."""
        mock_client = MagicMock()
        mock_client.login.return_value = {"access_token": "token"}
        mock_client_fn.return_value = mock_client

        config = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
            sandbox=True,
        )

        get_client(config=config)

        mock_client_fn.assert_called_once_with(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
            version="v61.0",
            login_url="test.salesforce.com",
        )

    @patch("sf_utils.client.sfdc.client")
    def test_skips_login_when_disabled(self, mock_client_fn):
        """Should not call login when login=False."""
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client

        config = SalesforceConfig(
            username="test@example.com",
            password="password",
            client_id="id",
            client_secret="secret",
        )

        client = get_client(config=config, login=False)

        mock_client.login.assert_not_called()
        assert client == mock_client
