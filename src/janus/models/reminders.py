"""Reminder memory extracted from mail and chat."""

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
)

from janus import database
from janus.models.enums import ReminderKind, ReminderStatus
from janus.time_utils import utcnow


class ReminderMemory(database.Base):
    """Durable Janus reminder memory derived from emails and planner context."""

    __tablename__ = "reminder_memory"

    id = Column(Integer, primary_key=True, index=True)
    source_email_id = Column(Integer, ForeignKey("emails.id"), nullable=True)
    synthetic_calendar_event_id = Column(Integer, ForeignKey("calendar_events.id"), nullable=True)
    kind = Column(Enum(ReminderKind), nullable=False)
    status = Column(Enum(ReminderStatus), default=ReminderStatus.ACTIVE, nullable=False)
    title = Column(String, nullable=False)
    notes = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=False)
    prepare_for_at = Column(DateTime, nullable=True)
    dedupe_key = Column(String, unique=True, nullable=False, index=True)
    confidence = Column(String, nullable=True)
    last_seen_at = Column(DateTime, default=utcnow, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
