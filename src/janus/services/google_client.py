"""The operator-configured Google OAuth client shared by Sign-In and "Connect Google".

The client id and secret come from ``settings.toml`` (``google_client_id`` /
``google_client_secret``) or ``JANUS_GOOGLE_CLIENT_ID`` / ``JANUS_GOOGLE_CLIENT_SECRET``.
They are deliberately not stored in the database or editable through the API.
"""

from __future__ import annotations

import logging

import httpx
from google_auth_oauthlib.flow import Flow

from janus import config
from janus.connectors import google_endpoints

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
#: Full Gmail access over IMAP (XOAUTH2); Google offers no narrower IMAP scope.
MAIL_SCOPE = "https://mail.google.com/"
#: Scopes a planner connection cannot work without; Google's consent screen lets users untick them.
REQUIRED_SCOPES = (CALENDAR_SCOPE, TASKS_SCOPE)
CONNECT_SCOPES = ["openid", "email", *REQUIRED_SCOPES, MAIL_SCOPE]
LOGIN_SCOPES = ["openid", "email", "profile"]


class GoogleNotConfiguredError(Exception):
    """The Google client id/secret are missing from the Janus configuration."""


def is_configured() -> bool:
    """Returns whether both the Google client id and secret are configured."""
    return bool(config.settings.google_client_id.strip() and config.settings.google_client_secret.strip())


def build_flow(
    scopes: list[str], redirect_uri: str, state: str | None = None, code_verifier: str | None = None
) -> Flow:
    """Builds an OAuth2 web-application flow from the configured client.

    ``code_verifier`` is the PKCE verifier saved from the original authorization request; when
    given, auto-generation is off so the callback's flow uses the verifier Google expects.

    Raises:
        GoogleNotConfiguredError: If the client id or secret is not configured.
    """
    if not is_configured():
        raise GoogleNotConfiguredError
    client_config = {
        "web": {
            "client_id": config.settings.google_client_id.strip(),
            "client_secret": config.settings.google_client_secret.strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": google_endpoints.token_uri(),
            "redirect_uris": [redirect_uri],
        }
    }
    kwargs: dict = {"redirect_uri": redirect_uri}
    if state is not None:
        kwargs["state"] = state
    if code_verifier is not None:
        kwargs["code_verifier"] = code_verifier
        kwargs["autogenerate_code_verifier"] = False
    return Flow.from_client_config(client_config, scopes=scopes, **kwargs)


def fetch_userinfo(access_token: str) -> dict:
    """Returns Google's userinfo (``sub``, ``email``), or ``{}`` on any failure.

    Best-effort: a userinfo failure must not block the connection, it only means the account
    label is unknown and Google Sign-In is not linked.
    """
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(google_endpoints.userinfo_url(), headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("Failed to fetch Google userinfo during connect.")
        return {}
    return data if isinstance(data, dict) else {}
