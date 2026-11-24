"""Status event log and grouped errors."""

import enum

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)

from janus import database
from janus.time_utils import utcnow


class EventType(str, enum.Enum):
    """Enumeration for event logging types."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class Event(database.Base):
    """An event-log row; ``user_id`` is the user it concerns (``None``: a system row, admins only; JAN-272)."""

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=utcnow)
    event_type = Column(Enum(EventType), default=EventType.INFO)
    source = Column(String)  # e.g., "IMAPMonitor", "PlannerService", "LLMService"
    message = Column(String)
    details = Column(String, nullable=True)  # JSON string of additional details
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)


class ErrorGroup(database.Base):
    """Database model for grouped error events."""

    __tablename__ = "error_groups"

    id = Column(Integer, primary_key=True, index=True)
    # Uniqueness lives in ``uq_error_groups_fingerprint`` below; the index is a plain lookup index (JAN-338).
    fingerprint = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)
    message_template = Column(String, nullable=False)
    first_seen = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)
    last_seen = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)
    occurrence_count = Column(Integer, default=1, server_default="1", nullable=False)
    sample_details = Column(String, nullable=True)  # JSON string of sample error details
    last_compacted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("fingerprint", name="uq_error_groups_fingerprint"),)
