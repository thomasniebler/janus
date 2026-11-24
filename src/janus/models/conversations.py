"""Chat conversations with their messages, per-message tool calls (JAN-249) and UI blocks (JAN-252)."""

import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.time_utils import utcnow


class MessageRole(str, enum.Enum):
    """Role of a participant in a conversation message."""

    USER = "user"
    ASSISTANT = "assistant"


class ConversationOrigin(str, enum.Enum):
    """Who started a conversation (JAN-319)."""

    #: The user opened it (the chat UI, the iOS app, the REST API).
    USER = "user"
    #: An unattended run started it with the ``start_conversation`` tool.
    JANUS = "janus"


class Conversation(database.Base):
    """Database model for a persisted chat conversation."""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=True)
    #: :class:`ConversationOrigin` value; ``janus`` for a conversation an unattended run started (JAN-319).
    origin = Column(String(16), nullable=False, default=ConversationOrigin.USER.value, server_default="user")
    #: The durable ``RuntimeTrigger.trigger_id`` of the run that started a ``janus`` conversation, else ``NULL``.
    origin_trigger_id = Column(String, nullable=True, index=True)
    #: ``True`` from when Janus starts the conversation until its owner first opens it (JAN-319).
    unread = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    messages = relationship(
        "ConversationMessage",
        back_populates="conversation",
        order_by="ConversationMessage.created_at",
        cascade="all, delete-orphan",
    )
    # No delete cascade on purpose: a delegation is its child task's durable
    # record, so deleting the conversation only nulls the back-link (the ORM's
    # default one-to-many behavior) instead of erasing the child's history.
    delegations = relationship("RuntimeDelegation", back_populates="conversation")
    user = relationship("User")


class ConversationMessage(database.Base):
    """Database model for a single message within a conversation."""

    __tablename__ = "conversation_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")
    tool_calls = relationship(
        "ConversationToolCall",
        back_populates="message",
        order_by="ConversationToolCall.position",
        cascade="all, delete-orphan",
    )
    ui_blocks = relationship(
        "ConversationUIBlock",
        back_populates="message",
        order_by="ConversationUIBlock.position",
        cascade="all, delete-orphan",
    )


class ConversationToolCall(database.Base):
    """One tool call of an assistant chat message, as streamed in its last ``__TOOL_EVENT__`` (JAN-249).

    ``args`` are redacted and ``result_preview`` is untrusted, truncated text;
    see ``services/tool_call_events.py``.
    """

    __tablename__ = "conversation_tool_calls"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    call_id = Column(String, nullable=False)
    tool_id = Column(String, nullable=True)
    tool_name = Column(String, nullable=False)
    phase = Column(String, nullable=False)
    args = Column(JSON, nullable=True)
    args_truncated = Column(Boolean, default=False, server_default="0", nullable=False)
    result_preview = Column(Text, nullable=True)
    result_truncated = Column(Boolean, default=False, server_default="0", nullable=False)
    duration_ms = Column(Integer, nullable=True)
    approval = Column(String, nullable=True)  # JAN-254 chat approval decision; NULL when none was asked
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)

    message = relationship("ConversationMessage", back_populates="tool_calls")


class ConversationUIBlock(database.Base):
    """One validated UI block an assistant chat message rendered (JAN-252, ``services/ui_blocks.py``)."""

    __tablename__ = "conversation_ui_blocks"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    block_id = Column(String, nullable=False)
    block_type = Column(String, nullable=False)
    schema_version = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)  # the block as streamed in __UI_BLOCK__ (at most 8 KB)
    created_at = Column(DateTime, default=utcnow, server_default=func.now(), nullable=False)

    message = relationship("ConversationMessage", back_populates="ui_blocks")
