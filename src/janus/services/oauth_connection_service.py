"""Owner-scoped storage for connected Google and Microsoft accounts.

A connection is the sign-in behind a "Connect ..." button in Settings. Calendars and
mailboxes reference it rather than keeping their own copy. Every helper filters on the
owner's id, so another user's connection answers like a missing one.
"""

from __future__ import annotations

import json
import logging
from typing import NamedTuple

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from janus import models, security
from janus.connectors import google_endpoints

logger = logging.getLogger(__name__)

GOOGLE = "google"
MICROSOFT = "microsoft"
PROVIDERS = (GOOGLE, MICROSOFT)


class ConnectionFlowError(Exception):
    """A "Connect ..." sign-in failed; ``code`` is a short, safe error code the Settings page explains."""

    def __init__(self, code: str) -> None:
        """Stores the safe, user-facing error code."""
        super().__init__(code)
        self.code = code


class FlowState(NamedTuple):
    """What a provider saved when the sign-in started, read back (once) by its callback."""

    payload: str | None
    code_verifier: str | None


class ConnectionUnusableError(RuntimeError):
    """A connection's stored sign-in is unusable; the message is safe to show the user."""


def get_connection(db: Session, user_id: int, provider: str) -> models.OAuthConnection | None:
    """Returns ``user_id``'s connection for ``provider``, or ``None``."""
    return (
        db.query(models.OAuthConnection)
        .filter(models.OAuthConnection.user_id == user_id, models.OAuthConnection.provider == provider)
        .first()
    )


def upsert_connection(
    db: Session,
    user_id: int,
    provider: str,
    *,
    account_email: str | None,
    credentials: str,
    scopes: list[str],
) -> models.OAuthConnection:
    """Creates or replaces ``user_id``'s connection for ``provider``; no commit."""
    connection = get_connection(db, user_id, provider)
    encrypted = security.security_service.encrypt(credentials)
    if connection is None:
        connection = models.OAuthConnection(
            user_id=user_id,
            provider=provider,
            account_email=account_email,
            credentials=encrypted,
            scopes=scopes,
        )
        db.add(connection)
    else:
        connection.account_email = account_email or connection.account_email
        connection.credentials = encrypted
        connection.scopes = scopes
    db.flush()
    return connection


def read_credentials(connection: models.OAuthConnection) -> str:
    """Returns the decrypted credential blob.

    Raises:
        ConnectionUnusableError: If it cannot be decrypted (changed Fernet key).
    """
    try:
        return security.security_service.decrypt(connection.credentials)
    except ValueError as exc:
        raise ConnectionUnusableError("The stored sign-in cannot be read. Reconnect the account.") from exc


def write_credentials(db: Session, connection: models.OAuthConnection, credentials: str) -> None:
    """Persists a refreshed credential blob and commits."""
    connection.credentials = security.security_service.encrypt(credentials)
    db.commit()


def google_credentials(db: Session, connection: models.OAuthConnection) -> Credentials:
    """Returns refreshed Google credentials, saving the new access token."""
    try:
        data = json.loads(read_credentials(connection))
        creds = google_endpoints.with_fake_token_uri(Credentials.from_authorized_user_info(data))
        if creds.expired or not creds.token:
            creds.refresh(GoogleAuthRequest())
            write_credentials(db, connection, creds.to_json())
    except ConnectionUnusableError:
        raise
    except Exception as exc:
        logger.warning("Refreshing the Google sign-in failed: %s", type(exc).__name__)
        raise ConnectionUnusableError("Google sign-in expired. Reconnect Google in Settings.") from exc
    return creds


def delete_connection(db: Session, user_id: int, provider: str) -> bool:
    """Removes the connection and everything that depended on it, and commits.

    Mailboxes using it are switched off (their stored mail stays), and the calendar and task
    sources of the provider are deleted together with what they imported (also when they
    predate connections and have no connection row). Returns whether anything existed.
    """
    connection = get_connection(db, user_id, provider)
    if connection is not None:
        for account in db.query(models.EmailAccount).filter(models.EmailAccount.connection_id == connection.id).all():
            account.is_active = False
            account.connection_id = None
            account.last_error = f"{provider.title()} was disconnected."
    providers = {
        GOOGLE: [models.Provider.GOOGLE_CALENDAR, models.Provider.GOOGLE_TASKS],
        MICROSOFT: [models.Provider.MICROSOFT_CALENDAR],
    }[provider]
    planner_ids = [
        row.id
        for row in db.query(models.PlannerAccount).filter(
            models.PlannerAccount.user_id == user_id, models.PlannerAccount.provider.in_(providers)
        )
    ]
    if planner_ids:
        # Imported rows have no ORM cascade (and SQLite does not enforce the foreign key), so they
        # are removed explicitly: left behind, they would attach to whichever planner account
        # SQLite next gives the freed id, even another user's (JAN-221).
        db.query(models.CalendarEvent).filter(models.CalendarEvent.planner_account_id.in_(planner_ids)).delete(
            synchronize_session=False
        )
        db.query(models.TaskRecord).filter(models.TaskRecord.planner_account_id.in_(planner_ids)).delete(
            synchronize_session=False
        )
        db.query(models.PlannerAccount).filter(models.PlannerAccount.id.in_(planner_ids)).delete(
            synchronize_session=False
        )
    if connection is not None:
        db.delete(connection)
    db.commit()
    return connection is not None or bool(planner_ids)
