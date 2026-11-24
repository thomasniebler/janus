"""Shared enumerations of the Janus ORM schema.

Priorities, providers, and planner, reminder, topic, memory, dashboard, WhatsApp and MCP
states. Domain enums that sit next to their tables (conversations, events, triggers,
delegation) live in those modules instead.
"""

import enum


class Priority(enum.Enum):
    """Enumeration for email priorities."""

    IMPORTANT = "important"
    DIGEST = "digest"
    IGNORE = "ignore"
    SPAM = "spam"


class Provider(enum.Enum):
    """Enumeration for planner providers."""

    GOOGLE_CALENDAR = "google_calendar"
    GOOGLE_TASKS = "google_tasks"
    MICROSOFT_CALENDAR = "microsoft_calendar"
    O365 = "o365"
    JIRA = "jira"
    ADO = "ado"


class LLMProvider(enum.Enum):
    """Enumeration for LLM providers."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    GITHUB_MODELS = "github_models"
    AZURE_OPENAI = "azure_openai"


class MonitorStatus(str, enum.Enum):
    """Lifecycle state of an IMAP monitor worker."""

    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"


class TaskStatus(str, enum.Enum):
    """Status values for planner tasks."""

    TODO = "Todo"
    IN_PROGRESS = "In Progress"
    DONE = "Done"


class TaskUrgency(str, enum.Enum):
    """Typed urgency for Janus-owned local planner tasks."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class CalendarEventSource(str, enum.Enum):
    """Origin for calendar entries."""

    PLANNER = "planner"
    JANUS_REMINDER = "janus_reminder"


class PlannerItemSource(str, enum.Enum):
    """Origin for Janus-owned and imported planner items."""

    MANUAL = "manual"
    EMAIL_AGENT = "email_agent"
    DIGEST_AGENT = "digest_agent"
    IMPORTED = "imported"


class PlannerItemKind(str, enum.Enum):
    """Supported Janus-owned planner item kinds."""

    TASK = "task"
    EVENT = "event"


class PlannerSuggestionType(str, enum.Enum):
    """Suggestion kinds awaiting planner review."""

    TASK = "task"
    EVENT = "event"
    TASK_UPDATE = "task_update"
    EVENT_UPDATE = "event_update"


class PlannerSuggestionStatus(str, enum.Enum):
    """Lifecycle state for reviewable planner suggestions."""

    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    APPLIED = "applied"


class PlannerLinkSource(str, enum.Enum):
    """Evidence/source record kinds linked to local planner items."""

    EMAIL = "email"
    TOPIC = "topic"
    REMINDER = "reminder"
    IMPORTED_TASK = "imported_task"
    IMPORTED_EVENT = "imported_event"


class ReminderKind(str, enum.Enum):
    """Types of reminder memory Janus can persist."""

    EVENT = "event"
    PREPARATION = "preparation"


class ReminderStatus(str, enum.Enum):
    """Lifecycle for reminder memory."""

    ACTIVE = "active"
    COMPLETED = "completed"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class ConfidenceLevel(str, enum.Enum):
    """Confidence levels for Janus-generated classifications and suggestions."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ClarificationStatus(str, enum.Enum):
    """Lifecycle for user-review clarification items."""

    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"


class TopicStatus(str, enum.Enum):
    """Lifecycle for a tracked topic."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SNOOZED = "snoozed"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class TopicOrigin(str, enum.Enum):
    """Origin of a tracked topic."""

    AUTO = "auto"
    MANUAL = "manual"
    RULE_SUGGESTION = "rule_suggestion"


class TopicEvidenceSource(str, enum.Enum):
    """Origin type for evidence linked to a topic."""

    EMAIL = "email"
    REMINDER = "reminder"
    CALENDAR_EVENT = "calendar_event"
    TASK = "task"
    MEMORY_FACT = "memory_fact"


class TopicEvidenceRole(str, enum.Enum):
    """How one piece of evidence supports a topic."""

    TRIGGER = "trigger"
    SUPPORTING = "supporting"
    DERIVED_LINK = "derived_link"


class TopicSuggestionType(str, enum.Enum):
    """Reviewable suggestion kinds for topic curation."""

    RENAME = "rename"
    MERGE = "merge"
    PIN = "pin"
    IGNORE_PATTERN = "ignore_pattern"
    PROMOTE_PATTERN = "promote_pattern"


class TopicSuggestionStatus(str, enum.Enum):
    """Lifecycle for a topic suggestion."""

    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"


class TopicExtractionScope(str, enum.Enum):
    """Priority scope used for automatic topic extraction."""

    IMPORTANT_ONLY = "important_only"
    IMPORTANT_AND_DIGEST = "important_and_digest"
    ALL_NON_SPAM = "all_non_spam"


class AgentMemorySource(str, enum.Enum):
    """Origin of an agent memory fact."""

    USER = "user"
    AGENT = "agent"
    COMPACTION = "compaction"


class AgentMemoryStatus(str, enum.Enum):
    """Lifecycle for a durable memory fact."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentMemoryCompactionStatus(str, enum.Enum):
    """Lifecycle state for a background memory compaction run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class DashboardWidgetKey(str, enum.Enum):
    """Supported dashboard widget identifiers."""

    BRIEFING = "briefing"
    TOPIC_FOCUS = "topic_focus"
    IMPORTANT_EMAILS = "important_emails"
    WHATSAPP_MESSAGES = "whatsapp_messages"
    SCHEDULE = "schedule"
    TASKS = "tasks"


class BriefingEmailWindow(str, enum.Enum):
    """Supported preset windows for briefing email context."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_24_HOURS = "last_24_hours"


class WhatsAppConnectionStatus(str, enum.Enum):
    """Connection and sync lifecycle for a WhatsApp account."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    DEGRADED = "degraded"


class WhatsAppSetupStatus(str, enum.Enum):
    """Lifecycle state for a transient WhatsApp setup session."""

    CREATED = "created"
    PAIRING = "pairing"
    AWAITING_FALLBACK = "awaiting_fallback"
    READY = "ready"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WhatsAppPairingState(str, enum.Enum):
    """Provider-facing pairing state within a WhatsApp setup session."""

    IDLE = "idle"
    STARTING = "starting"
    WAITING = "waiting"
    CODE_READY = "code_ready"
    PAIRED = "paired"
    IMPORTED = "imported"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WhatsAppConversationType(str, enum.Enum):
    """Supported WhatsApp conversation kinds."""

    DIRECT = "direct"
    GROUP = "group"


class WhatsAppMessageDirection(str, enum.Enum):
    """Direction of a WhatsApp message."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class MCPTransport(str, enum.Enum):
    """Supported remote MCP transport adapters."""

    STREAMABLE_HTTP = "streamable_http"


class MCPDiscoveryStatus(str, enum.Enum):
    """Discovery lifecycle for a managed MCP server."""

    NEVER = "never"
    READY = "ready"
    ERROR = "error"
    AUTH_REQUIRED = "auth_required"
