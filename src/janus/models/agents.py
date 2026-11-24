"""The single Janus agent's identity: character contract and skill library.

``CharacterProfile`` is the user-owned character contract; ``AgentSkill`` is the flat skill library.
"""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.time_utils import utcnow


class CharacterProfile(database.Base):
    """User-owned persistent character constitution."""

    __tablename__ = "character_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    constitution_id = Column(String, nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    name = Column(String, nullable=False)
    identity = Column(Text, nullable=False)
    core_values = Column(JSON, nullable=False, default=list)
    non_negotiables = Column(JSON, nullable=False, default=list)
    traits = Column(JSON, nullable=False, default=list)
    exploration_rate = Column(Float, nullable=False, default=0.0, server_default="0")
    #: Free-text instructions appended to the rendered constitution (JAN-230).
    additional_instructions = Column(Text, nullable=True)
    #: Replaces the built-in rules of the post-turn memory suggester (JAN-230).
    memory_instructions = Column(Text, nullable=True)
    #: Owner-set bounds and defaults of the adaptive character state (JAN-301); ``NULL`` = built-in envelope.
    state_envelope = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")


class AgentSkill(database.Base):
    """Database model for reusable agent skills."""

    __tablename__ = "agent_skills"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String, nullable=False)
    license = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
