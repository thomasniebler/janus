# Architecture Overview

Janus is one Python process per installation. It serves the REST API, three WebSockets and the built web app, and runs every background job as a daemon thread in the same process. State lives in one SQL database (SQLite by default). Clients are the web app, the `janus` CLI, and an iOS prototype.

## Process model

`janus start` (`cli.py`) is the entry point. In order it:

1. calls `startup.ensure_runtime_initialized()`: configure logging, take the single-instance lock (`runtime.enforce_single_instance`), require a real `JANUS_SECRET_KEY`, migrate the database (`database.ensure_schema_up_to_date`), print a setup token when public setup is on, then start the durable trigger worker, the knowledge-index worker and one Telegram worker per linked bot;
2. starts one mail worker per active mail account and one WhatsApp worker per enabled account;
3. starts the `error-compaction`, `planner` and `llm-status-probe` workers and, when eligible, the auto-update worker;
4. optionally starts the tray icon (`--tray`, the `desktop` extra; ignored in server mode);
5. runs uvicorn on the FastAPI app from `app_factory.py`, with `proxy_headers=False` so `TrustedProxyHeadersMiddleware` alone decides forwarded-header trust.

`update_boot.recover_on_failed_boot()` wraps the whole start, so a release that fails to boot after a self-update rolls back ([deployment](deployment-operations.md#self-hosted-auto-update)).

**Single instance.** The SQLite file, the in-process workers and the in-process domain-change bus all assume exactly one server process per database. A second `janus start` on the same runtime directory refuses to start.

## Threads and workers

All long-running work goes through `worker_manager` (supervised daemon threads; `restart_policy=True` restarts a crashed loop with backoff). `GET /api/status/workers` reports the mail and planner workers.

| Label | Started by | Loop |
| --- | --- | --- |
| `runtime-triggers` | `startup` (when `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED`) | `DurableTriggerWorker.run_forever`: scheduler tick, then claim and run queued trigger runs |
| `knowledge-index` | `startup` (when `JANUS_KNOWLEDGE_INDEX_WORKER_ENABLED`) | every 60 s: ingest knowledge sources, then embed stale facts, topics, chunks and entities |
| `telegram:<user id>` | `startup`, `POST /api/telegram/connect` | long-poll the user's bot |
| `imap:<address>` | `cli.start`, adding or switching on a mailbox (`workers/email.start_mailbox_worker`); stopped on delete, switch-off or disconnect | initial sync, then IMAP IDLE with heartbeat; Outlook accounts poll Microsoft Graph instead. A per-mailbox stop event ends the loop within about a second |
| `whatsapp:<account id>` | `cli.start`, setup completion | one blocking Neonize connection, event-driven |
| `planner` | `cli.start` | sync every planner account every 30 minutes, emit `planner.*` events |
| `error-compaction` | `cli.start` | compact grouped error events once a day |
| `llm-status-probe` | `cli.start` | probe the default LLM every 300 s for system status |
| `auto-update` | `workers/update.py` | check GitHub releases every `AUTO_UPDATE_INTERVAL_MINUTES` |

Two pools are not `worker_manager` threads: delegated child tasks run on a bounded `ThreadPoolTaskRunner` (`background_tasks.py`), and the toolset selector runs its model call on short-lived daemon threads. Neither is durable; durability comes from the database rows they work on.

Rule for background code: open a session per unit of work and close it in `finally`. Never hold a session across a model call.

## Module map

| Area | Modules |
| --- | --- |
| Composition roots | `main.py`, `app_factory.py`, `cli.py` |
| HTTP and WebSocket | `api/`, `websocket/`, `security.py`, `request_origin.py` |
| Configuration and process | `config.py`, `runtime.py`, `startup.py`, `worker_manager.py`, `resources.py` |
| Data | `models/`, `models_*.py`, `database.py`, `schemas/`, `alembic/` |
| Agent runtime | `runtime_adapter.py`, `plan_graph.py`, `plan_state.py`, `agent_runtime.py`, `agent_task_runner.py`, `unattended_execution.py`, `toolset_selection.py` |
| Tools | `tool_registry.py`, `agent_tools.py`, `agent_toolboxes/`, `capability_profile.py`, `tool_policy.py`, `prompt_injection.py` |
| Triggers | `trigger_service.py`, `trigger_runtime.py`, `runtime_trigger_handler.py`, `triggers.py`, `trigger_adapters.py`, `trigger_lease.py`, `trigger_definition_service.py`, `trigger_events.py`, `trigger_event_filters.py`, `trigger_run_settings.py`, `trigger_approval.py`, `trigger_proposal_service.py`, `builtin_triggers.py` |
| Delegation | `delegation_service.py`, `delegation_runtime.py`, `delegation_wait.py`, `background_tasks.py` |
| Mail | `imap_service.py`, `graph_mail_service.py`, `processor.py`, `workers/email.py` |
| Planner | `planner_service.py`, `connectors/`, `planner_trigger_events.py`, `services/local_planner_service.py` |
| Messaging | `whatsapp_neonize.py`, `whatsapp_trigger_events.py`, `workers/whatsapp.py`, `workers/telegram.py` |
| LLM | `llm_service.py`, `llm_usage.py`, `services/llm_*.py` |
| Domain services | `services/` (one module per domain; see its `CODEMAP.md`) |
| Status and events | `status_service.py`, `event_logger.py`, `domain_changes.py` |

## Request and run flows

**HTTP request.** Middleware checks the host, origin and forwarded headers; the router authenticates through a `security` dependency (and checks CSRF for cookie sessions on mutating routes); the router calls one service method, which filters on the owner in the same query; the commit triggers the domain-change hooks, which tell the owner's open tabs which data changed. See [Backend and API](backend-api.md).

**Chat turn.** The web app sends a message over `/ws/chat`. For each message the server renders the system prompt (the user's character profile plus the skill library), asks the utility model to narrow the toolset, authorizes the step through `RuntimeAdapter.prepare_step()`, and streams the agent's reply and tool events. Tools marked `confirmation="required"` pause for an approval card. The reply is committed before `__END__`. See [Background workers and runtime](background-workers-runtime.md#chat).

**Trigger run.** A schedule, an event, the API or a tool inserts a `runtime_triggers` row. The durable worker claims it, the handler builds a one-step plan, `unattended_execution` authorizes the step with the owner's tool policy, and `agent_task_runner` runs a bounded tool loop. The result, refusals and failures are stored on the plan state. See [Triggers](background-workers-runtime.md#triggers-and-the-durable-worker).

**New mail.** The mail worker hands each new message to `EmailProcessor.process_email`: regex rules first, then either a `mail.received` trigger run or the synchronous classifier. See [Mail ingestion](background-workers-runtime.md#mail-ingestion).

## Cross-cutting architecture rules

- **Thin routers.** Routers hold route declarations, dependency wiring and request/response mapping. Domain behavior lives in services.
- **Ownership in services.** User-owned rows are read and changed through `*_for_user` methods that filter on the owner in the same query. A foreign id answers exactly like a missing one.
- **Threads, not async workers.** Background work stays thread-based until an architecture change says otherwise.
- **Janus owns the writable planner.** Local tasks and events are the only writable planner records; imported providers are read-only context.
- **Docs are part of the product.** `docs/docs/` is served in the app and read by the agent's `docs` tool.
- **Import boundaries.** Four import-linter contracts in `pyproject.toml` (`task lint:imports`, checked in CI):
    - Only the composition roots (`main.py`, `app_factory.py`, `cli.py`) import `api/`, `websocket/`, `app_factory` or `main`.
    - The data layer (`models`, `models_*`, `database`, `schemas/`) never imports services, toolboxes, the runtime, `llm_service`, workers or connectors.
    - Services never import `agent_toolboxes/`, `agent_tools` or the runtime modules directly. Declared exceptions: `dashboard_service` imports `runtime_adapter` lazily, and `tool_approval` imports `runtime_adapter` and `agent_toolboxes.shared`.
    - `connectors/` never imports services, toolboxes, the registry, the runtime or workers.

    `tests/test_import_contracts.py` fails when a new top-level module is missing from the first contract.

## Quality gates

CI (`.github/workflows/ci.yml`) runs on pull requests to `main`, nightly at 03:07 UTC on `main`, and on dispatch. Every gate is also a `task` command.

| Job | Checks |
| --- | --- |
| `changes` | path-based job selection (`scripts/ci_paths.py`) |
| `backend-quality` | `uv lock --check`; dry-run install for the glibc 2.17 host; Ruff lint and format; strict mypy on the typed scope (`task typecheck`); import contracts; Vulture and deptry; wheel smoke test (`task package:smoke`); tests with coverage (`task coverage`) and changed-line coverage (`task coverage:diff`, 80% of changed backend lines) |
| `frontend-quality` | Vitest with coverage, ESLint, knip, production build |
| `openapi-check` | committed schema and generated types match the backend (`task openapi:check`) |
| `migration-tests` | the Alembic chain on PostgreSQL |
| `e2e-tests` | Playwright against a real backend, 4 shards; `e2e-report` merges the reports |
| `docs-build` | `task docs` plus the backend tests that read the docs tree |
| `semgrep`, `dependency-audit` | the security scans ([auth and security](auth-security.md#dependency-vulnerability-audit)) |
| `ci-result` | last job; fails when any job failed, or when a PR run was not full |

Floors and budgets:

- **Coverage.** Backend floors in `[tool.janus.coverage]` (88% statements, 72% branches), gated by `scripts/coverage_gate.py`. Frontend floors in `frontend/vitest.config.ts`. Floors only go up.
- **Complexity and size.** Ruff `C901` at most 15 per function; ESLint `complexity` 15, `max-depth` 4, `max-lines` 400 and `max-lines-per-function` 150. `tests/test_code_quality_structure.py` budgets module length: routers 350 lines, services 500, core modules 600.
- **Duplication.** jscpd (`.jscpd.json`, threshold 1% of production lines), reported weekly, not gated on PRs.

### Baselined hotspots

Existing breaches are baselined, never excluded: `# noqa: C901` lines (re-checked by `tests/test_complexity_baseline.py`), `frontend/eslint.baseline.json`, and per-file exceptions in `tests/test_code_quality_structure.py` (at most 10 lines of slack). An entry may shrink but not grow, and an entry for a file back within the limit fails as stale. This is the cleanup list.

### CI job selection

On a pull request, `scripts/ci_paths.py select` lists the changed files (merge-base diff, through the GitHub API), maps each path to the jobs it can affect with an ordered first-match rule table, and writes the selection and its reasons to the job summary. A skipped job means "not affected", not "failed".

| Changed path | Jobs |
| --- | --- |
| `src/janus/**` | backend, e2e, Semgrep, OpenAPI (models, `database.py`, `config.py` and other migration imports also PostgreSQL migrations) |
| `alembic/**` | backend, migrations, e2e |
| `tests/**` | backend |
| `frontend/src/**` | frontend, e2e, Semgrep |
| `frontend/e2e/**` | e2e |
| `docs/**`, `TECH_SPEC.md` | docs |
| `pyproject.toml`, `uv.lock` | everything, narrowed when only `[tool.*]` tables or the version changed |
| `ci.yml`, `Taskfile.yml` | everything |
| agent files, Markdown outside `docs/`, `ios/` | no suite |
| anything unmatched | everything |

`tests/test_ci_paths.py` checks that every tracked file matches a rule. Detection fails closed: an API error or a file count mismatch fails the `changes` job. The nightly run selects every job and skips itself when `main` has not moved since the last nightly run.

### Draft pull requests

A draft PR runs only the cheap checks. `ci.yml` passes the draft flag to `ci_paths.py select --draft`, which:

- skips `migration-tests`, `e2e-tests` and `semgrep` (`DRAFT_SKIPPED`), and
- sets the `full` output to `false`, which skips the test steps inside `backend-quality` and `frontend-quality`.

Lint, formatting, type checks, import contracts, dead-code scans, builds, the docs build, the OpenAPI check and the dependency audit still run. Marking the PR ready (`gh pr ready`) starts a full run, and every later push to a non-draft PR runs the full selection.

The `main` ruleset requires `ci-result` and `pr-title`. `ci-result` fails a pull-request run whose `full` output is false, so a draft run never satisfies it. A PR is merge-ready only after a full run is green on its final commit. Release Please opens its PR with the `RELEASE_PLEASE_TOKEN` secret so that PR runs its checks like any other. `main` itself is not re-checked per merge; the nightly run covers it.

### CI run bounds

A newer push cancels the older run of the same PR (`concurrency` per workflow and PR). Nightly and dispatched runs are never cancelled. Every runner job has a `timeout-minutes` (`tests/test_workflows.py` fails without one). Reports upload on `!cancelled()` and are kept 14 days.

### CI cost report

`scripts/actions_cost_report.py` reads run and job history (read-only token) and prices it.

- `collect` writes a snapshot of every run in a period: job attempts, conclusions, runner labels and step timestamps. No logs.
- `report` turns a snapshot into JSON and Markdown (schema `1`): per workflow and event, attempt and outcome counts and the medians of execution time, rounded minutes (an estimate, never invoice usage) and wall time, plus per-job medians and the slowest steps.
- `workload` freezes recent PR updates with their selected jobs into `.github/ci-cost/workload.json`, so policy changes can be priced against a fixed workload.

The weekly quality report runs `collect` and `report` over the last seven days.

### Weekly quality report

`.github/workflows/quality-report.yml` runs Mondays at 06:37 UTC and on dispatch; `task quality:report` runs the same locally. The logic is `scripts/quality_report.py`.

- **Checks.** `tests`, `coverage`, `complexity`, `size`, `duplication`, `types`, `semgrep`, `dependencies`, `deployment` (from the `ops-metrics` branch). Each gets one status: `pass`, `fail`, `measured`, `error` or `unavailable`. Error and unavailable checks contribute no numbers.
- **Schema.** `janus.quality-report/1` with fixed top-level keys (`schema`, `generated_at`, `commit`, `ref`, `run`, `tools`, `scope`, `checks`, `hotspots`, `previous`, `delta`, `baselines`, `regressions`). `validate()` rejects any deviation before the report is written, gated or published.
- **History.** On `main` the `history` job appends the report to `reports/quality-report.jsonl` on the orphan branch `quality-history` (`scripts/history_branch.py`, append-only, never force-pushed). Reports from other refs are never published. `task quality:history COUNT=4` prints the newest main reports.
- **Baselines.** Each metric's baseline starts at its first main value and only moves in its better direction. A worse value is listed under `regressions`. Nothing in the report changes a gate floor.
- **Hotspots.** Files ranked by `max complexity / 15 + lines / budget + duplicated lines / 50`; above 1 means at least one limit is exceeded.
- **Failure.** The final `gate` step fails when a gated check is `fail` or `error`, after the report was written and published.

### Monthly structural review

A monthly, read-only review of code structure, run by a coding agent from a bundle. `scripts/structural_review.py` uses only git and local files.

1. `.github/workflows/structural-review.yml` runs `bundle` on the 2nd of each month at 07:41 UTC (and on dispatch). It needs `contents: read` only and uploads the `structural-review` artifact. `task quality:review-bundle` does the same locally.
2. CI has no agent integration, so the review itself runs in a coding-agent session on that bundle and writes `review.json`.
3. `task quality:review-check FILE=review.json` validates it (`janus.structural-review/1`) and renders the report.
4. `task quality:review-record` appends the checked review to `reports/structural-review.jsonl` on `quality-history`. Recording moves the next window's start; it refuses a window already recorded or not on `origin/main`.

The bundle (`janus.structural-review-bundle/1`) holds the commit and time window, the newest four quality reports, the changed files mapped to their nearest `CODEMAP.md`, per-router metrics (lines, routes, helpers, database calls in handlers) and a capped patch. A review lists findings with category, priority, path and line, evidence and an action. Nothing in this path changes code or files issues.
