"""Reminder memory and synthetic calendar entry helpers."""

import logging
from datetime import datetime, timedelta
from typing import Any

from janus import event_logger, llm_service, models
from janus.time_utils import normalize_timestamp, utcnow

logger = logging.getLogger(__name__)
PREPARATION_HORIZON = timedelta(weeks=6)
EVENT_EXPIRY_GRACE = timedelta(hours=24)
PREPARATION_EXPIRY_GRACE = timedelta(hours=72)
SYNTHETIC_EVENT_DURATION = timedelta(minutes=30)


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


class ReminderService:
    """Derives, stores, and summarizes active reminder memory.

    Reminder memory has no owner column: a reminder belongs to the owner of
    its source email, i.e. the owner of the account that email arrived on
    (JAN-296), which is also how domain-change notifications attribute it.
    A reminder whose source email has no account owner (or no longer exists)
    has no owner and is read by nobody. Every read takes the owner's id.
    """

    def __init__(self, db):
        """Initializes the reminder service with an existing database session."""
        self.db = db
        self._llm: llm_service.LLMService | None = None

    def _get_llm(self) -> llm_service.LLMService:
        if self._llm is None:
            self._llm = llm_service.LLMService(db=self.db)
        return self._llm

    def _dedupe_key(self, email_record: models.EmailRecord, candidate: dict[str, Any], due_at: datetime) -> str:
        hint = candidate.get("dedupe_hint")
        if isinstance(hint, str) and hint.strip():
            return f"{email_record.account_id}:{candidate['kind']}:{_slug(hint)}"
        title = candidate.get("title") or email_record.subject or "reminder"
        return f"{email_record.account_id}:{candidate['kind']}:{_slug(title)}:{due_at.date().isoformat()}"

    def email_owner_id(self, email_record: models.EmailRecord) -> int | None:
        """Returns the owner of ``email_record``: its account's owner, or ``None``."""
        account = self.db.get(models.EmailAccount, email_record.account_id) if email_record.account_id else None
        return account.user_id if account is not None else None

    def _reminders_for_user(self, user_id: int):
        """Returns a query over the reminders whose source email arrived on one of ``user_id``'s accounts."""
        return (
            self.db.query(models.ReminderMemory)
            .join(models.EmailRecord, models.ReminderMemory.source_email_id == models.EmailRecord.id)
            .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
            .filter(models.EmailAccount.user_id == user_id)
        )

    def _reminder_owner_id(self, reminder: models.ReminderMemory) -> int | None:
        if reminder.source_email_id is None:
            return None
        email_record = self.db.get(models.EmailRecord, reminder.source_email_id)
        return self.email_owner_id(email_record) if email_record is not None else None

    def _owned_by_another_user(self, reminder: models.ReminderMemory, owner_id: int | None) -> bool:
        """Whether ``reminder`` has an owner other than ``owner_id``; an ownerless one may be adopted."""
        existing_owner_id = self._reminder_owner_id(reminder)
        return existing_owner_id is not None and existing_owner_id != owner_id

    def _is_stale(self, reminder: models.ReminderMemory, now: datetime) -> bool:
        anchor = normalize_timestamp(reminder.prepare_for_at or reminder.due_at)
        if anchor is None:
            return False
        grace = PREPARATION_EXPIRY_GRACE if reminder.kind == models.ReminderKind.PREPARATION else EVENT_EXPIRY_GRACE
        return now > anchor + grace

    def expire_stale_reminders(self, now: datetime | None = None) -> None:
        """Expires active reminders that are well past their relevant time."""
        now = normalize_timestamp(now or utcnow()) or utcnow()
        active = (
            self.db.query(models.ReminderMemory)
            .filter(models.ReminderMemory.status == models.ReminderStatus.ACTIVE)
            .all()
        )
        for reminder in active:
            if self._is_stale(reminder, now):
                reminder.status = models.ReminderStatus.EXPIRED
                if reminder.synthetic_calendar_event_id:
                    event = self.db.get(models.CalendarEvent, reminder.synthetic_calendar_event_id)
                    if event is not None:
                        self.db.delete(event)
                        reminder.synthetic_calendar_event_id = None
        self.db.flush()

    def get_active_reminders(self, user_id: int | None, now: datetime | None = None) -> list[models.ReminderMemory]:
        """Returns ``user_id``'s active reminders ordered by the next relevant timestamp.

        With no owner (``None``) there are none: ownerless reminders are never read.
        """
        if user_id is None:
            return []
        now = normalize_timestamp(now or utcnow()) or utcnow()
        reminders = (
            self._reminders_for_user(user_id).filter(models.ReminderMemory.status == models.ReminderStatus.ACTIVE).all()
        )
        reminders = [reminder for reminder in reminders if not self._is_stale(reminder, now)]
        reminders.sort(key=lambda reminder: normalize_timestamp(reminder.prepare_for_at or reminder.due_at) or now)
        return reminders

    def get_preparation_outlook(self, user_id: int | None, now: datetime | None = None) -> list[models.CalendarEvent]:
        """Returns the next 6 weeks of events imported from ``user_id``'s own planner accounts."""
        if user_id is None:
            return []
        now = now or utcnow()
        horizon_end = now + PREPARATION_HORIZON
        return (
            self.db.query(models.CalendarEvent)
            .join(models.PlannerAccount, models.CalendarEvent.planner_account_id == models.PlannerAccount.id)
            .filter(
                models.PlannerAccount.user_id == user_id,
                models.CalendarEvent.source_type == models.CalendarEventSource.PLANNER,
                models.CalendarEvent.start_time >= now,
                models.CalendarEvent.start_time < horizon_end,
            )
            .order_by(models.CalendarEvent.start_time.asc())
            .all()
        )

    def build_email_context(self, user_id: int | None, now: datetime | None = None) -> dict[str, Any]:
        """Returns ``user_id``'s reminder and calendar context for email extraction (JAN-296).

        Pass the owner of the email being extracted; with no owner both lists are empty.
        """
        now = now or utcnow()
        return {
            "active_reminders": [
                {
                    "title": reminder.title,
                    "kind": reminder.kind.value,
                    "due_at": (normalize_timestamp(reminder.due_at) or now).isoformat(),
                    "prepare_for_at": normalize_timestamp(reminder.prepare_for_at).isoformat()
                    if reminder.prepare_for_at
                    else None,
                    "notes": reminder.notes,
                }
                for reminder in self.get_active_reminders(user_id, now)
            ],
            "calendar_upcoming": [
                {"summary": event.summary, "time": (normalize_timestamp(event.start_time) or now).isoformat()}
                for event in self.get_preparation_outlook(user_id, now)
            ],
        }

    def _sync_synthetic_calendar_entry(self, reminder: models.ReminderMemory) -> None:
        start_time = normalize_timestamp(reminder.prepare_for_at or reminder.due_at) or utcnow()
        end_time = start_time + SYNTHETIC_EVENT_DURATION
        summary_prefix = "[Prep]" if reminder.kind == models.ReminderKind.PREPARATION else "[Event]"
        event = None
        if reminder.synthetic_calendar_event_id:
            event = self.db.get(models.CalendarEvent, reminder.synthetic_calendar_event_id)
        if event is None:
            event = models.CalendarEvent()
            self.db.add(event)
        event.planner_account_id = None
        event.external_id = f"janus-reminder:{reminder.id or reminder.dedupe_key}"
        event.summary = f"{summary_prefix} {reminder.title}"
        event.start_time = start_time
        event.end_time = end_time
        event.location = "Janus reminder"
        event.description = reminder.notes
        event.source_type = models.CalendarEventSource.JANUS_REMINDER
        self.db.flush()
        reminder.synthetic_calendar_event_id = event.id

    def capture_email_reminders(self, email_record: models.EmailRecord) -> list[models.ReminderMemory]:
        """Extracts and persists reminder memory from a processed email."""
        if email_record.priority in {models.Priority.SPAM, models.Priority.IGNORE}:
            return []

        now = normalize_timestamp(email_record.received_at) or utcnow()
        candidates = self._get_llm().extract_email_reminders(
            {
                "account_id": email_record.account_id,
                "message_id": email_record.message_id,
                "sender": email_record.sender,
                "subject": email_record.subject,
                "snippet": email_record.snippet,
                "received_at": now,
                "priority": email_record.priority.value,
            },
            self.build_email_context(self.email_owner_id(email_record), now),
        )
        if not candidates:
            return []

        return self.persist_email_reminders(email_record, candidates, now)

    def persist_email_reminders(
        self, email_record: models.EmailRecord, candidates: list[dict], now: datetime | None = None
    ) -> list[models.ReminderMemory]:
        """Upsert reminder candidates (from the extractor or the mail tools) for one email."""
        now = now or normalize_timestamp(email_record.received_at) or utcnow()
        owner_id = self.email_owner_id(email_record)
        created_or_updated: list[models.ReminderMemory] = []
        for candidate in candidates:
            kind = candidate.get("kind")
            title = candidate.get("title")
            due_at = normalize_timestamp(candidate.get("due_at"))
            if kind not in {models.ReminderKind.EVENT.value, models.ReminderKind.PREPARATION.value}:
                continue
            if not title or due_at is None:
                continue
            prepare_for_at = normalize_timestamp(candidate.get("prepare_for_at"))
            dedupe_key = self._dedupe_key(email_record, candidate, due_at)
            reminder = (
                self.db.query(models.ReminderMemory).filter(models.ReminderMemory.dedupe_key == dedupe_key).first()
            )
            if reminder is not None and self._owned_by_another_user(reminder, owner_id):
                # Never rewrite or re-point another user's reminder (JAN-296).
                logger.warning("Skipped a reminder candidate: its dedupe key belongs to reminder %s.", reminder.id)
                continue
            if reminder is None:
                reminder = models.ReminderMemory(
                    source_email_id=email_record.id,
                    kind=models.ReminderKind(kind),
                    status=models.ReminderStatus.ACTIVE,
                    title=title,
                    dedupe_key=dedupe_key,
                    due_at=due_at,
                )
                self.db.add(reminder)
            reminder.source_email_id = email_record.id
            reminder.kind = models.ReminderKind(kind)
            reminder.status = models.ReminderStatus.ACTIVE
            reminder.title = title
            reminder.notes = candidate.get("notes")
            reminder.due_at = due_at
            reminder.prepare_for_at = prepare_for_at
            reminder.confidence = candidate.get("confidence")
            reminder.last_seen_at = now
            self.db.flush()
            self._sync_synthetic_calendar_entry(reminder)
            created_or_updated.append(reminder)

        self.expire_stale_reminders(now)
        for reminder in created_or_updated:
            event_logger.log(
                models.EventType.INFO,
                "ReminderService",
                f"Reminder captured: {reminder.title}",
                {
                    "kind": reminder.kind.value,
                    "status": reminder.status.value,
                    "due_at": (normalize_timestamp(reminder.due_at) or now).isoformat(),
                    "prepare_for_at": normalize_timestamp(reminder.prepare_for_at).isoformat()
                    if reminder.prepare_for_at
                    else None,
                },
            )
        return created_or_updated
