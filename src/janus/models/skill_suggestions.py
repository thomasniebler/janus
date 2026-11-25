"""Dismissed skill suggestions (JAN-407)."""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from janus import database
from janus.time_utils import utcnow


class SkillSuggestionDismissal(database.Base):
    """A repeated tool pattern the user said "not now" to; hidden until ``dismissed_at`` plus the cooldown."""

    __tablename__ = "skill_suggestion_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "pattern_key", name="uq_skill_suggestion_dismissals_key"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    #: Hash of the tool-name sequence (``skill_proposer.pattern_key``).
    pattern_key = Column(String(32), nullable=False)
    dismissed_at = Column(DateTime, default=utcnow, nullable=False)


__all__ = ["SkillSuggestionDismissal"]
