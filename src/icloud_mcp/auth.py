"""Authentication management for iCloud MCP server."""

from typing import Tuple, Optional
from fastmcp import Context
from fastmcp.server.dependencies import get_http_headers
from .config import config


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


def get_credentials(context: Context) -> Tuple[str, str]:
    """Extract iCloud credentials from HTTP headers."""
    
    # Get HTTP headers using FastMCP's dependency function
    headers = get_http_headers()
    print(f"[AUTH DEBUG] Headers retrieved: {list(headers.keys())}")
    
    # Extract credentials from headers
    email: Optional[str] = headers.get("x-apple-email") or headers.get("X-Apple-Email")
    password: Optional[str] = headers.get("x-apple-app-specific-password") or headers.get("X-Apple-App-Specific-Password")
    
    print(f"[AUTH DEBUG] Email from headers: {email}")
    print(f"[AUTH DEBUG] Password from headers: {'***' if password else None}")
    
    # Fallback to environment variables
    if not email:
        email = config.FALLBACK_EMAIL
        print(f"[AUTH DEBUG] Using fallback email: {email}")
    if not password:
        password = config.FALLBACK_PASSWORD
        print(f"[AUTH DEBUG] Using fallback password: {'***' if password else None}")

    # Validate credentials
    if not email or not password:
        print(f"[AUTH DEBUG] AUTHENTICATION FAILED - email: {email}, password: {'***' if password else None}")
        raise AuthenticationError(
            "Authentication required. Provide credentials via headers "
            "(X-Apple-Email, X-Apple-App-Specific-Password) or environment variables "
            "(ICLOUD_EMAIL, ICLOUD_APP_SPECIFIC_PASSWORD)"
        )

    print(f"[AUTH DEBUG] Authentication successful for email: {email}")
    return email, password


def require_auth(context: Context) -> Tuple[str, str]:
    """Decorator-friendly authentication check."""
    print("[AUTH DEBUG] ========== require_auth CALLED ==========")
    print(f"[AUTH DEBUG] Context received: {context}")
    try:
        result = get_credentials(context)
        print(f"[AUTH DEBUG] ========== require_auth SUCCESS ==========")
        return result
    except Exception as e:
        print(f"[AUTH DEBUG] ========== require_auth FAILED: {e} ==========")
        raise
