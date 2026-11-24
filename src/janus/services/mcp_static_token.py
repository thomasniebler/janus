"""Owner-supplied static bearer tokens for remote MCP servers (JAN-342).

A server authenticates with OAuth (PKCE tokens, ``mcp_service``) or with a
fixed bearer token the owner pastes. Both live in the server's single
``MCPCredential`` row; a static token is the row with ``token_type =
"static"`` and no refresh token, expiry, or scopes. OAuth rows keep the
provider's ``token_type`` (usually ``Bearer``), so this distinct value is what
tells the two apart.

The rules kept here: a token is one line of printable ASCII (it cannot break
the ``Authorization`` header), it is never combined with OAuth settings, and
it is bound to the endpoint it was given for (a new endpoint drops it unless a
new token comes with the change). Refusals raise ``MCPServiceError`` like the
rest of the registry services.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from janus import models, security
from janus.schemas.mcp import MAX_ACCESS_TOKEN_CHARS as MAX_STATIC_TOKEN_CHARS
from janus.services.mcp_errors import MCPServiceError

STATIC_TOKEN_TYPE = "static"
STATIC_TOKEN_REJECTED = "Remote MCP server rejected the bearer token. Save a new token for this server."
#: The fields that make a server OAuth-configured (the API's ``oauth_configured``); scopes alone do not.
OAUTH_CONFIG_FIELDS = ("oauth_authorization_endpoint", "oauth_token_endpoint", "oauth_client_id")
OAUTH_AND_TOKEN = "Use either OAuth or a bearer token for an MCP server, not both."
#: Upstream statuses that mean "this credential is not accepted" for a static token.
REJECTION_STATUSES = frozenset({401, 403})


def credential_status(server: models.MCPServer) -> str:
    """Return ``"none"``, ``"oauth"`` (PKCE tokens), or ``"static"`` (an owner-supplied bearer token)."""
    credentials = server.credentials
    if credentials is None:
        return "none"
    return "static" if credentials.token_type == STATIC_TOKEN_TYPE else "oauth"


def normalize_static_token(value: Any) -> str | None:
    """Return the token to store, ``None`` for a missing or blank value, or raise for an unusable one.

    Accepts a ``SecretStr`` or a plain string and drops a leading ``Bearer `` the owner may have pasted.
    """
    if value is None:
        return None
    raw = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    token = raw.strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    if not token:
        return None
    if len(token) > MAX_STATIC_TOKEN_CHARS:
        raise MCPServiceError(f"The bearer token is longer than {MAX_STATIC_TOKEN_CHARS} characters.")
    if not token.isascii() or any(not char.isprintable() or char.isspace() for char in token):
        raise MCPServiceError("The bearer token must be one line of printable ASCII characters without spaces.")
    return token


def token_for_create(values: dict[str, Any]) -> str | None:
    """Pop and validate the token of a new server; refuse it next to OAuth settings."""
    token = normalize_static_token(values.pop("access_token", None))
    if token is not None and any(values.get(field) for field in OAUTH_CONFIG_FIELDS):
        raise MCPServiceError(OAUTH_AND_TOKEN)
    return token


def token_change_for_update(server: models.MCPServer, values: dict[str, Any]) -> tuple[str | None, bool]:
    """Pop the token fields of an update and return ``(new_token, drop_saved_token)``.

    ``values["endpoint"]``, when present, must already be normalized. Raises
    :class:`MCPServiceError` for a replace-and-clear request or for a token
    that would sit next to OAuth settings after the update.
    """
    token = normalize_static_token(values.pop("access_token", None))
    clear = bool(values.pop("clear_access_token", False))
    if token is not None and clear:
        raise MCPServiceError("Either replace or clear the bearer token, not both.")
    if clear and credential_status(server) == "oauth":
        raise MCPServiceError("This server is connected with OAuth, not a bearer token. Disconnect it to remove it.")
    endpoint_changed = values.get("endpoint") is not None and values["endpoint"] != server.endpoint
    has_static = credential_status(server) == "static"
    drop = has_static and (clear or (endpoint_changed and token is None))
    static_after = token is not None or (has_static and not drop)
    if static_after and any(values.get(field, getattr(server, field)) for field in OAUTH_CONFIG_FIELDS):
        raise MCPServiceError(OAUTH_AND_TOKEN)
    return token, drop


def store_static_token(db: Session, server: models.MCPServer, token: str) -> None:
    """Encrypt and save the token, replacing any credential (OAuth included) the server had."""
    credentials = server.credentials or models.MCPCredential(user_id=server.user_id, server_id=server.id)
    credentials.encrypted_access_token = security.security_service.encrypt(token)
    credentials.encrypted_refresh_token = None
    credentials.token_type = STATIC_TOKEN_TYPE
    credentials.expires_at = None
    credentials.scopes = None
    if credentials.id is None:
        db.add(credentials)
        server.credentials = credentials


def remove_static_token(db: Session, server: models.MCPServer) -> None:
    """Delete the server's saved token."""
    db.delete(server.credentials)
    server.credentials = None


def translate(server: models.MCPServer, exc: MCPServiceError) -> MCPServiceError:
    """Return the error to report for an upstream failure: a 401/403 to a static token says the token was rejected.

    Every call that reaches the server goes through this (``MCPService._remote``), so a new call path gets
    the same wording. Any other error comes back unchanged.
    """
    if exc.status_code in REJECTION_STATUSES and credential_status(server) == "static":
        return MCPServiceError(STATIC_TOKEN_REJECTED, auth_required=True, status_code=exc.status_code)
    return exc
