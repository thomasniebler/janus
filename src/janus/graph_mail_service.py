"""Outlook mailboxes read over Microsoft Graph (the ``Mail.Read`` permission), by polling.

Outlook mail cannot use IMAP here (the app registration has no Exchange IMAP permission), so
this monitor offers the surface the email worker needs: a first sync of recent mail, then a poll
every ``POLL_SECONDS``. Messages have no IMAP UID, so the account keeps a ``sync_cursor`` (the
newest ``receivedDateTime`` handled) and the processor de-duplicates on the Internet message id.
``Mail.ReadWrite`` is not granted, so mail is never marked read on the server. A message that is
moved into the inbox later keeps its old ``receivedDateTime`` and is not picked up.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from janus import database, event_logger, imap_service, models
from janus.connectors.microsoft_graph import MicrosoftGraphClient
from janus.services import microsoft_connect_service
from janus.services.oauth_connection_service import ConnectionUnusableError

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
HEARTBEAT_SECONDS = 30
_ERROR_BACKOFF_SECONDS = 30
#: A message that email_callback fails to store is retried on this many polls, then skipped.
_MAX_ATTEMPTS = 3
_PAGE_SIZE = 50


class GraphMailMonitor:
    """Polls one Outlook mailbox for new inbox mail."""

    def __init__(self, account: models.EmailAccount, client_factory: Callable[[], MicrosoftGraphClient] | None = None):
        """Monitors ``account``; ``client_factory`` builds the Graph client (a test seam)."""
        self.account = account
        self._client_factory = client_factory or self._graph_client
        self._failures: dict[str, int] = {}
        self._last_beat = 0.0

    def _graph_client(self) -> MicrosoftGraphClient:
        """Builds a Graph client for the account's connection, on a session of its own (monitor thread)."""

        def token() -> str:
            with database.SessionLocal() as db:
                connection = (
                    db.query(models.OAuthConnection)
                    .filter(models.OAuthConnection.id == self.account.connection_id)
                    .first()
                    if self.account.connection_id
                    else None
                )
                if connection is None:
                    raise ConnectionUnusableError("Microsoft is disconnected. Reconnect it in Settings.")
                return microsoft_connect_service.access_token(db, connection, [microsoft_connect_service.MAIL_SCOPE])

        return MicrosoftGraphClient(token)

    def test_connection(self) -> tuple[bool, str]:
        """Checks that the inbox can be read; returns a success flag and a message."""
        try:
            client = self._client_factory()
            try:
                client.list_messages(top=1)
            finally:
                client.close()
            return True, f"Successfully connected to {self.account.email}"
        except Exception as exc:
            return False, f"Outlook connection failed for {self.account.email}: {exc}"

    def _since(self) -> datetime:
        cursor = self.account.sync_cursor
        if cursor:
            return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return datetime.now(UTC) - timedelta(days=imap_service.INITIAL_LOOKBACK_DAYS)

    def fetch_new(self) -> list[dict]:
        """Returns mail received since the cursor as email dicts, oldest first.

        The very first sync keeps only the newest ``initial_sync_email_limit`` messages, like IMAP.
        """
        first_sync = not self.account.sync_cursor
        limit = imap_service.initial_history_limit() if first_sync else 0
        client = self._client_factory()
        try:
            if first_sync:
                raw = client.list_inbox_since(
                    self._since(), newest_first=True, max_pages=math.ceil(limit / _PAGE_SIZE)
                )[:limit]
                raw.reverse()
            else:
                raw = client.list_inbox_since(self._since())
        finally:
            client.close()
        emails = []
        for item in raw:
            message_id = item.get("internetMessageId") or item.get("id")
            if not message_id:
                continue
            sender = ((item.get("from") or {}).get("emailAddress") or {}).get("address") or ""
            body = ((item.get("body") or {}).get("content") or "").strip()
            received = datetime.fromisoformat(str(item["receivedDateTime"]).replace("Z", "+00:00"))
            emails.append(
                {
                    "message_id": message_id,
                    "graph_message_id": item.get("id"),
                    "sender": sender,
                    "subject": item.get("subject"),
                    "snippet": (body or item.get("bodyPreview") or "")[:100].strip(),
                    "body": body,
                    "received_at": received,
                    "account_id": self.account.id,
                    "attachments": [],
                }
            )
        return emails

    def _is_stored(self, message_id: str) -> bool:
        with database.SessionLocal() as db:
            return (
                db.query(models.EmailRecord.id)
                .filter(models.EmailRecord.account_id == self.account.id, models.EmailRecord.message_id == message_id)
                .first()
                is not None
            )

    def update_cursor(self, received_at: datetime) -> None:
        """Persists the newest handled ``receivedDateTime`` for this account (never moves it back)."""
        stamp = received_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with database.SessionLocal() as db:
            account = db.query(models.EmailAccount).filter(models.EmailAccount.id == self.account.id).first()
            if account and stamp > (account.sync_cursor or ""):
                account.sync_cursor = stamp
                self.account.sync_cursor = stamp
                db.commit()

    def _beat(self, *, force: bool = False) -> None:
        """Writes the heartbeat at most every ``HEARTBEAT_SECONDS`` (a long sync must not look stale)."""
        if force or time.monotonic() - self._last_beat >= HEARTBEAT_SECONDS:
            imap_service.write_heartbeat(self.account, models.MonitorStatus.RUNNING, source="Outlook monitor")
            self._last_beat = time.monotonic()

    def sync(self, callback: Callable[[dict, Any], None]) -> int:
        """Hands over mail not stored yet and advances the cursor; returns how many were handed over.

        The callback swallows its own errors, so success is checked by looking for the stored
        message. A message that fails stops the cursor in front of it so the next poll retries it,
        and after ``_MAX_ATTEMPTS`` polls it is skipped instead of blocking the mailbox.
        """
        handed_over = 0
        advance_to: datetime | None = None
        for email_data in self.fetch_new():
            message_id = email_data["message_id"]
            if not self._is_stored(message_id):
                self._beat()
                callback(email_data, self)
                handed_over += 1
                if not self._is_stored(message_id):
                    self._failures[message_id] = self._failures.get(message_id, 0) + 1
                    if self._failures[message_id] < _MAX_ATTEMPTS:
                        break
                    event_logger.log(
                        models.EventType.ERROR,
                        "GraphMailMonitor",
                        f"Skipping Outlook message {message_id} after {_MAX_ATTEMPTS} failed attempts.",
                    )
            advance_to = email_data["received_at"]
        if advance_to is not None:
            self.update_cursor(advance_to)
        return handed_over

    def mark_as_read(self, uid: Any) -> None:
        """No-op: ``Mail.ReadWrite`` is not granted, so the server copy is left alone."""

    def _still_wanted(self) -> bool:
        """Whether the mailbox is still active and connected (a disconnect deactivates it)."""
        with database.SessionLocal() as db:
            row = db.query(models.EmailAccount).filter(models.EmailAccount.id == self.account.id).first()
            return bool(row and row.is_active and row.connection_id)

    def _nap(self, seconds: int) -> None:
        """Sleeps in heartbeat-sized steps so the account keeps reporting that it is alive."""
        remaining = seconds
        while remaining > 0:
            step = min(remaining, HEARTBEAT_SECONDS)
            time.sleep(step)
            remaining -= step
            self._beat()

    def poll_loop(self, callback: Callable[[dict, Any], None]) -> None:
        """Polls until the mailbox is disconnected or deactivated, backing off on errors."""
        event_logger.log(
            models.EventType.INFO, "GraphMailMonitor", f"Starting Outlook polling for {self.account.email}"
        )
        while self._still_wanted():
            try:
                self.sync(callback)
                self._beat(force=True)
                self._nap(POLL_SECONDS)
            except Exception as exc:
                event_logger.log(
                    models.EventType.ERROR, "GraphMailMonitor", f"Error polling {self.account.email}: {exc}"
                )
                imap_service.write_heartbeat(
                    self.account, models.MonitorStatus.ERROR, error=str(exc), source="Outlook monitor"
                )
                time.sleep(_ERROR_BACKOFF_SECONDS)
        event_logger.log(models.EventType.INFO, "GraphMailMonitor", f"Stopped Outlook polling for {self.account.email}")
