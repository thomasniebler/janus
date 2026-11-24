"""Plan-driven runtime (JAN-157): trigger ingress, trigger definitions and tool policy.

Runtime triggers, saved trigger definitions and the per-user unattended tool policy.
This is the model new work should target.
"""

import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from janus import database
from janus.time_utils import utcnow


class RuntimeTriggerKind(str, enum.Enum):
    """Persisted ingress category for plan-runtime triggers."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"


class RuntimeTriggerStatus(str, enum.Enum):
    """Persisted lifecycle state for one runtime trigger."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"  # parked on its delegated tasks, not claimable (JAN-207)
    COMPLETED = "completed"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


class RuntimeTrigger(database.Base):
    """Durable trigger envelope and cross-process execution claim."""

    __tablename__ = "runtime_triggers"
    __table_args__ = (UniqueConstraint("task_id", "idempotency_key", name="uq_runtime_trigger_task_key"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    trigger_id = Column(String, unique=True, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    task_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    source = Column(String, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String, nullable=False, default=RuntimeTriggerStatus.QUEUED.value, index=True)
    claim_token = Column(String, nullable=True, index=True)
    claimed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at = Column(DateTime, nullable=True, index=True)
    #: The saved definition this run came from (``None`` for ad-hoc runs); ``definition_name`` snapshots its
    #: name at enqueue time and outlives its deletion, so the run keeps its definition provenance (JAN-204).
    definition_id = Column(Integer, ForeignKey("trigger_definitions.id", ondelete="SET NULL"), index=True)  # nullable
    definition_name = Column(String, nullable=True)
    #: The model's final answer for a completed run, without the audit header
    #: that the plan-state result carries. Built-in definitions (briefing,
    #: digest) deliver this text to their destination.
    output = Column(Text, nullable=True)
    #: Loop-guard lineage (JAN-274): the unattended run that scheduled this one, and the chain depth.
    parent_trigger_id = Column(String, nullable=True, index=True)
    chain_depth = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class TriggerDefinitionKind(str, enum.Enum):
    """How a saved trigger definition is activated."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    EVENT = "event"


class TriggerDefinitionOrigin(str, enum.Enum):
    """Who authored a trigger definition; drives the unattended confirmation policy."""

    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"


class TriggerProposalStatus(str, enum.Enum):
    """Review state of a trigger definition Janus drafted from chat (JAN-235).

    ``NULL`` on every ``user``/``system`` definition and on Janus's one-time
    follow-ups (JAN-257). See ``janus.trigger_proposal_service``.
    """

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class TriggerDefinition(database.Base):
    """A saved, reusable automation: objective + pre-selected tools + activation (JAN-228).

    Every run of a definition is an ordinary ``RuntimeTrigger`` row that points
    back here via ``RuntimeTrigger.definition_id``. Runs always speak as the
    owner's Janus character; the definition only contributes *what* to do
    (``objective``), *with which tools* (``tool_ids``) and *when* (manual,
    a recurring ``schedule``, or an ``event_type``).
    """

    __tablename__ = "trigger_definitions"
    __table_args__ = (
        UniqueConstraint("user_id", "builtin_key", name="uq_trigger_definitions_user_builtin"),
        Index("ix_trigger_definitions_user_proposal_status", "user_id", "proposal_status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: Stable key for Janus-seeded definitions (``builtin.briefing`` ...);
    #: ``None`` for user- or agent-authored ones.
    builtin_key = Column(String, nullable=True)
    name = Column(String, nullable=False)
    objective = Column(Text, nullable=False)
    tool_ids = Column(JSON, nullable=False, default=list)
    kind = Column(String, nullable=False, default=TriggerDefinitionKind.MANUAL.value)
    #: ``{"type": "daily", "time": "07:30", "days": [0, 1, 2, 3, 4]}`` or
    #: ``{"type": "interval", "minutes": 60}``; ``None`` unless kind is schedule.
    schedule = Column(JSON, nullable=True)
    timezone = Column(String, nullable=False, default="UTC", server_default="UTC")
    event_type = Column(String, nullable=True, index=True)
    # Event definitions only (JAN-363): payload conditions an event must pass, and a cap on
    # event runs per rolling hour; see janus.trigger_event_filters.
    event_filter = Column(JSON, nullable=True)
    max_runs_per_hour = Column(Integer, nullable=True)
    # Events the rate limit dropped (JAN-363), shown on the definition card.
    rate_limited_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_rate_limited_at = Column(DateTime, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    origin = Column(String, nullable=False, default=TriggerDefinitionOrigin.USER.value)
    #: Optional model override for this definition's runs (JAN-233); ``None``
    #: means the default model. An unusable choice fails the run loudly.
    llm_provider = Column(String, nullable=True)
    llm_model = Column(String, nullable=True)
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    # --- Agent-proposed automations (JAN-235); all NULL unless Janus drafted it ---
    #: ``TriggerProposalStatus`` value; only ``approved`` proposals may run.
    proposal_status = Column(String, nullable=True)
    #: Janus's plain-language reason; untrusted text, always rendered escaped.
    proposal_rationale = Column(Text, nullable=True)
    proposed_at = Column(DateTime, nullable=True)
    #: ``{"conversation_id": ..., "untrusted_context": ["mail", ...]}``.
    proposal_source = Column(JSON, nullable=True)
    #: The approved definition this proposal would replace once approved.
    supersedes_id = Column(Integer, ForeignKey("trigger_definitions.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: SHA-256 over the content the approval covers; re-checked before every run.
    approval_fingerprint = Column(String(64), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)


class ToolPolicy(database.Base):
    """Per-user overrides of a capability's Unattended and In chat switches (JAN-229, JAN-255, JAN-306).

    ``NULL`` columns and absent rows mean the default; see ``janus.tool_policy`` for the rules.
    """

    __tablename__ = "tool_policies"
    __table_args__ = (UniqueConstraint("user_id", "tool_id", name="uq_tool_policies_user_tool"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_id = Column(String, nullable=False)
    unattended_allowed = Column(Boolean, nullable=True)
    chat_mode = Column(String, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ToolPolicyMode(database.Base):
    """Per-user blanket permission modes over the per-tool switches (JAN-359).

    ``unattended_mode``: ``"per_tool"`` (default) or ``"allow_all"`` -- every
    switchable tool runs unattended, plus image generation and remote MCP tools.
    ``chat_mode``: ``"per_tool"`` (default) or ``"allow_all"`` -- no approval card
    in chat (tools switched *Off in chat* stay withheld). An absent row means both
    defaults; see ``janus.tool_policy``.
    """

    __tablename__ = "tool_policy_modes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    unattended_mode = Column(String, nullable=False, default="per_tool", server_default="per_tool")
    chat_mode = Column(String, nullable=False, default="per_tool", server_default="per_tool")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
