"""WhatsApp connector: accounts, pairing/setup sessions, conversations and messages."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from janus import database
from janus.models.enums import (
    ConfidenceLevel,
    Priority,
    WhatsAppConnectionStatus,
    WhatsAppConversationType,
    WhatsAppMessageDirection,
    WhatsAppPairingState,
    WhatsAppSetupStatus,
)
from janus.time_utils import utcnow


class WhatsAppAccount(database.Base):
    """Database model for a WhatsApp integration account."""

    __tablename__ = "whatsapp_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    display_name = Column(String, nullable=False)
    provider = Column(String, default="whatsapp_neonize", nullable=False)
    session_data = Column(Text, nullable=True)
    session_database_path = Column(String, nullable=True)
    client_uuid = Column(String, nullable=True)
    is_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    connection_status = Column(
        Enum(WhatsAppConnectionStatus, native_enum=False, create_constraint=True, validate_strings=True),
        default=WhatsAppConnectionStatus.DISCONNECTED,
        nullable=False,
    )
    initial_history_days = Column(Integer, default=3, server_default="3", nullable=False)
    last_synced_at = Column(DateTime, nullable=True)
    last_sync_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    setup_sessions = relationship("WhatsAppSetupSession", back_populates="account")
    conversations = relationship(
        "WhatsAppConversation",
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="WhatsAppConversation.updated_at.desc()",
    )
    messages = relationship("WhatsAppMessage", back_populates="account", cascade="all, delete-orphan")


class WhatsAppSetupSession(database.Base):
    """Transient setup-session state for in-app WhatsApp onboarding."""

    __tablename__ = "whatsapp_setup_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("whatsapp_accounts.id"), nullable=True, index=True)
    display_name = Column(String, nullable=False)
    initial_history_days = Column(Integer, default=3, server_default="3", nullable=False)
    setup_status = Column(
        Enum(WhatsAppSetupStatus, native_enum=False, create_constraint=True, validate_strings=True),
        default=WhatsAppSetupStatus.CREATED,
        nullable=False,
    )
    pairing_state = Column(
        Enum(WhatsAppPairingState, native_enum=False, create_constraint=True, validate_strings=True),
        default=WhatsAppPairingState.IDLE,
        nullable=False,
    )
    pairing_supported = Column(Boolean, default=False, server_default="0", nullable=False)
    show_fallback_import = Column(Boolean, default=False, server_default="0", nullable=False)
    pairing_message = Column(Text, nullable=True)
    pairing_code = Column(String, nullable=True)
    pairing_qr_payload = Column(Text, nullable=True)
    session_data = Column(Text, nullable=True)
    session_database_path = Column(String, nullable=True)
    last_error = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    account = relationship("WhatsAppAccount", back_populates="setup_sessions")


class WhatsAppConversation(database.Base):
    """Database model for one WhatsApp chat/conversation."""

    __tablename__ = "whatsapp_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    whatsapp_account_id = Column(Integer, ForeignKey("whatsapp_accounts.id"), nullable=False, index=True)
    provider_chat_id = Column(String, nullable=False, index=True)
    conversation_type = Column(
        Enum(WhatsAppConversationType, native_enum=False, create_constraint=True, validate_strings=True),
        default=WhatsAppConversationType.DIRECT,
        nullable=False,
    )
    title = Column(String, nullable=False)
    participant_summary = Column(Text, nullable=True)
    unread_count = Column(Integer, default=0, server_default="0", nullable=False)
    latest_message_at = Column(DateTime, nullable=True, index=True)
    latest_message_snippet = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    account = relationship("WhatsAppAccount", back_populates="conversations")
    messages = relationship(
        "WhatsAppMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="WhatsAppMessage.provider_timestamp.desc()",
    )

    __table_args__ = (
        UniqueConstraint(
            "whatsapp_account_id",
            "provider_chat_id",
            name="uq_whatsapp_conversation_account_provider_chat",
        ),
    )


class WhatsAppMessage(database.Base):
    """Database model for an ingested WhatsApp message."""

    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    whatsapp_account_id = Column(Integer, ForeignKey("whatsapp_accounts.id"), nullable=False, index=True)
    whatsapp_conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"), nullable=False, index=True)
    provider_message_id = Column(String, nullable=False, index=True)
    provider_timestamp = Column(DateTime, nullable=False, index=True)
    direction = Column(
        Enum(WhatsAppMessageDirection, native_enum=False, create_constraint=True, validate_strings=True),
        nullable=False,
    )
    sender_identifier = Column(String, nullable=True)
    sender_display_name = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    snippet = Column(Text, nullable=False)
    message_type = Column(String, nullable=True)
    attachment_summary = Column(Text, nullable=True)
    reply_to_message_id = Column(String, nullable=True)
    #: Set on an outgoing message an unattended run sent itself (the secretary's alert); counts the daily cap.
    origin_trigger_id = Column(String, nullable=True, index=True)
    priority = Column(Enum(Priority), default=Priority.DIGEST, nullable=False)
    reasoning = Column(String, nullable=True)
    classification_confidence = Column(Enum(ConfidenceLevel), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user = relationship("User")
    account = relationship("WhatsAppAccount", back_populates="messages")
    conversation = relationship("WhatsAppConversation", back_populates="messages")

    __table_args__ = (
        UniqueConstraint(
            "whatsapp_account_id",
            "provider_message_id",
            name="uq_whatsapp_message_account_provider_message",
        ),
    )
