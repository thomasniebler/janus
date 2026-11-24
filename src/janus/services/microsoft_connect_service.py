"""Per-user "Connect Microsoft": MSAL authorization-code flow with PKCE, stored per user.

Unlike ``microsoft_auth_service`` (one instance-wide sign-in for the agent's Graph tools,
made from the CLI), this connects each Janus user's own Microsoft account from the browser.
The user's MSAL token cache lives, Fernet-encrypted, on their ``OAuthConnection``. It serves
Outlook calendars (Graph) and Outlook mail (Graph ``Mail.Read``, polled by ``graph_mail_service``).

The app registration is the same ``microsoft_client_id`` / ``microsoft_tenant_id`` pair and
needs ``<base_url>/api/connections/microsoft/callback`` as a redirect URI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import msal
from sqlalchemy.orm import Session

from janus import config, models
from janus.connectors.microsoft_graph import GRAPH_SCOPE_PREFIX, MicrosoftGraphClient
from janus.services import oauth_connection_service as connections

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/api/connections/microsoft/callback"
# Only permissions the app registration has approved: Calendars.Read.Shared reads the user's own and
# shared calendars, Mail.Read the inbox. openid, profile and offline_access are added by MSAL itself.
GRAPH_SCOPES = [f"{GRAPH_SCOPE_PREFIX}Calendars.Read.Shared"]
MAIL_SCOPE = f"{GRAPH_SCOPE_PREFIX}Mail.Read"
LOGIN_SCOPES = [*GRAPH_SCOPES, MAIL_SCOPE]
_BARE_SCOPES = {"openid", "profile", "email", "offline_access"}


class MicrosoftNotConfiguredError(Exception):
    """The Microsoft client/tenant ids are missing from the Janus configuration."""


class MicrosoftLoginError(connections.ConnectionFlowError):
    """The sign-in failed; ``code`` is a short, safe error code for the UI."""


def is_configured() -> bool:
    """Returns whether the app registration ids are configured."""
    return bool(config.settings.microsoft_client_id.strip() and config.settings.microsoft_tenant_id.strip())


def _application(cache: msal.SerializableTokenCache | None = None) -> msal.PublicClientApplication:
    if not is_configured():
        raise MicrosoftNotConfiguredError
    return msal.PublicClientApplication(
        config.settings.microsoft_client_id.strip(),
        authority=f"https://login.microsoftonline.com/{config.settings.microsoft_tenant_id.strip()}",
        token_cache=cache,
    )


def begin_login(db: Session, user_id: int, redirect_uri: str) -> str:
    """Starts the auth-code flow, stores its state for the callback, and returns Microsoft's sign-in URL."""
    try:
        flow = _application().initiate_auth_code_flow(LOGIN_SCOPES, redirect_uri=redirect_uri, prompt="select_account")
    except (ValueError, OSError) as exc:  # malformed tenant id, or authority discovery unreachable
        logger.warning("Microsoft sign-in could not start: %s", type(exc).__name__)
        raise MicrosoftLoginError("microsoft_flow_failed") from exc
    if "auth_uri" not in flow:
        logger.warning("Microsoft refused to start the sign-in: %s", flow.get("error"))
        raise MicrosoftLoginError("microsoft_flow_failed")
    db.add(models.OAuthState(state=flow["state"], user_id=user_id, payload=json.dumps(flow)))
    db.commit()
    return str(flow["auth_uri"])


def complete_login(db: Session, user_id: int, flow_payload: str, query: dict[str, str]) -> models.OAuthConnection:
    """Finishes the flow with the callback's query parameters and stores the connection; no commit.

    Raises:
        MicrosoftLoginError: With a safe code when Microsoft rejected the sign-in or a scope is missing.
    """
    cache = msal.SerializableTokenCache()
    application = _application(cache)
    try:
        result = application.acquire_token_by_auth_code_flow(json.loads(flow_payload), query)
    except ValueError as exc:  # state mismatch
        raise MicrosoftLoginError("microsoft_state_invalid") from exc
    except OSError as exc:  # Microsoft unreachable
        logger.warning("Microsoft token exchange failed: %s", type(exc).__name__)
        raise MicrosoftLoginError("microsoft_token_exchange_failed") from exc
    if "access_token" not in result:
        logger.warning("Microsoft sign-in failed: %s", result.get("error"))
        raise MicrosoftLoginError(
            "microsoft_denied" if result.get("error") == "access_denied" else "microsoft_token_exchange_failed"
        )
    claims = result.get("id_token_claims") or {}
    email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
    # Graph may answer with bare names ("Mail.Read"); store them as full URIs so checks compare equal.
    granted = [
        scope if scope in _BARE_SCOPES or scope.startswith("http") else f"{GRAPH_SCOPE_PREFIX}{scope}"
        for scope in str(result.get("scope", "")).split()
    ]
    return connections.upsert_connection(
        db,
        user_id,
        connections.MICROSOFT,
        account_email=str(email) if email else None,
        credentials=cache.serialize(),
        scopes=granted,
    )


def access_token(db: Session, connection: models.OAuthConnection, scopes: list[str]) -> str:
    """Returns an access token for ``scopes``, renewed silently from the cached refresh token.

    Raises:
        ConnectionUnusableError: If the sign-in expired or was revoked, so the user must reconnect.
    """
    cache = msal.SerializableTokenCache()
    try:
        cache.deserialize(connections.read_credentials(connection))
    except ValueError as exc:
        raise connections.ConnectionUnusableError("The stored Microsoft sign-in cannot be read. Reconnect.") from exc
    try:
        application = _application(cache)
    except MicrosoftNotConfiguredError as exc:
        raise connections.ConnectionUnusableError("Microsoft is not configured on this server.") from exc
    accounts = application.get_accounts()
    result: dict[str, Any] | None = application.acquire_token_silent(scopes, account=accounts[0]) if accounts else None
    if cache.has_state_changed:
        connections.write_credentials(db, connection, cache.serialize())
    if not result or "access_token" not in result:
        raise connections.ConnectionUnusableError("Microsoft sign-in expired. Reconnect Microsoft in Settings.")
    return str(result["access_token"])


def graph_client(db: Session, connection: models.OAuthConnection) -> MicrosoftGraphClient:
    """Returns a Graph client for the connection; the token is fetched once and reused by the client."""
    token: list[str] = []

    def _token() -> str:
        if not token:
            token.append(access_token(db, connection, GRAPH_SCOPES))
        return token[0]

    return MicrosoftGraphClient(_token)
