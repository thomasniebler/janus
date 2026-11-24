"""Durable memory facts of the single Janus agent and their compaction runs."""

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import AgentMemoryCompactionStatus, AgentMemorySource, AgentMemoryStatus
from janus.time_utils import utcnow


class AgentMemoryFact(database.Base):
    """Durable user memory facts accessible to memory-enabled chat agents."""

    __tablename__ = "agent_memory_facts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    summary = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    source = Column(Enum(AgentMemorySource), default=AgentMemorySource.USER, nullable=False)
    status = Column(Enum(AgentMemoryStatus), default=AgentMemoryStatus.ACTIVE, nullable=False, index=True)
    source_conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True, index=True)
    compacted_into_id = Column(Integer, ForeignKey("agent_memory_facts.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    last_accessed_at = Column(DateTime, nullable=True)

    user = relationship("User")
    compacted_into = relationship("AgentMemoryFact", remote_side=[id])


class AgentMemoryCompactionRun(database.Base):
    """Track on-demand background memory compaction runs."""

    __tablename__ = "agent_memory_compaction_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(Enum(AgentMemoryCompactionStatus), default=AgentMemoryCompactionStatus.QUEUED, nullable=False)
    result_summary = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    facts_considered = Column(Integer, default=0, nullable=False)
    facts_archived = Column(Integer, default=0, nullable=False)
    facts_created = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User")
