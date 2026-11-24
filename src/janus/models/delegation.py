"""Plan-driven runtime (JAN-157): delegated child tasks and durable plan state."""

import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.time_utils import utcnow


class RuntimeDelegationStatus(str, enum.Enum):
    """Persisted lifecycle state for one delegated child task (JAN-200)."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RuntimeDelegation(database.Base):
    """Parent-task -> child-task delegation record; ``child_task_id`` is the child's plan-state task ID.

    Finishing needs the private claim token; an interrupted run is requeued once, leased from ``requeued_at``.
    """

    __tablename__ = "runtime_delegations"

    id = Column(Integer, primary_key=True, index=True)
    delegation_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    parent_task_id = Column(String, nullable=False, index=True)
    parent_step_id = Column(String, nullable=True)
    child_task_id = Column(String, unique=True, nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    depth = Column(Integer, nullable=False, default=1)
    objective = Column(Text, nullable=False)
    requested_tool_ids = Column(JSON, nullable=False, default=list)
    allowed_tool_ids = Column(JSON, nullable=False, default=list)
    #: The parent run's model choice; a child always runs on its parent's model (JAN-233).
    llm_provider = Column(String, nullable=True)
    llm_model = Column(String, nullable=True)
    status = Column(String, nullable=False, default=RuntimeDelegationStatus.QUEUED.value, index=True)
    claim_token = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    requeue_count = Column(Integer, nullable=False, default=0, server_default="0")
    requeued_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="delegations")


class PlanRuntimeState(database.Base):
    """Owner-scoped durable snapshot of one plan graph cursor."""

    __tablename__ = "plan_runtime_states"
    __table_args__ = (UniqueConstraint("user_id", "task_id", name="uq_plan_runtime_state_owner_task"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(String, nullable=False, index=True)
    node = Column(String, nullable=False, index=True)
    plan_status = Column(String, nullable=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
