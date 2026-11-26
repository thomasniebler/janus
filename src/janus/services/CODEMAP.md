# Service Layer Code Map

Scope: domain services under `src/janus/services/`. Routers, toolboxes, and workers call these;
behavior detail lives in `docs/docs/tech-spec/` and module docstrings.

## Responsibilities

- reusable, owner-scoped business logic so routers stay thin
- external clients (LLM providers, GitHub, Linear, Google, Microsoft, MCP, A2A, Telegram, WhatsApp)
- the knowledge stack (sources, index, graph, retrieval, wiki)
- chat-turn plumbing (WebSocket frames, approvals, events) and unattended-tool fences

## File map

| File | Purpose |
| --- | --- |
| `base.py` | Shared service base patterns |
| `auth_service.py` | Password hashing, JWT, setup tokens, Fernet-encrypted settings |
| `email_service.py` | Email, account, and rule persistence; owner-scoped email queries and sending |
| `conversation_service.py` | Chat conversations: CRUD, delegation listing, Janus-started conversations |
| `dashboard_service.py` | Dashboard aggregates and per-user widget preferences |
| `desktop_service.py` | Counts for the desktop widget |
| `inbox_service.py` | Action inbox aggregation and its device-safe actions |
| `device_token_service.py` | Device tokens for the inbox (create, revoke, authenticate) |
| `stats_service.py` | Settings → Stats figures (activity, approvals, LLM usage) |
| `error_service.py` | Error-group persistence and compaction |
| `event_service.py` | System event log queries and the shared visibility rule |
| `character_service.py` | Character constitution profile persistence |
| `character_state_service.py` | Adaptive character state, proposals, audit, rollback |
| `memory_service.py` | Durable memory facts: storage, search, compaction |
| `topic_service.py`, `topic_text.py` | Tracked topics: persistence, extraction, text helpers |
| `secretary_service.py` | Proactive Secretary built-in: prepare gate, change detection, alert caps |
| `runtime_task_service.py` | Read model of trigger runs and delegated tasks for the Agent Tasks page |
| `toolset_store.py` | Persisted per-task tool selections (`runtime_tool_selections`) |
| `skill_catalog_service.py` | Remote GitHub-hosted `SKILL.md` catalog |
| `skill_proposer.py` | Skill suggestions from repeated tool routines in chats |
| `docs_service.py` | Confined read-only access to `docs/docs` for the API and `docs` toolbox |
| `database_copy_service.py` | `janus db copy` between databases (e.g. SQLite to PostgreSQL) |
| `systemd_unit.py` | `janus service` systemd user unit |
| `update_service.py`, `update_tool_install.py`, `update_boot.py` | Self-update: release check, git-checkout / `uv tool` / `pipx` install, restart and rollback |
| `changelog_service.py` | Parses `CHANGELOG.md` into "what is new" notes |
| **Chat turn** | |
| `page_context.py` | Parses inbound chat frames and renders the untrusted page context block |
| `tool_call_events.py` | `__TOOL_EVENT__` frames, redaction, stored tool-call rows |
| `plan_step_events.py` | `__PLAN_EVENT__` plan/step timeline of a chat turn |
| `tool_approval.py` | Per-call chat approval broker for confirmation-required tools |
| `ui_blocks.py`, `ui_actions.py`, `ui_introspection.py` | Generative UI blocks, page actions, page introspection: validation, frames, brokers |
| `image_generation.py`, `image_store.py` | Cloud image providers; generated-image storage and quota |
| **LLM and models** | |
| `llm_settings.py` | Per-provider LLM, embedding, and image settings updates |
| `llm_model_catalog.py` | Provider model listing for the AI settings dropdown |
| `llm_provider_clients.py` | Chat-client builders (GitHub Models, Azure OpenAI) and API routing |
| `azure_deployment_models.py` | Cached lookup of the model behind an Azure deployment |
| `azure_auth_service.py` | `DefaultAzureCredential` wrapper for ARM tokens |
| `embedding_service.py` | Builds the configured embeddings client |
| `model_locality.py` | Whether a model choice is local or cloud |
| `injection_classifier.py` | Optional model-based prompt-injection classifier |
| **Knowledge** | |
| `knowledge_sources.py` | Knowledge sources and chunks; ingestion tick and backfill entry point |
| `knowledge_discovery.py`, `knowledge_parsing.py`, `knowledge_chunking.py` | Finding source rows, document conversion, chunking |
| `knowledge_index.py`, `vector_index.py` | Index sync and hybrid search; sqlite-vec / FTS5 adapters |
| `knowledge_graph.py`, `knowledge_extraction.py` | Entity graph and the Knowledge extraction built-in |
| `knowledge_retrieval.py`, `knowledge_provenance.py` | Hybrid chunk retrieval; citation provenance and topic/entity graph views |
| `wiki_service.py`, `wiki_synthesis.py` | Wiki pages and the Wiki synthesis built-in |
| **Integrations** | |
| `github_service.py` | GitHub REST/GraphQL for issues, PRs, and merge gates |
| `linear_service.py`, `linear_queries.py` | Linear GraphQL client and its query documents |
| `local_planner_service.py` | Janus-owned planner CRUD and unified planner reads |
| `planner_account_service.py` | Owner-scoped planner accounts and the Google calendar picker |
| `planner_item_deletion.py` | Deleting local planner tasks and events with their links |
| `connect_providers.py` | Registry of connectable providers (Google, Microsoft) |
| `oauth_connection_service.py` | Per-user `OAuthConnection` rows and shared flow state |
| `google_client.py`, `google_oauth.py`, `google_connect_service.py` | Google OAuth client config, code exchange, "Connect Google" |
| `microsoft_auth_service.py`, `microsoft_connect_service.py` | Microsoft Graph sign-in (CLI) and per-user "Connect Microsoft" |
| `mailbox_oauth_service.py` | Gmail/Outlook mailboxes on a connected account |
| `mcp_service.py` | Remote MCP registry: lifecycle, transport, OAuth |
| `mcp_static_token.py`, `mcp_errors.py`, `mcp_metadata.py`, `mcp_status.py` | MCP static tokens, errors, tool metadata, discovery status |
| `a2a_client.py`, `a2a_service.py` | Agent2Agent client and remote agent registry |
| `webhook_service.py` | Inbound webhooks: endpoints and signed delivery |
| `telegram_service.py` | Telegram bot connector |
| `whatsapp_service.py`, `whatsapp_setup_service.py` | WhatsApp accounts, messages, sending; QR pairing sessions |
| **Agent tool backends** | |
| `web_search_service.py` | SearXNG/Brave search for `web_search` |
| `web_fetch_service.py`, `web_page_text.py` | SSRF-fenced page fetch and HTML-to-text for `web_fetch` |
| `shell_service.py` | Command runner for `shell` |
| `coding_agent_service.py` | Local coding-agent CLIs for `coding_agents` |
| `self_evolution_*.py` | Self-evolution loop: settings and caps, intake, merge gates, provenance, post-release checks |

## Where to look

- Owner scoping of a domain object -> its service above, then `../api/CODEMAP.md` for the router
- Tool behind an agent toolbox -> the service above, then `../agent_toolboxes/`
- Chat WebSocket frames -> the chat-turn services, then `../websocket/chat.py`
- Knowledge search -> `knowledge_retrieval.py` and `knowledge_index.py`; ingestion -> `knowledge_sources.py`
- Built-in trigger work (Secretary, extraction, wiki) -> the service, then `../builtin_triggers.py`
- Prompt-injection handling -> `injection_classifier.py`, then `../prompt_injection.py`
