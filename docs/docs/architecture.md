# Architecture

## Module overview

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                              EXTERNAL SYSTEMS                                  ║
║                                                                                 ║
║  ┌──────────────┐  ┌─────────────────┐  ┌──────────────┐  ┌──────────────────┐   ║
║  │  IMAP Server │  │ LLM: Ollama/OAI/│  │Google Calendar│  │  Jira            │   ║
║  │  (email)     │  │ OpenRouter/GHM/ │  │  / Tasks     │  │  (planner;       │   ║
║  │              │  │ Gemini/Claude/Az│  │  (planner)   │  │  AzDO/O365: TODO)│   ║
║  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   ║
╚═════════│════════════════│════════════════════│═════════════════════│═══════════╝
          │                │                    │                     │
          ▼                ▼                    ▼                     ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║                         BACKGROUND WORKERS (daemon threads)                     ║
║                                                                                 ║
║  ┌────────────────────┐          ┌────────────────────────────────────────────┐ ║
║  │   imap_service.py  │          │             planner_service.py             │ ║
║  │  IMAPMonitor       │          │  PlannerService (factory)                  │ ║
║  │  · IDLE/FETCH      │          │  · connectors/google.py                    │ ║
║  │  · one thread/acct │          │  · connectors/jira.py                      │ ║
║  └────────┬───────────┘          │  · syncs every 30 min                      │ ║
║           │ email_callback()     └────────────────┬───────────────────────────┘ ║
║           ▼                                       │ daily context               ║
╚══════════════════════════════════════════════════╪══════════════════════════════╝
                                                   │
          ┌────────────────────────────────────────┘
          ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║                            CORE PROCESSING                                      ║
║                                                                                 ║
║  ┌────────────────────┐    ┌──────────────────────┐    ┌──────────────────────┐ ║
║  │   processor.py     │    │    llm_service.py     │    │  reminder_service.py │ ║
║  │  EmailProcessor    │───▶│  LangChain wrapper    │    │  extraction +        │ ║
║  │  · regex rules     │    │  Ollama/OAI/Gemini/   │    │  scheduling          │ ║
║  │  · LLM fallback    │    │  OpenRouter/Anthropic │    └──────────────────────┘ ║
║  │  · IMPORTANT/      │    │  · chat stream        │                            ║
║  │    DIGEST/IGNORE   │    │  · JSON extractors    │    ┌──────────────────────┐ ║
║  └────────────────────┘    │  · agent tools        │    │   agent_tools.py     │ ║
║                             └──────────────────────┘    │  · registry-backed  │ ║
║  ┌────────────────────┐                                 │  · connection gates │ ║
║  │   agent_runtime.py │◀────────────────────────────────│  · ordered selection │ ║
║  │  trigger runs,     │                                 └──────────────────────┘ ║
║  │  delegation        │                 ┌──────────────────────┐                  ║
║  └────────────────────┘                 │ agent_toolboxes/     │                  ║
║                                         │ · common/mail        │                  ║
║                                         │ · calendar/tasks     │                  ║
║                                         │ · memory/delegation  │                  ║
║                                         │ · skills/triggers    │                  ║
║                                         └──────────────────────┘                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
          │
          ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║                           SERVICE LAYER (src/janus/services/)                   ║
║                                                                                 ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  ║
║  │ auth_service │  │ email_service│  │memory_service│  │skill_catalog_service│  ║
║  │ · JWT/bcrypt │  │ · CRUD       │  │ · user facts │  │ · skill CRUD        │  ║
║  │ · Fernet enc │  │ · priorities │  │ · per-user   │  │ · name normalization│  ║
║  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────────┘  ║
║  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────────┐   ║
║  │linear_service│  │github_service│  │       character_service               │   ║
║  │ · Linear API │  │ · PR / issue │  │  · constitution + memory rules        │   ║
║  └──────────────┘  └──────────────┘  └──────────────────────────────────────┘   ║
║  ┌─────────────────────────────────────────────────────────────────────────┐     ║
║  │ dashboard_service  · widget preferences  · prompt overrides             │     ║
║  └─────────────────────────────────────────────────────────────────────────┘     ║
╚══════════════════════════════════════════════════════════════════════════════════╝
          │
          ▼
╔══════════════════════════════════════════════════════════════════════════════════╗
║              APP COMPOSITION  (app_factory.py + websocket/ + startup.py)       ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │        app_factory.py mounts REST API routers from api/ and SPA          │    ║
║  │  /api/auth  /api/emails  /api/planner  /api/runtime /api/conversations  /api/skills  /api/memory  │    ║
║  │  /api/dashboard  /api/docs  /api/status  /api/github  /api/linear                │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║  ┌──────────────────────────────────────────┐                                   ║
║  │      websocket/ registers endpoints       │                                   ║
║  │  /ws/briefing  (briefing run)             │                                   ║
║  │  /ws/chat      (conversation)             │                                   ║
║  │  /ws/changes   (live domain changes)      │                                   ║
║  └──────────────────────────────────────────┘                                   ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  startup.py · security.py · request_origin.py · database.py             │    ║
║  │  cli.py starts worker_manager.py threads and optional desktop tray      │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
╚══════════════════════════════════════════════════════════════════════════════════╝
          │                          │
          ▼                          ▼
╔══════════════════════╗    ╔══════════════════════════════════════════════════╗
║   SQLite (janus.db)  ║    ║           CLIENTS                                ║
║   SQLAlchemy 2.0     ║    ║                                                  ║
║   Alembic migrations ║    ║  ┌────────────────┐  ┌──────────┐  ┌─────────┐  ║
║   models/            ║    ║  │  React/TS SPA  │  │ iOS app  │  │  CLI    │  ║
╚══════════════════════╝    ║  │  frontend/     │  │ ios/     │  │ Typer   │  ║
                             ║  │  TanStack Query│  │ SwiftUI  │  │ janus   │  ║
                             ║  │  Vite/Tailwind │  │          │  │ brief/  │  ║
                             ║  └────────────────┘  └──────────┘  │ digest  │  ║
                             ║                                     └─────────┘  ║
                             ╚══════════════════════════════════════════════════╝
```

## Key flows

**Email ingestion**
: IMAP IDLE thread → `processor.py` (the receiving account owner's regex rules first; JAN-295) → if the owner has an enabled `mail.received` trigger definition and the trigger worker is on: `EmailRecord` stored (provisional DIGEST) → `trigger_definition_service.emit_event()` → one trigger run per definition, which classifies and records reminders/topics/planner suggestions through the `mail.processing` tools; otherwise the synchronous LLM classifier and JSON extractors → `EmailRecord` persisted → notification if IMPORTANT

**Briefing / digest**
: the built-in **Morning briefing** and **Afternoon digest** trigger definitions (`builtin_triggers.py`) run on their schedules through the durable trigger worker; `/ws/briefing` sends today's completed briefing run or runs it inline (`trigger_runtime.run_definition_inline`), and `janus brief` / `janus digest` run them inline from the CLI

**Chat**
: WebSocket `/ws/chat` → `RuntimeAdapter.prepare_step()` authorizes a fresh toolset per message → `llm_service` streams the reply as the user's character → post-turn memory suggestion

**Live UI refresh**
: any committed write to a tracked model (REST, chat tool, trigger run, worker) → `domain_changes.py` session hooks record the owner's domains → `after_commit` publishes to the in-process bus → `/ws/changes` sends `{"type": "changed", "domains": [...]}` → the tab invalidates the mapped TanStack Query keys

**Agent tools (MCP)**
: LLM call → `RuntimeAdapter.prepare_step()` intersects the task's allowlist (a fixed per-mode list for chat and the system pipelines, the owner's unattended switch for unattended runs), `DEFAULT_TOOL_REGISTRY` entries (chat adds the user's offered remote MCP tools per message, JAN-343) enabled for the user's real connections, granted capabilities, and the confirmation gate (the unattended switch for unattended runs) → toolbox builders → service layer → DB read/write → result returned to LLM (a failing tool answers `ERROR`, a switched-off one `BLOCKED` in unattended runs)

**Plan-driven runtime**
: task context → `agent_runtime.PlanDrivenRuntime` injects plan retrieval, generation, and step execution around `plan_graph.py`'s stable retrieve/generate/select/execute/wait/complete/failure transitions → pause at `WAIT` or finish at `COMPLETE`/`FAILED`; model-generated plans provide data, not graph topology

**Trigger definitions**
: `/api/runtime/definitions` (Settings → Triggers) stores objective + tools + manual/schedule/event activation in `trigger_definitions`; the worker's scheduler tick (`run_due_schedules`) and `emit_event` enqueue ordinary `runtime_triggers` rows with a `definition_id`, which run in the `automation` task mode fenced by the definition's tools and the owner's per-tool unattended switch

**Durable trigger ingress**
: authenticated `POST /api/runtime/triggers/` → `trigger_service.py` persists a user-owned, idempotent `runtime_triggers` record; strict-integer `max_steps` rejects booleans and strings rather than coercing them, and a cross-owner `(task_id, idempotency_key)` collision returns `409 Conflict` without exposing the existing row. Authenticated `GET /api/runtime/triggers/` provides owner-filtered lifecycle observability without returning payload or tool-policy data, and authenticated `GET /api/runtime/triggers/worker` reports whether the worker is enabled and running. The `DurableTriggerWorker` (enabled by default via `runtime_trigger_worker_enabled`) polls a bounded batch, claims each row with an expiring ownership lease, and invokes its injected handler outside a database session. Failed work uses persisted attempt counts and optional exponential backoff before becoming terminal `exhausted`. `PlanRuntimeTriggerHandler` fail-closed validates the narrow `objective`/`task_mode`/`max_steps` payload contract, while `DurablePlanRuntimeTriggerHandler` persists owner-scoped graph state and resumes waiting plans; neither path authorizes tools or selects capabilities.

**Character adaptation**
: `character.py` defines the bounded runtime contract; `character_profiles` persists each authenticated user's versioned constitution through `GET/PUT /api/character` → missing profiles use a safe default. The rendered constitution (plus its optional additional instructions) and the full stored skill library are the system prompt for chat, every trigger run (briefing, digest, mail processing, user automations), and every delegated task; the synchronous mail-triage fallback uses it as classifier guidance. `POST /api/character/preview` renders that prompt for a draft. Agent definitions no longer exist (JAN-227). The bounded adaptive state (tone, verbosity, proactivity, current focus; JAN-301) is rendered into the same prompts: the owner sets its bounds, Janus only proposes changes from chat, the owner approves them on Settings → Character, and every change is audited with rollback and reset (`services/character_state_service.py`, `/api/character/state*`).

**Planner sync**
: background daemon thread → connectors (`google.py`, `jira.py`) → `planner_service` caches items → daily context injected into briefing/chat prompts. Azure DevOps and Office 365 are enum values on `models.Provider` with no connector implementation yet (`PlannerService.get_connector()` returns `None` for them, so a configured account is silently skipped).

**Authentication**
: HTTP-only cookie + JWT; most routes use `Depends(security.get_current_user)`; setup and login endpoints (`/api/auth/login`, `/api/auth/setup`) are intentionally public; WebSocket endpoints use `security.get_websocket_user_or_none` (the permissive check on a short-lived session, so an open socket pins no pooled DB connection) to allow optional authentication; sensitive credentials stored Fernet-encrypted at rest

## Layer responsibilities

| Layer | Files | Responsibility |
|---|---|---|
| Background workers | `workers/`, `imap_service.py`, `planner_service.py`, `worker_manager.py` | Long-running daemon threads; one IMAP monitor per account, planner sync every 30 min |
| Core processing | `processor.py`, `llm_service.py`, `agent_runtime.py`, `plan_graph.py`, `character.py`, `agent_tools.py`, `tool_registry.py`, `tool_policy.py`, `runtime_adapter.py`, `trigger_definition_service.py`, `builtin_triggers.py`, `trigger_runtime.py`, `agent_toolboxes/`, `reminder_service.py` | Email classification, LLM interaction, stable plan-graph transitions, bounded character-state transitions, trigger definitions and built-ins, trigger/delegation execution, reminder extraction, and registry-backed, policy-gated tool exposure |
| Service layer | `services/` | Domain logic — auth, email CRUD, memory, skills, character, Linear, dashboard widget preferences |
| API layer | `api/`, `app_factory.py`, `websocket/`, `startup.py`, compatibility `main.py` | Thin FastAPI routers, app wiring, WebSocket registration, and shared startup initialization with legacy imports preserved |
| Persistence | `models/` (domain modules), `database.py`, `alembic/` | SQLAlchemy 2.0 models, SQLite, Alembic migrations |
| Clients | `frontend/`, `ios/`, CLI (`cli.py` via the `janus` entrypoint), `desktop/` | React SPA, SwiftUI app scaffold, Typer CLI commands, and optional tray integration |

## Agent toolbox layout

`agent_tools.py` defines the ordered `DEFAULT_TOOL_REGISTRY` (support in
`tool_registry.py`); each entry is one capability that wraps a toolbox
builder, and several capabilities may share one builder so read and write
halves carry different confirmation levels. Every live surface (chat,
triggers, delegated tasks) authorizes through `RuntimeAdapter.prepare_step()`:
model-proposed tool IDs are only requests, and the effective selection must
also satisfy the non-model task allowlist, real connection state, granted
capabilities, and the confirmation gate. `build_tool_set()` remains only as an
introspection/test helper.

| Capability | Toolbox | Tools | Confirmation |
|---|---|---|---|
| `core` | `common` | `get_current_datetime` | never |
| `events` | `events` | `list_recent_events` (chat only) | never |
| `trigger-history` | `events` | `list_my_trigger_history` | never |
| `mail` | `mail` | `search_emails`, `get_email_detail`, `get_email_details_batch` | never |
| `mail.label` | `mail` | `relabel_email`, `relabel_emails_batch` | recommended |
| `mail.send` | `mail` | `send_email` | required |
| `mail.processing` | `mail_processing` | `set_email_priority`, `create_reminder`, `track_email_topic`, `suggest_planner_item` | recommended |
| `calendar` | `calendar` | `list_calendar_events`, `get_event_detail` | never |
| `jira` | `tasks` | `list_tasks`, `get_task_detail` | never |
| `planner.local` | `local_tasks` | `list_local_tasks`, `get_local_task` | never |
| `planner.local.write` | `local_tasks` | `create_local_task`, `update_local_task`, `complete_local_task`, `move_local_task`, `reorder_local_task` | required |
| `dashboard` | `dashboard` | `get_daily_briefing`, `list_dashboard_widgets`, `update_dashboard_widget`, `set_dashboard_widget_prompt`, `clear_dashboard_widget_prompt` | recommended |
| `github` | `github` | `list_prs`, `merge_pr` | required |
| `linear` | `linear` | `list_linear_teams`, `list_linear_tasks`, `add_linear_task`, `add_linear_subtask`, `update_linear_task`, `comment_on_linear_task`, `start_linear_task`, `start_linear_subtask` | required |
| `web_search` | `web_search` | `web_search` | never |
| `web_fetch` | `web_fetch` | `web_fetch` (one public http(s) page as untrusted text; SSRF-fenced; JAN-329) | required |
| `memory` | `memory` | `list_memory_facts`, `search_facts`, `add_memory_fact`, `update_fact`, `delete_fact`, `group_facts` | recommended |
| `topics` | `topics` | `list_topics`, `create_topic`, `search_topics`, `get_topic_detail`, `update_topic_state` | recommended |
| `knowledge` | `knowledge` | `search_knowledge`, `get_entity` | never |
| `wiki` | `wiki` | `list_wiki`, `read_wiki` | never |
| `wiki.propose` | `wiki` | `propose_wiki_edit` | required |
| `triggers` | `triggers` | `create_scheduled_trigger`, `list_my_triggers`, `get_trigger`, `cancel_trigger` | recommended |
| `rules` | `rules` | `list_mail_rules`, `list_pending_rule_suggestions` | never |
| `rules.write` | `rules` | `create_mail_rule`, `update_mail_rule`, `delete_mail_rule`, `approve_rule_suggestion`, `dismiss_rule_suggestion` | required |
| `delegation` | `delegation` | `delegate_task`, `get_delegated_task`, `list_delegated_tasks` | recommended |
| `skills` | `skills` | `create_skill`, `list_skills`, `update_skill`, `search_remote_skills`, `import_remote_skill` | required |
| `docs` | `docs` | `list_docs`, `read_doc`, `search_docs` (Janus's own documentation, without `reviews/`; JAN-297) | never |
| `shell` | `shell` | `run_shell_command` (one command on the Janus host; no shell by default, scrubbed env, timeout, output cap; JAN-353) | required |
| `coding_agents` | `coding_agents` | `ask_coding_agent` (one prompt to an installed coding-agent CLI: Claude Code or Codex; read-only presets by default, the shell tool's process fences; JAN-403) | required |

Gating is by real state, not per-agent flags (agent definitions were removed
in JAN-227): `mail*` and `rules*` need a connected mail account; `calendar`,
`jira`, and `dashboard` need their connection; GitHub, Linear, and web search
need their instance-wide configuration (otherwise `missing_capability`);
`skills` is admin-only; `shell` is admin-only and needs the operator setting
`shell_tool_enabled` (off by default); `coding_agents` is admin-only and needs
`coding_agents_enabled` (off by default) plus an installed agent CLI;
`delegation`, `web_search`, `web_fetch`, `shell`, and `coding_agents` exist
only inside a plan step (`web_fetch` needs no configuration). `core`, `docs`, `events`, `trigger-history`, `memory`, `topics`, `knowledge`, `wiki`,
both local-planner entries, and `triggers` are always available.

Every `mail*` tool acts only on the run owner's own mail (JAN-286): search,
detail, and batch reads skip email from other users' accounts, a foreign email
id reads as "not found", and `send_email` only sends from the owner's own
active account. Relabel learning only creates or updates the owner's own rules
(JAN-287). Incoming mail is only classified by its owner's rules (JAN-295),
and the reminder and planner context fed to mail extraction and briefings is
the owner's own (JAN-296).

"Confirmation" is the registry default. In chat, every `required` capability
asks you first: the call pauses on an approval card and runs only if you
approve that one call (JAN-254); everything else runs without asking. The
owner can also switch a `required` capability *Off in chat* (JAN-306), which
withholds it from chat entirely, or *Always allow* (JAN-359), which skips the
card for it; the user-wide *In chat: Allow all* mode skips every card.
*Unattended: Allow everything* (JAN-359) overrides every per-tool unattended
switch at once and also opens image generation and remote MCP tools to
triggers and automations. Unattended runs (ad-hoc triggers, trigger definitions
including the built-ins, and delegated tasks) follow one per-user, per-tool
switch instead, **Unattended: Allowed / Off** (Settings → Tools &
Connections, JAN-255): by default every `required` capability is off and
everything else is allowed (remote MCP tools are chat only and their switch is locked, JAN-343); off tools are still offered but answer `BLOCKED`
and are listed as refused in the run result, and tools the user selected on a
trigger definition they wrote are allowed for that definition. See
[Runtime triggers](runtime-triggers.md) and the
[LLM and agent safety spec](tech-spec/llm-agent-safety.md#unattended-tool-switch-jan-229-jan-255).

Which capabilities each task mode may even request:

- **`chat`** — everything except `mail.processing` (`TASK_MODE_ALLOWED_TOOL_IDS`
  in `runtime_adapter.py`, like the fixed `briefing`, `digest`, and
  `ingestion` pipeline lists).
- **`automation`** (runs of a trigger definition) — the definition's own tools
  plus `core`.
- **`trigger`** (ad-hoc and agent-scheduled runs) — everything; `delete_fact`
  and the dashboard widget prompt set/clear tools are withheld by name.
- **`delegated`** (background child tasks) — the read-only tools the parent
  step was authorized for (never `delegation` or `triggers`); at most 4 queued or running
  per user. A child interrupted by a restart is re-run once after its lease
  (~5 minutes) expires, through the trigger worker's poll, and marked failed if
  it is interrupted again (JAN-207). A trigger run can wait for its children
  (`waiting`) and resumes with their results once they have all finished.

For the three unattended modes the switch then decides which of those tools
may run (`tool_policy.unattended_tool_sets`).

Six tools are locked out of unattended runs (`tool_policy.LOCKED_UNATTENDED`):
the chat-only `triggers.propose` (JAN-235), `ui_actions`, the page actions
that drive the user's open web page (JAN-251), `ui_blocks`, the structured
UI blocks Janus renders in chat replies (JAN-252), `images`, image generation
(JAN-253), `ui_introspection`, which reads the outline and styles of the
user's open web page (JAN-282..285), and `character.propose`, Janus's
proposals for its adaptive character state (JAN-301). `events` reads the event log owner-scoped: every row records the
user it concerns (`models.Event.user_id`, JAN-272), an unattended run sees
only its owner's rows, and in chat an admin sees the whole log and anyone else
their own rows. `triggers` may schedule follow-ups from an unattended run
only within a chain guard enforced by the toolbox (each follow-up records its
parent run and depth; depth at most 2, at most 3 per run, at most 10 per owner
per day, at least 15 minutes ahead, one-time only; JAN-274).

The `rules` capabilities author the regex rules that classify incoming mail.
`rules.write` is `confirmation="required"` because a wrong pattern silently
misroutes future mail; its writes call the same `EmailService` methods as
`/api/emails/rules` and `/api/emails/clarifications`, including the shared
`processor.validate_regex` pattern check.
