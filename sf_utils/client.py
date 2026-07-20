"""Salesforce client connection management.

Supports two authentication methods:
1. Password OAuth flow (legacy, for non-MFA accounts)
2. JWT Bearer OAuth flow (recommended, for MFA-enabled accounts)

The authentication method is auto-detected from environment variables:
- If SF_PRIVATE_KEY_PATH is set → JWT Bearer flow
- Otherwise → Password flow
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceAuthenticationFailed

from sf_utils.exceptions import SalesforceAuthError

logger = logging.getLogger(__name__)


@dataclass
class SalesforceConfig:
    """Configuration for Salesforce password-based connection."""

    username: str
    password: str
    client_id: str
    client_secret: str
    sandbox: bool = False
    api_version: str = "v61.0"

    @classmethod
    def from_env(cls) -> "SalesforceConfig":
        """Load configuration from environment variables.

        Expects: SF_USERNAME, SF_PASSWORD, SF_CLIENT_ID, SF_CLIENT_SECRET
        Optional: SF_SANDBOX (bool), SF_API_VERSION
        """
        load_dotenv()

        username = os.environ.get("SF_USERNAME")
        password = os.environ.get("SF_PASSWORD")
        client_id = os.environ.get("SF_CLIENT_ID")
        client_secret = os.environ.get("SF_CLIENT_SECRET")

        missing = []
        if not username:
            missing.append("SF_USERNAME")
        if not password:
            missing.append("SF_PASSWORD")
        if not client_id:
            missing.append("SF_CLIENT_ID")
        if not client_secret:
            missing.append("SF_CLIENT_SECRET")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        sandbox_str = os.environ.get("SF_SANDBOX", "false").lower()
        sandbox = sandbox_str in ("true", "1", "yes")

        api_version = os.environ.get("SF_API_VERSION", "v61.0")

        return cls(
            username=username,
            password=password,
            client_id=client_id,
            client_secret=client_secret,
            sandbox=sandbox,
            api_version=api_version,
        )


@dataclass
class SalesforceJWTConfig:
    """Configuration for Salesforce JWT Bearer OAuth flow.

    JWT Bearer flow is required for MFA-enabled accounts and is the
    recommended authentication method for production integrations.

    Attributes:
        username: Salesforce username (email).
        client_id: Connected App Consumer Key.
        private_key_path: Path to RSA private key file (PEM format).
        sandbox: If True, use test.salesforce.com.
        api_version: Salesforce API version (e.g., "v61.0").
        private_key_passphrase: Optional passphrase for encrypted private key.
    """

    username: str
    client_id: str
    private_key_path: Path
    sandbox: bool = False
    api_version: str = "v61.0"
    private_key_passphrase: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> "SalesforceJWTConfig":
        """Load JWT configuration from environment variables.

        Expects: SF_USERNAME, SF_CLIENT_ID, SF_PRIVATE_KEY_PATH
        Optional: SF_SANDBOX (bool), SF_API_VERSION, SF_PRIVATE_KEY_PASSPHRASE

        Returns:
            SalesforceJWTConfig instance.

        Raises:
            ValueError: If required variables are missing.
            FileNotFoundError: If private key file doesn't exist.
        """
        load_dotenv()

        username = os.environ.get("SF_USERNAME")
        client_id = os.environ.get("SF_CLIENT_ID")
        private_key_path_str = os.environ.get("SF_PRIVATE_KEY_PATH")

        missing = []
        if not username:
            missing.append("SF_USERNAME")
        if not client_id:
            missing.append("SF_CLIENT_ID")
        if not private_key_path_str:
            missing.append("SF_PRIVATE_KEY_PATH")

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        private_key_path = Path(private_key_path_str)
        if not private_key_path.exists():
            raise FileNotFoundError(
                f"Private key file not found: {private_key_path}"
            )

        sandbox_str = os.environ.get("SF_SANDBOX", "false").lower()
        sandbox = sandbox_str in ("true", "1", "yes")

        api_version = os.environ.get("SF_API_VERSION", "v61.0")
        passphrase = os.environ.get("SF_PRIVATE_KEY_PASSPHRASE")

        return cls(
            username=username,
            client_id=client_id,
            private_key_path=private_key_path,
            sandbox=sandbox,
            api_version=api_version,
            private_key_passphrase=passphrase,
        )


def _load_private_key(key_path: Path, passphrase: Optional[str] = None) -> str:
    """Load and validate RSA private key from PEM file.

    Args:
        key_path: Path to PEM-formatted private key file.
        passphrase: Optional passphrase for encrypted keys.

    Returns:
        Private key as PEM string.

    Raises:
        ValueError: If key format is invalid.
        FileNotFoundError: If key file doesn't exist.
    """
    logger.debug("Loading private key from %s", key_path)

    key_data = key_path.read_bytes()

    # Validate key format by attempting to load it
    try:
        passphrase_bytes = passphrase.encode() if passphrase else None
        serialization.load_pem_private_key(
            key_data,
            password=passphrase_bytes,
            backend=default_backend()
        )
    except Exception as e:
        logger.error("Invalid private key format: %s", str(e))
        raise ValueError(f"Invalid private key format: {str(e)}") from e

    logger.debug("Private key loaded successfully")
    return key_data.decode()


def _detect_auth_method() -> str:
    """Detect authentication method from environment variables.

    Returns:
        'jwt' if SF_PRIVATE_KEY_PATH is set, 'password' otherwise.
    """
    load_dotenv()

    if os.environ.get("SF_PRIVATE_KEY_PATH"):
        logger.debug("Detected JWT Bearer auth method (SF_PRIVATE_KEY_PATH set)")
        return "jwt"
    else:
        logger.debug("Detected password auth method")
        return "password"


def get_client(
    config: Optional[Union[SalesforceConfig, SalesforceJWTConfig]] = None,
    login: bool = True,
) -> Salesforce:
    """Create and optionally authenticate a Salesforce client.

    Automatically detects authentication method from environment variables:
    - If SF_PRIVATE_KEY_PATH is set → JWT Bearer flow
    - Otherwise → Password flow

    Args:
        config: Salesforce configuration. If None, loads from environment
            and auto-detects authentication method.
        login: Whether to authenticate immediately. Defaults to True.
            For simple-salesforce, authentication always happens at construction.

    Returns:
        Authenticated Salesforce client.

    Raises:
        ValueError: If required configuration is missing.
        SalesforceAuthError: If authentication fails.
        FileNotFoundError: If JWT private key file doesn't exist.
    """
    if config is None:
        # Auto-detect authentication method
        auth_method = _detect_auth_method()
        if auth_method == "jwt":
            config = SalesforceJWTConfig.from_env()
        else:
            config = SalesforceConfig.from_env()

    if isinstance(config, SalesforceJWTConfig):
        return _get_jwt_client(config)
    else:
        return _get_password_client(config, login=login)


def _get_password_client(
    config: SalesforceConfig,
    login: bool = True,
) -> Salesforce:
    """Create a Salesforce client using password OAuth flow.

    Args:
        config: Password-based Salesforce configuration.
        login: Whether to authenticate immediately.

    Returns:
        Authenticated Salesforce client.

    Raises:
        SalesforceAuthError: If authentication fails.
    """
    logger.debug(
        "Creating Salesforce client (password flow) for user=%s sandbox=%s version=%s",
        config.username,
        config.sandbox,
        config.api_version,
    )

    # Determine domain based on sandbox setting
    domain = "test" if config.sandbox else "login"

    # Strip 'v' prefix from version for simple-salesforce
    version = config.api_version.lstrip("v")

    try:
        if login:
            logger.debug("Authenticating with Salesforce")
            client = Salesforce(
                username=config.username,
                password=config.password,
                consumer_key=config.client_id,
                consumer_secret=config.client_secret,
                domain=domain,
                version=version,
            )
            logger.debug("Salesforce login successful")
        else:
            # simple-salesforce always authenticates on construction
            # To skip login, we'd need to pass session_id directly
            # For now, just authenticate
            logger.debug("Note: simple-salesforce authenticates on construction")
            client = Salesforce(
                username=config.username,
                password=config.password,
                consumer_key=config.client_id,
                consumer_secret=config.client_secret,
                domain=domain,
                version=version,
            )

        return client

    except SalesforceAuthenticationFailed as e:
        logger.error("Salesforce login failed: %s", str(e))
        raise SalesforceAuthError(
            message=f"Salesforce login failed: {str(e)}"
        ) from e
    except Exception as e:
        logger.error("Unexpected error during Salesforce login: %s", str(e))
        raise SalesforceAuthError(
            message=f"Salesforce login failed - check credentials: {str(e)}"
        ) from e


def _get_jwt_client(config: SalesforceJWTConfig) -> Salesforce:
    """Create a Salesforce client using JWT Bearer OAuth flow.

    JWT Bearer flow is required for MFA-enabled accounts.

    Args:
        config: JWT-based Salesforce configuration.

    Returns:
        Authenticated Salesforce client.

    Raises:
        SalesforceAuthError: If authentication fails.
        ValueError: If private key format is invalid.
    """
    logger.debug(
        "Creating Salesforce client (JWT flow) for user=%s sandbox=%s version=%s",
        config.username,
        config.sandbox,
        config.api_version,
    )

    # Load private key
    private_key = _load_private_key(
        config.private_key_path,
        config.private_key_passphrase
    )

    # Determine domain based on sandbox setting
    domain = "test" if config.sandbox else "login"

    # Strip 'v' prefix from version for simple-salesforce
    version = config.api_version.lstrip("v")

    try:
        logger.debug("Authenticating with Salesforce via JWT Bearer flow")

        # simple-salesforce natively supports JWT Bearer flow
        client = Salesforce(
            username=config.username,
            consumer_key=config.client_id,
            privatekey=private_key,
            domain=domain,
            version=version,
        )

        logger.info(
            "Salesforce JWT login successful: instance=%s",
            client.sf_instance
        )

        return client

    except SalesforceAuthenticationFailed as e:
        logger.error("Salesforce JWT login failed: %s", str(e))
        raise SalesforceAuthError(
            message=f"Salesforce JWT login failed: {str(e)}"
        ) from e
    except Exception as e:
        logger.error("Unexpected error during Salesforce JWT login: %s", str(e))
        raise SalesforceAuthError(
            message=f"Salesforce JWT login failed: {str(e)}"
        ) from e


def get_client_from_token(
    access_token: str,
    instance_url: str,
    api_version: str = "v61.0",
) -> Salesforce:
    """Create a Salesforce client using an existing access token.

    Useful for token caching/reuse scenarios where authentication
    has already been performed externally.

    Args:
        access_token: Valid Salesforce access token.
        instance_url: Salesforce instance URL (e.g., "https://na1.salesforce.com").
        api_version: Salesforce API version.

    Returns:
        Salesforce client configured with the provided token.
    """
    logger.debug(
        "Creating Salesforce client from existing token, instance=%s version=%s",
        instance_url,
        api_version,
    )

    # Strip 'v' prefix from version for simple-salesforce
    version = api_version.lstrip("v")

    # Extract instance domain from URL
    # e.g., "https://na1.salesforce.com" → "na1.salesforce.com"
    instance = instance_url.replace("https://", "").replace("http://", "")

    client = Salesforce(
        session_id=access_token,
        instance=instance,
        version=version,
    )

    logger.debug("Salesforce client created from token")
    return client
