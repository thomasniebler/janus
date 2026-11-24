"""Connected Google and Microsoft accounts: status, sign-in (one auth-url and callback per provider), disconnect."""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from janus import database, models, request_origin, security
from janus.api import auth as auth_api
from janus.schemas import connections as connection_schemas
from janus.schemas import email as email_schemas
from janus.services import connect_providers, mailbox_oauth_service, oauth_connection_service
from janus.services.connect_providers import ConnectProvider
from janus.services.oauth_connection_service import ConnectionFlowError, FlowState

router = APIRouter(prefix="/api/connections", tags=["connections"])
logger = logging.getLogger(__name__)

_STATE_TTL_MINUTES = 10


def _provider(name: str) -> ConnectProvider:
    """Returns the provider called ``name`` or raises 404."""
    provider = connect_providers.PROVIDERS.get(name)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider.")
    return provider


def _callback_url(request: Request, provider: ConnectProvider) -> str:
    return f"{request_origin.public_base_url(request)}{provider.callback_path}"


def _status(db: Session, user: models.User, request: Request, provider: ConnectProvider):
    connection = oauth_connection_service.get_connection(db, user.id, provider.name)
    return connection_schemas.ConnectionStatus(
        provider=provider.name,  # type: ignore[arg-type]
        configured=provider.is_configured(),
        connected=connection is not None,
        account_email=connection.account_email if connection else None,
        mail_supported=provider.mail_scope is not None,
        mail_ready=bool(connection and provider.mail_scope in (connection.scopes or [])),
        redirect_uris=[_callback_url(request, provider)],
        config_keys=list(provider.config_keys),
    )


@router.get("", response_model=connection_schemas.ConnectionsOverview)
def get_connections(
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns whether Google and Microsoft are configured and connected for the current user."""
    return connection_schemas.ConnectionsOverview(
        **{name: _status(db, user, request, provider) for name, provider in connect_providers.PROVIDERS.items()}
    )


@router.get("/{provider}/auth-url")
def auth_url(
    provider: str,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Starts "Connect <provider>" and returns the provider's sign-in URL."""
    chosen = _provider(provider)
    if not chosen.is_configured():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{provider.title()} is not configured. Set {' and '.join(chosen.config_keys)} in settings.toml.",
        )
    # Drop expired states first so the table cannot grow without bound.
    cutoff = datetime.now(UTC) - timedelta(minutes=_STATE_TTL_MINUTES)
    db.query(models.OAuthState).filter(models.OAuthState.created_at < cutoff).delete()
    try:
        return {"url": chosen.begin(db, user.id, _callback_url(request, chosen))}
    except ConnectionFlowError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{provider.title()} could not start the sign-in.") from exc


def _take_state(db: Session, state: str, *, signed_in: bool) -> tuple[int | None, FlowState] | None:
    """Reads and deletes the single-use state (so a replay fails); ``None`` when unknown or expired.

    ``signed_in`` selects Connect states (bound to a user) or Google Sign-In states (``user_id`` is null).
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=_STATE_TTL_MINUTES)
    user_filter = models.OAuthState.user_id.isnot(None) if signed_in else models.OAuthState.user_id.is_(None)
    row = (
        db.query(models.OAuthState)
        .filter(models.OAuthState.state == state, models.OAuthState.created_at >= cutoff, user_filter)
        .first()
    )
    if row is None:
        return None
    taken = (row.user_id, FlowState(row.payload, row.code_verifier))
    db.delete(row)
    db.commit()
    return taken


@router.get("/{provider}/callback")
def callback(
    provider: str,
    request: Request,
    db: Session = Depends(database.get_db),
):
    """Where the provider's consent screen returns to, for every provider.

    Intentionally unauthenticated: the browser arrives from the provider, and the single-use
    state saved when the sign-in started identifies the user. Redirects to Settings ->
    Connections with ``connected=<provider>`` or ``error=<code>``. For Google, a state without a
    user is a Sign-In from the login page and ends in a session instead.
    """
    chosen = _provider(provider)
    query = dict(request.query_params)
    state = query.get("state")
    if provider == oauth_connection_service.GOOGLE and state:
        login = _take_state(db, state, signed_in=False)
        if login is not None:
            return auth_api.finish_google_login(request, db, login[1], query, _callback_url(request, chosen))
    settings_url = f"{request_origin.public_base_url(request)}/settings?tab=connections"

    def fail(code: str) -> RedirectResponse:
        return RedirectResponse(f"{settings_url}&error={code}")

    if query.get("error"):
        return fail(f"{provider}_denied")
    if not state or not query.get("code"):
        return fail(f"{provider}_invalid_callback")
    taken = _take_state(db, state, signed_in=True)
    if taken is None or not chosen.owns_state(taken[1]):
        return fail(f"{provider}_state_invalid")
    if not chosen.is_configured():
        return fail(f"{provider}_not_configured")
    user_id, flow_state = taken
    try:
        chosen.complete(db, user_id, flow_state, query, _callback_url(request, chosen))  # type: ignore[arg-type]
    except ConnectionFlowError as exc:
        db.rollback()
        return fail(exc.code)
    return RedirectResponse(f"{settings_url}&connected={provider}")


@router.delete("/{provider}", status_code=204)
def disconnect(
    provider: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Disconnects a provider: removes its sign-in, calendars and the mailboxes' access."""
    _provider(provider)
    if not oauth_connection_service.delete_connection(db, user.id, provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{provider.title()} is not connected.")


@router.post("/{provider}/mailbox", response_model=email_schemas.EmailAccount)
def add_mailbox(
    provider: str,
    payload: connection_schemas.MailboxCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Adds a mailbox that reads the user's connected Gmail or Outlook account (IMAP over OAuth)."""
    _provider(provider)
    try:
        return mailbox_oauth_service.create_mailbox(db, user.id, provider, payload.name)
    except mailbox_oauth_service.MailboxError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
