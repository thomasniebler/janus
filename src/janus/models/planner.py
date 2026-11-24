"""Planner: calendar/task connector accounts, synced tasks and events, suggestions and links."""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import (
    CalendarEventSource,
    ConfidenceLevel,
    PlannerItemSource,
    PlannerLinkSource,
    PlannerSuggestionStatus,
    PlannerSuggestionType,
    Provider,
    TaskStatus,
    TaskUrgency,
)
from janus.time_utils import utcnow


class PlannerAccount(database.Base):
    """Database model for a planner integration account."""

    __tablename__ = "planner_accounts"
    # Names are unique per owner, not globally: every user's Google connection
    # creates accounts named "Google Calendar"/"Google Tasks" (JAN-318).
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_planner_accounts_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, index=True)
    provider = Column(Enum(Provider))
    api_url = Column(String, nullable=True)  # e.g., for Jira base URL
    token = Column(String)  # Access token, PAT, or serialized OAuth credentials
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_error = Column(String, nullable=True)
    # Google Calendar only: calendar IDs to sync (e.g. shared/secondary calendars).
    # None/empty means the account's "primary" calendar.
    selected_calendar_ids = Column(JSON, nullable=True)


class TaskRecord(database.Base):
    """Database model for a task from a planner."""

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    planner_account_id = Column(Integer, ForeignKey("planner_accounts.id"))
    external_id = Column(String, index=True)
    title = Column(String)
    status = Column(Enum(TaskStatus), default=TaskStatus.TODO)
    due_date = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow)


class CalendarEvent(database.Base):
    """Database model for a calendar event from a planner."""

    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    planner_account_id = Column(Integer, ForeignKey("planner_accounts.id"))
    external_id = Column(String, index=True)
    summary = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    location = Column(String, nullable=True)
    description = Column(String, nullable=True)
    source_type = Column(Enum(CalendarEventSource), default=CalendarEventSource.PLANNER, nullable=False)


class PlannerTask(database.Base):
    """Janus-owned writable planner task."""

    __tablename__ = "planner_tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(TaskStatus), default=TaskStatus.TODO, nullable=False, index=True)
    parent_task_id = Column(Integer, ForeignKey("planner_tasks.id"), nullable=True, index=True)
    urgency = Column(
        Enum(TaskUrgency, native_enum=False, create_constraint=True, validate_strings=True),
        default=TaskUrgency.NORMAL,
        nullable=False,
    )
    sort_order = Column(Integer, nullable=True)
    priority = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    source = Column(Enum(PlannerItemSource), default=PlannerItemSource.MANUAL, nullable=False)
    confidence = Column(Enum(ConfidenceLevel), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    parent = relationship("PlannerTask", remote_side=[id], back_populates="children")
    children = relationship("PlannerTask", back_populates="parent")


class PlannerEvent(database.Base):
    """Janus-owned writable single-instance planner event."""

    __tablename__ = "planner_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=False)
    location = Column(String, nullable=True)
    all_day = Column(Boolean, default=False, server_default="0", nullable=False)
    source = Column(Enum(PlannerItemSource), default=PlannerItemSource.MANUAL, nullable=False)
    confidence = Column(Enum(ConfidenceLevel), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")


class PlannerSuggestion(database.Base):
    """Reviewable planner suggestion derived from email or digest analysis."""

    __tablename__ = "planner_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    suggestion_type = Column(Enum(PlannerSuggestionType), nullable=False, index=True)
    status = Column(
        Enum(PlannerSuggestionStatus),
        default=PlannerSuggestionStatus.PENDING,
        nullable=False,
        index=True,
    )
    source = Column(Enum(PlannerItemSource), default=PlannerItemSource.EMAIL_AGENT, nullable=False)
    confidence = Column(Enum(ConfidenceLevel), nullable=True)
    reason = Column(Text, nullable=True)
    dedupe_key = Column(String, nullable=True, index=True)
    title = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    task_status = Column(Enum(TaskStatus), nullable=True)
    priority = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    location = Column(String, nullable=True)
    all_day = Column(Boolean, default=False, server_default="0", nullable=False)
    source_email_id = Column(Integer, ForeignKey("emails.id"), nullable=True, index=True)
    source_topic_id = Column(Integer, ForeignKey("tracked_topics.id"), nullable=True, index=True)
    target_task_id = Column(Integer, ForeignKey("planner_tasks.id"), nullable=True, index=True)
    target_event_id = Column(Integer, ForeignKey("planner_events.id"), nullable=True, index=True)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    source_email = relationship("EmailRecord")
    source_topic = relationship("TrackedTopic")
    target_task = relationship("PlannerTask")
    target_event = relationship("PlannerEvent")


class PlannerLink(database.Base):
    """Evidence mapping between a local planner item and a source record."""

    __tablename__ = "planner_links"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    planner_task_id = Column(Integer, ForeignKey("planner_tasks.id"), nullable=True, index=True)
    planner_event_id = Column(Integer, ForeignKey("planner_events.id"), nullable=True, index=True)
    source_type = Column(Enum(PlannerLinkSource), nullable=False, index=True)
    source_id = Column(Integer, nullable=True)
    source_detail = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    user = relationship("User")
    planner_task = relationship("PlannerTask")
    planner_event = relationship("PlannerEvent")
