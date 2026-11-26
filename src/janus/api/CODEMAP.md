# API Router Code Map

Scope: FastAPI routers under `src/janus/api/`. Routers stay thin: auth dependencies here,
domain logic in `../services/` (see [`../services/CODEMAP.md`](../services/CODEMAP.md)).
Router registration and mount order live in `../app_factory.py`.

## File map

| File | Responsibility |
| --- | --- |
| `auth.py` | Setup, registration, login/logout, session inspection |
| `character.py` | Character constitution CRUD, prompt preview, adaptive state routes (`/api/character/state`) |
| `conversations.py` | Conversation list/detail, message history, delegated tasks, per-message tool selection |
| `dashboard.py` | Dashboard landing-page summary |
| `desktop.py` | Aggregate summary for desktop-client widgets |
| `inbox.py` | Action inbox (polling, mark read, dismiss) and device tokens |
| `dev.py` | Admin-only Storybook static serving under `/dev/storybook` |
| `emails.py` | Email accounts, rules, email list/detail |
| `errors.py` | Error groups: list, detail, delete, compaction |
| `events.py` | Filterable system event log |
| `images.py` | Fetch and delete the user's chat-generated images |
| `github.py`, `linear.py` | GitHub / Linear integration settings and issue/idea submission |
| `web_search.py` | Admin web-search backend settings |
| `self_evolution.py` | Self-evolution loop settings and audit view |
| `memory.py` | Memory facts: list/search, create, edit, delete, compaction |
| `stats.py` | Per-day owner statistics (`/api/stats`) |
| `skills.py`, `skill_suggestions.py` | Skill-library CRUD, catalog import, design assist; suggested skills (mounted before `skills.py`) |
| `llm_models.py` | Per-provider LLM model listing and the admin LLM connection test |
| `planner.py` | Planner accounts, planner summary, system/LLM settings |
| `planner_sources.py` | Calendar listing/selection per provider and on-demand planner sync |
| `planner_items.py` | Delete local planner tasks/events |
| `connections.py` | `/api/connections`: Google/Microsoft OAuth connect, callback, disconnect, add mailbox |
| `runtime_tasks.py` | Agent Tasks page: owner-scoped run/delegation list and detail |
| `runtime_triggers.py` | Ad-hoc trigger ingress, run history, worker health |
| `trigger_definitions.py` | Trigger definition CRUD, run now, history, event catalog, proposal review |
| `webhooks.py` | Inbound webhook management and the public signed delivery endpoint |
| `a2a.py` | Remote A2A agent registry (mounted before `tools.py`) |
| `telegram.py` | The user's Telegram bot: status, connect, test, disconnect |
| `tools.py` | Tool registry (built-in + MCP), per-tool policies, policy modes, injection-classifier settings |
| `knowledge.py` | Knowledge sources (upload/list/delete), search, entities, merge review, graph |
| `wiki.py` | Wiki pages, provenance, proposals, revisions, synthesis switch |
| `topics.py` | Tracked-topic CRUD and status transitions |
| `whatsapp.py` | WhatsApp accounts, sync, conversations/messages |
| `docs.py` | In-app documentation navigation and Markdown content |
| `status.py` | System status summary, service detail, version and release notes |

## Read-next guidance

Each router delegates to the like-named service in `../services/` (e.g. `memory.py` ->
`../services/memory_service.py`, `webhooks.py` -> `../services/webhook_service.py`). Non-obvious ones:

- Auth cookies/sessions -> `auth.py`, then `../security.py`
- Character -> `../services/character_service.py`, `../services/character_state_service.py`, `../character.py`
- Chat tool selection -> `../websocket/chat.py`, then `../runtime_adapter.py`
- Inbox and device tokens -> `../services/inbox_service.py`, `../services/device_token_service.py`
- Emails -> `../processor.py`, `../services/email_service.py`
- Event log -> `../event_logger.py`
- Images -> `../services/image_store.py`
- LLM model listing/test -> `../services/llm_model_catalog.py`
- OAuth connections -> `../services/connect_providers.py`
- Planner -> `../planner_service.py`, `../services/planner_account_service.py`, [`../connectors/CODEMAP.md`](../connectors/CODEMAP.md)
- Runtime triggers -> `../trigger_service.py`, `../trigger_runtime.py`
- Agent Tasks -> `../services/runtime_task_service.py`
- Trigger definitions -> `../trigger_definition_service.py`, `../trigger_proposal_service.py`, `../builtin_triggers.py`
- Tools -> `../services/mcp_service.py`, `../tool_policy.py`
- A2A -> `../services/a2a_service.py`, `../services/a2a_client.py`
- Knowledge and wiki -> `../services/knowledge_*.py`, `../services/wiki_*.py`
- Skills -> `../services/skill_catalog_service.py`, `../models/agents.py`
- Status -> `../status_service.py`
- Storybook -> [`../../../frontend/CODEMAP.md`](../../../frontend/CODEMAP.md)
- Request/response models -> `../schemas/`
