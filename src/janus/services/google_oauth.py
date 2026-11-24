"""Google OAuth2 authorization-code exchange for planner connect and Google Sign-In (JAN-318).

Both Google callbacks check the single-use OAuth state against ``oauth_states``
themselves, then call :func:`exchange_code`. It turns each way the exchange can
fail into a :class:`GoogleOAuthError` whose ``code`` is a short, safe error code
the UI can explain, and logs the real cause without tokens or secrets.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from janus import models
from janus.services.oauth_connection_service import ConnectionFlowError

logger = logging.getLogger(__name__)

TOKEN_EXCHANGE_FAILED = "google_token_exchange_failed"
UNREACHABLE = "google_unreachable"
SCOPE_MISSING = "google_scope_missing"

#: OAuth2 ``error`` values from Google's token endpoint that have their own code.
_OAUTH_ERROR_CODES = {
    # The code expired, was already used, or its PKCE verifier does not match.
    "invalid_grant": "google_invalid_grant",
    # The stored Client ID / Client Secret pair is wrong or the client was deleted.
    "invalid_client": "google_invalid_client",
    "unauthorized_client": "google_invalid_client",
    "redirect_uri_mismatch": "google_redirect_uri_mismatch",
}


class GoogleOAuthError(ConnectionFlowError):
    """A Google OAuth step failed; ``code`` is the error code shown to the user."""


def exchange_code(flow: Flow, code: str, required_scopes: Iterable[str] = ()) -> Credentials:
    """Exchanges an authorization ``code`` and returns the flow's credentials.

    The bare code is exchanged instead of the redirected callback URL: the
    caller has already checked the state, and oauthlib refuses any ``http://``
    callback URL (``InsecureTransportError``), which failed every exchange on a
    plain-HTTP install such as the default ``http://localhost:8080``.

    Google answers with the scopes it granted in their full form (``email``
    comes back as ``https://www.googleapis.com/auth/userinfo.email``) and, with
    ``include_granted_scopes``, adds scopes granted earlier. oauthlib raises
    that difference as a bare ``Warning`` that carries the parsed token, so
    every exchange that asked for ``email`` failed. The token is kept, and the
    scopes Janus needs are checked against what Google granted instead.

    Raises:
        GoogleOAuthError: With ``google_scope_missing`` if a scope in
            ``required_scopes`` was not granted, or with the code for the
            failure Google or the network reported.
    """
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        token = getattr(exc, "token", None) if isinstance(exc, Warning) else None
        if not isinstance(token, Mapping):
            raise GoogleOAuthError(_classify_exchange_failure(exc)) from exc
        logger.info("Google granted different scopes than requested; checking the required ones.")
        flow.oauth2session.token = token

    missing = missing_scopes(flow.oauth2session.token, required_scopes)
    if missing:
        logger.warning("Google connection is missing required scopes: %s", ", ".join(missing))
        raise GoogleOAuthError(SCOPE_MISSING)
    return flow.credentials


def _classify_exchange_failure(exc: Exception) -> str:
    """Logs why the token request failed (never tokens or secrets) and returns its error code."""
    oauth_error = getattr(exc, "error", None)
    if isinstance(oauth_error, str):  # oauthlib's OAuth2Error: Google's own `error` field.
        logger.warning(
            "Google rejected the authorization code exchange: error=%s description=%s",
            oauth_error,
            getattr(exc, "description", None),
        )
        return _OAUTH_ERROR_CODES.get(oauth_error, TOKEN_EXCHANGE_FAILED)
    if isinstance(exc, OSError):  # requests' RequestException subclasses OSError.
        logger.warning("Could not reach Google's token endpoint: %s", type(exc).__name__)
        return UNREACHABLE
    logger.warning("Google authorization code exchange failed: %s", type(exc).__name__)
    return TOKEN_EXCHANGE_FAILED


def granted_scopes(token: object, requested: Iterable[str]) -> list[str]:
    """Returns the scopes the token response granted, or ``requested`` when it names none.

    Google lets the user untick optional scopes (Gmail), and the credentials object still lists
    every requested scope, so the response's own ``scope`` is the truth.
    """
    scope = token.get("scope") if isinstance(token, Mapping) else None
    if not scope:
        return list(requested)
    return list(scope.split() if isinstance(scope, str) else scope)


def missing_scopes(token: object, required_scopes: Iterable[str]) -> list[str]:
    """Returns the ``required_scopes`` the token response did not grant, sorted.

    A response without ``scope`` granted exactly what was requested (RFC 6749
    section 5.1), so nothing is missing.
    """
    scope = token.get("scope") if isinstance(token, Mapping) else None
    if not scope:
        return []
    granted = set(scope.split() if isinstance(scope, str) else scope)
    return sorted(set(required_scopes) - granted)


def serialize_credentials(credentials: Credentials, refresh_token: str) -> str:
    """Returns the JSON credential blob ``connectors/google.py`` reads back."""
    return json.dumps(
        {
            "token": credentials.token,
            "refresh_token": refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": list(credentials.scopes) if credentials.scopes else [],
        }
    )


def link_sign_in(db: Session, user_id: int, google_login_id: str | None) -> None:
    """Links a Google subject id to ``user_id`` for Google Sign-In; no commit.

    Best-effort part of "Connect Google": nothing is linked when the id is
    unknown or already belongs to another Janus user.
    """
    if not google_login_id:
        return
    already_linked = (
        db.query(models.User).filter(models.User.google_id == google_login_id, models.User.id != user_id).first()
    )
    if already_linked:
        logger.warning("Google account is already linked to a different Janus user; skipping Sign-In linking.")
        return
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user:
        user.google_id = google_login_id
