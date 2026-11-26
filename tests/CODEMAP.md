# Tests Code Map

Backend pytest suite: app, runtime, connectors, and repository tooling. Behavior lives in `../docs/docs/tech-spec/`.

| Area | Files |
| --- | --- |
| Harness | `conftest.py` (isolation, outbound-network guard, adversarial-email fixtures), `asgi_client.py` (in-process ASGI HTTP/WebSocket client), `runtime_task_fixtures.py` (Agent Tasks seed helpers), `fixtures/`, `test_asgi_client_teardown.py`, `test_fixture_isolation.py` |
| App, API, auth | `test_api.py`, `test_auth.py`, `test_main.py` (CLI, startup, config doctor), `test_startup.py`, `test_config.py`, `test_cli_client.py`, `test_request_origin.py`, `test_tenant_isolation.py`, `test_dev_storybook.py`, `test_domain_changes.py`, `test_inbox.py` |
| Storage | `test_database_*.py`, `test_postgres_migrations.py` (runs only with JANUS_TEST_POSTGRES_URL set) |
| Status, events | `test_error_*.py`, `test_event*.py`, `test_stats.py`, `test_dashboard_service.py`, `test_status_service.py`, `test_changelog_service.py` |
| Mail | `test_processor.py`, `test_redos.py` (regex rule safety), `test_imap_service.py`, `test_graph_mail_service.py`, `test_outlook_mail_flow.py`, `test_email_*.py`, `test_mail_processing_tools.py`, `test_reminder_service.py` |
| LLM, prompt safety | `test_llm_*.py`, `test_model_choices.py`, `test_prompt_*.py`, `test_injection_classifier.py`, `test_character*.py` |
| Chat, WebSockets | `test_websocket*.py`, `test_conversation*.py`, `test_janus_conversations.py`, `test_chat_*.py` (end to end over the chat socket), `test_page_context.py`, `test_ui_*.py`, `test_image*.py` |
| Agent runtime, tools | `test_agent_*.py`, `test_runtime_adapter.py`, `test_runtime_bridges.py`, `test_runtime_tasks.py`, `test_tool*.py` (policy, registry, approval), `test_plan_*.py`, `test_single_agent_ungated_tools.py`, `test_delegation*.py`, `test_docs_tools.py`, `test_shell_tool.py`, `test_coding_agents_tool.py`, `test_web_*.py`, `test_skill*.py`, `test_mcp_*.py`, `test_remote_mcp_tools.py`, `test_a2a_agents.py`, `test_self_evolution.py` |
| Triggers | `test_trigger*.py`, `test_runtime_trigger_*.py`, `test_connector_trigger_events.py`, `test_builtin_triggers.py`, `test_secretary.py`, `test_webhooks.py` |
| Memory, knowledge, planner | `test_memory_service.py`, `test_topic_service.py`, `test_knowledge_*.py`, `test_wiki.py`, `test_local_*.py`, `test_planner_*.py` |
| Connectors | `test_google_*.py`, `test_jira_connector.py`, `test_linear_*.py`, `test_microsoft_*.py`, `test_azure.py`, `test_whatsapp_*.py`, `test_telegram.py`, `test_connections.py` |
| Deploy, update | `test_update_*.py`, `test_systemd_service.py`, `test_deployment_capabilities.py`, `test_release_*.py`, `test_ops_metrics.py`, `test_todo_chat_installer.py` |
| Repo tooling, CI | `test_workflows.py`, `test_ci_paths.py`, `test_actions_cost_report.py`, `test_openapi_export.py`, `test_coverage_gate.py`, `test_dependency_audit.py`, `test_semgrep_scan.py`, `test_quality_report.py`, `test_structural_review.py`, `test_complexity_baseline.py`, `test_code_quality_structure.py`, `test_import_contracts.py`, `test_docs_links.py` |

- A chat feature has an end-to-end test in `test_chat_*.py` and a unit-level partner among `test_tool*.py`, `test_plan_step_events.py`, `test_ui_*.py`, `test_page_context.py`.
