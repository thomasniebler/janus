# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Janus is a privacy-first, local-first personal AI agent platform. It started as an IMAP email assistant (LLM + regex classification) and has grown into a single always-on agent with a plan-driven tool-calling runtime, durable scheduled/event triggers, background task delegation, durable memory facts, structured topic tracking, an MCP tool registry, and a WhatsApp connector — surfaced through a web dashboard (three interchangeable UI shells), a CLI, and a native iOS client scaffold. It runs entirely locally (SQLite + optional Ollama) with optional cloud integrations (Google Calendar, Jira, OpenAI; Azure DevOps and O365 connectors are planned but not yet implemented — `planner_service.py` only has a placeholder for them). For the authoritative, actively-maintained architecture detail behind this summary, start at [`TECH_SPEC.md`](TECH_SPEC.md) and its children under `docs/docs/tech-spec/`.

## Commands

All Python tasks use `uv`, frontend uses `npm` inside `frontend/`.

```bash
# Backend
task run              # Start backend API server (uv run janus start)
task test             # Run all tests (uv run pytest)
task lint             # Lint Python (uv run ruff check .)
task lint:dead        # Find dead code and unused deps (vulture + deptry + knip)
task format           # Format Python (black + ruff --fix)

# Run a single test
uv run pytest tests/test_auth.py::test_password_hashing -v

# Frontend
cd frontend && npm run dev    # Dev server on :5173
cd frontend && npm run build  # Production build to frontend/dist/

# OpenAPI schema + generated frontend API types (offline, no server needed)
task openapi:generate   # Export schema and regenerate frontend/src/generated/api.d.ts after an API change
task openapi:check      # Fail when the committed schema or generated types drift (CI: openapi-check)

# Database
uv run alembic upgrade head   # Apply migrations
uv run alembic revision --autogenerate -m "description"  # New migration

# Documentation
task docs                     # Build docs
task docs:serve               # Local docs server

# CLI tools
uv run janus brief    # Morning briefing
uv run janus digest   # Afternoon digest
```

Pre-commit hooks run `ruff format`, `ruff check --fix`, dead-code scans (`vulture`/`deptry` for the backend, `knip` for the frontend), and block direct commits to `main`. Configured in `prek.toml`.

## Documentation housekeeping (high priority)

Every `CODEMAP.md`, `TECH_SPEC.md`, and its child docs under `docs/docs/tech-spec/` describe the actual repository. Keeping them accurate is a first-class part of any change, not optional cleanup or a follow-up:

- Any change that adds, removes, renames, or moves a file, module, service, or directory MUST update every `CODEMAP.md` whose file-and-submodule table references it, in the same change.
- Any change to architecture, a service boundary, a schema, or an integration MUST update the relevant `docs/docs/tech-spec/` child doc(s) first (see "Spec-first" below), then refresh `TECH_SPEC.md`'s own index/links if its summaries or links changed.
- Before considering a task done, check whether the diff touched anything a `CODEMAP.md` or tech-spec doc describes; if so, fix the doc in the same change. A stale codemap or tech spec is a defect, on par with a failing test.
- When asked for a housekeeping or documentation-audit pass (not tied to one specific change), walk every `CODEMAP.md` and tech-spec doc against the real, current repository state and fix whatever has drifted.

## Architecture

### Backend (FastAPI + Python 3.12, `src/janus/`)

**Entry point:** `main.py` — mounts all routers, starts background threads (IMAP monitors, planner sync, WhatsApp connectors, durable trigger worker), and registers WebSocket endpoints.

**API layer** (`api/`): Thin routers that delegate to services — see [`src/janus/api/CODEMAP.md`](src/janus/api/CODEMAP.md) for the full, current router list. Auth uses HTTP-only cookies + JWT; `security.py` provides FastAPI `Depends()` guards (`get_current_user`, `admin_required`).

**Service layer** — see [`src/janus/services/CODEMAP.md`](src/janus/services/CODEMAP.md) for the full, current list. Core ones:
- `services/auth_service.py` — password hashing (bcrypt), JWT creation/verification, Fernet encryption for stored credentials
- `services/email_service.py` — email CRUD, priority updates
- `llm_service.py` — LangChain abstraction over the LLM providers; streams chat via WebSocket and runs the JSON extractors and memory suggester (briefings and digests are trigger runs, not `llm_service` methods)
- `imap_service.py` — persistent IMAP connections with IDLE support; one background thread per account
- `processor.py` — applies `Rule` patterns (regex) to classify emails as IMPORTANT/DIGEST/IGNORE/SPAM, then hands new mail to `mail.received` trigger definitions or falls back to the synchronous LLM classifier
- `planner_service.py` — factory over calendar connectors (Google, Jira); Azure DevOps and O365 are placeholders, not implemented; syncs every 30 min

**Plan-driven agent runtime** (`runtime_adapter.py`, `tool_registry.py`, `tool_policy.py`, `character.py`, `agent_task_runner.py`) is the live execution path for chat, triggers, and delegated tasks: it turns an inbound task/message into an authorized, per-message toolset (`agent_toolboxes/`) gated by task mode, real connection state, and a confirmation gate. Every surface renders the same system prompt: the user's `CharacterProfile` constitution (plus its optional `additional_instructions`) and the full flat skill library. There are no agent definitions, skill assignments, or handoffs any more (removed in JAN-169/JAN-227, migration `d5e8f1a2b3c4`); the skill-library tools survive as the admin-only `skills` toolbox. See [`docs/docs/tech-spec/background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md).

**Triggers, definitions, and delegation:** everything Janus does outside chat is a trigger run. `trigger_service.py`/`trigger_runtime.py` persist and execute runs through a polling `DurableTriggerWorker`, whose scheduler hook enqueues due recurring runs each poll. `trigger_definition_service.py` owns saved `TriggerDefinition`s (objective + `tool_ids` + manual / daily-or-interval schedule / event activation; events come from the catalog in `trigger_events.py`: `mail.received`, emitted by `processor.py` after regex rules for mail they did not file as SPAM/IGNORE, the `whatsapp.*` events for new incoming live messages from `whatsapp_neonize.py` (JAN-361), and the `planner.*` created/changed events from the planner sync (JAN-362); an event definition may narrow its events with a payload filter and a `max_runs_per_hour` limit, checked by `trigger_event_filters.py` before a run is enqueued, JAN-363), which run in the `automation` task mode fenced by the definition's tools. `builtin_triggers.py` seeds per-user built-ins — Morning briefing, Afternoon digest, Mail processing — with `prepare`/`finalize` hooks; `/ws/briefing`, `janus brief`, and `janus digest` read or run them inline. `tool_policy.py` is the per-user, per-tool "Unattended: Allowed / Off" switch (JAN-255) covering ad-hoc triggers, definition runs, and delegated tasks (default: `confirmation="required"` entries off; off tools are still offered but answer `BLOCKED` and are recorded as refused; the per-user "Unattended: Allow everything" mode (JAN-359, `tool_policy_modes`) overrides every switch and also lifts the `images` and remote MCP locks; in chat a `required` tool can be set to "Always allow" per tool or via the "In chat: Allow all" mode; only the chat-only `LOCKED_UNATTENDED` entries are locked out — `events` reads only the run owner's event-log rows (`models.Event.user_id`, JAN-272) and `triggers` enforces a chain guard on follow-ups an unattended run schedules (JAN-274); tools a user selected on a definition they authored are allowed for it); `tool_policy.unattended_tool_sets` computes each unattended run's offered and confirmed tools. Janus proposes, the user approves (JAN-235): from chat, the `triggers.propose` toolbox lets Janus draft definitions (`origin="agent"`, `proposal_status="proposed"`, saved disabled and never activated); `trigger_proposal_service.py` enforces the limits and the proposable-tool allowlist, only the user-authenticated approve endpoint writes the approval, and `trigger_approval.py`'s content fingerprint must still match before an approved draft's tools count as confirmed. `LOCKED_UNATTENDED` holds the chat-only entries: `triggers.propose` (JAN-235), `ui_actions` (page actions, JAN-251), `ui_blocks` (generative UI blocks, JAN-252), `images` (image generation, JAN-253), and `ui_introspection` (reading the user's open page, JAN-283). Janus's one-time chat follow-ups (JAN-257) keep `proposal_status = NULL` and are outside this flow. `delegation_service.py`/`delegation_runtime.py` let a chat or trigger step spawn a bounded, read-only child task (depth 1, capped fan-out) on a background thread pool. See [`docs/docs/tech-spec/background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md) and [`docs/docs/runtime-triggers.md`](docs/docs/runtime-triggers.md).

**Other agent-facing subsystems:** durable memory facts (`services/memory_service.py`), structured topic tracking (`services/topic_service.py`), an owner-scoped MCP tool registry (`services/mcp_service.py`), and a WhatsApp connector via Neonize (`whatsapp_neonize.py`, `services/whatsapp_service.py`, `services/whatsapp_setup_service.py`) — one supervised thread per account, event-driven (no pull API). Neonize is the optional `whatsapp` extra and the tray icon (pystray, Pillow) the optional `desktop` extra; production installs neither (see `docs/docs/tech-spec/deployment-operations.md`).

**Database:** SQLAlchemy 2.0 with SQLite (`janus.db`). Schema in `models.py`; migrations via Alembic in `alembic/versions/`. Session provided via `Depends(database.get_db)`.

**WebSockets:** `/ws/briefing` sends today's Morning briefing trigger run (running it inline when needed or on `?refresh=1`); `/ws/chat` streams the follow-up conversation.

### Frontend (React 19 + TypeScript + Vite, `frontend/src/`)

**Three interchangeable UI shells:** the app shell is selectable per user (classic "UIv1", terminal-styled "UIv2", rusty-console "UIv3") via a shell registry in `contexts/uiVersion.ts`; see [`components/CODEMAP.md`](frontend/src/components/CODEMAP.md) and [`docs/docs/tech-spec/frontend.md`](docs/docs/tech-spec/frontend.md).

**State management:** TanStack Query (server state/caching) + local `useState`. No global state store.

**Component layers** — see [`frontend/src/components/CODEMAP.md`](frontend/src/components/CODEMAP.md) for the full, current list:
- `components/ui/` — atomic (Button, Input, Modal, Card, Badge)
- `components/features/` — domain-specific with own data fetching (BriefingChat, EmailList, RuleManager, AccountManager, LLMConfig, and many more)
- `components/layout*/` — the three page shells
- `pages/` — route-level compositions

**API client:** `api.ts` — Axios instance with typed request functions; base URL proxied through Vite in dev.

**Styling:** Tailwind CSS v4, dark mode via `class` strategy.

### iOS client (`ios/`)

A native SwiftUI scaffold covering REST session auth and a JWT-backed `/ws/chat` connection; see [`ios/CODEMAP.md`](ios/CODEMAP.md) and [`docs/docs/tech-spec/mobile-ios.md`](docs/docs/tech-spec/mobile-ios.md).

### Key Design Decisions

- **Regex ReDoS protection:** `processor.py` validates rule patterns; `tests/test_redos.py` covers this explicitly.
- **Encryption at rest:** Sensitive fields (IMAP passwords, API keys) stored Fernet-encrypted in SQLite.
- **Background threading:** IMAP monitors, planner sync, WhatsApp connectors, and the durable trigger worker all run as daemon threads, not async tasks.
- **Trigger/delegation safety fences:** unattended (trigger/automation/delegated) tool calls are bounded by the per-user unattended switch (`tool_policy.py`), which refuses (with a recorded `BLOCKED` answer) any tool switched off rather than auto-approving it, any `LOCKED_UNATTENDED` tool (the chat-only `triggers.propose`, JAN-235; `ui_actions` page actions, JAN-251; `ui_blocks` generative UI blocks, JAN-252; `images` image generation, JAN-253; `ui_introspection` page outline and element styles, JAN-283), the `triggers` toolbox's own chain guard (lineage via `RuntimeTrigger.parent_trigger_id`/`chain_depth`; depth ≤ 2, ≤ 3 follow-ups per run, ≤ 10 unattended-scheduled follow-ups per owner per day, ≥ 15 min ahead, one-time only), owner-only event-log reads, a definition's own tool list for `automation` runs, per-mode tool withholding, and, for delegated children, the parent's read-only grant; only a user-authored definition's own tool selection, or that of a Janus-drafted definition the user approved whose approval fingerprint still matches (JAN-235), overrides an off switch. There are no per-mode allowlists for these modes (`TASK_MODE_ALLOWED_TOOL_IDS` only covers `chat`, `briefing`, `digest`, `ingestion`). See [`docs/docs/tech-spec/background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md) before changing what an unattended run can do.
- **Spec-first:** Major architectural changes should update the relevant child technical-spec document(s) first, then refresh the root `TECH_SPEC.md` index when its summaries or links need to change. See "Documentation housekeeping" above for the authoritative, high-priority version of this rule, which also covers `CODEMAP.md` files.
- **Docs are delivery artifacts:** Planned-feature docs (`docs/docs/features/planned/`), feature specs (`docs/docs/feature-specs/`), and user-facing pages (quickstart, configuration, integrations) must be updated in the same change as the behavior they describe — not as optional follow-up. Every planned-feature/feature-spec page carries a `Status` / `Linear` / `Depends on` block per [`docs/docs/tech-spec/delivery-artifacts.md`](docs/docs/tech-spec/delivery-artifacts.md): `Status` must move to `Shipped` in the same PR that ships the feature, `Linear` names the issue (plain ID, the workspace is private) that is the source of truth for live progress (the doc does not mirror sprint status), and `Depends on` names other specs/systems it needs.
- **Running things on the production host:** there is no direct SSH from agent sessions. To learn what the Uberspace host supports, add a read-only probe to `scripts/deployment_capabilities.py` (documented in [`docs/docs/tech-spec/deployment-operations.md`](docs/docs/tech-spec/deployment-operations.md#deployment-capability-checks)) and dispatch the `Deployment capability checks` workflow; its SSH setup mirrors `deploy-uberspace.yml`. Do not add free-form remote commands to workflows.
- **CI budget (draft PRs):** open pull requests as drafts (`gh pr create --draft`) and mark them ready for review (`gh pr ready`) once the change is validated and the docs pass is pushed. A draft PR runs only the cheap CI checks; the test suites run on `ready_for_review` and on pushes to a non-draft PR. A PR is merge-ready only after a full run is green on its final commit. `main` is not re-checked per merge; a nightly CI run covers it. See [`docs/docs/tech-spec/architecture-overview.md#draft-pull-requests`](docs/docs/tech-spec/architecture-overview.md#draft-pull-requests).
- **Dependency bumps:** never merge Dependabot PRs one by one. Combine them into a single validated PR, then close the originals with a link to it. Follow the `combine-dependabot-prs` skill (`.claude/skills/`, a symlink to `.agents/skills/combine-dependabot-prs/SKILL.md`).
- **Implementation workflow:** scope the task, write a short plan, implement, validate (`task test`, `task lint`), reflect on the final diff, then branch/commit/push, update the user-facing docs, and open a PR. The delivery-artifacts convention above is part of "review-ready", not a nice-to-have.
