"""Minimal OAuth 2.0 provider for single-user iCloud MCP server.

Implements the full authorization code flow with PKCE that Claude.ai expects,
but keeps it simple: in-memory storage, PIN-gated authorization, static
token validation.

If MCP_AUTH_PIN is set, the authorize step shows a PIN entry form.
If not set, authorization is auto-approved (local dev convenience).
"""

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


class ICloudOAuthProvider:
    """Single-user OAuth provider. Stores everything in memory.

    On server restart, clients re-register automatically (Claude.ai handles this).
    """

    def __init__(self, auth_token: str, auth_pin: str | None = None,
                 base_url: str = ""):
        self.auth_token = auth_token
        self.auth_pin = auth_pin
        self.base_url = base_url.rstrip("/")
        # In-memory stores (fine for single-user, restart just triggers re-auth)
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # Pending authorizations awaiting PIN confirmation
        self._pending_auths: dict[str, dict] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            client_info.client_id = f"icloud-mcp-{secrets.token_hex(16)}"
        client_info.client_id_issued_at = int(time.time())
        self._clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Authorize a client. If PIN is configured, redirect to PIN page first."""

        if self.auth_pin:
            # Store pending auth and redirect to PIN entry page
            session_id = secrets.token_urlsafe(32)
            self._pending_auths[session_id] = {
                "client": client,
                "params": params,
                "created_at": time.time(),
            }
            return f"{self.base_url}/confirm-pin?session={session_id}"

        # No PIN configured — auto-approve
        return self._issue_auth_code(client, params)

    def _issue_auth_code(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Generate an auth code and return the redirect URL."""
        code = secrets.token_urlsafe(32)

        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 600,  # 10 minutes
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )

        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
        )

    def confirm_pin(self, session_id: str, pin: str) -> str | None:
        """Validate PIN and return redirect URL, or None if invalid.

        Called by the /confirm-pin route handler.
        """
        pending = self._pending_auths.get(session_id)
        if not pending:
            return None

        # Expire after 5 minutes
        if time.time() - pending["created_at"] > 300:
            self._pending_auths.pop(session_id, None)
            return None

        if pin != self.auth_pin:
            return None

        # PIN correct — clean up and issue auth code
        self._pending_auths.pop(session_id, None)
        return self._issue_auth_code(pending["client"], pending["params"])

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code_obj = self._auth_codes.get(authorization_code)
        if code_obj is None:
            return None
        if code_obj.client_id != client.client_id:
            return None
        if time.time() > code_obj.expires_at:
            del self._auth_codes[authorization_code]
            return None
        return code_obj

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._auth_codes.pop(authorization_code.code, None)

        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        expires_in = 3600 * 24 * 365  # 1 year

        self._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + expires_in,
            resource=authorization_code.resource,
        )

        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + expires_in * 2,
        )

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
            refresh_token=refresh_token,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token_obj = self._refresh_tokens.get(refresh_token)
        if token_obj is None:
            return None
        if token_obj.client_id != client.client_id:
            return None
        if token_obj.expires_at and time.time() > token_obj.expires_at:
            del self._refresh_tokens[refresh_token]
            return None
        return token_obj

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._refresh_tokens.pop(refresh_token.token, None)

        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        expires_in = 3600 * 24 * 365  # 1 year

        use_scopes = scopes if scopes else refresh_token.scopes

        self._access_tokens[new_access] = AccessToken(
            token=new_access,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=int(time.time()) + expires_in,
        )

        self._refresh_tokens[new_refresh] = RefreshToken(
            token=new_refresh,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=int(time.time()) + expires_in * 2,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(use_scopes) if use_scopes else None,
            refresh_token=new_refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # Check dynamic tokens first
        token_obj = self._access_tokens.get(token)
        if token_obj:
            if token_obj.expires_at and time.time() > token_obj.expires_at:
                del self._access_tokens[token]
                return None
            return token_obj

        # Also accept the static auth token (for Claude Code / curl)
        if token == self.auth_token:
            return AccessToken(
                token=token,
                client_id="static",
                scopes=[],
            )

        return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
        elif isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)
