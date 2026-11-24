"""Email and rule schemas for Janus API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from janus.models import ClarificationStatus, ConfidenceLevel, Priority, TopicStatus


class EmailAccountBase(BaseModel):
    """Base email account schema."""

    name: str
    email: EmailStr
    imap_server: str
    imap_port: int
    username: str
    is_active: bool = True


class EmailAccountCreate(EmailAccountBase):
    """Schema for creating an email account."""

    username: str
    password: str


class EmailAccount(EmailAccountBase):
    """Schema for an email account."""

    id: int
    #: ``password`` for a custom IMAP login, ``google``/``microsoft`` for a connected account.
    auth_type: str = "password"

    model_config = ConfigDict(from_attributes=True)


class EmailAccountUpdate(BaseModel):
    """Schema for partially updating an email account."""

    name: str | None = None
    email: EmailStr | None = None
    imap_server: str | None = None
    imap_port: int | None = None
    username: str | None = None
    password: str | None = None
    is_active: bool | None = None


class EmailTopicBadge(BaseModel):
    """Compact tracked-topic metadata attached to an email surface."""

    id: int
    title: str
    status: TopicStatus
    pinned: bool


class EmailRecord(BaseModel):
    """Schema for an email record."""

    id: int
    message_id: str
    sender: str
    subject: str
    snippet: str
    body: str | None = None
    priority: Priority
    reasoning: str | None = None
    classification_confidence: ConfidenceLevel | None = None
    received_at: datetime
    account_id: int
    topics: list[EmailTopicBadge] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class RuleBase(BaseModel):
    """Base rule schema."""

    name: str
    pattern: str
    field: str = "sender"
    priority: Priority = Priority.IMPORTANT
    is_active: bool = True
    order: int = 0


class RuleCreate(RuleBase):
    """Schema for creating a rule."""

    pass


class RuleUpdate(BaseModel):
    """Schema for partial rule updates (e.g. reordering)."""

    order: int | None = None
    is_active: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class Rule(RuleBase):
    """Schema for a prioritization rule."""

    id: int

    model_config = ConfigDict(from_attributes=True)


class EmailClarificationRequestBase(BaseModel):
    """Shared fields for an email clarification request."""

    previous_priority: Priority
    requested_priority: Priority
    suggested_rule_name: str
    suggested_rule_pattern: str
    suggested_rule_field: str
    suggested_rule_priority: Priority
    suggestion_confidence: ConfidenceLevel
    suggestion_reason: str
    status: ClarificationStatus


class EmailClarificationEmail(BaseModel):
    """Minimal email details included in the clarification queue."""

    id: int
    sender: str
    subject: str
    snippet: str
    priority: Priority
    classification_confidence: ConfidenceLevel | None = None

    model_config = ConfigDict(from_attributes=True)


class EmailClarificationRequest(EmailClarificationRequestBase):
    """Schema for a reviewable clarification item."""

    id: int
    email_id: int
    applied_rule_id: int | None = None
    email: EmailClarificationEmail
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationPreferenceBase(BaseModel):
    """Shared fields for notification preferences."""

    notify_important: bool = True
    notify_digest: bool = True
    daily_digest_enabled: bool = False
    daily_digest_hour: int = Field(default=16, ge=0, le=23)
    daily_digest_minute: int = Field(default=0, ge=0, le=59)
    digest_lookback_hours: int = Field(default=24, ge=1, le=168)
    sender_allowlist: str | None = None
    sender_blocklist: str | None = None
    keyword_allowlist: str | None = None
    keyword_blocklist: str | None = None
    secretary_active_start_hour: int = Field(default=8, ge=0, le=23)
    secretary_active_end_hour: int = Field(default=21, ge=1, le=24)
    secretary_whatsapp_enabled: bool = False
    secretary_whatsapp_number: str | None = None


class NotificationPreferenceUpdate(BaseModel):
    """Schema for partial updates to notification preferences."""

    notify_important: bool | None = None
    notify_digest: bool | None = None
    daily_digest_enabled: bool | None = None
    daily_digest_hour: int | None = Field(default=None, ge=0, le=23)
    daily_digest_minute: int | None = Field(default=None, ge=0, le=59)
    digest_lookback_hours: int | None = Field(default=None, ge=1, le=168)
    sender_allowlist: str | None = None
    sender_blocklist: str | None = None
    keyword_allowlist: str | None = None
    keyword_blocklist: str | None = None
    secretary_active_start_hour: int | None = Field(default=None, ge=0, le=23)
    secretary_active_end_hour: int | None = Field(default=None, ge=1, le=24)
    secretary_whatsapp_enabled: bool | None = None
    secretary_whatsapp_number: str | None = None


class NotificationPreference(NotificationPreferenceBase):
    """Schema for persisted notification preference records."""

    id: int
    user_id: int
    last_daily_digest_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MailIngestionSettings(BaseModel):
    """Settings that control Janus mail ingestion behavior."""

    initial_sync_email_limit: int


class MailIngestionSettingsUpdate(BaseModel):
    """Schema for partial mail-ingestion settings updates."""

    initial_sync_email_limit: int | None = Field(default=None, ge=1)
