"""ORM models of the optional model-based prompt-injection classifier (JAN-289); re-exported from ``janus.models``.

Neither table ever holds the untrusted text itself: the cache keys on a
SHA-256 of it, and the settings row only counts tokens.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.types import JSON

from janus import database
from janus.time_utils import utcnow

#: Default monthly token budget for the classifier.
DEFAULT_INJECTION_CLASSIFIER_BUDGET = 200_000


class InjectionClassifierSettings(database.Base):
    """Per-user switch (off by default), monthly token budget, and this month's usage."""

    __tablename__ = "injection_classifier_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    monthly_token_budget = Column(
        Integer,
        nullable=False,
        default=DEFAULT_INJECTION_CLASSIFIER_BUDGET,
        server_default=str(DEFAULT_INJECTION_CLASSIFIER_BUDGET),
    )
    #: ``YYYY-MM`` the counters below belong to; a new month starts them at zero.
    usage_period = Column(String(7), nullable=True)
    tokens_used = Column(Integer, nullable=False, default=0, server_default="0")
    calls = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class InjectionClassification(database.Base):
    """One cached verdict, keyed by the SHA-256 of the classified text (never the text)."""

    __tablename__ = "injection_classifications"
    __table_args__ = (UniqueConstraint("user_id", "content_hash", name="uq_injection_classifications_user_hash"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    suspected = Column(Boolean, nullable=False, default=False)
    #: Labels as stored (``model_<rule id>``), empty when not suspected.
    labels = Column(JSON, nullable=False, default=list)
    model = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
