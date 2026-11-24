"""IMAP service for monitoring and fetching emails."""

import email
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email import utils as email_utils
from typing import TYPE_CHECKING, Any

from imapclient import IMAPClient

from janus import database, event_logger, models, security, status_service
from janus.services import mailbox_oauth_service
from janus.time_utils import format_imap_date

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
_FETCH_BATCH_SIZE = 200
_DEFAULT_INITIAL_HISTORY_LIMIT = 100
# How many days back to look on the very first sync (no last_uid).
# Only emails received within this window are candidates; the count cap
# (initial_sync_email_limit) is then applied as a secondary safety net.
_DEFAULT_INITIAL_LOOKBACK_DAYS = 30


INITIAL_LOOKBACK_DAYS = _DEFAULT_INITIAL_LOOKBACK_DAYS


def initial_history_limit(db: "Session | None" = None) -> int:
    """Returns the configured cap for a mailbox's first catch-up (``Settings.initial_sync_email_limit``).

    If *db* is provided, the existing session is used and not closed.
    If *db* is None a short-lived session is created and closed here.
    """
    own_session = db is None
    if own_session:
        db = database.SessionLocal()
    try:
        settings = db.query(models.Settings).first()
        if settings and settings.initial_sync_email_limit > 0:
            return settings.initial_sync_email_limit
        return _DEFAULT_INITIAL_HISTORY_LIMIT
    finally:
        if own_session:
            db.close()


class IMAPMonitor:
    """Monitors an IMAP account for new emails."""

    def __init__(self, account: models.EmailAccount):
        """Initializes an IMAP monitor for a given account.

        Args:
            account (EmailAccount): The email account to monitor.
        """
        self.account = account
        self.client = None

    def _fetch_in_batches(self, message_ids: list[Any], parts: list[bytes]) -> list[tuple[Any, dict[bytes, Any]]]:
        """Fetch IMAP messages in bounded chunks to avoid oversized UID FETCH commands."""
        results: list[tuple[Any, dict[bytes, Any]]] = []
        for start in range(0, len(message_ids), _FETCH_BATCH_SIZE):
            batch = message_ids[start : start + _FETCH_BATCH_SIZE]
            fetched = self.client.fetch(batch, parts)
            results.extend(fetched.items())
        return results

    def _get_initial_history_limit(self, db: "Session | None" = None) -> int:
        """Returns the configured cap for the first mailbox catch-up."""
        return initial_history_limit(db)

    def connect(self):
        """Connects to the IMAP server, logs in, and checks UIDVALIDITY."""
        try:
            self.client = IMAPClient(self.account.imap_server, port=self.account.imap_port, ssl=True)
            _login(self.client, self.account)
            self.client.select_folder("INBOX")
            event_logger.log(models.EventType.INFO, "IMAPMonitor", f"Successfully connected to {self.account.email}")
            self._check_uid_validity()
        except Exception as e:
            event_logger.log(models.EventType.ERROR, "IMAPMonitor", f"Failed to connect to {self.account.email}: {e}")
            raise

    def _check_uid_validity(self) -> None:
        """Checks UIDVALIDITY; clears cached emails and resets last_uid if it changed."""
        status = self.client.folder_status("INBOX", ["UIDVALIDITY"])
        server_uid_validity = status[b"UIDVALIDITY"]

        db = database.SessionLocal()
        try:
            account = db.query(models.EmailAccount).filter(models.EmailAccount.id == self.account.id).first()
            if account is None:
                return

            if account.uid_validity is None:
                # First connection — store validity and proceed with full sync
                account.uid_validity = server_uid_validity
                db.commit()
            elif account.uid_validity != server_uid_validity:
                # Mailbox was rebuilt — purge cached emails and start fresh
                event_logger.log(
                    models.EventType.WARNING,
                    "IMAPMonitor",
                    (
                        f"UIDVALIDITY changed for {self.account.email} "
                        f"({account.uid_validity} → {server_uid_validity}). Purging cached emails."
                    ),
                )
                db.query(models.EmailRecord).filter(models.EmailRecord.account_id == self.account.id).delete()
                account.uid_validity = server_uid_validity
                account.last_uid = 0
                db.commit()

            # Keep local reference in sync
            self.account.uid_validity = account.uid_validity
            self.account.last_uid = account.last_uid
        finally:
            db.close()

    def update_last_uid(self, uid: int) -> None:
        """Persists the highest processed UID for this account."""
        db = database.SessionLocal()
        try:
            account = db.query(models.EmailAccount).filter(models.EmailAccount.id == self.account.id).first()
            if account and uid > (account.last_uid or 0):
                account.last_uid = uid
                self.account.last_uid = uid
                db.commit()
        finally:
            db.close()

    def logout(self):
        """Logs out from the IMAP server."""
        if self.client:
            try:
                self.client.logout()
                event_logger.log(models.EventType.INFO, "IMAPMonitor", f"Logged out from {self.account.email}")
            except Exception as e:
                event_logger.log(
                    models.EventType.WARNING, "IMAPMonitor", f"Error during logout from {self.account.email}: {e}"
                )
            self.client = None

    def mark_as_read(self, uid: Any):
        """Marks a message as read on the server.

        Args:
            uid: The UID or sequence number of the message.
        """
        if not self.client:
            self.connect()
        try:
            self.client.add_flags(uid, [b"\\Seen"])
            event_logger.log(
                models.EventType.INFO, "IMAPMonitor", f"Marked email {uid} as read on {self.account.email}"
            )
        except Exception as e:
            event_logger.log(models.EventType.ERROR, "IMAPMonitor", f"Failed to mark email {uid} as read: {e}")

    def test_connection(self) -> tuple[bool, str]:
        """Tests the IMAP connection and login.

        Returns:
            tuple[bool, str]: A tuple containing a success boolean and a message.
        """
        try:
            self.connect()
            self.logout()
            return True, f"Successfully connected to {self.account.email}"
        except Exception as e:
            return False, f"IMAP connection failed for {self.account.email}: {e}"

    def _decode_payload(self, payload: bytes, charset: str | None) -> str:
        """Decodes a message payload with a safe charset fallback."""
        effective_charset = charset or "utf-8"
        try:
            return payload.decode(effective_charset, errors="ignore")
        except LookupError:
            return payload.decode("utf-8", errors="ignore")

    def _extract_body(self, message: email.message.Message) -> str:
        """Extracts plain text body content from an email message."""
        text_parts: list[str] = []

        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() != "text/plain":
                    continue
                if part.get_content_disposition() == "attachment":
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    decoded = self._decode_payload(payload, part.get_content_charset()).strip()
                    if decoded:
                        text_parts.append(decoded)
        else:
            payload = message.get_payload(decode=True)
            if payload:
                decoded = self._decode_payload(payload, message.get_content_charset()).strip()
                if decoded:
                    text_parts.append(decoded)

        return "\n\n".join(text_parts).strip()

    def _extract_attachments(self, message: email.message.Message) -> list[dict]:
        """Returns the message's attachment parts as ``{filename, mime_type, data}`` (JAN-242).

        The processor stages them as knowledge sources once the email row exists;
        which of them are parsed (documents up to 10 MB) is decided there.
        """
        attachments: list[dict] = []
        if not message.is_multipart():
            return attachments
        for part in message.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if part.get_content_disposition() != "attachment" and not filename:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            attachments.append({"filename": filename, "mime_type": part.get_content_type(), "data": payload})
        return attachments

    def _extract_snippet(self, message: email.message.Message, limit: int = 100) -> str:
        """Extracts a plain text snippet from an email message body.

        Args:
            message (email.message.Message): The email message object.
            limit (int): The maximum length of the snippet.

        Returns:
            str: The extracted snippet.
        """
        return self._extract_body(message)[:limit].strip()

    def _normalize_email_timestamp(self, value: datetime | None) -> datetime | None:
        """Normalizes IMAP/header timestamps to timezone-aware UTC datetimes.

        Naive datetimes are treated as UTC to keep persisted email ordering stable.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _resolve_received_at(
        self, message: email.message.Message, internal_date: datetime | None = None, uid: Any | None = None
    ) -> datetime:
        """Returns the best available message timestamp.

        Preference order:
        1) RFC 5322 Date header on the message
        2) IMAP INTERNALDATE metadata
        3) Current UTC time fallback

        This function always returns a non-None datetime.
        """
        date_header = message.get("Date")
        if date_header:
            try:
                parsed = email_utils.parsedate_to_datetime(date_header)
                normalized = self._normalize_email_timestamp(parsed)
                if normalized is not None:
                    return normalized
            except Exception:
                event_logger.log(
                    models.EventType.WARNING,
                    "IMAPMonitor",
                    f"Invalid Date header for message {uid or 'unknown'} on {self.account.email}; falling back.",
                    {"uid": uid, "date_header": date_header},
                )

        normalized_internal = self._normalize_email_timestamp(internal_date)
        if normalized_internal is not None:
            return normalized_internal

        event_logger.log(
            models.EventType.WARNING,
            "IMAPMonitor",
            (
                f"Missing message timestamp for message {uid or 'unknown'} on "
                f"{self.account.email}; using current UTC time."
            ),
            {"uid": uid},
        )
        return datetime.now(UTC)

    def fetch_since_uid(self, last_uid: int = 0) -> list[dict]:
        """Fetches all emails with UID greater than last_uid.

        On the very first sync (last_uid == 0) only emails received within the
        last _DEFAULT_INITIAL_LOOKBACK_DAYS days are considered, capped further
        to the most recent Settings.initial_sync_email_limit messages (default:
        _DEFAULT_INITIAL_HISTORY_LIMIT).

        Args:
            last_uid (int): The highest UID already processed. 0 triggers the
                date-bounded initial sync.

        Returns:
            List[dict]: Email data dicts, ordered oldest first.
        """
        if not self.client:
            self.connect()

        effective_last_uid = self.account.last_uid or last_uid
        if effective_last_uid > 0:
            search_criteria: list = ["UID", f"{effective_last_uid + 1}:*"]
        else:
            # Initial sync: only look back the configured default window so we
            # never pull years of history.
            cutoff = datetime.now(UTC) - timedelta(days=_DEFAULT_INITIAL_LOOKBACK_DAYS)
            search_criteria = ["SINCE", format_imap_date(cutoff)]
        uids = self.client.search(search_criteria)
        if not uids:
            return []
        if effective_last_uid <= 0:
            # Secondary safety net: cap to the most recent N messages.
            uids = uids[-self._get_initial_history_limit() :]

        results = []
        for uid, data in self._fetch_in_batches(uids, [b"BODY.PEEK[]", b"INTERNALDATE"]):
            if uid <= effective_last_uid:
                continue  # Server may return the boundary UID; skip it
            email_body = data.get(b"BODY[]")
            if not email_body:
                continue
            internal_date = data.get(b"INTERNALDATE")
            message = email.message_from_bytes(email_body)

            sender_name, sender_email = email_utils.parseaddr(message.get("From", ""))
            subject = message.get("Subject")
            message_id = message.get("Message-ID")

            body = self._extract_body(message)
            results.append(
                {
                    "uid": uid,
                    "message_id": message_id,
                    "sender": sender_email,
                    "subject": subject,
                    "snippet": body[:100].strip(),
                    "body": body,
                    "received_at": self._resolve_received_at(message, internal_date=internal_date, uid=uid),
                    "account_id": self.account.id,
                    "attachments": self._extract_attachments(message),
                }
            )

        results.sort(key=lambda e: (e["received_at"], e["uid"]))
        return results

    def fetch_latest_emails(self, limit: int = 10) -> list[dict]:
        """Fetches the latest emails from the server, regardless of seen status.

        Args:
            limit (int): The number of emails to fetch.

        Returns:
            List[dict]: A list of dictionary representations of the latest emails.
        """
        if not self.client:
            self.connect()

        # Get all message IDs and take the last 'limit' ones
        messages = self.client.search(["ALL"])
        if not messages:
            return []

        # messages are usually in ascending order of arrival, so we take the last ones
        latest_ids = messages[-limit:]

        results = []
        # Use BODY.PEEK[] to avoid marking as seen
        for msgid, data in self._fetch_in_batches(latest_ids, [b"BODY.PEEK[]", b"INTERNALDATE"]):
            email_body = data.get(b"BODY[]")
            if not email_body:
                continue
            internal_date = data.get(b"INTERNALDATE")
            message = email.message_from_bytes(email_body)

            sender_name, sender_email = email_utils.parseaddr(message.get("From"))
            subject = message.get("Subject")
            message_id = message.get("Message-ID")

            body = self._extract_body(message)

            results.append(
                {
                    "uid": msgid,
                    "message_id": message_id,
                    "sender": sender_email,
                    "subject": subject,
                    "snippet": body[:100].strip(),
                    "body": body,
                    "received_at": self._resolve_received_at(message, internal_date=internal_date, uid=msgid),
                    "account_id": self.account.id,
                    "attachments": self._extract_attachments(message),
                }
            )

        results.sort(key=lambda e: (e["received_at"], e["uid"]), reverse=True)
        return results

    def fetch_new_emails(self) -> list[dict]:
        """Fetches unseen emails from the server.

        Returns:
            List[dict]: A list of dictionary representations of new emails.
        """
        if not self.client:
            self.connect()

        # Search for unseen messages
        messages = self.client.search(["UNSEEN"])
        results = []

        if not messages:
            return []

        # Use BODY.PEEK[] to avoid marking as seen
        for msgid, data in self._fetch_in_batches(messages, [b"BODY.PEEK[]", b"INTERNALDATE"]):
            email_body = data[b"BODY[]"]
            internal_date = data.get(b"INTERNALDATE")
            message = email.message_from_bytes(email_body)

            sender_name, sender_email = email_utils.parseaddr(message.get("From"))
            subject = message.get("Subject")
            message_id = message.get("Message-ID")

            body = self._extract_body(message)

            results.append(
                {
                    "uid": msgid,
                    "message_id": message_id,
                    "sender": sender_email,
                    "subject": subject,
                    "snippet": body[:100].strip(),
                    "body": body,
                    "received_at": self._resolve_received_at(message, internal_date=internal_date, uid=msgid),
                    "account_id": self.account.id,
                    "attachments": self._extract_attachments(message),
                }
            )

        return results

    def _write_heartbeat(self, status: models.MonitorStatus, error: str | None = None) -> None:
        """Persists monitor liveness and status to the database."""
        write_heartbeat(self.account, status, error)

    def idle_loop(self, callback: Callable[[dict, Any], None]):
        """Monitors for new emails using IDLE if supported.

        Args:
            callback (Callable[[dict, Any], None]): A function to call with new email data and monitor instance.
        """
        self.connect()
        event_logger.log(models.EventType.INFO, "IMAPMonitor", f"Starting IDLE loop for {self.account.email}")
        self._write_heartbeat(models.MonitorStatus.RUNNING)

        _heartbeat_interval = 30  # seconds
        _last_heartbeat = time.monotonic()

        while True:
            try:
                self.client.idle()
                responses = self.client.idle_check(timeout=_heartbeat_interval)
                self.client.idle_done()

                now = time.monotonic()
                if now - _last_heartbeat >= _heartbeat_interval:
                    self._write_heartbeat(models.MonitorStatus.RUNNING)
                    _last_heartbeat = now

                if any(resp[1] == b"EXISTS" for resp in responses):
                    event_logger.log(
                        models.EventType.INFO,
                        "IMAPMonitor",
                        f"New activity detected for {self.account.email}",
                        {"action": "fetching_emails"},
                    )
                    new_emails = self.fetch_since_uid(self.account.last_uid or 0)
                    for email_data in new_emails:
                        callback(email_data, self)
                    if new_emails:
                        self.update_last_uid(new_emails[-1]["uid"])

            except Exception as e:
                error_msg = str(e)
                event_logger.log(
                    models.EventType.ERROR, "IMAPMonitor", f"Error in IDLE loop for {self.account.email}: {e}"
                )
                self._write_heartbeat(models.MonitorStatus.ERROR, error=error_msg)
                time.sleep(10)  # Wait before retry
                try:
                    self.connect()
                    # Catch up on any emails that arrived during the outage
                    missed = self.fetch_since_uid(self.account.last_uid or 0)
                    for email_data in missed:
                        callback(email_data, self)
                    if missed:
                        self.update_last_uid(missed[-1]["uid"])
                    self._write_heartbeat(models.MonitorStatus.RUNNING)
                    _last_heartbeat = time.monotonic()
                except Exception:
                    pass  # Supervisor will restart the whole worker on persistent failure


def write_heartbeat(
    account: models.EmailAccount,
    status: models.MonitorStatus,
    error: str | None = None,
    *,
    source: str = "IMAP monitor",
) -> None:
    """Persists a mailbox monitor's liveness and status (shared by the IMAP and Outlook monitors)."""
    try:
        db = database.SessionLocal()
        try:
            row = db.query(models.EmailAccount).filter(models.EmailAccount.id == account.id).first()
            if row:
                previous_status = (
                    status_service.STATUS_OUTAGE
                    if row.monitor_status == models.MonitorStatus.ERROR
                    else status_service.STATUS_OPERATIONAL
                )
                row.monitor_status = status
                row.last_heartbeat = datetime.now(UTC)
                row.last_error = error if error is not None else None
                db.commit()
                next_status = (
                    status_service.STATUS_OUTAGE
                    if status == models.MonitorStatus.ERROR
                    else status_service.STATUS_OPERATIONAL
                )
                status_service.log_status_transition(
                    service_id="mail-sync",
                    service_name="Mail Sync",
                    component_id=f"mail-account-{row.id}",
                    component_name=row.name or row.email,
                    previous_status=previous_status,
                    new_status=next_status,
                    message=row.last_error or f"The {source} heartbeat is healthy.",
                )
        finally:
            db.close()
    except Exception as e:
        logger.warning("Failed to write heartbeat for %s: %s", account.email, e)


def _login(client: IMAPClient, account: models.EmailAccount) -> None:
    """Logs in with the account's password, or XOAUTH2 with a fresh token for a connected Google/Microsoft one."""
    if account.auth_type in (None, "password"):
        client.login(account.username, security.security_service.decrypt(account.password))
    else:
        client.oauth2_login(account.username, mailbox_oauth_service.imap_access_token(account))


def mark_message_read(account: models.EmailAccount, uid: int) -> bool:
    """Marks one INBOX message read over a short-lived connection of its own.

    For callers off the account's IDLE thread (the ``mail.received`` trigger
    run), whose monitor connection cannot be shared. The mailbox is left alone
    when its UIDVALIDITY no longer matches the stored one, because ``uid`` then
    names a different message. Failures are logged and reported as ``False``.
    """
    try:
        with IMAPClient(account.imap_server, port=account.imap_port, ssl=True, timeout=30) as client:
            _login(client, account)
            client.select_folder("INBOX")
            if account.uid_validity is not None:
                status = client.folder_status("INBOX", ["UIDVALIDITY"])
                if status.get(b"UIDVALIDITY") != account.uid_validity:
                    event_logger.log(
                        models.EventType.WARNING,
                        "IMAPMonitor",
                        f"Not marking email {uid} read on {account.email}: the mailbox UIDVALIDITY changed.",
                        user_id=account.user_id,
                    )
                    return False
            client.add_flags([uid], [b"\\Seen"])
    except Exception as e:
        event_logger.log(
            models.EventType.ERROR,
            "IMAPMonitor",
            f"Failed to mark email {uid} as read: {e}",
            user_id=account.user_id,
        )
        return False
    event_logger.log(
        models.EventType.INFO,
        "IMAPMonitor",
        f"Marked email {uid} as read on {account.email}",
        user_id=account.user_id,
    )
    return True
