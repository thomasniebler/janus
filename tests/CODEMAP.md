# Tests Code Map

Scope: backend and integration-focused tests under `tests/`.

## Responsibilities

- protect authentication, email processing, planner/reminder, status, and migration behavior
- provide regression coverage for schema upgrades and security-sensitive paths
- guard repository tooling (scripts, workflows, structure ratchets)

## Shared infrastructure

| File | Purpose |
| --- | --- |
| `conftest.py` | Shared isolation (runtime/XDG paths, outbound-network guard) and adversarial-email fixtures |
| `asgi_client.py` | In-process ASGI HTTP/WebSocket client that waits for server teardown after each request |
| `runtime_task_fixtures.py` | Seed helpers for Agent Tasks tests (trigger runs, delegations, plan snapshots) |
| `fixtures/` | Static fixtures (gzipped Linear GraphQL schema) |
| `test_asgi_client_teardown.py`, `test_fixture_isolation.py` | Tests for the test harness itself |

## File map

Files are grouped by area; a pattern row covers every matching file.

### Core app, API, auth, storage

| File | Purpose |
| --- | --- |
| `test_api.py` | API-level behavior across the FastAPI surface |
| `test_auth.py` | Auth setup, login, password hashing |
| `test_main.py` | CLI/startup compatibility, worker registration, tray, digest scheduling, config doctor |
| `test_startup.py` | Runtime trigger worker startup/shutdown wiring |
| `test_config.py`, `test_cli_client.py` | User config file; CLI token cache |
| `test_request_origin.py` | Trusted-proxy request-origin reconstruction |
| `test_tenant_isolation.py` | Cross-user data isolation (404 on foreign rows) |
| `test_database_migrations.py` | Alembic migration and schema-upgrade regressions |
| `test_postgres_migrations.py` | Migrations against real PostgreSQL (only with `JANUS_TEST_POSTGRES_URL`) |
| `test_database_concurrency.py` | SQLite engine settings and lock tolerance |
| `test_domain_changes.py` | Domain-change notifications for `/ws/changes` |
| `test_dev_storybook.py` | Admin-gated `/dev/storybook` route |
| `test_error_*.py` | Error-group API and service |
| `test_events_api.py`, `test_event_ownership.py` | Event-log API and per-owner attribution |
| `test_stats.py`, `test_dashboard_service.py` | Activity stats; dashboard briefing context and tasks widget |
| `test_status_service.py`, `test_changelog_service.py` | System status; release notes and version endpoint |
| `test_inbox.py` | Action inbox and device tokens |

### Mail

| File | Purpose |
| --- | --- |
| `test_processor.py` | Email classification and processing flow |
| `test_redos.py` | Regex rule safety (ReDoS) and owner-scoped rule evaluation |
| `test_imap_service.py`, `test_graph_mail_service.py` | IMAP monitor; Outlook (Graph) mailbox polling |
| `test_outlook_mail_flow.py` | Outlook mailbox plumbing (scopes, tokens, worker hand-off, paging) |
| `test_email_service.py`, `test_email_schemas.py` | Outgoing SMTP service; email schema defaults |
| `test_mail_processing_tools.py` | Event-driven mail processing and the mail-processing tools |
| `test_reminder_service.py` | Reminder extraction and persistence |

### LLM and prompt safety

| File | Purpose |
| --- | --- |
| `test_llm_service.py` | Provider construction, extractors, chat, memory suggestion |
| `test_llm_model_catalog.py`, `test_llm_settings.py` | Model dropdown listing; provider settings persistence |
| `test_llm_provider_clients.py` | GitHub Models and Azure OpenAI clients |
| `test_llm_usage.py` | Token usage accounting |
| `test_model_choices.py` | Per-trigger, chat, and utility model choices |
| `test_prompt_context_budgets.py` | Prompt-context compaction under small budgets |
| `test_prompt_injection*.py`, `test_injection_classifier.py` | Injection detector, hostile-input contracts, optional model classifier |
| `test_character*.py` | Character constitution, API, prompt sourcing, adaptive state |

### Chat and WebSockets

| File | Purpose |
| --- | --- |
| `test_websocket.py` | Briefing and chat WebSocket protocol |
| `test_websocket_db_sessions.py`, `test_websocket_closing.py` | WebSocket DB-session lifetime; errors after client disconnect |
| `test_conversations.py`, `test_janus_conversations.py` | Conversation history/threading; Janus-started conversations |
| `test_chat_sqlite_locking.py` | `/ws/chat` SQLite write-lock regression |
| `test_chat_*.py` | `/ws/chat` end-to-end features (tool events, approval, plan events, page context, UI actions/blocks/introspection) |
| `test_tool_call_events.py`, `test_plan_step_events.py`, `test_tool_approval.py` | Unit contracts behind chat tool events, plan steps, approval gate |
| `test_page_context.py`, `test_ui_*.py` | Unit contracts for page context, UI actions, blocks, introspection |
| `test_image_generation.py`, `test_images.py` | Image providers; generated-image storage and API |

### Agent runtime and tools

| File | Purpose |
| --- | --- |
| `test_runtime_adapter.py`, `test_tool_registry.py`, `test_runtime_bridges.py` | Per-step tool authorization and registry |
| `test_plan_graph.py`, `test_plan_state.py`, `test_agent_runtime.py` | Plan graph, plan-state persistence, plan-driven runtime |
| `test_agent_task_runner.py` | Bounded human-absent tool-calling loop |
| `test_toolset_selection.py` | Model-selected toolsets and fallbacks |
| `test_tool_policy*.py` | Unattended/in-chat tool switches and modes |
| `test_single_agent_ungated_tools.py` | Tools ungated for the single agent |
| `test_agent_tools.py` | `build_tool_set` per toolbox and connection gating |
| `test_agent_*_tools.py`, `test_agent_toolbox_triggers.py` | Event-log, rule, and trigger toolboxes |
| `test_delegation*.py`, `test_conversation_delegations.py` | Delegation, wait/resume, conversation delegation API |
| `test_runtime_tasks.py` | Agent Tasks read model and API |
| `test_docs_tools.py`, `test_shell_tool.py`, `test_coding_agents_tool.py` | Docs, shell, and coding-agent tools |
| `test_web_search_tool.py`, `test_web_fetch_tool.py` | Web search and fetch tools |
| `test_skill_*.py`, `test_skills_api.py` | Skill catalog, skill suggestions, skills API |
| `test_mcp_*.py`, `test_remote_mcp_tools.py` | MCP registry, API, static tokens, migration, remote tools |
| `test_a2a_agents.py` | Remote A2A agents |
| `test_self_evolution.py` | Autonomous self-evolution loop |

### Triggers

| File | Purpose |
| --- | --- |
| `test_trigger_adapters.py`, `test_triggers.py`, `test_trigger_service.py` | Trigger envelopes, dispatch, durable storage |
| `test_trigger_runtime.py`, `test_runtime_trigger_handler.py` | Trigger-to-runtime wiring and plan handlers |
| `test_runtime_trigger_api.py` | Runtime-trigger ingress and run API |
| `test_trigger_definitions.py` | Saved definitions, schedules, scheduler tick |
| `test_trigger_proposals.py` | Janus-proposed definitions and approvals |
| `test_trigger_event_filters.py`, `test_connector_trigger_events.py` | Event filters/rate limits; WhatsApp and planner events |
| `test_trigger_loop_guard.py` | Chain guard on unattended follow-ups |
| `test_builtin_triggers.py` | Built-in briefing, digest, mail processing |
| `test_secretary.py` | Proactive secretary built-in |
| `test_webhooks.py` | Inbound webhooks |

### Memory, topics, knowledge, planner

| File | Purpose |
| --- | --- |
| `test_memory_service.py`, `test_topic_service.py` | Memory facts and `/api/memory`; topic selection |
| `test_knowledge_*.py`, `test_wiki.py` | Knowledge layer: sources, graph, retrieval, provenance, index; wiki |
| `test_local_planner_service.py`, `test_local_task_capability.py` | Local planner and task hierarchy |
| `test_planner_sync_utc.py` | Planner sync stores UTC |

### Connectors

| File | Purpose |
| --- | --- |
| `test_google_*.py` | Google OAuth, token exchange, RFC3339 bounds |
| `test_jira_connector.py` | Jira connector |
| `test_linear_*.py` | Linear GraphQL client and toolbox |
| `test_microsoft_*.py` | Microsoft Graph, sign-in, tools, CLI |
| `test_azure.py` | Azure ARM client and tool gating |
| `test_whatsapp_*.py` | WhatsApp connector, sending, service, tools |
| `test_telegram.py` | Telegram connector |
| `test_connections.py` | Connected Google/Microsoft accounts: status, mailboxes, disconnect |

### Deployment, update, repository tooling

| File | Purpose |
| --- | --- |
| `test_update_*.py`, `test_systemd_service.py` | Auto-update (git and tool installs, boot recovery); `janus service` |
| `test_deployment_capabilities.py`, `test_release_*.py`, `test_ops_metrics.py` | Deployment probes and post-release checks |
| `test_workflows.py`, `test_ci_paths.py`, `test_actions_cost_report.py` | GitHub Actions policy and CI scripts |
| `test_openapi_export.py`, `test_coverage_gate.py`, `test_dependency_audit.py`, `test_semgrep_scan.py` | Script wrappers for OpenAPI, coverage, audits, Semgrep |
| `test_quality_report.py`, `test_structural_review.py` | Scheduled quality reports |
| `test_complexity_baseline.py`, `test_code_quality_structure.py`, `test_import_contracts.py` | Structure ratchets and import-layer contracts |
| `test_docs_links.py` | Tech-spec Markdown link checks |
| `test_todo_chat_installer.py` | Todo Chat installer script |

## Read-next guidance

- Bug fix in existing behavior -> the matching test file first
- Migration or startup issue -> `test_database_migrations.py`, `test_postgres_migrations.py`, `test_main.py`
- Chat WebSocket feature -> `test_websocket.py`, then the matching `test_chat_*.py` and its unit-level partner
- Tool authorization or unattended policy -> `test_tool_policy.py`, `test_runtime_adapter.py`, `test_tool_registry.py`
- Triggers or built-ins -> `test_trigger_definitions.py`, `test_builtin_triggers.py`, `test_trigger_runtime.py`
- Regex rules or mail flow -> `test_redos.py`, `test_processor.py`, `test_mail_processing_tools.py`
- Prompt injection -> `test_prompt_injection.py`, then `test_prompt_injection_contracts.py`
- Knowledge or semantic search -> `test_knowledge_index.py`, then the other `test_knowledge_*.py`
- Behavior details -> `../docs/docs/tech-spec/`
