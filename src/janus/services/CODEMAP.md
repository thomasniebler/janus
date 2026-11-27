# Service Layer Code Map

Owner-scoped domain logic behind `../api/`, `../agent_toolboxes/`, and the workers; behavior lives in docstrings and `../../../docs/docs/tech-spec/`.

| Area | Files |
| --- | --- |
| Core domain | `auth_service.py`, `email_service.py`, `mailbox_service.py`, `conversation_service.py`, `dashboard_service.py`, `desktop_service.py`, `inbox_service.py`, `device_token_service.py`, `stats_service.py`, `error_service.py`, `event_service.py`, `memory_service.py`, `memory_curation.py` (memory index, merged-fact review state), `memory_review.py` (nightly Memory review built-in), `topic_*.py`, `character_*.py`, `docs_service.py`, `changelog_service.py`, `base.py` (shared base patterns) |
| Agent runs | `runtime_task_service.py` (Agent Tasks read model), `toolset_store.py` (persisted per-task tool selections), `secretary_service.py` (proactive Secretary built-in), `skill_catalog_service.py`, `skill_proposer.py` |
| Chat turn | `page_context.py` (inbound frames, page context), `tool_call_events.py`, `plan_step_events.py`, `tool_approval.py` (per-call approval broker), `ui_*.py`, `image_*.py` |
| LLM | `llm_*.py`, `azure_*.py`, `embedding_service.py`, `model_locality.py`, `injection_classifier.py` |
| Knowledge | `knowledge_*.py` (entry: `knowledge_sources.py`), `vector_index.py`, `wiki_*.py` |
| Planner | `local_planner_service.py`, `planner_*.py` |
| Integrations | `connect_providers.py` (connectable-provider registry), `oauth_connection_service.py`, `google_*.py`, `microsoft_*.py`, `mailbox_oauth_service.py`, `github_service.py`, `linear_*.py`, `mcp_*.py` (entry: `mcp_service.py`), `a2a_*.py`, `webhook_service.py`, `telegram_service.py`, `whatsapp_*.py` |
| Tool backends | `web_*.py`, `shell_service.py`, `coding_agent_service.py`, `self_evolution_*.py` |
| Operations | `update_*.py` (self-update), `systemd_unit.py` (`janus service`), `database_copy_service.py` (`janus db copy`) |

## Where to look

- Router for a service -> `../api/CODEMAP.md`; toolbox using it -> `../agent_toolboxes/`
- Chat WebSocket frames -> the chat-turn files, then `../websocket/chat.py`
- Built-in trigger work (Secretary, extraction, wiki) -> the service, then `../builtin_triggers.py`
