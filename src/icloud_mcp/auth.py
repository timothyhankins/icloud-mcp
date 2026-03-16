"""Authentication management for iCloud MCP server.

MCP-level auth (OAuth / bearer token) is handled by the OAuth provider in oauth.py.
This module provides iCloud service credentials (email + app-specific password)
for CalDAV, CardDAV, and IMAP/SMTP connections.
"""

from typing import Tuple
from .config import config


class AuthenticationError(Exception):
    """Raised when iCloud credentials are missing or invalid."""
    pass


def get_credentials() -> Tuple[str, str]:
    """Get iCloud credentials from environment variables."""
    email = config.FALLBACK_EMAIL
    password = config.FALLBACK_PASSWORD

    if not email or not password:
        raise AuthenticationError(
            "iCloud credentials required. Set ICLOUD_EMAIL and "
            "ICLOUD_APP_SPECIFIC_PASSWORD environment variables."
        )

    return email, password


def require_auth() -> Tuple[str, str]:
    """Get iCloud credentials, raising AuthenticationError if missing."""
    return get_credentials()
