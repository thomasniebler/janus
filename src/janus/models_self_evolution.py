"""ORM models of the autonomous self-evolution loop (JAN-320); re-exported from ``janus.models``.

``SelfEvolutionSettings`` is one row per user: the opt-in switch (off by
default), the Claude Code Routine's API-trigger id (``trig_...``) with its
Fernet-encrypted per-routine bearer token, the GitHub repository the loop works on, and
the production base URL its self-checks probe. ``SelfEvolutionAction`` is the
loop's own audit trail: one row per attempted action (issue filed, job
started, PR merged, self-check, revert), which is also what the daily caps are
counted from. See ``services/self_evolution_service.py``.
"""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from janus import database
from janus.time_utils import utcnow


class SelfEvolutionSettings(database.Base):
    """Per-user configuration of the self-evolution loop (JAN-320)."""

    __tablename__ = "self_evolution_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    #: The opt-in switch; nothing in the loop runs while it is off.
    enabled = Column(Boolean, nullable=False, default=False, server_default="0")
    #: Claude Code Routine API-trigger id (``trig_...``); the fire URL is built from it in code.
    routine_trigger_id = Column(String, nullable=True)
    #: The routine's own bearer token (``sk-ant-oat01-...``), Fernet-encrypted at rest.
    routine_token = Column(String, nullable=True)
    #: ``owner/name``; ``None`` falls back to ``Settings.github_default_repo``.
    github_repo = Column(String, nullable=True)
    #: https base URL of the production instance the self-checks probe.
    production_base_url = Column(String, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SelfEvolutionAction(database.Base):
    """One attempted self-evolution action; the audit trail and the source of the daily caps."""

    __tablename__ = "self_evolution_actions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: ``issue_filed`` | ``job_started`` | ``feature_merged`` | ``release_merged`` |
    #: ``self_check`` | ``revert_opened`` (see ``self_evolution_service.Action``).
    action = Column(String, nullable=False, index=True)
    #: ``pending`` | ``ok`` | ``failed`` | ``refused`` | ``passed`` (self-checks).
    status = Column(String, nullable=False)
    #: What the action was about: a Linear identifier, ``#<pr>``, a check id.
    subject = Column(String, nullable=True, index=True)
    #: Short human-readable outcome (refusal reason, error, URL).
    detail = Column(Text, nullable=True)
    data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
