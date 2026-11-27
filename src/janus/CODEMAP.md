# Janus Package Code Map

Main Python package; behavior lives in [`docs/docs/tech-spec/`](../../docs/docs/tech-spec/).
Import boundaries (import-linter, contracts in `pyproject.toml`): [architecture-overview](../../docs/docs/tech-spec/architecture-overview.md#cross-cutting-architecture-rules).

- [`api/`](api/CODEMAP.md): HTTP routers
- [`services/`](services/CODEMAP.md): domain services
- [`models/`](models/CODEMAP.md): ORM schema
- [`connectors/`](connectors/CODEMAP.md): planner and Microsoft Graph clients

| Area | Files |
| --- | --- |
| Entry points | `main.py` (re-exports), `app_factory.py` (app and router wiring), `startup.py` (shared init), `cli.py`, `cli_*.py`, `websocket/` |
| Agent runtime | `runtime_adapter.py` (task -> authorized toolset), `agent_runtime.py` (system prompt), `plan_graph.py`, `plan_state.py`, `agent_tools.py` (tool entries), `tool_registry.py`, `toolset_selection.py`, `tool_policy.py` (unattended/in-chat switches), `capability_profile.py`, `prompt_injection.py`, `character.py`, `agent_toolboxes/`, `agent_task_runner.py`, `unattended_execution.py` |
| Triggers | `triggers.py`, `trigger_*.py`, `runtime_trigger_handler.py`, `builtin_triggers.py`, `planner_trigger_events.py`, `whatsapp_trigger_events.py` |
| Delegation | `delegation_*.py` |
| Mail | `processor.py` (rules, classification), `imap_service.py`, `graph_mail_service.py` (Outlook) |
| Planner | `planner_service.py`, `reminder_service.py` |
| WhatsApp | `whatsapp_neonize.py`, `whatsapp_translate.py` |
| LLM | `llm_service.py`, `llm_usage.py` |
| Persistence | `database.py`, `models_*.py` (re-exported by `models/`), `schemas/` (Pydantic) |
| Workers | `workers/`, `worker_manager.py`, `background_tasks.py` |
| Config/infra | `config.py`, `security.py` (auth, encryption), `request_origin.py`, `resources.py` (shipped-file paths), `runtime.py` (paths, logging, lock), `status_service.py`, `domain_changes.py` (feeds `/ws/changes`), `notifications.py`, `event_logger.py`, `time_utils.py`, `desktop/` (tray) |
