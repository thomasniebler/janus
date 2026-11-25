# ORM Models Code Map

Scope: the `janus.models` package under `src/janus/models/`, the SQLAlchemy ORM schema grouped by domain (JAN-136).

## Responsibilities

- declare every core table on the shared declarative base `janus.database.Base`
- keep `from janus import models` / `models.X` and `from janus.models import X` working: `__init__.py` re-exports every name
- give Alembic (`alembic/env.py` reads `models.database.Base.metadata`) the full model set, including the `models_*.py` feature modules next to this package

## File map

| File | Responsibility |
| --- | --- |
| `__init__.py` | Imports every domain module and re-exports all models and enums, `to_dict`, `database`, and `utcnow` (the e2e seed scripts call `models.utcnow()`); also re-exports the feature modules `../models_character.py`, `../models_images.py`, `../models_knowledge.py`, `../models_safety.py`, `../models_self_evolution.py`, and `../models_toolsets.py` |
| `enums.py` | Shared enums used across domains: `Priority`, `Provider`, `LLMProvider`, `MonitorStatus`, task, planner, reminder, `ConfidenceLevel`, clarification, topic, memory, dashboard-widget, briefing-window, WhatsApp, and MCP states |
| `users.py` | `User` (`users`), `NotificationPreference`, `DashboardWidgetPreference` |
| `agents.py` | The Janus agent's identity: `CharacterProfile` (`character_profiles`, the user-owned character contract) and `AgentSkill` (`agent_skills`, the flat skill library) |
| `email.py` | `EmailAccount`, `EmailRecord` (`emails`), `Rule` (`rules`), `EmailClarificationRequest` |
| `planner.py` | `PlannerAccount`, `TaskRecord` (`tasks`), `CalendarEvent`, `PlannerTask`, `PlannerEvent`, `PlannerSuggestion`, `PlannerLink` |
| `whatsapp.py` | `WhatsAppAccount`, `WhatsAppSetupSession`, `WhatsAppConversation`, `WhatsAppMessage` |
| `reminders.py` | `ReminderMemory` (`reminder_memory`) |
| `topics.py` | `TrackedTopic`, `TopicEvidence`, `TopicSuggestion`, `TopicTrackingPreference` |
| `conversations.py` | `MessageRole`, `ConversationOrigin`, `Conversation`, `ConversationMessage`, `ConversationToolCall` (JAN-249), `ConversationUIBlock` (JAN-252) |
| `events.py` | `EventType`, `Event` (`events`, the status event log), `ErrorGroup` (`error_groups`) |
| `triggers.py` | Plan-driven runtime ingress (JAN-157): `RuntimeTrigger` and its kind/status enums, `TriggerDefinition` and its kind/origin/proposal-status enums, `ToolPolicy` (`tool_policies`, the unattended and In chat switches), `ToolPolicyMode` (`tool_policy_modes`, the blanket Allow everything / Allow all modes, JAN-359) |
| `delegation.py` | `RuntimeDelegationStatus`, `RuntimeDelegation` (JAN-200), `PlanRuntimeState` (`plan_runtime_states`) |
| `memory.py` | `AgentMemoryFact`, `AgentMemoryCompactionRun` |
| `auth.py` | `RevokedSession`, `OAuthState`, `OAuthConnection`, `DeviceToken` (hashed, revocable inbox tokens) |
| `settings.py` | `Settings` (`settings`, per-user system and LLM settings) |
| `mcp.py` | `MCPServer`, `MCPDiscoveredTool`, `MCPCredential` |
| `webhooks.py` | `WebhookEndpoint` (`webhook_endpoints`, JAN-406): inbound webhook URLs with the SHA-256 of their key, a display prefix, the Fernet-encrypted HMAC secret, and a delivery counter |
| `llm_usage.py` | `LlmUsageDay` (`llm_usage_days`, JAN-408): model calls and input/output tokens per user, UTC day, surface and model, plus calls whose provider reported no usage |
| `a2a.py` | `A2AAgent` (`a2a_agents`, JAN-404): the user's remote A2A agents with the bounded card, JSON-RPC URL and Fernet-encrypted bearer token |

## Rules

- Domain modules import only `janus.database`, `janus.time_utils`, SQLAlchemy, and `enums.py`; relationships name other models by string, so no domain module imports another.
- A new model goes into its domain module (or a new module that `__init__.py` imports and re-exports); each module stays within the 600-line default of `tests/test_code_quality_structure.py`.
- The data-layer import contract in `pyproject.toml` covers the package and all its modules.

## Read-next guidance

- Schema changes and migrations -> the domain module here, then `../../../alembic/` and `docs/docs/tech-spec/data-model-migrations.md`
- Engine, sessions, and the declarative base -> `../database.py`
- Knowledge, images, character state, safety, self-evolution, and toolset tables -> the `../models_*.py` modules
