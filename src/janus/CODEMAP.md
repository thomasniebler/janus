# Janus Package Code Map

Scope: the main Python package in `src/janus/`.

## Responsibilities

- define the FastAPI app and Typer CLI entrypoint
- hold persistence, security, domain services, and background worker orchestration
- run the plan-driven agent runtime, triggers, and delegation

Behavior detail lives in [`docs/docs/tech-spec/`](../../docs/docs/tech-spec/), especially
[`background-workers-runtime.md`](../../docs/docs/tech-spec/background-workers-runtime.md).

## Import boundaries

Enforced by import-linter (`task lint:imports`, contracts in `pyproject.toml`); see
[`architecture-overview.md`](../../docs/docs/tech-spec/architecture-overview.md#cross-cutting-architecture-rules).

- Only `main.py`, `app_factory.py`, and `cli.py` may import `api/` and `websocket/`.
- `models/`, `models_knowledge.py`, `database.py`, and `schemas/` depend on no behavior modules.
- `services/` does not import `agent_toolboxes/` or the runtime, except the exceptions declared in `pyproject.toml`.
- `connectors/` is a leaf: no services, toolboxes, runtime, or workers.

## Child maps

| Path | Responsibility |
| --- | --- |
| `api/` | Thin FastAPI routers -> [`api/CODEMAP.md`](api/CODEMAP.md) |
| `services/` | Focused domain services used by routers, runtime, and workers -> [`services/CODEMAP.md`](services/CODEMAP.md) |
| `models/` | SQLAlchemy ORM schema, one module per domain -> [`models/CODEMAP.md`](models/CODEMAP.md) |
| `connectors/` | Planner provider connectors and Graph/Azure clients -> [`connectors/CODEMAP.md`](connectors/CODEMAP.md) |

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `main.py` | Compatibility entrypoint re-exporting the CLI, app, startup helpers, and WebSocket handlers |
| `app_factory.py` | FastAPI app factory: middleware, router registration, static/frontend serving, WebSocket registration |
| `startup.py` | One-time runtime initialization shared by CLI start and API startup (trigger worker, knowledge index) |
| `cli.py` | Typer CLI command tree |
| `cli_*.py` | CLI sub-commands mounted by `cli.py`: `service` (systemd), `update`, `microsoft` login; `cli_client.py` talks to the API |
| `websocket/` | WebSocket endpoints: `briefing.py`, `chat.py` (streamed chat, tool approvals, page actions, UI blocks), `chat_inbox.py`, `changes.py` (`/ws/changes`), `closing.py` |
| `workers/` | Background worker loops: email, planner, WhatsApp, Telegram, error compaction, auto-update |
| `worker_manager.py` | Supervises background worker threads |
| `desktop/` | Optional desktop tray icon |
| `schemas/` | Pydantic request/response models shared across routers |
| `database.py` | SQLAlchemy engine/session setup and schema upgrade helpers |
| `models_*.py` | Feature ORM modules re-exported through `models/`: character state, images, knowledge, safety classifier, self-evolution, toolsets |
| `config.py` | Application settings via `pydantic-settings` |
| `security.py` | Auth dependencies, password hashing, credential encryption, request/WebSocket identity |
| `request_origin.py` | Trusted proxy headers and external request origin |
| `resources.py` | Paths of shipped files (Alembic config, frontend dist, docs) for wheel and repo installs |
| `runtime.py` | Runtime path preparation, server logging, single-instance lock |
| `processor.py` | Email classification: regex rules, hand-off to mail trigger definitions, LLM fallback, attachment staging |
| `imap_service.py` | IMAP monitor lifecycle, syncing, attachment extraction |
| `graph_mail_service.py` | Outlook mailbox polling monitor over Microsoft Graph |
| `reminder_service.py` | Reminder memory and calendar-style reminder materialization |
| `planner_service.py` | Planner sync orchestration over `connectors/` (Google, Jira, Microsoft) |
| `planner_trigger_events.py`, `whatsapp_trigger_events.py` | Change detection and publishing of `planner.*` / `whatsapp.*` trigger events |
| `whatsapp_neonize.py` | Neonize WhatsApp pairing and live per-account connector |
| `llm_service.py` | LLM backend selection, JSON extractors, chat streaming, memory suggester |
| `llm_usage.py` | LLM token accounting per user, day, surface, and model |
| `agent_runtime.py` | Janus system prompt and the durable `PlanDrivenRuntime` |
| `plan_graph.py`, `plan_state.py` | Plan-graph contracts (no LLM/DB) and their durable persistence |
| `runtime_adapter.py` | `RuntimeAdapter`: single entrypoint turning a task/message into a plan step with an authorized toolset and confirmation gate |
| `agent_tools.py` | `DEFAULT_TOOL_REGISTRY`: one entry per tool capability, gated by connection state and role |
| `tool_registry.py` | Toolbox registry metadata and task-scoped tool selection |
| `toolset_selection.py` | Model-selected, task-scoped toolsets narrowed to the authorized set |
| `tool_policy.py` | Per-user per-tool unattended and in-chat switches, blanket modes, chat-only locked tools |
| `capability_profile.py` | Per-user capability signal (live connections, providers) for the runtime's capability gate |
| `prompt_injection.py` | Prompt-injection detection, untrusted-output spotlighting, shared untrusted-content policy text |
| `character.py` | Character constitution, adaptive trait state, and its system-prompt rendering |
| `agent_toolboxes/` | One module per tool collection (mail, calendar, planner, memory, knowledge, triggers, UI, integrations...); `shared.py`/`common.py` helpers |
| `agent_task_runner.py` | Bounded tool-calling loop for human-absent runs |
| `unattended_execution.py` | Shared authorize-and-execute path for one human-absent plan step |
| `triggers.py`, `trigger_adapters.py` | Trigger envelope types and adapters for scheduled/event activations |
| `trigger_service.py` | Durable `RuntimeTrigger` storage and execution claims |
| `trigger_runtime.py` | Trigger handler executing runs through the runtime; inline runs for briefing/CLI |
| `runtime_trigger_handler.py` | Guarded adapter from durable triggers into the runtime (resume/retry) |
| `trigger_definition_service.py` | Saved trigger definitions: validation, scheduling tick, event fan-out, run enqueueing |
| `trigger_events.py` | Trigger event catalog (mail, WhatsApp, planner, webhook events) |
| `trigger_event_filters.py` | Payload filters and rate limits on event definitions |
| `trigger_proposal_service.py`, `trigger_approval.py` | Janus-proposed automations, user approval, approval fingerprint |
| `builtin_triggers.py` | Seeded built-in definitions (briefing, digest, mail, Secretary, knowledge) with prepare/finalize hooks |
| `delegation_service.py`, `delegation_runtime.py`, `delegation_wait.py` | Delegated child tasks: records and caps, execution on a thread pool, parent waiting |
| `background_tasks.py` | Minimal submit/status abstraction over thread workers |
| `domain_changes.py` | Owner-scoped domain-change notifications feeding `/ws/changes` |
| `status_service.py` | Persisted system-health aggregation and LLM probe |
| `notifications.py`, `event_logger.py`, `time_utils.py` | Notifications, structured event log, time helpers |

## Read-next guidance

- HTTP route or response shape -> `api/CODEMAP.md`
- App boot or router wiring -> `app_factory.py`, then `startup.py`
- Auth or sessions -> `security.py`, then `services/CODEMAP.md`
- Email ingestion/classification -> `processor.py`, `imap_service.py`, `graph_mail_service.py`
- Planner provider or sync -> `connectors/CODEMAP.md`, then `planner_service.py`
- Runtime, tool authorization, or prompt -> `runtime_adapter.py`, `agent_runtime.py`, `tool_registry.py`, `tool_policy.py`, `agent_toolboxes/`
- Triggers, schedules, events, built-ins -> `trigger_definition_service.py`, `trigger_events.py`, `builtin_triggers.py`, `trigger_runtime.py`
- Janus-proposed automations -> `trigger_proposal_service.py`, `trigger_approval.py`, `agent_toolboxes/automation_proposals.py`
- Delegated tasks -> `delegation_*.py`
- Chat socket behavior -> `websocket/chat.py`
- Live UI refresh after a write -> `domain_changes.py`, then `websocket/changes.py`
- Background workers -> `workers/`, `worker_manager.py`
- Tests -> [`tests/CODEMAP.md`](../../tests/CODEMAP.md)
