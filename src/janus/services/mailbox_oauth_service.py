"""Mailboxes backed by a connected Google or Microsoft account.

Gmail speaks IMAP with an OAuth access token instead of a password, so these
mailboxes run through the same monitor, UID tracking and classification as a custom IMAP
account. This module creates them and hands the monitor a fresh access token at login.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from janus import database, models
from janus.services import google_client, microsoft_connect_service, oauth_connection_service
from janus.services.oauth_connection_service import ConnectionUnusableError

IMAP_PRESETS = {
    oauth_connection_service.GOOGLE: ("imap.gmail.com", 993, "Gmail"),
    # Outlook is read over Graph (``graph_mail_service``); the server and port are placeholders.
    oauth_connection_service.MICROSOFT: ("graph.microsoft.com", 443, "Outlook"),
}
MAIL_SCOPES = {
    oauth_connection_service.GOOGLE: google_client.MAIL_SCOPE,
    oauth_connection_service.MICROSOFT: microsoft_connect_service.MAIL_SCOPE,
}


class MailboxError(Exception):
    """A mailbox could not be created; the message is safe to show the user."""


def create_mailbox(db: Session, user_id: int, provider: str, name: str | None = None) -> models.EmailAccount:
    """Creates the user's mailbox on their connected ``provider`` account and commits.

    Raises:
        MailboxError: If the provider is not connected, the sign-in does not grant mailbox
            access (reconnect to grant it), or the address already has a mailbox.
    """
    if provider not in IMAP_PRESETS:
        raise MailboxError("Unknown provider.")
    connection = oauth_connection_service.get_connection(db, user_id, provider)
    if connection is None:
        raise MailboxError(f"Connect {provider.title()} first.")
    if MAIL_SCOPES[provider] not in (connection.scopes or []):
        raise MailboxError(f"The {provider.title()} sign-in does not include mail access. Reconnect it.")
    address = connection.account_email
    if not address:
        raise MailboxError(f"{provider.title()} did not report the account's address. Reconnect it.")
    server, port, label = IMAP_PRESETS[provider]
    chosen_name = (name or "").strip() or f"{label} ({address})"
    orphan = (
        db.query(models.EmailAccount)
        .filter(
            models.EmailAccount.user_id == user_id,
            models.EmailAccount.email == address,
            models.EmailAccount.auth_type == provider,
            models.EmailAccount.connection_id.is_(None),
        )
        .first()
    )
    if orphan is not None:
        # Disconnecting keeps the mailbox and its mail; connecting again takes it back.
        orphan.connection_id = connection.id
        orphan.is_active = True
        db.commit()
        db.refresh(orphan)
        return orphan
    taken = db.query(models.EmailAccount).filter(
        (models.EmailAccount.email == address) | (models.EmailAccount.name == chosen_name)
    )
    if taken.first() is not None:
        raise MailboxError("A mailbox with this address or name already exists.")
    account = models.EmailAccount(
        user_id=user_id,
        name=chosen_name,
        email=address,
        imap_server=server,
        imap_port=port,
        username=address,
        password=None,
        auth_type=provider,
        connection_id=connection.id,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def imap_access_token(account: models.EmailAccount) -> str:
    """Returns a fresh IMAP access token for an OAuth mailbox, on a session of its own.

    Called from monitor threads, which have no request session.

    Raises:
        ConnectionUnusableError: If the connection is gone or its sign-in expired.
    """
    with database.SessionLocal() as db:
        connection = (
            db.query(models.OAuthConnection).filter(models.OAuthConnection.id == account.connection_id).first()
            if account.connection_id
            else None
        )
        if connection is None:
            raise ConnectionUnusableError(f"{account.auth_type.title()} is disconnected. Reconnect it in Settings.")
        if account.auth_type == oauth_connection_service.GOOGLE:
            token = oauth_connection_service.google_credentials(db, connection).token
            return str(token)
        raise ConnectionUnusableError("Outlook mailboxes are read over Graph, not IMAP.")
