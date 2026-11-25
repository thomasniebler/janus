"""Owner-scoped registry of remote A2A (Agent2Agent protocol) agents (JAN-404)."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from janus import database
from janus.time_utils import utcnow


class A2AAgent(database.Base):
    """A remote agent the user registered; Janus may ask it for help from chat.

    ``card`` keeps the bounded subset of the agent card Janus uses (name,
    description, skills, protocol version, JSON-RPC URL); ``encrypted_token`` is
    an optional bearer token, Fernet-encrypted and never returned by the API.
    """

    __tablename__ = "a2a_agents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    #: The user's label for the agent (defaults to the card's name).
    name = Column(String(80), nullable=False)
    #: Base URL the agent card is fetched from (``<base>/.well-known/agent-card.json``).
    base_url = Column(String(2048), nullable=False)
    enabled = Column(Boolean, default=False, server_default="0", nullable=False)
    card = Column(JSON, nullable=True)
    #: The JSON-RPC endpoint from the card, validated like the base URL.
    rpc_url = Column(String(2048), nullable=True)
    encrypted_token = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["A2AAgent"]
