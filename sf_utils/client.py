"""Salesforce client connection management."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
import SalesforcePy as sfdc
from SalesforcePy.sfdc import Client

logger = logging.getLogger(__name__)


@dataclass
class SalesforceConfig:
    """Configuration for Salesforce connection."""

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


def get_client(
    config: Optional[SalesforceConfig] = None,
    login: bool = True,
) -> Client:
    """Create and optionally authenticate a Salesforce client.

    Args:
        config: Salesforce configuration. If None, loads from environment.
        login: Whether to authenticate immediately. Defaults to True.

    Returns:
        Authenticated (or unauthenticated if login=False) Salesforce client.

    Raises:
        ValueError: If required configuration is missing.
        Exception: If authentication fails.
    """
    if config is None:
        config = SalesforceConfig.from_env()

    logger.debug(
        "Creating Salesforce client for user=%s sandbox=%s version=%s",
        config.username,
        config.sandbox,
        config.api_version,
    )

    # Determine login URL based on sandbox setting
    login_url = "test.salesforce.com" if config.sandbox else "login.salesforce.com"

    client = sfdc.client(
        username=config.username,
        password=config.password,
        client_id=config.client_id,
        client_secret=config.client_secret,
        version=config.api_version,
        login_url=login_url,
    )

    if login:
        logger.debug("Authenticating with Salesforce")
        response = client.login()

        if response is None or not response:
            logger.error("Salesforce login failed - no response")
            raise Exception("Salesforce login failed")

        logger.debug("Salesforce login successful")

    return client
