"""Tests for JWT Bearer OAuth flow authentication.

Tests the JWT authentication implementation using simple-salesforce's
native JWT Bearer flow support.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from simple_salesforce.exceptions import SalesforceAuthenticationFailed

from sf_utils.client import (
    SalesforceJWTConfig,
    get_client,
    _load_private_key,
    _detect_auth_method,
)
from sf_utils.exceptions import SalesforceAuthError


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def mock_private_key():
    """Mock RSA private key in PEM format."""
    return """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAy8Dbv8prpJ/0kKhlGeJYozo2t60EG8L0561g13R29LvMR5hy
vGZlGJpmn65+A4xHXInJYiPuKzrKUnApeLZ+vw1HocOAZtWK0z3r26uA8kQYOKX9
Qt/DbCdvsF9wF8gRK0ptx9M6R13NvBxvVQApfc9jB9nTzphOgM4JiEYvlV8FLhg9
yZovMYd6Wwf3aoXK891VQxTr/kQYoq1Yp+68i6T4nNq7NWC+UNVjQHxNQMQMzU6l
WCX8zyg13yitsKTiMNvPh5a1EfNpk0qVfJqSNJdYVSxg9KZ4KaGjqK6v2DK8KFzB
z+J2lWPVpEKiKEpRXnKpGzKwN8rqcHqEfNSALwIDAQABAoIBAD5Eo5KqanPPr+KD
h3vkExN6vrcC8b6JCVBH48LXKYc5+OKt9YhSz8YLqZF0emRjpUj5BjNqGvD8M3n5
dJf5qKq7N/YrOKKgmVnLM6sLxCJzLvKaOPqCL8gM6Yo2wj9jq9Hp1p1k3bDLWGfQ
pFpPFCr+PHe1qGlcT8ZiCLKqLRQkkpXuPvKqD2lXEPVc1xkw8K4qe3AJ7vPZYhH9
I0qLZj6MXHF4J5pCJZLVCVq4rU2Z6YqLmRJG5zcMDMvkjGYMF6kbPVnMqG5XJhCQ
6t7V5K5rqGXLjqtBCaGxZKfVv5KeGFzNMpY7LR8jMF1LG8qTQF1u9jqnPQJYzvQv
8kJCLQECgYEA9bGCCRlKHKlGzNqKfHVLBqZBPuXiJ5hqNxQ6YQwDtUDxJvKvNgcR
r8KJB7K9xMzRuRYnHHsUlBQRqUBF8u8yPvXRqVSHJGBPRpWJZQGFfOvjFOD8qdNP
EQp9dKzDDqh8+Zu8nXrXYLYSPWTqJFNqfYMKmKwcMzQMdLoLAKvCpb8CgYEA1Fvw
Yt5p3t8L3CJVK8N8TkYmqNHLfKJW3JLLBZ5TGxMZD2KE/s5QKz3QmE0tR8yNXBQx
F5XD5BXNvPvDVR3KQHK7dLBEUpQxZDZ2kGGLj6qGRJqb6a7H8oNLGKVqGxKDZJ9B
6/iXjKp2gJQGfJhCqLM2hJCLLqL0uFvGxWCYWAECgYB9HXOhpLwXLy3ULJEJiKlQ
sL3AKGLfPQKGxQvD+tL3pGLwU7gJKOvPNy6VmVqDDqLJBQd1DLM7cKXpGmF7JLDT
fLNPFYF3d3LLzGqJLKCcqBQdDXqC3LPLpYXQAJULCLhxVL6VfQPrKRiNQQKKKP1a
VxRYqQQRhqCq2vqLKQ7pQwKBgQCPUv3bKf3q8C6kGy3F1pFQx7VCqBRLHJqJCJZB
jVnmQJKKP6YpQRqGKxLqBfQJhZJKkZLqLxLqLCLpYxQQQRqGVfJKKPqL3LpYQQRq
GKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPL
pYQQRqAQKBgQDCVfJKKPqL3LpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPL
pYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQ
RqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGKPLpYQQRqGK
PL==
-----END RSA PRIVATE KEY-----"""


@pytest.fixture
def jwt_env_vars(tmp_path, mock_private_key):
    """Environment variables for JWT Bearer flow authentication."""
    key_path = tmp_path / "server.key"
    key_path.write_text(mock_private_key)

    return {
        "SF_USERNAME": "integration@example.com",
        "SF_CLIENT_ID": "3MVG9YDQS5WtC11...",
        "SF_PRIVATE_KEY_PATH": str(key_path),
        "SF_SANDBOX": "false",
        "SF_API_VERSION": "v61.0",
    }


# ============================================================================
# Configuration Tests
# ============================================================================

class TestJWTConfiguration:
    """Test JWT-specific configuration loading and validation."""

    def test_load_jwt_config_from_env(self, jwt_env_vars):
        """Should load JWT configuration from environment variables."""
        with patch.dict(os.environ, jwt_env_vars, clear=True):
            config = SalesforceJWTConfig.from_env()

        assert config.username == "integration@example.com"
        assert config.client_id == "3MVG9YDQS5WtC11..."
        assert config.private_key_path.exists()
        assert config.sandbox is False

    def test_jwt_config_missing_private_key(self):
        """Should raise ValueError when private key path is missing."""
        env = {
            "SF_USERNAME": "user@example.com",
            "SF_CLIENT_ID": "client_id",
            # Missing SF_PRIVATE_KEY_PATH
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError) as exc_info:
                SalesforceJWTConfig.from_env()

        assert "SF_PRIVATE_KEY_PATH" in str(exc_info.value)

    def test_jwt_config_nonexistent_key_file(self):
        """Should raise FileNotFoundError when private key file doesn't exist."""
        env = {
            "SF_USERNAME": "user@example.com",
            "SF_CLIENT_ID": "client_id",
            "SF_PRIVATE_KEY_PATH": "/nonexistent/path/server.key",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(FileNotFoundError) as exc_info:
                SalesforceJWTConfig.from_env()

        assert "server.key" in str(exc_info.value)

    def test_jwt_config_sandbox_setting(self, tmp_path, mock_private_key):
        """Should use sandbox setting from environment."""
        key_path = tmp_path / "server.key"
        key_path.write_text(mock_private_key)

        env = {
            "SF_USERNAME": "user@example.com.sandbox",
            "SF_CLIENT_ID": "client_id",
            "SF_PRIVATE_KEY_PATH": str(key_path),
            "SF_SANDBOX": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            config = SalesforceJWTConfig.from_env()

        assert config.sandbox is True


# ============================================================================
# Auth Method Detection Tests
# ============================================================================

class TestAuthMethodDetection:
    """Test authentication method auto-detection."""

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


# ============================================================================
# JWT Client Creation Tests
# ============================================================================

class TestJWTClient:
    """Test JWT client creation."""

    @patch("sf_utils.client._load_private_key")
    @patch("sf_utils.client.Salesforce")
    def test_get_jwt_client_success(self, mock_salesforce_class, mock_load_key, jwt_env_vars):
        """Should create authenticated client using JWT Bearer flow."""
        mock_client = MagicMock()
        mock_client.sf_instance = "na1.salesforce.com"
        mock_salesforce_class.return_value = mock_client
        mock_load_key.return_value = "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"

        with patch.dict(os.environ, jwt_env_vars, clear=True):
            config = SalesforceJWTConfig.from_env()
            client = get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="integration@example.com",
            consumer_key="3MVG9YDQS5WtC11...",
            privatekey="-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
            domain="login",
            version="61.0",
        )
        assert client == mock_client

    @patch("sf_utils.client._load_private_key")
    @patch("sf_utils.client.Salesforce")
    def test_get_jwt_client_sandbox(self, mock_salesforce_class, mock_load_key, tmp_path, mock_private_key):
        """Should use test domain for sandbox orgs."""
        key_path = tmp_path / "server.key"
        key_path.write_text(mock_private_key)

        mock_client = MagicMock()
        mock_salesforce_class.return_value = mock_client
        mock_load_key.return_value = "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"

        config = SalesforceJWTConfig(
            username="test@example.com.sandbox",
            client_id="client_id",
            private_key_path=key_path,
            sandbox=True,
        )

        get_client(config=config)

        mock_salesforce_class.assert_called_once_with(
            username="test@example.com.sandbox",
            consumer_key="client_id",
            privatekey="-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----",
            domain="test",
            version="61.0",
        )

    @patch("sf_utils.client._load_private_key")
    @patch("sf_utils.client.Salesforce")
    def test_jwt_auth_failure_raises_error(self, mock_salesforce_class, mock_load_key, tmp_path, mock_private_key):
        """Should raise SalesforceAuthError when JWT auth fails."""
        key_path = tmp_path / "server.key"
        key_path.write_text(mock_private_key)

        mock_load_key.return_value = "-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----"
        mock_salesforce_class.side_effect = SalesforceAuthenticationFailed(
            code=400, auth_message="invalid_grant"
        )

        config = SalesforceJWTConfig(
            username="test@example.com",
            client_id="client_id",
            private_key_path=key_path,
        )

        with pytest.raises(SalesforceAuthError):
            get_client(config=config)


# ============================================================================
# Private Key Loading Tests
# ============================================================================

class TestPrivateKeyLoading:
    """Test RSA private key loading and validation."""

    def test_load_valid_private_key(self, tmp_path):
        """Should load valid RSA private key."""
        # Generate a minimal valid key for testing
        # In real tests, we'd use a proper RSA key
        key_content = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MfszT7Xm8wMcmRz1
dPBnQcFYOD2wjwCsNKGmdtPHEfZ+FbnNflQVdoNzk6TRaH0BSj8zDBsfaAP2gDbL
TpDqIJaYWZOz3k5RpPVh0sYoVvP5rxHQiG9HdDMJ9xlU0kqOiY1g4K6Y8b0PXlHS
MYvPCkMJaEiXHsXMh7/tz9M9bTp2kGiKBqKEJYSJw5PSbf2rPHQhRMK8RdYdLXcE
9Z5dNdAqE3FaZ7h0RkYcgKc8Lq0w3bN8EYSM9NhLrYMYdw4p3T6UMbP5FP3F0z6z
0f8Y8KMYz+KqDpMdXE0M4Oz8YDd0wC7zXq9hNwIDAQABAoIBAAi8C/PfPzzA3DG2
pS4OA+h0xGWPRBkEC9j6MXtFcpD6VEjRxe3P2S8dNX9j1+YL9Qm8U+l4rlCMT9Tp
3xY3C5rZ5PG7MYS3FT4J4V5OWPEsQ8LmQOWTvJXoYvNk8oREJKMBDSqGRi+bKxFz
yJuO6k2V4lZ9fJr9M7+L8TN7fWPwCas7QBVRJqCQ4N3aXdnpChHYBhe3h7yf6dgK
7FoF/VGQCwxrRTLKdC5PCAJ4aHxrBqKiXZthHKLxG8X7c7pPHfxXMBM0E4LQSMBC
G3D0C+DE0L3f6qZCjZr6pf3a9z5Rg5a3F0p3F1nVcZ0c+Zd6iQxREQDJ3wYzTRB5
PbMm8oECgYEA7yt5TcKCxJzyG6A1zTkRC7Da0tsHF5jhLtE0IYbYrtDj4k9n2L0p
4HCVJl9I7N1K8N5EB8l0uh05L3jQVn+vdmP3Xnr1jP3k7jBvY0G7EHQX3Gw3N3E3
N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3E3N3ECgYEA
4E0H8nKLBhx1HJJCr3B3FNi6lPe0Gn5B3zYBJB0JB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5Z
B5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5ZB5cC
gYEAq8V8X8D8F8E8B8A8s8r8q8p8o8n8m8l8k8j8i8h8g8f8e8d8c8b8a8Z8Y8X8W
8V8U8T8S8R8Q8P8O8N8M8L8K8J8I8H8G8F8E8D8C8B8A8z8y8x8w8v8u8t8sECgYB
P8O8N8M8L8K8J8I8H8G8F8E8D8C8B8A8z8y8x8w8v8u8t8s8r8q8p8o8n8m8l8k8j
8i8h8g8f8e8d8c8b8a8Z8Y8X8W8V8U8T8S8R8Q8P8O8N8M8L8K8J8I8H8G8FCgYAv
8u8t8s8r8q8p8o8n8m8l8k8j8i8h8g8f8e8d8c8b8a8Z8Y8X8W8V8U8T8S8R8Q8P8
O8N8M8L8K8J8I8H8G8F8E8D8C8B8A8z8y8x8w8v8u8t8s8r8q8p8o8n8m8l8k==
-----END RSA PRIVATE KEY-----"""
        key_path = tmp_path / "server.key"
        key_path.write_text(key_content)

        # The key above is not valid for cryptographic operations,
        # so we'll mock the validation
        with patch("sf_utils.client.serialization.load_pem_private_key"):
            result = _load_private_key(key_path)

        assert "BEGIN RSA PRIVATE KEY" in result

    def test_load_nonexistent_key_file(self, tmp_path):
        """Should raise FileNotFoundError for nonexistent file."""
        key_path = tmp_path / "nonexistent.key"

        with pytest.raises(FileNotFoundError):
            _load_private_key(key_path)

    def test_load_invalid_key_format(self, tmp_path):
        """Should raise ValueError for invalid key format."""
        key_path = tmp_path / "invalid.key"
        key_path.write_text("not-a-valid-key")

        with pytest.raises(ValueError) as exc_info:
            _load_private_key(key_path)

        assert "Invalid private key format" in str(exc_info.value)


# ============================================================================
# Integration Test Markers
# ============================================================================

@pytest.mark.integration
@pytest.mark.skip(reason="Requires real Salesforce Connected App with MFA")
class TestJWTRealSalesforce:
    """Integration tests against real Salesforce org (manual execution only).

    These tests CANNOT run in CI/CD pipelines as they require:
    1. Real Connected App configured in Salesforce
    2. Real RSA certificate uploaded to Connected App
    3. Real user pre-authorized for JWT Bearer flow
    4. Private key file accessible to test environment

    To run these tests locally:
    1. Set up Connected App in Salesforce with certificate
    2. Create .env.jwt file with real credentials
    3. Run: pytest tests/test_jwt_auth.py::TestJWTRealSalesforce -m integration
    """

    def test_real_jwt_authentication(self):
        """Authenticate with real Salesforce org using JWT Bearer flow."""
        pytest.skip("Manual integration test only - requires real SF org")
