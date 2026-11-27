"""WhatsApp schemas for Janus API."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from janus.models import (
    ConfidenceLevel,
    Priority,
    WhatsAppConnectionStatus,
    WhatsAppConversationType,
    WhatsAppMessageDirection,
    WhatsAppPairingState,
    WhatsAppSetupStatus,
)

#: ``initial_history_days`` is still accepted and stored (the column stays) but nothing reads it: WhatsApp sends
#: history once after pairing, as much as it decides to.
_HISTORY_DAYS_NOTE = "Ignored. Accepted for compatibility; WhatsApp sends history once after pairing."


class WhatsAppAccountBase(BaseModel):
    """Shared fields for a WhatsApp account."""

    display_name: str
    is_enabled: bool = True
    initial_history_days: int = Field(default=3, ge=1, le=30, deprecated=True, description=_HISTORY_DAYS_NOTE)
    session_data: str | None = None
    session_database_path: str | None = None


class WhatsAppAccountCreate(WhatsAppAccountBase):
    """Payload for creating a WhatsApp account."""


class WhatsAppAccountUpdate(BaseModel):
    """Payload for updating a WhatsApp account."""

    display_name: str | None = None
    is_enabled: bool | None = None
    initial_history_days: int | None = Field(default=None, ge=1, le=30, deprecated=True, description=_HISTORY_DAYS_NOTE)
    session_data: str | None = None
    session_database_path: str | None = None


class WhatsAppAccount(BaseModel):
    """API-safe WhatsApp account payload."""

    id: int
    display_name: str
    provider: str
    is_enabled: bool
    connection_status: WhatsAppConnectionStatus
    initial_history_days: int
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    session_database_path: str | None = None
    session_configured: bool = False

    model_config = ConfigDict(from_attributes=True)


class WhatsAppConversationSummary(BaseModel):
    """Dashboard-friendly summary for a WhatsApp conversation."""

    id: int
    whatsapp_account_id: int
    provider_chat_id: str
    title: str
    conversation_type: WhatsAppConversationType
    participant_summary: str | None = None
    unread_count: int
    latest_message_at: datetime | None = None
    latest_message_snippet: str | None = None

    model_config = ConfigDict(from_attributes=True)


class WhatsAppMessage(BaseModel):
    """API payload for one WhatsApp message."""

    id: int
    whatsapp_account_id: int
    whatsapp_conversation_id: int
    provider_message_id: str
    provider_timestamp: datetime
    direction: WhatsAppMessageDirection
    sender_identifier: str | None = None
    sender_display_name: str | None = None
    body: str | None = None
    snippet: str
    message_type: str | None = None
    attachment_summary: str | None = None
    reply_to_message_id: str | None = None
    priority: Priority
    reasoning: str | None = None
    classification_confidence: ConfidenceLevel | None = None

    model_config = ConfigDict(from_attributes=True)


class WhatsAppPriorityUpdate(BaseModel):
    """Payload for updating one WhatsApp message priority."""

    priority: Priority


class WhatsAppSyncResult(BaseModel):
    """Result payload for (re)starting the live Neonize connector for one account.

    There is no pull-based history API in Neonize, so this reports whether the persistent
    connection was (re)established, not a count of newly fetched messages.
    """

    account_id: int
    connector_started: bool
    message: str


class WhatsAppSetupSessionCreate(BaseModel):
    """Payload for creating a transient WhatsApp setup session."""

    display_name: str
    initial_history_days: int = Field(default=3, ge=1, le=30, deprecated=True, description=_HISTORY_DAYS_NOTE)
    account_id: int | None = None


class WhatsAppSetupSessionFallbackImport(BaseModel):
    """Fallback session artifact import for a setup session."""

    session_data: str | None = None
    session_database_path: str | None = None

    @model_validator(mode="after")
    def validate_session_artifact(self) -> Self:
        """Require at least one fallback artifact and reject double-empty payloads."""
        if (self.session_data or "").strip() or (self.session_database_path or "").strip():
            return self
        raise ValueError("Provide session_data or session_database_path.")


class WhatsAppSetupSession(BaseModel):
    """API-safe payload for an in-progress WhatsApp setup session."""

    id: int
    account_id: int | None = None
    display_name: str
    initial_history_days: int
    setup_status: WhatsAppSetupStatus
    pairing_state: WhatsAppPairingState
    pairing_supported: bool
    show_fallback_import: bool
    pairing_message: str | None = None
    pairing_code: str | None = None
    pairing_qr_payload: str | None = None
    last_error: str | None = None
    expires_at: datetime
    completed_at: datetime | None = None
    session_configured: bool = False

    model_config = ConfigDict(from_attributes=True)


class WhatsAppSetupCompleteResult(BaseModel):
    """Completion payload for a WhatsApp setup session."""

    account: WhatsAppAccount
    sync_result: WhatsAppSyncResult
