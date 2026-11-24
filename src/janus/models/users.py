"""User accounts and their per-user notification and dashboard preferences."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import BriefingEmailWindow, DashboardWidgetKey
from janus.time_utils import utcnow


class User(database.Base):
    """Database model for a system user."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Boolean, default=False, nullable=False)
    last_login = Column(DateTime, nullable=True)
    session_generation = Column(Integer, default=0, server_default="0", nullable=False)
    google_id = Column(String, nullable=True, unique=True, index=True)
    ui_version = Column(String, default="v1", server_default="v1", nullable=False)

    @property
    def google_linked(self) -> bool:
        """Returns True if a Google account is linked to this user."""
        return self.google_id is not None


class NotificationPreference(database.Base):
    """Database model for configurable notification behavior."""

    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    notify_important = Column(Boolean, default=True, server_default="1", nullable=False)
    notify_digest = Column(Boolean, default=True, server_default="1", nullable=False)
    daily_digest_enabled = Column(Boolean, default=False, server_default="0", nullable=False)
    daily_digest_hour = Column(Integer, default=16, server_default="16", nullable=False)
    daily_digest_minute = Column(Integer, default=0, server_default="0", nullable=False)
    digest_lookback_hours = Column(Integer, default=24, server_default="24", nullable=False)
    sender_allowlist = Column(Text, nullable=True)
    sender_blocklist = Column(Text, nullable=True)
    keyword_allowlist = Column(Text, nullable=True)
    keyword_blocklist = Column(Text, nullable=True)
    last_daily_digest_at = Column(DateTime, nullable=True)
    #: Proactive secretary (waking hours in the Secretary definition's timezone; WhatsApp interrupt channel).
    secretary_active_start_hour = Column(Integer, default=8, server_default="8", nullable=False)
    secretary_active_end_hour = Column(Integer, default=21, server_default="21", nullable=False)
    secretary_whatsapp_enabled = Column(Boolean, default=False, server_default="0", nullable=False)
    secretary_whatsapp_number = Column(String, nullable=True)
    #: Hash of the synced calendar and task content at the Secretary's last real run (change gate; not an API field).
    secretary_planner_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")


class DashboardWidgetPreference(database.Base):
    """Per-user dashboard widget visibility and collapse preferences."""

    __tablename__ = "dashboard_widget_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    widget_key = Column(
        Enum(
            DashboardWidgetKey,
            name="dashboardwidgetkey",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    is_visible = Column(Boolean, default=True, server_default="1", nullable=False)
    is_collapsed = Column(Boolean, default=False, server_default="0", nullable=False)
    prompt_override = Column(Text, nullable=True)
    briefing_email_window = Column(
        Enum(
            BriefingEmailWindow,
            name="briefingemailwindow",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False)

    user = relationship("User")

    __table_args__ = (UniqueConstraint("user_id", "widget_key", name="uq_dashboard_widget_preferences_user_widget"),)
