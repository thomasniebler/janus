"""Email ingestion callbacks and IMAP worker helpers."""

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from janus import database, event_logger, graph_mail_service, imap_service, models, notifications, processor
from janus.services import oauth_connection_service
from janus.services.email_service import EmailService
from janus.services.topic_service import TopicService
from janus.services.topic_text import strip_candidate_topics
from janus.worker_manager import worker_manager

logger = logging.getLogger("janus")


def _parse_notification_filter_values(raw_values: str | None) -> list[str]:
    """Parse a comma/newline-delimited preference field into normalized tokens."""
    if not raw_values:
        return []
    values = []
    for token in raw_values.replace("\n", ",").split(","):
        normalized = token.strip().lower()
        if normalized:
            values.append(normalized)
    return values


def _as_utc_aware(value: datetime | None) -> datetime | None:
    """Normalize datetimes to timezone-aware UTC for robust schedule comparisons."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _email_matches_notification_filters(
    email_record: models.EmailRecord, preference: models.NotificationPreference | None
) -> bool:
    """Return whether an email passes configured sender/keyword allow/block filters."""
    if preference is None:
        return True

    sender_text = (email_record.sender or "").lower()
    content_text = f"{email_record.subject or ''} {email_record.snippet or ''}".lower()

    sender_allowlist = _parse_notification_filter_values(preference.sender_allowlist)
    sender_blocklist = _parse_notification_filter_values(preference.sender_blocklist)
    keyword_allowlist = _parse_notification_filter_values(preference.keyword_allowlist)
    keyword_blocklist = _parse_notification_filter_values(preference.keyword_blocklist)

    if sender_blocklist and any(token in sender_text for token in sender_blocklist):
        return False
    if keyword_blocklist and any(token in content_text for token in keyword_blocklist):
        return False
    if sender_allowlist and not any(token in sender_text for token in sender_allowlist):
        return False
    if keyword_allowlist and not any(token in content_text for token in keyword_allowlist):  # noqa: SIM103
        return False
    return True


def _get_background_notification_preference(db: Session, user_id: int | None) -> models.NotificationPreference | None:
    """Return the notification preference of the email's owner, creating defaults when needed."""
    if user_id is None:
        return None

    preference = (
        db.query(models.NotificationPreference).filter(models.NotificationPreference.user_id == user_id).first()
    )
    if preference is not None:
        return preference

    preference = models.NotificationPreference(user_id=user_id)
    db.add(preference)
    db.commit()
    db.refresh(preference)
    return preference


def _should_notify_important_email(
    email_record: models.EmailRecord, preference: models.NotificationPreference | None
) -> bool:
    """Return whether immediate notifications should be emitted for this IMPORTANT email."""
    if preference is None:
        return True
    if not preference.notify_important:
        return False
    return _email_matches_notification_filters(email_record, preference)


def _build_digest_generation_payloads(
    db: Session,
    emails: list[models.EmailRecord],
    *,
    user: models.User | None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Return digest email payloads and surfaced topic IDs for topic-aware digests."""
    if not emails:
        return [], []

    user_id = user.id if user else None
    if user_id is None or not TopicService(db).is_digest_grouping_enabled(user_id):
        return (
            [{"sender": e.sender, "subject": e.subject, "snippet": e.snippet} for e in emails],
            [],
        )

    payloads = strip_candidate_topics(EmailService(db).serialize_emails(emails, user=user))
    topic_ids = sorted(
        {
            int(topic["id"])
            for payload in payloads
            for topic in payload.get("topics", [])
            if isinstance(topic, dict) and isinstance(topic.get("id"), int)
        }
    )
    return payloads, topic_ids


def email_callback(email_data: dict, monitor: Any = None):
    """Callback function triggered when a new email is received."""
    db = database.SessionLocal()
    proc = processor.EmailProcessor(db)
    try:
        email_record = proc.process_email(email_data)
        # The receiving account's owner decides, attributed like the rest of ingestion (JAN-298).
        owner_id = proc._resolve_user_id({"account_id": email_record.account_id})
        preference = _get_background_notification_preference(db, owner_id)

        # A provisional DIGEST (waiting for its mail.received trigger run) is
        # not a decision yet, so it must not mark the message read on the server.
        awaiting_janus = email_record.reasoning == processor.PENDING_TRIGGER_PROCESSING_REASON
        if email_record.priority == models.Priority.DIGEST and not awaiting_janus and monitor and "uid" in email_data:
            monitor.mark_as_read(email_data["uid"])

        if (
            email_record.priority == models.Priority.IMPORTANT
            and not email_record.notified
            and _should_notify_important_email(email_record, preference)
        ):
            notifications.NotificationService.notify(email_record)
            email_record.notified = True
            db.commit()
            event_logger.log(
                models.EventType.INFO,
                "EmailProcessor",
                f"Email {email_record.message_id} classified as IMPORTANT and notified.",
                {"subject": email_record.subject, "sender": email_record.sender},
            )
    except Exception as e:
        event_logger.log(
            models.EventType.ERROR, "EmailProcessor", f"Failed to process email: {e}", {"email_data": email_data}
        )
    finally:
        db.close()


def initial_sync_worker(account: models.EmailAccount, stop_event: threading.Event | None = None):
    """Perform initial sync then hand off to the IDLE loop, logging as the account's owner (JAN-272)."""
    with event_logger.owned_by(account.user_id):
        _sync_and_idle(account, stop_event or threading.Event())


def _sync_and_idle(account: models.EmailAccount, stop_event: threading.Event) -> None:
    if account.auth_type == oauth_connection_service.MICROSOFT:
        # Outlook mail is read over Graph (no IMAP permission); the first poll is the initial sync.
        logger.info(f"Starting Outlook polling for {account.email}...")
        graph_mail_service.GraphMailMonitor(account, stop_event=stop_event).poll_loop(email_callback)
        return
    monitor = imap_service.IMAPMonitor(account, stop_event=stop_event)
    logger.info(f"Starting background initial sync for {account.email}...")
    new_emails = monitor.fetch_since_uid(account.last_uid or 0)
    for email_data in new_emails:
        if monitor.stopped:
            return
        email_callback(email_data, monitor)
        monitor.update_last_uid(email_data["uid"])
    logger.info(f"Initial sync completed for {account.email} ({len(new_emails)} new).")
    monitor.idle_loop(email_callback)


_ACCOUNT_COLUMNS = sa_inspect(models.EmailAccount).column_attrs

# One stop signal per mailbox id, shared by the monitor thread and whoever stops it.
_stop_events: dict[int, threading.Event] = {}
_stop_events_lock = threading.Lock()


def mailbox_worker_label(email_address: str) -> str:
    """The worker_manager label of a mailbox's monitor thread."""
    return f"imap:{email_address}"


def start_mailbox_worker(account: models.EmailAccount) -> None:
    """Start, or restart, the monitor thread of an active mailbox; an inactive one is left alone.

    Called at ``janus start`` for every active mailbox and whenever one is added or switched on.
    """
    if not account.is_active:
        return
    # The thread gets its own copy of the row's columns: the caller's object belongs to a request session.
    own = models.EmailAccount(**{column.key: getattr(account, column.key) for column in _ACCOUNT_COLUMNS})
    stop_event = threading.Event()
    with _stop_events_lock:
        previous = _stop_events.get(account.id)
        _stop_events[account.id] = stop_event
    if previous is not None:
        previous.set()
    try:
        worker_manager.start(mailbox_worker_label(own.email), initial_sync_worker, args=(own, stop_event))
    except RuntimeError as exc:
        # The previous monitor is still finishing a message; it stops on its own, but this one could not start.
        logger.warning("Could not start the monitor for %s: %s", account.email, exc)


def stop_mailbox_worker(account_id: int, email_address: str) -> None:
    """Stop a mailbox's monitor thread (deleted or switched off) without waiting for it to finish."""
    with _stop_events_lock:
        stop_event = _stop_events.pop(account_id, None)
    if stop_event is not None:
        stop_event.set()
    worker_manager.stop(mailbox_worker_label(email_address), timeout=0)
