"""ORM models of the bounded adaptive character state (JAN-301); re-exported from ``janus.models``.

``CharacterStateRecord`` is one row per user: the current typed state (tone,
verbosity, proactivity, current focus) and a revision counter. No row means
the owner's envelope defaults. ``CharacterStateChange`` is the audit trail:
one row per change with who made it (the owner, or Janus through an approved
proposal from a chat turn), the previous and the new state. Janus never
writes the state itself: ``CharacterStateProposal`` holds what it proposed in
chat until the owner approves or rejects it. See
``services/character_state_service.py``.
"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text

from janus import database
from janus.time_utils import utcnow


class CharacterStateRecord(database.Base):
    """The owner's current adaptive character state (JAN-301)."""

    __tablename__ = "character_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    tone = Column(String(32), nullable=False)
    verbosity = Column(String(32), nullable=False)
    proactivity = Column(String(32), nullable=False)
    current_focus = Column(String(200), nullable=True)
    #: Bumped on every change; matches ``CharacterStateChange.revision`` of the latest audit row.
    revision = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class CharacterStateProposal(database.Base):
    """A change Janus proposed in chat; inert until the owner approves it (JAN-301)."""

    __tablename__ = "character_state_proposals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: ``proposed``, ``approved`` or ``rejected``.
    status = Column(String(16), nullable=False, default="proposed", server_default="proposed", index=True)
    #: Only the proposed dimensions, e.g. ``{"verbosity": "brief"}``.
    changes = Column(JSON, nullable=False)
    rationale = Column(Text, nullable=False)
    #: The plan step id of the chat turn that proposed it (``chat-conversation-<id>::message-<n>``).
    chat_turn_id = Column(String(200), nullable=True)
    conversation_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)


class CharacterStateChange(database.Base):
    """One audited change of the adaptive character state (JAN-301)."""

    __tablename__ = "character_state_changes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    #: ``owner`` or ``janus`` (a proposal from a chat turn the owner approved).
    actor = Column(String(16), nullable=False)
    #: ``edit``, ``proposal``, ``rollback``, ``reset`` or ``envelope``.
    action = Column(String(16), nullable=False)
    chat_turn_id = Column(String(200), nullable=True)
    proposal_id = Column(
        Integer, ForeignKey("character_state_proposals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: For ``rollback``: the audit row whose resulting state was restored.
    rollback_of_id = Column(Integer, nullable=True)
    previous_state = Column(JSON, nullable=False)
    new_state = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
