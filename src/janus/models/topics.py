"""Structured topic tracking: topics, their evidence, suggestions and per-user preferences."""

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
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import (
    ConfidenceLevel,
    TopicEvidenceRole,
    TopicEvidenceSource,
    TopicExtractionScope,
    TopicOrigin,
    TopicStatus,
    TopicSuggestionStatus,
    TopicSuggestionType,
)
from janus.time_utils import utcnow


class TrackedTopic(database.Base):
    """Durable user-scoped topic derived from email and related Janus context."""

    __tablename__ = "tracked_topics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    slug = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    marker_keywords = Column(Text, nullable=True)
    status = Column(Enum(TopicStatus), default=TopicStatus.ACTIVE, nullable=False, index=True)
    pinned = Column(Boolean, default=False, server_default="0", nullable=False)
    user_confirmed = Column(Boolean, default=False, server_default="0", nullable=False)
    origin = Column(Enum(TopicOrigin), default=TopicOrigin.AUTO, nullable=False)
    confidence = Column(Enum(ConfidenceLevel), nullable=True)
    first_seen_at = Column(DateTime, default=utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow, nullable=False)
    snoozed_until = Column(DateTime, nullable=True)
    last_briefed_at = Column(DateTime, nullable=True)
    last_digested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")

    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_tracked_topics_user_slug"),)


class TopicEvidence(database.Base):
    """Structured evidence showing why a topic exists."""

    __tablename__ = "topic_evidence"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("tracked_topics.id"), nullable=False, index=True)
    source_type = Column(Enum(TopicEvidenceSource), nullable=False)
    source_id = Column(Integer, nullable=True)
    excerpt = Column(Text, nullable=True)
    evidence_role = Column(Enum(TopicEvidenceRole), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    topic = relationship("TrackedTopic")


class TopicSuggestion(database.Base):
    """Reviewable suggestion related to tracked-topic management."""

    __tablename__ = "topic_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("tracked_topics.id"), nullable=True, index=True)
    suggestion_type = Column(Enum(TopicSuggestionType), nullable=False)
    status = Column(Enum(TopicSuggestionStatus), default=TopicSuggestionStatus.PENDING, nullable=False, index=True)
    payload_json = Column(Text, nullable=True)
    reason = Column(String, nullable=False)
    confidence = Column(Enum(ConfidenceLevel), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    topic = relationship("TrackedTopic")


class TopicTrackingPreference(database.Base):
    """Per-user overrides for structured topic tracking behavior."""

    __tablename__ = "topic_tracking_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    extraction_scope = Column(
        Enum(TopicExtractionScope),
        default=TopicExtractionScope.IMPORTANT_AND_DIGEST,
        nullable=False,
    )
    minimum_confidence = Column(Enum(ConfidenceLevel), default=ConfidenceLevel.MEDIUM, nullable=False)
    min_confirmation_mentions = Column(Integer, default=2, server_default="2", nullable=False)
    max_active_topics = Column(Integer, default=10, server_default="10", nullable=False)
    reminder_linking_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    digest_grouping_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    dashboard_widget_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    chat_context_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    suggestion_generation_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    allowlist_keywords = Column(Text, nullable=True)
    blocklist_keywords = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
