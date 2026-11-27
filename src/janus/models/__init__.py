"""SQLAlchemy models for Janus database, grouped by domain (JAN-136).

Every model, enum and helper is re-exported here, so ``from janus import models`` /
``models.X`` and ``from janus.models import X`` keep working. The declarative base is
``janus.database.Base``; ``models.database`` stays available for ``alembic/env.py`` and
``models.utcnow`` for the e2e seed scripts.
"""

from janus import database
from janus.models.a2a import A2AAgent
from janus.models.agents import (
    AgentSkill,
    CharacterProfile,
)
from janus.models.auth import (
    DeviceToken,
    OAuthConnection,
    OAuthState,
    RevokedSession,
)
from janus.models.conversations import (
    Conversation,
    ConversationMessage,
    ConversationOrigin,
    ConversationToolCall,
    ConversationUIBlock,
    MessageRole,
)
from janus.models.delegation import (
    PlanRuntimeState,
    RuntimeDelegation,
    RuntimeDelegationStatus,
)
from janus.models.email import (
    EmailAccount,
    EmailClarificationRequest,
    EmailRecord,
    Rule,
)
from janus.models.enums import (
    CONNECTED_PLANNER_PROVIDERS,
    AgentMemoryCompactionStatus,
    AgentMemorySource,
    AgentMemoryStatus,
    BriefingEmailWindow,
    CalendarEventSource,
    ClarificationStatus,
    ConfidenceLevel,
    ConnectedPlannerProvider,
    DashboardWidgetKey,
    LLMProvider,
    MCPDiscoveryStatus,
    MCPTransport,
    MonitorStatus,
    PlannerItemKind,
    PlannerItemSource,
    PlannerLinkSource,
    PlannerSuggestionStatus,
    PlannerSuggestionType,
    Priority,
    Provider,
    ReminderKind,
    ReminderStatus,
    TaskStatus,
    TaskUrgency,
    TopicEvidenceRole,
    TopicEvidenceSource,
    TopicExtractionScope,
    TopicOrigin,
    TopicStatus,
    TopicSuggestionStatus,
    TopicSuggestionType,
    WhatsAppConnectionStatus,
    WhatsAppConversationType,
    WhatsAppMessageDirection,
    WhatsAppPairingState,
    WhatsAppSetupStatus,
)
from janus.models.events import (
    ErrorGroup,
    Event,
    EventType,
)
from janus.models.llm_usage import LlmUsageDay
from janus.models.mcp import (
    MCPCredential,
    MCPDiscoveredTool,
    MCPServer,
)
from janus.models.memory import (
    AgentMemoryCompactionRun,
    AgentMemoryFact,
)
from janus.models.planner import (
    CalendarEvent,
    PlannerAccount,
    PlannerEvent,
    PlannerLink,
    PlannerSuggestion,
    PlannerTask,
    TaskRecord,
)
from janus.models.reminders import (
    ReminderMemory,
)
from janus.models.settings import (
    Settings,
)
from janus.models.skill_suggestions import SkillSuggestionDismissal
from janus.models.telegram import TelegramBot
from janus.models.topics import (
    TopicEvidence,
    TopicSuggestion,
    TopicTrackingPreference,
    TrackedTopic,
)
from janus.models.triggers import (
    RuntimeTrigger,
    RuntimeTriggerKind,
    RuntimeTriggerStatus,
    ToolPolicy,
    ToolPolicyMode,
    TriggerDefinition,
    TriggerDefinitionKind,
    TriggerDefinitionOrigin,
    TriggerProposalStatus,
)
from janus.models.users import (
    DashboardWidgetPreference,
    NotificationPreference,
    User,
)
from janus.models.webhooks import WebhookEndpoint
from janus.models.whatsapp import (
    WhatsAppAccount,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppSetupSession,
)
from janus.time_utils import utcnow


def to_dict(obj):
    """Helper to convert SQLAlchemy models to dictionaries."""
    if obj is None:
        return None
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# Models split into their own modules are re-exported: callers keep using ``models.X``.
from janus.models_character import (  # noqa: E402  (JAN-301)
    CharacterStateChange,
    CharacterStateProposal,
    CharacterStateRecord,
)
from janus.models_images import GeneratedImage  # noqa: E402  (JAN-253)
from janus.models_knowledge import (  # noqa: E402
    KnowledgeChunk,
    KnowledgeEmbedding,
    KnowledgeEntity,
    KnowledgeEntityType,
    KnowledgeLlmUsage,
    KnowledgeMention,
    KnowledgeMergeSuggestion,
    KnowledgeMergeSuggestionStatus,
    KnowledgePreference,
    KnowledgeRelation,
    KnowledgeRelationEvidence,
    KnowledgeSource,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    WikiPage,
    WikiPageRevision,
)
from janus.models_safety import InjectionClassification, InjectionClassifierSettings  # noqa: E402  (JAN-289)
from janus.models_self_evolution import SelfEvolutionAction, SelfEvolutionSettings  # noqa: E402  (JAN-320)
from janus.models_toolsets import RuntimeToolSelection  # noqa: E402  (JAN-300)

__all__ = [
    "AgentMemoryCompactionRun",
    "AgentMemoryCompactionStatus",
    "AgentMemoryFact",
    "AgentMemorySource",
    "AgentMemoryStatus",
    "AgentSkill",
    "BriefingEmailWindow",
    "CalendarEvent",
    "CalendarEventSource",
    "CharacterProfile",
    "CharacterStateChange",
    "CharacterStateProposal",
    "CharacterStateRecord",
    "ClarificationStatus",
    "ConfidenceLevel",
    "ConnectedPlannerProvider",
    "Conversation",
    "ConversationMessage",
    "ConversationOrigin",
    "ConversationToolCall",
    "ConversationUIBlock",
    "DashboardWidgetKey",
    "DashboardWidgetPreference",
    "DeviceToken",
    "EmailAccount",
    "EmailClarificationRequest",
    "EmailRecord",
    "ErrorGroup",
    "Event",
    "EventType",
    "GeneratedImage",
    "InjectionClassification",
    "InjectionClassifierSettings",
    "KnowledgeChunk",
    "KnowledgeEmbedding",
    "KnowledgeEntity",
    "KnowledgeEntityType",
    "KnowledgeLlmUsage",
    "KnowledgeMention",
    "KnowledgeMergeSuggestion",
    "KnowledgeMergeSuggestionStatus",
    "KnowledgePreference",
    "KnowledgeRelation",
    "KnowledgeRelationEvidence",
    "KnowledgeSource",
    "KnowledgeSourceStatus",
    "KnowledgeSourceType",
    "LLMProvider",
    "A2AAgent",
    "MCPCredential",
    "MCPDiscoveredTool",
    "MCPDiscoveryStatus",
    "MCPServer",
    "MCPTransport",
    "MessageRole",
    "MonitorStatus",
    "NotificationPreference",
    "OAuthConnection",
    "OAuthState",
    "PlanRuntimeState",
    "PlannerAccount",
    "PlannerEvent",
    "PlannerItemKind",
    "PlannerItemSource",
    "PlannerLink",
    "PlannerLinkSource",
    "PlannerSuggestion",
    "PlannerSuggestionStatus",
    "PlannerSuggestionType",
    "PlannerTask",
    "Priority",
    "CONNECTED_PLANNER_PROVIDERS",
    "Provider",
    "ReminderKind",
    "ReminderMemory",
    "ReminderStatus",
    "RevokedSession",
    "Rule",
    "RuntimeDelegation",
    "RuntimeDelegationStatus",
    "RuntimeToolSelection",
    "RuntimeTrigger",
    "RuntimeTriggerKind",
    "RuntimeTriggerStatus",
    "SelfEvolutionAction",
    "SelfEvolutionSettings",
    "Settings",
    "TaskRecord",
    "TaskStatus",
    "TaskUrgency",
    "ToolPolicy",
    "ToolPolicyMode",
    "TopicEvidence",
    "TopicEvidenceRole",
    "TopicEvidenceSource",
    "TopicExtractionScope",
    "TopicOrigin",
    "TopicStatus",
    "TopicSuggestion",
    "TopicSuggestionStatus",
    "TopicSuggestionType",
    "TopicTrackingPreference",
    "TrackedTopic",
    "TriggerDefinition",
    "TriggerDefinitionKind",
    "TriggerDefinitionOrigin",
    "TriggerProposalStatus",
    "User",
    "WhatsAppAccount",
    "WhatsAppConnectionStatus",
    "WhatsAppConversation",
    "WhatsAppConversationType",
    "WhatsAppMessage",
    "WhatsAppMessageDirection",
    "WhatsAppPairingState",
    "WhatsAppSetupSession",
    "WhatsAppSetupStatus",
    "WikiPage",
    "WikiPageRevision",
    "database",
    "to_dict",
    "utcnow",
    "WebhookEndpoint",
    "LlmUsageDay",
    "SkillSuggestionDismissal",
    "TelegramBot",
]
