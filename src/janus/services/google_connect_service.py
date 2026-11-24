"""Per-user "Connect Google": the authorization-code flow behind the Connect Google button.

Mirrors ``microsoft_connect_service`` so both providers plug into ``connect_providers`` the same
way: ``begin_login`` stores the single-use state (and the PKCE verifier) and returns the consent
URL, ``complete_login`` runs on the callback and stores the connection, the Calendar/Tasks
planner sources and the best-effort Sign-In link. The OAuth client comes from ``settings.toml``
(see ``google_client``).
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from janus import models, security
from janus.services import google_client, google_oauth, planner_account_service
from janus.services import oauth_connection_service as connections

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/api/connections/google/callback"


def begin_login(db: Session, user_id: int, redirect_uri: str) -> str:
    """Stores a single-use state and returns Google's consent URL.

    Raises:
        google_client.GoogleNotConfiguredError: If the client id or secret is missing.
    """
    flow = google_client.build_flow(google_client.CONNECT_SCOPES, redirect_uri)
    state = secrets.token_urlsafe(32)
    url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", state=state, prompt="consent")
    # PKCE: the flow minted a code_verifier for this URL; the callback builds a separate Flow
    # and must exchange the code with the same verifier.
    db.add(models.OAuthState(state=state, user_id=user_id, code_verifier=flow.code_verifier))
    db.commit()
    return str(url)


def complete_login(
    db: Session, user_id: int, flow_state: connections.FlowState, query: dict[str, str], redirect_uri: str
) -> None:
    """Exchanges the callback's code and stores the connection and planner sources, and commits.

    Raises:
        ConnectionFlowError: With a safe code when the exchange or the save failed.
    """
    try:
        flow = google_client.build_flow(
            google_client.CONNECT_SCOPES, redirect_uri, query.get("state"), flow_state.code_verifier
        )
    except google_client.GoogleNotConfiguredError as exc:
        logger.warning("Google callback arrived, but the Google OAuth client is not configured.")
        raise connections.ConnectionFlowError("google_not_configured") from exc
    credentials = google_oauth.exchange_code(flow, query["code"], required_scopes=google_client.REQUIRED_SCOPES)
    refresh_token = credentials.refresh_token or planner_account_service.get_google_refresh_token_for_user(
        db, user_id, credentials.client_id
    )
    if not refresh_token:
        logger.warning("Google returned no refresh token and none is stored for this client.")
        raise google_oauth.GoogleOAuthError("google_no_refresh_token")
    blob = google_oauth.serialize_credentials(credentials, refresh_token)
    userinfo = google_client.fetch_userinfo(credentials.token)
    try:
        planner_account_service.upsert_google_accounts_for_user(db, user_id, security.security_service.encrypt(blob))
        connections.upsert_connection(
            db,
            user_id,
            connections.GOOGLE,
            account_email=userinfo.get("email"),
            credentials=blob,
            scopes=google_oauth.granted_scopes(flow.oauth2session.token, credentials.scopes or []),
        )
        google_oauth.link_sign_in(db, user_id, userinfo.get("sub"))
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("Saving the Google connection failed: %s", getattr(exc, "orig", None) or type(exc).__name__)
        raise connections.ConnectionFlowError("google_account_save_failed") from exc
