# API Router Code Map

Scope: FastAPI routers under `src/janus/api/`.

## Responsibilities

- define route surfaces and request/response wiring
- keep authentication/session dependencies at the router layer
- delegate domain logic to services and shared modules

## File map

| File | Responsibility |
| --- | --- |
| `auth.py` | Setup, registration, login/logout, and session inspection endpoints |
| `character.py` | Authenticated CRUD for the user's character constitution profile (incl. additional and memory instructions), a prompt preview, and the owner-only adaptive state routes under `/api/character/state` (edit, envelope, proposals approve/reject, history, rollback, reset; JAN-301) |
| `conversations.py` | Conversation list/detail (each with `origin` and `unread`; opening a Janus-started conversation marks it read, JAN-319), message history (each message with its stored `tool_calls`, JAN-249, and `ui_blocks`, JAN-252), the conversation's delegated tasks (`GET /{id}/delegations`, newest-first and paginated, 404 for another user's conversation, JAN-208), and per-message tool-selection endpoints |
| `dashboard.py` | Dashboard summary endpoints used by the main app landing page |
| `images.py` | `GET /api/images/{id}`: the current user's chat-generated image (JAN-253), and `DELETE /api/images/{id}` to remove one (JAN-359, e.g. an image an unattended run stored without a conversation), owner-scoped and path-confined in `services/image_store.py`, with its sniffed type, `Cache-Control: no-store`, and `nosniff` |
| `inbox.py` | Action inbox for the owner: `GET /api/inbox` (ETag polling) and the low-risk `mark_read`/`dismiss` actions, which accept the web session or a device token, plus device-token create/list/revoke (web session only) |
| `desktop.py` | Aggregate summary endpoint for lightweight desktop-client widgets |
| `dev.py` | Admin-only static-file server for the built Storybook site under `/dev/storybook` |
| `emails.py` | Email account, rule, and email list/detail operations |
| `errors.py` | Error-group listing, detail, deletion, and compaction endpoints |
| `events.py` | Filterable, paginated system event log (`GET /api/events`) and distinct-source listing |
| `github.py` | GitHub integration settings and issue-submission endpoints |
| `linear.py` | Linear integration settings and chat idea submission endpoints |
| `web_search.py` | Admin-managed web search backend settings for the chat `web_search` tool |
| `self_evolution.py` | The caller's self-evolution loop settings, caps, usage, scope fence and recent actions (`GET`), admin-only `PATCH` (JAN-320) |
| `memory.py` | Durable agent-memory fact listing/search (incl. the `source` filter, JAN-245), manual creation, editing (`PATCH`, JAN-224), deletion, and compaction triggers |
| `stats.py` | `GET /api/stats`: owner-scoped mail, trigger, memory/knowledge, messaging, and approval (JAN-371) statistics bucketed per local day (JAN-258); delegates to `services/stats_service.py` |
| `skill_suggestions.py` | Skill suggestions from repeated chat tool sequences (JAN-407): `GET /api/skills/suggestions` and `POST /api/skills/suggestions/dismiss`; mounted before `skills.py` so `/api/skills/{skill_id}` does not capture it |
| `skills.py` | Skill-library CRUD, community-catalog discovery/import, and LLM design assist |
| `llm_models.py` | `POST /api/planner/llm-models`: per-provider model listing for the AI settings dropdown, and `POST /api/planner/test-llm`: the admin-only connection test with draft settings, which never sends the stored key to a draft base URL (JAN-222); both delegate the credential policy to `services/llm_model_catalog.py` |
| `planner.py` | Planner account management, planner summary endpoints, and system/LLM settings (the LLM connection test lives in `llm_models.py`) |
| `connections.py` | `/api/connections`: one route set for Google and Microsoft through `services/connect_providers.py` — status, `/{provider}/auth-url`, the shared `/{provider}/callback` (also the landing point of Sign in with Google), disconnect, and adding a Gmail/Outlook mailbox on a connected account |
| `planner_sources.py` | Calendar listing and selection for every connected provider (`/api/planner/{provider}/calendars`, one `CalendarSource` per provider) and the on-demand `POST /api/planner/sync` |
| `planner_items.py` | `DELETE /api/planner/local/{tasks,events}/{id}` (JAN-221): deletes one of the caller's local planner items through `services/planner_item_deletion.py`; 404 for a foreign, imported, or missing id. Kept out of `planner.py`, a baselined size hotspot |
| `runtime_tasks.py` | Read-only, owner-scoped Agent Tasks endpoints (JAN-204): the paginated list of trigger runs, definition runs and delegated tasks, and the trigger-run / delegated-task detail with plan steps and tool trace; another user's id is a 404 |
| `runtime_triggers.py` | Authenticated ad-hoc runtime-trigger ingress, the filterable (status/kind/definition/date), paginated run history labelled with each run's definition (JAN-256), and worker health endpoints |
| `trigger_definitions.py` | Authenticated CRUD for saved trigger definitions, "run now", per-definition run history, the list of subscribable event types, and the review side of Janus-proposed automations (proposal list and count, approve, reject; JAN-235) |
| `webhooks.py` | Inbound webhooks (JAN-406): owner-scoped management at `/api/webhooks` (list, create returning the URL path and secret once, patch, rotate-secret, delete) and the public, signature-authenticated delivery `POST /api/webhooks/in/{key}` (body capped while streaming) |
| `a2a.py` | Owner-scoped remote A2A agent registry at `/api/tools/a2a-agents` (JAN-404): list, create (fetches the card), patch (name, URL, `enabled`, write-only token / `clear_token`), `refresh`, delete; the token is never returned. Mounted before `tools.py` |
| `tools.py` | Authenticated management API for the built-in and remote MCP tool registry, the per-user, per-tool switches on `/api/tools/policies`: Unattended Allowed/Off (JAN-255) and, for `confirmation="required"` tools only, In chat `ask`/`always`/`off` (`chat_mode`, JAN-306, JAN-359; `PUT` changes only the fields sent, `422` for a locked tool or a `chat_mode` on a tool without per-call approval), the blanket modes on `/api/tools/policy-modes` (`per_tool`/`allow_all` for unattended runs and chat, JAN-359), and the optional prompt-injection classifier's settings (`/api/tools/injection-classifier`, JAN-289) |
| `knowledge.py` | Knowledge-source endpoints (JAN-242): document upload (10 MB, supported types only, parsed in the request), owner-scoped source listing with type/status filters and chunk counts, and source deletion; knowledge extraction settings, hybrid knowledge search, entity listing and detail, and merge-suggestion accept/reject (JAN-243); the topic/entity graph (`/graph`, JAN-334) |
| `wiki.py` | Wiki endpoints (JAN-244): page list/detail/create/edit, page provenance (`/pages/{id}/provenance`, JAN-334), proposal accept/dismiss, revision restore, and the Wiki synthesis switch |
| `topics.py` | CRUD and status-transition endpoints for structured tracked topics |
| `whatsapp.py` | WhatsApp account management, bounded sync triggers, and conversation/message endpoints |
| `docs.py` | In-app documentation navigation and Markdown content delivery, login required (`get_current_user` on the router, JAN-299); file access and path confinement live in `../services/docs_service.py` (shared with the `docs` agent toolbox) |
| `status.py` | Persisted system-status summary and service detail endpoints, plus `GET /api/status/version` (running version and the release notes since the caller's last seen version) |

## Read-next guidance

- Login/setup/auth cookie behavior -> `auth.py`, then `../services/CODEMAP.md`
- Character constitution routes -> `character.py`, then `../services/character_service.py` and `../character.py` (constitution model); adaptive state routes -> `character.py`, then `../services/character_state_service.py`
- Conversation history -> `conversations.py` (persistence in `../services/conversation_service.py`); per-message tool selection -> `../websocket/chat.py`, then `../runtime_adapter.py`
- Action inbox, device tokens, or popup/phone polling -> `inbox.py`, then `../services/inbox_service.py` and `../services/device_token_service.py`
- Desktop widget summary -> `desktop.py`, then `../services/desktop_service.py`
- Storybook deployment route (`/dev/storybook`) -> `dev.py`, then `../../../frontend/CODEMAP.md`
- Email CRUD or prioritization routes -> `emails.py`, then `../processor.py` or `../services/email_service.py`
- Error grouping or compaction routes -> `errors.py`, then `../services/error_service.py`
- System event log filtering/paging -> `events.py`, then `../event_logger.py` and `../schemas/events.py`
- GitHub integration settings or issue submission -> `github.py`, then `../services/github_service.py`
- Linear idea submission or integration settings -> `linear.py`, then `../services/linear_service.py`
- Web search provider settings -> `web_search.py`, then `../services/web_search_service.py`
- Self-evolution settings and audit view -> `self_evolution.py`, then `../services/self_evolution_service.py`
- Agent memory facts or compaction UI data -> `memory.py`, then `../services/memory_service.py`
- Skill-library CRUD, catalog import, or design assist -> `skills.py`, then `../models/agents.py` and `../services/skill_catalog_service.py`
- Planner sync and provider settings routes -> `planner.py`, then `../planner_service.py`, `../services/planner_account_service.py` (owner-scoped account rows), and `../connectors/CODEMAP.md`
- Runtime-trigger ingress or worker status -> `runtime_triggers.py`, then `../trigger_service.py` and `../trigger_runtime.py`
- The Agent Tasks page's data (persisted task/plan state, JAN-204) -> `runtime_tasks.py`, then `../services/runtime_task_service.py`
- Trigger definitions (briefing/digest/mail built-ins, user automations) -> `trigger_definitions.py`, then `../trigger_definition_service.py` and `../builtin_triggers.py`
- MCP tool registry management -> `tools.py`, then `../services/mcp_service.py`
- Inbound webhooks -> `webhooks.py`, then `../services/webhook_service.py`
- Remote A2A agents -> `a2a.py`, then `../services/a2a_service.py` and `../services/a2a_client.py`
- Tracked-topic routes -> `topics.py`, then `../services/topic_service.py`
- Settings → Stats figures -> `stats.py`, then `../services/stats_service.py` and `../schemas/stats.py`
- Wiki pages and synthesis settings -> `wiki.py`, then `../services/wiki_service.py`, `../services/wiki_synthesis.py`, and `../schemas/wiki.py`
- The knowledge graph or a wiki page's provenance -> `knowledge.py` / `wiki.py`, then `../services/knowledge_provenance.py`
- Document uploads, the knowledge-source list, entities, or merge review -> `knowledge.py`, then `../services/knowledge_sources.py`, `../services/knowledge_graph.py`, `../services/knowledge_extraction.py`, and `../schemas/knowledge.py`
- WhatsApp account, sync, or message routes -> `whatsapp.py`, then `../services/whatsapp_service.py`
- Documentation rendering API -> `docs.py`, then `../services/docs_service.py` and `../../../docs/CODEMAP.md`
- Service-health UI data -> `status.py`, then `../status_service.py`
