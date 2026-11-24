"""Finds what the knowledge sources are built from (JAN-242).

For each source type, a ``_stale_*`` function returns the owner rows that
have no source yet or changed since their source was built: mail by missing
source, chats/WhatsApp/planner tasks by ``updated_at``, and calendar events by
text hash (calendar sync edits them in place without a timestamp). ``orphans``
returns sources whose owner is gone (matched on owner id and user, since SQLite
can reuse a deleted row's id), plus mail since filed SPAM or IGNORE.
Mail still waiting for its ``mail.received`` trigger run carries a provisional
priority, so neither it nor its staged attachments are ingested until Janus has
classified it.
``knowledge_sources`` processes both.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from janus import models
from janus.services import knowledge_chunking

SourceType = models.KnowledgeSourceType

_SKIPPED_PRIORITIES = (models.Priority.SPAM, models.Priority.IGNORE)


def ingestible_mail():
    """SQL condition: mail with a final classification that is not SPAM/IGNORE.

    Mail handed to ``mail.received`` triggers is stored as a provisional DIGEST
    until ``set_email_priority`` runs; ingesting it earlier could send mail
    that ends up SPAM or IGNORE to a cloud embedding provider.
    """
    from janus.processor import PENDING_TRIGGER_PROCESSING_REASON  # processor imports this module

    email = models.EmailRecord
    return and_(
        email.priority.not_in(_SKIPPED_PRIORITIES),
        or_(email.reasoning.is_(None), email.reasoning != PENDING_TRIGGER_PROCESSING_REASON),
    )


def staged_sources(db: Session, limit: int) -> list[models.KnowledgeSource]:
    """Pending sources holding bytes, except attachments of mail that is not (yet) ingestible."""
    source, email = models.KnowledgeSource, models.EmailRecord
    return (
        db.query(source)
        .outerjoin(email, and_(source.source_type == SourceType.EMAIL_ATTACHMENT.value, email.id == source.owner_id))
        .filter(
            source.status == models.KnowledgeSourceStatus.PENDING.value,
            source.staged_content.is_not(None),
            or_(source.source_type != SourceType.EMAIL_ATTACHMENT.value, ingestible_mail()),
        )
        .order_by(source.id)
        .limit(limit)
        .all()
    )


def content_hash(text: str) -> str:
    """SHA-256 of a source's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerText:
    """The text a source is built from, plus what identifies and dates it."""

    owner_id: int
    user_id: int
    title: str
    text: str
    blocks: Callable[[str], list[str]]
    updated_at: datetime | None


def _messages(text: str) -> list[str]:
    """Chat/WhatsApp blocks: one per message (messages are separated by blank lines)."""
    return [block for block in text.split("\n\n") if block.strip()]


# --- Discovery -----------------------------------------------------------------


def _source_join(source_type: SourceType, owner_id_column, user_id_column):
    source = models.KnowledgeSource
    return and_(
        source.user_id == user_id_column,
        source.source_type == source_type.value,
        source.owner_id == owner_id_column,
        source.part == 0,
    )


def _stale_emails(db: Session, limit: int) -> list[OwnerText]:
    email, account, source = models.EmailRecord, models.EmailAccount, models.KnowledgeSource
    rows = (
        db.query(email, account.user_id)
        .join(account, account.id == email.account_id)
        .outerjoin(source, _source_join(SourceType.EMAIL, email.id, account.user_id))
        .filter(account.user_id.is_not(None), source.id.is_(None), ingestible_mail())
        .order_by(email.id)
        .limit(limit)
        .all()
    )
    return [
        OwnerText(
            owner_id=row.id,
            user_id=user_id,
            title=row.subject or "(no subject)",
            text=(
                f"Subject: {row.subject or ''}\nFrom: {row.sender or ''}\n"
                f"Date: {f'{row.received_at:%Y-%m-%d %H:%M}' if row.received_at else 'unknown'}\n\n"
                f"{row.body or row.snippet or ''}"
            ),
            blocks=knowledge_chunking.email_blocks,
            updated_at=row.received_at,
        )
        for row, user_id in rows
    ]


def _stale_by_updated_at(db: Session, source_type: SourceType, owner_model, limit: int):
    """Owner rows with no source, or updated since their source was built."""
    source = models.KnowledgeSource
    return (
        db.query(owner_model)
        .outerjoin(source, _source_join(source_type, owner_model.id, owner_model.user_id))
        .filter(
            (source.id.is_(None))
            | (source.source_updated_at.is_(None))
            | (source.source_updated_at != owner_model.updated_at)
        )
        .order_by(owner_model.id)
        .limit(limit)
        .all()
    )


def _stale_conversations(db: Session, limit: int) -> list[OwnerText]:
    result = []
    for conversation in _stale_by_updated_at(db, SourceType.CONVERSATION, models.Conversation, limit):
        messages = (
            db.query(models.ConversationMessage)
            .filter(models.ConversationMessage.conversation_id == conversation.id)
            .order_by(models.ConversationMessage.id)
            .all()
        )
        text = "\n\n".join(f"{message.role.value}: {message.content.strip()}" for message in messages)
        result.append(
            OwnerText(
                owner_id=conversation.id,
                user_id=conversation.user_id,
                title=conversation.title or "Chat",
                text=text,
                blocks=_messages,
                updated_at=conversation.updated_at,
            )
        )
    return result


def _stale_whatsapp(db: Session, limit: int) -> list[OwnerText]:
    result = []
    for chat in _stale_by_updated_at(db, SourceType.WHATSAPP_CONVERSATION, models.WhatsAppConversation, limit):
        messages = (
            db.query(models.WhatsAppMessage)
            .filter(models.WhatsAppMessage.whatsapp_conversation_id == chat.id)
            .order_by(models.WhatsAppMessage.provider_timestamp, models.WhatsAppMessage.id)
            .all()
        )
        text = "\n\n".join(
            f"[{m.provider_timestamp:%Y-%m-%d %H:%M}] {m.sender_display_name or m.sender_identifier or 'unknown'}: "
            f"{(m.body or m.snippet or '').strip()}"
            for m in messages
        )
        result.append(OwnerText(chat.id, chat.user_id, chat.title, text, _messages, chat.updated_at))
    return result


def _stale_planner_tasks(db: Session, limit: int) -> list[OwnerText]:
    return [
        OwnerText(
            task.id,
            task.user_id,
            task.title,
            "\n".join(
                part
                for part in (
                    f"Task: {task.title}",
                    f"Status: {task.status.value}",
                    f"Due: {task.due_at:%Y-%m-%d}" if task.due_at else "",
                    task.description or "",
                )
                if part
            ),
            knowledge_chunking.document_blocks,
            task.updated_at,
        )
        for task in _stale_by_updated_at(db, SourceType.PLANNER_TASK, models.PlannerTask, limit)
    ]


def _calendar_text(event: models.CalendarEvent) -> str:
    when = f"{event.start_time:%Y-%m-%d %H:%M}" if event.start_time else "unscheduled"
    if event.start_time and event.end_time:
        when += f" to {event.end_time:%Y-%m-%d %H:%M}"
    lines = [f"Event: {event.summary or '(untitled)'}", f"When: {when}"]
    if event.location:
        lines.append(f"Where: {event.location}")
    if event.description:
        lines.append(event.description)
    return "\n".join(lines)


def _stale_calendar_events(db: Session, limit: int) -> list[OwnerText]:
    """Calendar sync edits events in place without a timestamp, so these compare text hashes."""
    event, account, source = models.CalendarEvent, models.PlannerAccount, models.KnowledgeSource
    rows = (
        db.query(event, account.user_id, source.content_hash)
        .join(account, account.id == event.planner_account_id)
        .outerjoin(source, _source_join(SourceType.CALENDAR_EVENT, event.id, account.user_id))
        .filter(account.user_id.is_not(None))
        .order_by(event.id)
        .all()
    )
    result = []
    for row, user_id, stored_hash in rows:
        text = _calendar_text(row)
        if stored_hash != content_hash(text):
            result.append(
                OwnerText(
                    owner_id=row.id,
                    user_id=user_id,
                    title=row.summary or "(untitled event)",
                    text=text,
                    blocks=knowledge_chunking.document_blocks,
                    updated_at=None,
                )
            )
            if len(result) >= limit:
                break
    return result


DISCOVERERS: tuple[tuple[SourceType, Callable[[Session, int], list[OwnerText]]], ...] = (
    (SourceType.EMAIL, _stale_emails),
    (SourceType.CONVERSATION, _stale_conversations),
    (SourceType.WHATSAPP_CONVERSATION, _stale_whatsapp),
    (SourceType.PLANNER_TASK, _stale_planner_tasks),
    (SourceType.CALENDAR_EVENT, _stale_calendar_events),
)


# --- Cleanup -------------------------------------------------------------------


def orphans(db: Session) -> list[models.KnowledgeSource]:
    """Sources whose owner row is gone, plus mail (and its attachments) now filed SPAM/IGNORE."""
    source = models.KnowledgeSource
    email = models.EmailRecord
    calendar, account = models.CalendarEvent, models.PlannerAccount
    # An owner matches on id *and* user: SQLite reuses the highest primary key after a delete, so
    # an id alone can point at a newer row of another user and keep a dead source searchable.
    owners = {
        SourceType.CONVERSATION: select(models.Conversation.id, models.Conversation.user_id),
        SourceType.WHATSAPP_CONVERSATION: select(models.WhatsAppConversation.id, models.WhatsAppConversation.user_id),
        SourceType.PLANNER_TASK: select(models.PlannerTask.id, models.PlannerTask.user_id),
        SourceType.CALENDAR_EVENT: select(calendar.id, account.user_id).join(
            account, account.id == calendar.planner_account_id
        ),
    }
    orphans: list[models.KnowledgeSource] = []
    for source_type, owner_rows in owners.items():
        owner = owner_rows.subquery()
        orphans += (
            db.query(source)
            .outerjoin(owner, and_(owner.c.id == source.owner_id, owner.c.user_id == source.user_id))
            .filter(source.source_type == source_type.value, owner.c.id.is_(None))
            .all()
        )
    orphans += (
        db.query(source)
        .outerjoin(email, email.id == source.owner_id)
        .filter(
            source.source_type.in_([SourceType.EMAIL.value, SourceType.EMAIL_ATTACHMENT.value]),
            (email.id.is_(None)) | (email.priority.in_(_SKIPPED_PRIORITIES)),
        )
        .all()
    )
    return orphans
