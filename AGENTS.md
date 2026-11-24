# Agent Instructions for Janus

## Documentation housekeeping (high priority)

Every `CODEMAP.md`, `TECH_SPEC.md`, and its child docs under `docs/docs/tech-spec/` describe the actual repository. Keeping them accurate is a first-class part of any change, not optional cleanup or a follow-up:

- Any change that adds, removes, renames, or moves a file, module, service, or directory MUST update every `CODEMAP.md` whose file-and-submodule table references it, in the same change.
- Any change to architecture, a service boundary, a schema, or an integration MUST update the relevant `docs/docs/tech-spec/` child doc(s) first (spec-first, see Key conventions below), then refresh `TECH_SPEC.md`'s own index/links if its summaries or links changed.
- Before considering a task or PR done, check whether the diff touched anything a `CODEMAP.md` or tech-spec doc describes; if so, fix the doc in the same PR. A stale codemap or tech spec is a defect, on par with a failing test - not something to leave for later.
- When asked for a housekeeping or documentation-audit pass (not tied to one specific change), walk every `CODEMAP.md` and tech-spec doc against the real, current repository state and fix whatever has drifted.

## Default navigation workflow

- For repository navigation, start with the root [`CODEMAP.md`](CODEMAP.md) and then follow the nearest child `CODEMAP.md` for the area you are changing.
- Use the skill in `.agents/skills/analyze-submodule-map/` as the default way to understand a submodule before doing broader ad-hoc searching.
- Only fall back to wider repository searches when the relevant code map is missing, stale, or insufficient for the task.

## Build, test, and lint commands

Python dependencies are managed with `uv`. Code consistency hooks use `prek`.

```bash
# Setup (the optional extras, so the WhatsApp, tray and document-parsing tests run)
uv sync --extra documents --extra whatsapp --extra desktop
uv run prek install

# Backend
task run
task test
uv run pytest tests/test_auth.py::test_password_hashing -v
task lint
task typecheck           # strict mypy on the scope in [tool.mypy] (pyproject.toml)
task lint:imports       # import boundaries (import-linter contracts in pyproject.toml)
task lint:dead
task security:semgrep    # pinned Semgrep scan with the Janus rules in .semgrep/ (JAN-267)
task coverage            # backend tests + statement/branch coverage floors (pyproject.toml)
task coverage:diff       # >= 80% of changed backend lines covered (after task coverage)
task coverage:frontend   # Vitest with V8 coverage floors (frontend/vitest.config.ts)
task audit:deps          # pip-audit + npm audit over the lockfiles (JAN-268)
task quality:duplication # jscpd on production code, fails above 1% (JAN-265)
task quality:report      # weekly report: every quality check in one JSON report + delta vs the last main report, ungated knip/vulture findings (JAN-265/266/270; ONLY=complexity,size for a subset)
task quality:history     # newest COUNT main quality reports from the quality-history branch as JSON Lines (JAN-270)
task quality:review-bundle  # monthly structural review input in structural-review/ (JAN-271; BASE=<sha> overrides the window start)
task quality:review-check   # FILE=review.json: validate the structural review against the bundle, render review.md
task quality:review-record  # FILE=review.json: append a checked main review to the quality-history branch
task ops:verify-version  # hosted health endpoint answers HTTP 200 with the expected version (JAN-269)
task ops:probe           # availability samples of the hosted health endpoint, nothing published
task ops:metrics         # deploy and availability summaries from the ops-metrics branch
task ops:export          # DATASET=deploys|availability FORMAT=csv|jsonl export of that history
task openapi:generate    # offline OpenAPI export + frontend/src/generated/api.d.ts after an API change (JAN-139)
task openapi:check       # fail when the committed schema or generated API types drift from the backend
task format
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
uv run janus brief
task digest
task docs

# Frontend
cd frontend && npm run dev
cd frontend && npm run build
cd frontend && npm run lint

# Tailwind assets used outside the Vite frontend
npm run build:css

# Run repository hooks manually
uv run prek run --all-files
```

## High-level architecture

Janus is a privacy-first, local-first personal AI agent platform: it started as an IMAP email assistant and has grown into a single always-on agent with a plan-driven tool-calling runtime, durable scheduled/event triggers, background task delegation, durable memory facts, structured topic tracking, an MCP tool registry, and a WhatsApp connector, surfaced through a web dashboard (three interchangeable UI shells), a CLI, and a native iOS client scaffold. The backend is a FastAPI app with a Typer CLI in the same package (`src/janus/main.py`), backed by SQLite via SQLAlchemy and migrations in Alembic. The same entrypoint mounts the REST routers, registers the `/ws/briefing` and `/ws/chat` WebSocket endpoints, starts long-running background workers (IMAP monitors, planner sync, WhatsApp connectors, the durable trigger worker), and serves the built React app when `frontend/dist` exists. For the authoritative, actively-maintained architecture detail behind this summary, start at `TECH_SPEC.md` and its children under `docs/docs/tech-spec/`.

The main backend flow is: IMAP monitors receive mail via IDLE, `email_callback()` passes messages into `EmailProcessor`, rules are evaluated first, and mail no rule filed as SPAM/IGNORE is either handed to the owner's `mail.received` trigger definitions (when the trigger worker is enabled and one is subscribed; the email is stored with a provisional priority and Janus classifies it with the `mail.processing` tools) or falls through to the synchronous LLM classification with planner context before `EmailRecord` rows are stored. Important messages trigger notifications; digest messages are marked read on the IMAP server. Planner data comes through `PlannerService`, which syncs external providers currently implemented for Google Calendar and Jira (Azure DevOps and O365 are placeholders in `planner_service.py`, not yet implemented) and also supplies the daily context used by the Morning briefing trigger and chat.

The plan-driven agent runtime (`runtime_adapter.py`, `tool_registry.py`, `tool_policy.py`, `character.py`, `agent_task_runner.py`) is the live execution path for chat, triggers, and delegated tasks: it turns an inbound task/message into an authorized, per-message toolset (`agent_toolboxes/`) gated by task mode, real connection state, and a confirmation gate, and every surface renders the same prompt — the user's `CharacterProfile` constitution plus the flat skill library. Agent definitions, skill assignment, and handoffs were removed (JAN-169/JAN-227). Everything outside chat is a trigger run: `trigger_service.py`/`trigger_runtime.py` persist and execute runs through a polling durable worker whose scheduler hook enqueues due schedules; `trigger_definition_service.py` owns saved trigger definitions (objective + tools + manual/schedule/event activation, e.g. `mail.received`) and `builtin_triggers.py` seeds the per-user Morning briefing, Afternoon digest, and Mail processing definitions; `tool_policy.py` decides which tools an unattended run may use without a human confirming (blocked tools answer `BLOCKED` and are recorded). Janus can draft definitions from chat (`triggers.propose`, JAN-235) that stay inactive until the user approves them; see `trigger_proposal_service.py` and `trigger_approval.py`. `delegation_service.py`/`delegation_runtime.py` let a chat or trigger step spawn a bounded, read-only child task. Durable memory facts (`services/memory_service.py`), structured topic tracking (`services/topic_service.py`), and an owner-scoped MCP tool registry (`services/mcp_service.py`) round out the agent-facing subsystems. See `docs/docs/tech-spec/background-workers-runtime.md` and `docs/docs/tech-spec/llm-agent-safety.md` before changing what an unattended (trigger/delegated) run can do.

The frontend is a React 19 + TypeScript + Vite app in `frontend/`, with three interchangeable UI shells (classic "UIv1", terminal-styled "UIv2", rusty-console "UIv3") selectable per user via a shell registry in `contexts/uiVersion.ts`. Routing is in `frontend/src/App.tsx`; pages compose feature components from `components/features/` and reusable primitives from `components/ui/`. Server state uses TanStack Query, API access is centralized in `frontend/src/api.ts` through an Axios client with `withCredentials: true`, and real-time briefing/chat uses browser WebSockets directly instead of an additional state or socket framework. A native SwiftUI iOS client scaffold in `ios/` covers REST session auth and a JWT-backed `/ws/chat` connection.

Documentation is part of the product surface: the backend serves Markdown from `docs/` through `/api/docs/*`, and the frontend renders it as an in-app documentation view rather than treating docs as a separate site.

## Key conventions

- Significant changes are spec-first (see "Documentation housekeeping" above, which is the authoritative, high-priority version of this rule): update the relevant child technical-spec document(s) first, then refresh the root `TECH_SPEC.md` index when its summaries or links need to change.
- Delivery agents should minimize unnecessary user interaction once the task is actionable, and they should treat push + PR as the default completion path.
- When validation surfaces a pre-existing unrelated failure that blocks trustworthy verification, the active delivery workflow may fix that blocker before resuming feature work or else track it immediately as a blocking follow-up.
- Backend code uses package-root imports (`from janus ...`) rather than relative imports.
- FastAPI routers are intentionally thin. Put request/session/auth wiring in the router, and keep domain logic in services such as `auth_service`, `email_service`, `planner_service`, `llm_service`, and `processor`.
- Request authentication normally uses `Depends(security.get_current_user)`. When a route can be anonymous, use `security.get_user_or_none` instead; a WebSocket uses `security.get_websocket_user_or_none`, whose DB session is closed before the socket opens, so a long-lived socket does not pin a pooled connection (JAN-336).
- Database access follows the shared session dependency pattern in `janus.database.get_db`; long-running or background code closes sessions explicitly in `finally` blocks.
- Background work is thread-based, not async-worker-based. IMAP monitoring and planner sync run as daemon threads, so changes in those areas should fit the current threading model.
- Regex-based email rules are security-sensitive. Keep `validate_regex()` and `EmailProcessor._safe_search()` behavior intact and update `tests/test_redos.py` when rule evaluation changes.
- Sensitive credentials are stored encrypted at rest through `security.SecurityService`; `.janus_key` is the local Fernet key file used for that encryption.
- The frontend does not use a global client-side store. TanStack Query is the source of truth for server data, while feature components own their own queries and mutations.
- `components/features/` are meant to be self-sufficient domain components, and `pages/` should compose them rather than accumulate business logic.
- Repository consistency is enforced with `prek`, not standard `pre-commit`. Hooks run Ruff formatting/checking, dead-code scans (`vulture`/`deptry` for the backend, `knip` for the frontend), basic file integrity checks, prettier for JSON/YAML, TOML sorting, and block direct commits to `main`.
- PR titles intended for squash merge into `main` must use conventional-commit format such as `feat(scope): summary`, `fix: summary`, or `chore: summary`. GitHub uses the PR title as the default squash commit subject, and `release-please` parses those merge commit subjects to decide release notes and version bumps. Do not use bare ticket prefixes or plain-English titles like `JAN-134: ...` or `Add ...` for mergeable PRs.
- Bug fixes must ship with a regression test that exercises the original failure mode, especially for schema/startup issues where upgraded code must continue to work against existing local databases.
- New functionality should ship with meaningful automated tests. Aim for roughly 90-95%+ coverage over time, but do not chase the metric at the expense of test quality or behavioral relevance.
- Every piece of UI functionality (a new page, route, form, button, or user-visible flow) must ship with an end-to-end test under `frontend/e2e/` (Playwright) exercising it, in the same change. Every UI component (`components/ui/`, `components/features/`, `components/layout*/`) must have its own `*.stories.tsx` file covering its meaningfully different states (e.g. loading, empty, error, populated, disabled) — not just a single default render. A component or flow shipped without its story/e2e coverage is not done, the same way a stale `CODEMAP.md` is not an acceptable "later."
- Delivery workflow is part of the engineering contract here: work from a dedicated branch for implementation tasks, keep commits meaningful, validate the finished change, reflect on the diff, then push the branch and open a PR before considering the work done. Treat push + PR as the default completion path after a fully tested branched implementation.
- CI budget: open pull requests as drafts and mark them ready for review once the change is validated and the docs pass is pushed. A draft PR runs only the cheap CI checks; the test suites run on `ready_for_review` and on pushes to a non-draft PR, and a PR is merge-ready only after a full run is green on its final commit. `main` is checked by a nightly CI run, not per merge. See `docs/docs/tech-spec/architecture-overview.md#draft-pull-requests`.
- Dependency bumps: never merge Dependabot PRs one by one. Combine them into a single validated PR, then close the originals with a link to it. Follow `.agents/skills/combine-dependabot-prs/SKILL.md`, which covers the inclusion rules, lockfile regeneration, the Uberspace wheel constraints, and the PR body layout.
- Never end a session, or report a task as finished, with committed work sitting only in a local branch. A finished local branch with no PR is not done — it is invisible to review, CI, release-please, and everyone else, and it will silently rot (a real incident: a `feat/health-endpoint-version` branch sat fully committed and untested-by-CI for a day because it was never pushed, and a later session almost re-implemented part of it from scratch because nothing on GitHub showed it existed). If a task's implementation is complete, pushing the branch and opening the PR is part of "complete," not a follow-up step. If push/PR genuinely cannot be done — no network, no push access, no configured remote — do not silently report the task as finished and do not retry indefinitely either; say explicitly that the branch is local-only and why, name the exact commit/branch, and say what would unblock it. An honest "blocked, here's the local branch" beats both a false "done" and a silent retry loop.

- Operational and quality metrics that must outlive artifact retention go to an append-only JSON Lines file on a dedicated orphan history branch through `scripts/history_branch.py` (`ops-metrics` for deploy and availability metrics, JAN-269; `quality-history` for the weekly quality report, JAN-270). Never rewrite, force-push, or merge such a branch, and never write secrets or e-mail addresses into it; `scripts/ops_metrics.py` refuses secret-looking records. Keep the logic in tested scripts under `scripts/` and the workflow YAML thin. A deploy is done only when the health endpoint reports the expected release version, not on HTTP 200 alone. Definitions: `docs/docs/tech-spec/deployment-operations.md#deploy-and-availability-metrics`.
- The weekly quality report (`scripts/quality_report.py`, `.github/workflows/quality-report.yml`, JAN-270) has a fixed JSON schema (`janus.quality-report/1`); add a metric to its `METRICS` table and the docs together, never ad hoc. Its durable history is the append-only `quality-history` branch (main reports only, through `scripts/history_branch.py`); never rewrite or force-push it, and do not treat artifact retention as history. Mark a check that could not run as `error` or `unavailable` instead of dropping it. Baselines only ratchet; never lower a baseline or a gate floor automatically, only in a reviewed commit. Policy: `docs/docs/tech-spec/architecture-overview.md#weekly-quality-report`.
- The monthly structural review (JAN-271) builds on that report instead of adding metrics of its own. `.github/workflows/structural-review.yml` runs `scripts/structural_review.py bundle` on the 2nd of each month (read-only, no secrets, no LLM) and uploads the `structural-review` input: the window of `main` since the last recorded review (commit range and time range), the newest main quality reports, the main diff, the CODEMAPs, and router metrics. GitHub Actions has no agent integration, so the review itself runs in a coding-agent session (for example Claude Code or Codex); the job summary reports that step as blocked. The review keeps the automatic metrics (the bundle) apart from qualitative findings (`review.json`, schema `janus.structural-review/1`: file and line at the head commit, priority, action, reconciliation with an existing issue or `proposed`), and `task quality:review-check` rejects anything else, including secret-looking text and an unreported missing capability. The review never changes code, merges, or creates issues; the person who asked files the proposed ones. `task quality:review-record` appends a checked review of a `main` window to `reports/structural-review.jsonl` on `quality-history` (once per window), which starts the next window. Policy: `docs/docs/tech-spec/architecture-overview.md#monthly-structural-review`.

## Issues and contributions

Bug reports and feature requests go to GitHub Issues. Changes come in as pull requests against `main`; see the conventions above for branch, PR title, test, and documentation expectations.
