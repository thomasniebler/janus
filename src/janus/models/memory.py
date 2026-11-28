"""Durable memory facts of the single Janus agent and their compaction runs."""

from sqlalchemy import (
    Boolean,
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
    #: Times Janus's memory tools returned the fact; orders the memory review and the index.
    access_count = Column(Integer, default=0, server_default="0", nullable=False)
    #: ``AgentMemoryVerification`` value: how far the fact was checked against its source conversation.
    verification = Column(String(20), default="unverified", server_default="unverified", nullable=False)
    verified_at = Column(DateTime, nullable=True)
    #: The exact passage of the source conversation that supports the fact (verbatim, checked on save).
    evidence_quote = Column(Text, nullable=True)
    #: When the memory review should check the fact again; ``NULL`` means it is due.
    review_due_at = Column(DateTime, nullable=True)
    #: Chosen by the memory review for the small memory index injected into every prompt.
    promoted = Column(Boolean, default=False, server_default="0", nullable=False)
    #: ``AgentMemoryPin`` value: the user's own pinned/hidden override of ``promoted``; ``NULL`` lets the review decide.
    pin = Column(String(10), nullable=True)

    user = relationship("User")
    compacted_into = relationship("AgentMemoryFact", remote_side=[id])

    @property
    def in_memory_index(self) -> bool:
        """Whether the fact is eligible for the memory index (pinned, or promoted and not hidden).

        Eligibility only: ``memory_curation.index_facts`` also requires an active,
        confirmed fact and applies the index's size caps.
        """
        return self.pin == "pinned" or (self.pin is None and bool(self.promoted))


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
