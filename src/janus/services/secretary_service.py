"""Proactive secretary: when to think, what to remember, what landed, and the WhatsApp alert cap.

The built-in **Secretary** trigger (``builtin_triggers.BUILTIN_SECRETARY``) runs
hourly. Everything that decides *whether* a run may cost a model call, and
everything the run is told about its own past, lives here as plain code so it is
testable without a model:

* :func:`prepare` -- the hook's decision: skip outside the owner's waking hours,
  skip when nothing changed since the last completed run (re-checking at least
  every :data:`REFRESH_AFTER`), otherwise build the context (its own recent runs
  and which of its conversations the owner opened or answered).
* :func:`whatsapp_alerts_since` / :func:`whatsapp_alerts_of_run` -- the counts
  behind the ``notify_me_on_whatsapp`` caps.

See ``docs/docs/tech-spec/background-workers-runtime.md#proactive-secretary``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from janus import models, prompt_injection

#: A run that has nothing to say ends with exactly this, so history and the UI can tell it from a finding.
QUIET_OUTPUT = "NOTHING_TO_REPORT"
#: What a skipped run records as its output. A skipped run completes like any other, so the gate and the history
#: must recognise these and look past them: a skip is not a time the Secretary last thought.
SKIP_OUTSIDE_HOURS = "Outside the secretary's active hours"
SKIP_NOTHING_NEW = "Nothing new since the last secretary run."
#: With no new mail, messages, reminders, or planner changes, re-check anyway after this long.
REFRESH_AFTER = timedelta(hours=6)
#: Earlier runs shown to the next run, and the characters kept of each.
HISTORY_RUNS = 4
HISTORY_CHARS = 1_200
#: How far back the engagement feedback looks.
ENGAGEMENT_WINDOW = timedelta(days=14)
ENGAGEMENT_LIMIT = 15
#: WhatsApp alerts one run and one owner may send in a rolling day.
MAX_WHATSAPP_ALERTS_PER_RUN = 1
MAX_WHATSAPP_ALERTS_PER_DAY = 3
WHATSAPP_ALERT_WINDOW = timedelta(days=1)
MAX_WHATSAPP_ALERT_CHARS = 600


@dataclass(frozen=True)
class SecretaryPlan:
    """The decision for one run: a reason to skip the model call, or the context to run with."""

    skip_output: str | None = None
    context: str | None = None
    #: The planner fingerprint this run saw; remembered after a successful run (:func:`remember_planner_fingerprint`).
    planner_fingerprint: str | None = None


def _naive_utc(moment: datetime) -> datetime:
    """Stored timestamps are naive UTC; compare against the same."""
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def get_preference(db: Session, user_id: int) -> models.NotificationPreference | None:
    """Return the owner's notification preference row, if one exists (defaults apply otherwise)."""
    return db.query(models.NotificationPreference).filter(models.NotificationPreference.user_id == user_id).first()


def get_preference_fingerprint(db: Session, user_id: int) -> str | None:
    """Return the planner fingerprint stored at the last real run, or ``None`` when there is none yet."""
    preference = get_preference(db, user_id)
    return preference.secretary_planner_fingerprint if preference else None


def active_window(preference: models.NotificationPreference | None) -> tuple[int, int]:
    """Return the waking-hours window ``(start_hour, end_hour)``, ``end`` exclusive (default 8 to 21)."""
    if preference is None:
        return 8, 21
    return preference.secretary_active_start_hour, preference.secretary_active_end_hour


def within_active_hours(now: datetime, tz_name: str | None, start_hour: int, end_hour: int) -> bool:
    """Whether ``now`` falls in ``[start_hour, end_hour)`` on the clock of ``tz_name`` (UTC when unknown)."""
    try:
        zone = ZoneInfo(tz_name or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    aware = now if now.tzinfo else now.replace(tzinfo=UTC)
    return start_hour <= aware.astimezone(zone).hour < end_hour


def is_skipped_output(output: str | None) -> bool:
    """Whether a completed run's output is a skip note: the model was not called for that run."""
    text = (output or "").strip()
    return text.startswith((SKIP_OUTSIDE_HOURS, SKIP_NOTHING_NEW))


def last_completed_run(db: Session, definition: models.TriggerDefinition) -> models.RuntimeTrigger | None:
    """Return the definition's newest run that actually thought (not a skipped one), or ``None``."""
    runs = (
        db.query(models.RuntimeTrigger)
        .filter(
            models.RuntimeTrigger.definition_id == definition.id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.COMPLETED.value,
            models.RuntimeTrigger.completed_at.is_not(None),
        )
        .order_by(models.RuntimeTrigger.completed_at.desc(), models.RuntimeTrigger.id.desc())
        .limit(500)  # hourly skips pile up overnight; the newest thinking run is always within a day or two
    )
    return next((run for run in runs if not is_skipped_output(run.output)), None)


def changes_since(db: Session, user_id: int, since: datetime) -> dict[str, int]:
    """Count what is new or changed for the owner since ``since`` (naive UTC): mail, WhatsApp, reminders, planner."""
    since = _naive_utc(since)
    mail = (
        db.query(func.count(models.EmailRecord.id))
        .join(models.EmailAccount, models.EmailAccount.id == models.EmailRecord.account_id)
        .filter(models.EmailAccount.user_id == user_id, models.EmailRecord.received_at > since)
        .scalar()
    )
    whatsapp = (
        db.query(func.count(models.WhatsAppMessage.id))
        .filter(
            models.WhatsAppMessage.user_id == user_id,
            models.WhatsAppMessage.direction == models.WhatsAppMessageDirection.INCOMING,
            models.WhatsAppMessage.created_at > since,
        )
        .scalar()
    )
    reminders = (
        # A reminder is owned through the mail it came from (``ReminderService._reminders_for_user``).
        db.query(func.count(models.ReminderMemory.id))
        .join(models.EmailRecord, models.ReminderMemory.source_email_id == models.EmailRecord.id)
        .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
        .filter(models.EmailAccount.user_id == user_id, models.ReminderMemory.updated_at > since)
        .scalar()
    )
    planner = (
        db.query(func.count(models.PlannerEvent.id))
        .filter(models.PlannerEvent.user_id == user_id, models.PlannerEvent.updated_at > since)
        .scalar()
    )
    return {"mail": mail or 0, "whatsapp": whatsapp or 0, "reminders": reminders or 0, "planner": planner or 0}


def planner_fingerprint(db: Session, user_id: int, *, now: datetime | None = None) -> str:
    """Hash what the owner's planners currently hold: synced calendar events and tasks, and Janus's local tasks.

    Synced events have no change timestamp and the sync upserts in place, so a timestamp cannot say "a
    meeting appeared" or "a deadline moved"; the content can. Events are taken from the start of today
    (UTC) on, so the hash changes at midnight when yesterday drops out, not hourly as time passes.
    """
    today = (now or datetime.now(UTC)).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    account_ids = db.query(models.PlannerAccount.id).filter(models.PlannerAccount.user_id == user_id).scalar_subquery()
    events = (
        db.query(
            models.CalendarEvent.external_id,
            models.CalendarEvent.summary,
            models.CalendarEvent.start_time,
            models.CalendarEvent.end_time,
            models.CalendarEvent.location,
        )
        .filter(models.CalendarEvent.planner_account_id.in_(account_ids), models.CalendarEvent.start_time >= today)
        .order_by(models.CalendarEvent.planner_account_id, models.CalendarEvent.external_id)
        .all()
    )
    synced_tasks = (
        db.query(
            models.TaskRecord.external_id,
            models.TaskRecord.title,
            models.TaskRecord.status,
            models.TaskRecord.due_date,
        )
        .filter(models.TaskRecord.planner_account_id.in_(account_ids))
        .order_by(models.TaskRecord.planner_account_id, models.TaskRecord.external_id)
        .all()
    )
    local_tasks = (
        db.query(
            models.PlannerTask.id,
            models.PlannerTask.title,
            models.PlannerTask.status,
            models.PlannerTask.due_at,
            models.PlannerTask.scheduled_for,
        )
        .filter(models.PlannerTask.user_id == user_id)
        .order_by(models.PlannerTask.id)
        .all()
    )
    digest = hashlib.sha256()
    for group in (events, synced_tasks, local_tasks):
        digest.update(repr([tuple(row) for row in group]).encode())
        digest.update(b"|")
    return digest.hexdigest()


def remember_planner_fingerprint(db: Session, user_id: int, fingerprint: str) -> None:
    """Store the fingerprint of a run that actually thought, for the next change gate."""
    preference = get_preference(db, user_id)
    if preference is None:
        preference = models.NotificationPreference(user_id=user_id)
        db.add(preference)
    preference.secretary_planner_fingerprint = fingerprint
    db.commit()


def _recent_runs_block(db: Session, definition: models.TriggerDefinition) -> str | None:
    runs = (
        db.query(models.RuntimeTrigger)
        .filter(
            models.RuntimeTrigger.definition_id == definition.id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.COMPLETED.value,
            models.RuntimeTrigger.output.is_not(None),
        )
        .order_by(models.RuntimeTrigger.completed_at.desc(), models.RuntimeTrigger.id.desc())
        .limit(HISTORY_RUNS * 24)  # skip notes and quiet runs are dropped below
        .all()
    )
    entries = []
    for run in reversed(runs):
        text = (run.output or "").strip()
        if not text or text.startswith(QUIET_OUTPUT) or is_skipped_output(text) or run.completed_at is None:
            continue
        entries.append(f"[{run.completed_at.strftime('%Y-%m-%d %H:%M')} UTC] {text[:HISTORY_CHARS]}")
    entries = entries[-HISTORY_RUNS:]
    if not entries:
        return None
    return "--- Your own recent findings (do not repeat them; build on them) ---\n" + (
        prompt_injection.prepared_context_block("secretary history", "\n\n".join(entries))
    )


def _engagement_block(db: Session, user_id: int, definition: models.TriggerDefinition) -> str | None:
    since = _naive_utc(datetime.now(UTC) - ENGAGEMENT_WINDOW)
    # A subquery, not a list: an hourly definition accumulates thousands of runs.
    run_ids = db.query(models.RuntimeTrigger.trigger_id).filter(models.RuntimeTrigger.definition_id == definition.id)
    conversations = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.user_id == user_id,
            models.Conversation.origin == models.ConversationOrigin.JANUS.value,
            models.Conversation.origin_trigger_id.in_(run_ids.scalar_subquery()),
            models.Conversation.created_at > since,
        )
        .order_by(models.Conversation.created_at.desc())
        .limit(ENGAGEMENT_LIMIT)
        .all()
    )
    if not conversations:
        return None
    lines = []
    for conversation in conversations:
        replied = (
            db.query(func.count(models.ConversationMessage.id))
            .filter(
                models.ConversationMessage.conversation_id == conversation.id,
                models.ConversationMessage.role == models.MessageRole.USER,
            )
            .scalar()
        )
        outcome = "replied" if replied else ("ignored (still unread)" if conversation.unread else "opened, no reply")
        lines.append(f'- {conversation.created_at.strftime("%Y-%m-%d")} "{conversation.title or ""}": {outcome}')
    return "--- What happened to the conversations you started (last 14 days) ---\n" + (
        prompt_injection.prepared_context_block("conversation outcomes", "\n".join(lines))
    )


def prepare(
    db: Session,
    user: models.User,
    definition: models.TriggerDefinition,
    *,
    now: datetime | None = None,
) -> SecretaryPlan:
    """Decide one run: skip it (no model call) or return the context to run with."""
    now = now or datetime.now(UTC)
    start_hour, end_hour = active_window(get_preference(db, user.id))
    if not within_active_hours(now, definition.timezone, start_hour, end_hour):
        return SecretaryPlan(skip_output=f"{SKIP_OUTSIDE_HOURS} ({start_hour:02d}:00-{end_hour:02d}:00).")
    previous = last_completed_run(db, definition)
    fingerprint = planner_fingerprint(db, user.id, now=now)
    planner_changed = get_preference_fingerprint(db, user.id) != fingerprint
    if previous is not None and previous.completed_at is not None:
        age = _naive_utc(now) - _naive_utc(previous.completed_at)
        quiet = not planner_changed and not any(changes_since(db, user.id, previous.completed_at).values())
        if age < REFRESH_AFTER and quiet:
            return SecretaryPlan(skip_output=SKIP_NOTHING_NEW)
    parts = [
        f"Current time (UTC): {now.astimezone(UTC).strftime('%Y-%m-%d %H:%M')}. The owner's waking hours are "
        f"{start_hour:02d}:00-{end_hour:02d}:00 ({definition.timezone or 'UTC'})."
    ]
    if previous is not None and previous.completed_at is not None:
        changed = changes_since(db, user.id, previous.completed_at)
        summary = ", ".join(f"{count} {name}" for name, count in changed.items())
        planner_note = "; calendar or tasks changed" if planner_changed else ""
        parts.append(f"Changed since your last run: {summary}{planner_note}.")
    for block in (_recent_runs_block(db, definition), _engagement_block(db, user.id, definition)):
        if block:
            parts.append(block)
    return SecretaryPlan(context="\n\n".join(parts), planner_fingerprint=fingerprint)


def whatsapp_alerts_since(db: Session, user_id: int, since: datetime) -> int:
    """Count the owner's outgoing WhatsApp messages an unattended run sent since ``since``."""
    return (
        db.query(func.count(models.WhatsAppMessage.id))
        .filter(
            models.WhatsAppMessage.user_id == user_id,
            models.WhatsAppMessage.direction == models.WhatsAppMessageDirection.OUTGOING,
            models.WhatsAppMessage.origin_trigger_id.is_not(None),
            models.WhatsAppMessage.created_at > _naive_utc(since),
        )
        .scalar()
        or 0
    )


def whatsapp_alerts_of_run(db: Session, user_id: int, trigger_id: str) -> int:
    """Count the alerts one run already sent."""
    return (
        db.query(func.count(models.WhatsAppMessage.id))
        .filter(
            models.WhatsAppMessage.user_id == user_id,
            models.WhatsAppMessage.origin_trigger_id == trigger_id,
        )
        .scalar()
        or 0
    )


__all__ = [
    "MAX_WHATSAPP_ALERTS_PER_DAY",
    "MAX_WHATSAPP_ALERTS_PER_RUN",
    "MAX_WHATSAPP_ALERT_CHARS",
    "QUIET_OUTPUT",
    "REFRESH_AFTER",
    "SKIP_NOTHING_NEW",
    "SKIP_OUTSIDE_HOURS",
    "SecretaryPlan",
    "WHATSAPP_ALERT_WINDOW",
    "active_window",
    "changes_since",
    "get_preference",
    "is_skipped_output",
    "get_preference_fingerprint",
    "last_completed_run",
    "planner_fingerprint",
    "remember_planner_fingerprint",
    "prepare",
    "whatsapp_alerts_of_run",
    "whatsapp_alerts_since",
    "within_active_hours",
]
