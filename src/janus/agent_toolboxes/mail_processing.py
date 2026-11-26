"""Tools Janus uses to process one incoming email during a ``mail.received`` trigger run (JAN-232).

These replace the one-shot JSON extractors (classification, reminders, tracked
topics, planner suggestions) for the event-driven mail path: instead of the
code parsing a model's JSON answer, Janus decides what the email means and
records it by calling tools, with the same persistence and dedupe rules the
extractors used. Every tool is owner-scoped: an email outside the user's own
accounts reads as "not found".

All writes stay Janus-local (priority/reasoning on the stored email,
reminders, topics, *pending* planner suggestions), with one exception that
mirrors the synchronous classifier: classifying a still-provisional email as
DIGEST marks it read on the mail server. Nothing here sends anything.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import event_logger, models, notifications
from janus.agent_toolboxes.shared import ToolContext, parse_priority
from janus.reminder_service import ReminderService
from janus.services.email_service import EmailService
from janus.services.local_planner_service import LocalPlannerService
from janus.services.topic_service import TopicService

_CONFIDENCE_BY_NAME = {level.value.lower(): level for level in models.ConfidenceLevel}


def _parse_confidence(value: str | None) -> models.ConfidenceLevel:
    return _CONFIDENCE_BY_NAME.get((value or "medium").strip().lower(), models.ConfidenceLevel.MEDIUM)


def build_mail_processing_tools(context: ToolContext) -> list[BaseTool]:
    """Build the owner-scoped mail-processing toolbox."""
    db = context.db
    user = context.user
    email_service = EmailService(db)

    def _owned_email(email_id: int) -> models.EmailRecord:
        record = email_service.get_email_for_user(email_id, user.id)
        if record is None:
            raise ValueError(f"email {email_id} not found")
        return record

    @tool("set_email_priority")
    def set_email_priority(email_id: int, priority: str, reasoning: str, confidence: str = "medium") -> dict[str, Any]:
        """Classify a stored email as IMPORTANT, DIGEST, IGNORE, or SPAM and record why.

        Use this once per processed email. IMPORTANT emails trigger the user's
        immediate notification (subject to their notification filters). A new
        email classified as DIGEST is marked read on the mail server.
        """
        # Imported lazily: the processor and the email worker import the
        # runtime that builds this toolbox.
        from janus import imap_service
        from janus.processor import PENDING_TRIGGER_PROCESSING_REASON

        record = _owned_email(email_id)
        was_provisional = record.reasoning == PENDING_TRIGGER_PROCESSING_REASON
        parsed = parse_priority(priority)
        cleaned_reasoning = reasoning.strip()
        if not cleaned_reasoning:
            raise ValueError("reasoning must explain the classification")
        record.priority = parsed
        record.reasoning = cleaned_reasoning[:1000]
        record.classification_confidence = _parse_confidence(confidence)
        notified_now = False
        if parsed == models.Priority.IMPORTANT and not record.notified:
            from janus.workers.email import _should_notify_important_email

            preference = (
                db.query(models.NotificationPreference).filter(models.NotificationPreference.user_id == user.id).first()
            )
            if _should_notify_important_email(record, preference):
                notifications.NotificationService.notify(record)
                record.notified = True
                notified_now = True
        db.commit()
        # The IMAP worker leaves a provisional DIGEST unread until Janus decides
        # (JAN-232); once it is decided, do what the synchronous path does.
        marked_read = False
        if parsed == models.Priority.DIGEST and was_provisional and record.imap_uid is not None:
            marked_read = imap_service.mark_message_read(record.account, record.imap_uid)
        event_logger.log(
            models.EventType.INFO,
            "MailProcessingTools",
            f"Email {record.message_id} classified as {parsed.value} by Janus.",
            {"email_id": record.id, "priority": parsed.value},
            user_id=user.id,
        )
        return {"email_id": record.id, "priority": parsed.value, "notified": notified_now, "marked_read": marked_read}

    @tool("create_reminder")
    def create_reminder(
        email_id: int,
        kind: str,
        title: str,
        due_at: str,
        prepare_for_at: str | None = None,
        notes: str | None = None,
        confidence: str = "medium",
    ) -> dict[str, Any]:
        """Remember a dated obligation from an email.

        ``kind`` is ``event`` (something happens at ``due_at``) or
        ``preparation`` (something must be ready by ``due_at``; set
        ``prepare_for_at`` to when to start). Timestamps are ISO-8601.
        Re-recording the same reminder updates it instead of duplicating it.
        """
        record = _owned_email(email_id)
        reminders = ReminderService(db).persist_email_reminders(
            record,
            [
                {
                    "kind": kind.strip().lower(),
                    "title": title.strip(),
                    "due_at": due_at,
                    "prepare_for_at": prepare_for_at,
                    "notes": notes,
                    "confidence": confidence,
                }
            ],
        )
        db.commit()
        if not reminders:
            raise ValueError("reminder not recorded: kind must be event or preparation, with a title and valid due_at")
        return {"reminder_ids": [reminder.id for reminder in reminders], "title": reminders[0].title}

    @tool("track_email_topic")
    def track_email_topic(
        email_id: int,
        title: str,
        summary: str | None = None,
        status_hint: str | None = None,
        confidence: str = "medium",
        dedupe_hint: str | None = None,
    ) -> dict[str, Any]:
        """Attach an email to a recurring tracked topic (an ongoing project, thread, or obligation).

        Reuse an existing topic's title or ``dedupe_hint`` to add evidence to it;
        a brand-new topic starts as a candidate until further mail corroborates it.
        """
        record = _owned_email(email_id)
        topics = TopicService(db).record_email_topic(
            record,
            user_id=user.id,
            title=title,
            summary=summary,
            status_hint=status_hint,
            confidence=confidence,
            dedupe_hint=dedupe_hint,
        )
        db.commit()
        return {
            "topics": [{"id": topic.id, "title": topic.title, "status": topic.status.value} for topic in topics],
            "recorded": bool(topics),
        }

    @tool("suggest_planner_item")
    def suggest_planner_item(
        email_id: int,
        suggestion_type: str,
        title: str,
        description: str | None = None,
        due_at: str | None = None,
        scheduled_for: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        location: str | None = None,
        all_day: bool = False,
        priority: str | None = None,
        reason: str | None = None,
        confidence: str = "medium",
        dedupe_hint: str | None = None,
        target_task_id: int | None = None,
        target_event_id: int | None = None,
    ) -> dict[str, Any]:
        """Propose a planner change caused by an email; the user accepts or dismisses it.

        ``suggestion_type`` is ``task``, ``event``, ``task_update``, or
        ``event_update`` (updates need ``target_task_id`` / ``target_event_id``).
        This never changes the planner directly. A pending suggestion with the
        same ``dedupe_hint`` or title is updated instead of duplicated; one the
        user already dismissed or applied is returned unchanged (its
        ``status`` says so) and is not proposed again.
        """
        record = _owned_email(email_id)
        rows = LocalPlannerService(db).record_email_suggestions(
            record,
            [
                {
                    "suggestion_type": suggestion_type,
                    "title": title,
                    "description": description,
                    "due_at": due_at,
                    "scheduled_for": scheduled_for,
                    "start_time": start_time,
                    "end_time": end_time,
                    "location": location,
                    "all_day": all_day,
                    "priority": priority,
                    "reason": reason,
                    "confidence": confidence,
                    "dedupe_hint": dedupe_hint,
                    "target_task_id": target_task_id,
                    "target_event_id": target_event_id,
                }
            ],
            user_id=user.id,
        )
        if not rows:
            raise ValueError(
                "suggestion not recorded: suggestion_type must be task, event, task_update, or event_update"
            )
        return rows[0]

    return [set_email_priority, create_reminder, track_email_topic, suggest_planner_item]


__all__ = ["build_mail_processing_tools"]
