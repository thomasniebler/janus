# Architecture Overview

## Product Shape

Janus is a private, passive AI assistant designed to help users manage their digital lives without compromising privacy. It monitors communications, synchronizes with productivity tools, and provides a consolidated, AI-driven overview via a web-based dashboard, CLI, and native iOS client scaffold.

## Technology Stack

### Backend

- **Framework:** FastAPI on Python 3.12+.
- **Database:** SQLite with SQLAlchemy ORM.
- **Migrations:** Alembic.
- **Task management:** Background threads using Python `threading` for IMAP and planner synchronization.
- **AI integration:** LangChain abstraction supporting Ollama-compatible local models and cloud providers.
- **Security:** JWT-based authentication with secure HTTP-only cookies.

### Frontend

- **Core:** React 19 with TypeScript.
- **Build tool:** Vite 7.
- **Styling:** Tailwind CSS v4 with class-based theming — three interchangeable dark app shells (classic glass `dark`, Terminal `ui-v2`, and Rusty Console `ui-v3`), see [Interchangeable App Shells](frontend.md#interchangeable-app-shells).
- **Data fetching:** TanStack Query v5 and Axios.
- **Icons:** Lucide React.
- **Communication:** WebSockets for real-time AI briefing and chat, plus `/ws/changes`, which tells the tab which data domains a committed backend write changed so TanStack Query refetches them (JAN-104).

### Mobile

- **Core:** SwiftUI for iOS 17+.
- **Networking:** `URLSession` with cookie-backed Janus session auth.
- **Configuration:** Runtime-configurable Janus API base URL persisted on-device.

## Core Backend Services

- **`AuthService`:** Manages user registration, login, JWT session handling, and session-related auth behavior.
- **`EmailService`:** Manages email accounts and prioritization rules.
- **`IMAPMonitor`:** Handles long-running connections to mail servers, initial sync, and IDLE listening.
- **`EmailProcessor`:** Orchestrates rule evaluation and AI classification.
- **`PlannerService`:** Factory over read-only imported-planner connectors. Implemented today: Google Calendar, Google Tasks (`connectors/google.py`), Outlook calendars (`connectors/microsoft_calendar.py`), and Jira (`connectors/jira.py`). Azure DevOps and O365 are named in `models.Provider` and the original project roadmap but have no connector implementation yet — `PlannerService.get_connector()` falls through to `None` for both (see the `# Placeholder for O365 and ADO` comment), so accounts configured with those providers sync nothing. `GoogleCalendarConnector.fetch_events()` syncs the calendars listed in `PlannerAccount.selected_calendar_ids` (defaulting to `primary` alone), configurable via `GET`/`PUT /api/planner/google/calendars`, so shared/secondary Google calendars aren't silently skipped (the default `primary` alias marks the calendar Google flags `primary` as selected in that listing, since Google lists it under its real id; JAN-221); connector fetch failures now propagate instead of being swallowed, so `PlannerService.sync_all()`'s existing per-account `try`/`except` records them in `PlannerAccount.last_sync_error` (surfaced in the frontend's Google Account settings card) rather than only appearing in backend logs. `JiraConnector` maps each issue's status onto `TaskStatus` by Jira's fixed status category (`new` → Todo, `indeterminate` → In Progress, `done` → Done; a status name matching a Janus status otherwise, else Todo): the raw, workflow-specific name ("To Do", "In Review") used to be stored as-is and broke every later read of imported tasks (JAN-221). Disconnecting Google deletes the accounts' imported `CalendarEvent`/`TaskRecord` rows with them, since the rows have no cascade and SQLite reuses a freed account id (JAN-221).
- **`LocalPlannerService`:** Owns Janus-managed planner tasks, single-instance events, suggestion review flows, and the unified read layer that combines local writable items with imported context.
- **`LLMService`:** Handles prompt templating and streaming communication with AI models.

## Build Automation

- **Unified Taskfile:** Janus uses `Taskfile.yml` to manage development workflows.
- **Frontend synchronization:** The `run` task ensures that the React frontend is built whenever `frontend/src` changes so the served dashboard reflects the latest code.
- **Operations metrics (JAN-269):** `task ops:verify-version`, `ops:probe`, `ops:metrics` and `ops:export` run `scripts/ops_metrics.py`, the same script the deploy and nightly workflows call: release version check, deploy duration, sampled availability, and the append-only `ops-metrics` history branch (`scripts/history_branch.py`). Definitions and schemas: [deployment-operations.md](deployment-operations.md#deploy-and-availability-metrics).

## Quality Gates

CI (`.github/workflows/ci.yml`) runs on every pull request to `main` (opened, pushed to, reopened, or marked ready for review), nightly on `main`, and on manual dispatch. A pull request runs the jobs its changed paths can affect ([CI job selection](#ci-job-selection)); a draft pull request runs only the cheap ones ([Draft pull requests](#draft-pull-requests)). The nightly run and a dispatch run every job. Merges to `main` are not re-run per push, to keep GitHub Actions usage down; the nightly run is where a PR that passed against an older `main` is caught. Each gate below is also a `task` command, so a contributor runs the same check locally.

- **Backend (`backend-quality`):**
  - `uv lock --check` and a dry-run install for the glibc 2.17 host;
  - Ruff lint and format;
  - strict mypy on the typed scope (`task typecheck`);
  - the import-linter contracts (`task lint:imports`);
  - Vulture and deptry (`task lint:dead:backend`);
  - building the wheel and installing it as a uv tool outside the repository (`task package:smoke`, `scripts/wheel_smoke.sh`);
  - the test suite with coverage (`task coverage`).
- **Backend coverage (JAN-261):**
  - `task coverage` measures statement and branch coverage of `src/janus` and lists every skipped test with its reason (`-rs`).
  - It writes `coverage.xml`, `coverage.json`, and `htmlcov/`, and CI keeps them as the `backend-coverage` artifact.
  - `scripts/coverage_gate.py` gates the two numbers separately against the floors in `[tool.janus.coverage]` in `pyproject.toml`. The floors are the measured main baseline rounded down: on 2026-10-04 that measured 88.38% statements and 72.87% branches, so the floors are 88% and 72%.
  - Floors are raised when coverage improves and never lowered to let a PR pass.
- **Backend test runtime (JAN-311):**
  - `task coverage` prints the 30 slowest setup, call, and teardown phases (`--durations=30`).
  - Most API tests create a user and log in. At the production bcrypt cost (12), each hash or check takes about 0.3 s, which was half of the suite's time. So `tests/conftest.py` hashes at cost 4 by default (`fast_password_hashing`). Only the cost factor changes; hashing and verification run the production code, and the cost is stored in each hash.
  - `tests/test_auth.py` is marked `production_password_cost` and keeps cost 12 for hashing, verification, and the legacy-hash regressions. `tests/test_fixture_isolation.py` checks that the switch is per test.
  - Measured on one 4-core machine (2026-10-05): the coverage run went from 547 s to 267 s, with identical coverage (88.99% statements, 74.40% branches).
  - What remains is mostly real work: the Alembic upgrade and downgrade chains in `test_database_migrations.py` (about 45 s) and the production-cost auth tests (about 20 s).
  - pytest-xdist was not adopted. The serial suite meets the target, and workers would need worker-local databases, runtime paths, and background-thread cleanup first.
- **Changed-line coverage (JAN-262):**
  - On pull requests, `task coverage:diff` runs diff-cover on `coverage.xml` against the PR's base branch, using the merge base. CI checks out the full history for that.
  - Fewer than 80% of the changed executable backend lines being covered fails the job.
  - A diff with no measurable lines (docs, frontend, tests) reports that and passes. `diff-cover.html` and `diff-cover.json` go into the same artifact.
  - Only `src/janus` is measured, so test and script changes never count as uncovered lines.
- **Frontend (`frontend-quality`):**
  - Vitest with V8 coverage (`task coverage:frontend`, `npm run test:coverage`);
  - ESLint;
  - knip (`task lint:dead:frontend`);
  - the production build.
- **OpenAPI drift (`openapi-check`, JAN-139):**
  - `task openapi:check` fails when the committed schema (`frontend/src/generated/openapi.json`) or the generated frontend types (`frontend/src/generated/api.d.ts`) no longer match the backend. A failure is fixed with `task openapi:generate` and a commit of both files.
  - The export (`scripts/export_openapi.py`) is offline: it builds the schema from the FastAPI app object in-process, without Uvicorn, startup hooks, workers, a database, or the network, and it does not depend on `public_api_docs_enabled` (production keeps `/openapi.json` closed). See [Frontend: generated API types](frontend.md#validation-and-resilience).
  - It runs when backend code, `scripts/export_openapi.py`, `frontend/src/generated/**`, or the frontend manifest or lockfile change ([CI job selection](#ci-job-selection)).
- **Frontend coverage (JAN-261):**
  - Every file under `frontend/src` counts, whether a test imports it or not. Tests, stories, `*Fixtures.ts`, `src/test/`, generated API types, and declaration files are excluded.
  - The floors live in `vitest.config.ts` (`coverage.thresholds`). The 2026-10-04 baseline was 61.84% statements, 57.25% branches, 51.72% functions, and 62.2% lines, so the floors are 61%, 57%, 51%, and 62%.
  - The report (text summary, JSON, HTML, Cobertura) is the `frontend-coverage` artifact.
- **Type checking (JAN-260):**
  - `task typecheck` runs strict mypy with the pydantic plugin on the `files` in `[tool.mypy]` of `pyproject.toml`. That scope is `schemas/` plus the pure helpers and runtime contracts: `capability_profile`, `plan_graph`, `prompt_injection`, `request_origin`, `services/page_context` (the chat page-context snapshot, JAN-250), `services/ui_actions` (the chat page-action allowlists and broker, JAN-251), `services/ui_introspection` (the chat UI-introspection sanitizers and broker, JAN-284), `services/ui_blocks` (the chat UI-block catalog and collector, JAN-252), `services/image_generation` (the cloud image providers, JAN-253; `services/image_store` reads ORM attributes and waits for JAN-136), `time_utils`, `tool_registry`, `trigger_adapters`, and `trigger_events`.
  - Imports from outside the scope are followed silently, so only the scope reports errors.
  - The one third-party override is `re2`, which has no type stubs. `prompt_injection.py` describes the part of it that it uses with a Protocol.
  - **Not checked yet:** every module that reads ORM attributes (services, API routers, the runtime, `tool_policy`, `plan_state`, and others). The `janus.models` package (`src/janus/models/`) declares columns with `Column(...)`, not `Mapped[...]`, so mypy types each attribute as `Column[...]` rather than its value. Moving the models to `Mapped[...]`, one domain module at a time now that JAN-136 split them by domain, is the next step.
  - The scope grows one module at a time. `ignore_errors` and blanket `Any` casts are not used.
  - The frontend keeps `tsc` (`tsc -b` in `npm run build`).
- **Backend lint rules (JAN-259):**
  - Ruff checks pycodestyle, Pyflakes, isort and pydocstyle (`E`, `F`, `I`, `D`), plus bugbear (`B`), pyupgrade (`UP`), simplify (`SIM`), and cyclomatic complexity (`C901`, at most 15 per function).
  - FastAPI's and Typer's dependency markers (`Depends`, `Query`, `Option`, …) are allowed in argument defaults.
  - `UP042` (`str, Enum` to `StrEnum`) is off, because it would change how stored and serialized enum members render.
  - Existing findings are baselined line by line with `# noqa: <code>`, never by excluding a file. A new finding fails `task lint` and CI.
  - A `noqa` would hide any complexity, so `tests/test_complexity_baseline.py` re-runs C901 with `--ignore-noqa`. Each baselined function is held at its measured complexity, a new function over 15 fails, and so does an entry for a function that has dropped back to 15 or below.
  - The pre-commit hook (`prek`), `task lint`, and CI all read the same `pyproject.toml`.
- **Frontend lint rules (JAN-259, JAN-264):** ESLint applies these limits to production code under `frontend/src`. Stories, tests, `*Fixtures.ts`, `src/test/`, and generated types are exempt.
  - `complexity` 15;
  - `max-depth` 4;
  - `max-nested-callbacks` 4;
  - `max-lines` 400 per file and `max-lines-per-function` 150, both not counting blank lines and comments.
  - Files that already broke a limit are held at their measured value in `frontend/eslint.baseline.json`. They may shrink but not grow.
- **Backend size budgets (JAN-264):** `tests/test_code_quality_structure.py` sets line budgets per module kind: API routers 350 lines, services 500, top-level core modules 600.
  - Each hotspot exception is a bounded baseline: at most 10 lines above the file's real size.
  - An exception for a file already within the default limit fails as stale.
  - So a hotspot cannot quietly grow into old slack, and a budget is raised only on purpose, with a reason in the comment next to it.
- **Duplication (JAN-265):** jscpd 5.4.0 (`.jscpd.json`, `task quality:duplication`) looks for clones of at least 8 lines and 70 tokens in production code: `src/janus` and `frontend/src`.
  - It skips generated API types, tests, stories, fixtures, `node_modules`, and everything git ignores (lockfiles and build output are outside the scanned paths).
  - Baseline on 2026-10-04: 22 clones, 0.70% of lines (Python 0.35%, TSX 1.75%). The gate is 1%: above the baseline with room for normal churn, low enough that a large copy-paste fails. Lower it when the baseline drops.
  - Test code (`.jscpd.tests.json`: everything under `tests/`, frontend tests, stories, fixtures, `src/test/` helpers, and the e2e specs and helpers) is reported separately and not gated, because test setup legitimately repeats. Baseline: 79 clones, 2.92% of lines.
  - The weekly `Quality report` workflow runs both through `scripts/quality_report.py` ([Weekly quality report](#weekly-quality-report)) and uploads the JSON and HTML reports, which list every clone with its locations, in the `quality-report` artifact. Duplication is not checked on pull requests yet. Use the report to find candidates; do not merge clones where that costs readability.
- **Dead-code and dependency exceptions (JAN-266):** Vulture, deptry, and knip (`task lint:dead`) gate in CI; their exceptions and blind spots are:
  - **Vulture:** scans `src/janus` only, at 80% confidence. `tests/` is out, because fixtures and mocks trip its unused-argument heuristics. The only ignored names are the `__exit__` parameters `exc_type`, `exc_val`, `exc_tb`. The weekly report lists the 60–79% candidates, mostly FastAPI routes and Pydantic fields found by reflection, without gating them.
  - **deptry:** all rules are on. DEP003 (a transitive dependency imported directly) was ignored until JAN-266; the four packages it flagged (`pydantic`, `starlette`, `requests`, `segno`) are now declared. DEP002 (declared but not imported) lists, in `pyproject.toml`, the runtime packages frameworks load implicitly. `azure-devops`, which had no call site, was removed (JAN-397); an Azure DevOps connector would add it back with its first call site.
  - **knip:** `frontend/` only. `knip:ci` excludes unused exports and types: on 2026-10-04 that hides 5 files with unused exports and 3 with unused types, mostly `lib/schemas.ts` and `types.ts`, which a generated API client (JAN-139) would replace. The weekly report lists them. `src/generated/**` is ignored, and the `uv` binary that `package.json` scripts call is allowed.
  - **Not covered on purpose:** the root `package.json`, which only builds Tailwind CSS for the server-rendered pages and has no source to scan, and `modules/todo-chat/`, a separately installed companion module with its own toolchain. The dependency audit still checks both lockfiles.
- **Baselined hotspots (follow-up):** the entries in `frontend/eslint.baseline.json`, the backend budget exceptions, and the `# noqa` lines are the cleanup list. Lower an entry when its code improves and delete it once the code is within the limit.
  - **Worst frontend hotspots:** `pages/Dashboard.tsx` (complexity 48, 876 lines), `pages/Settings.tsx`, `WhatsAppAccountManager.tsx`, `GoogleCalendarConfig.tsx`, and `TopicManager.tsx`. `types.ts` and `api.ts` are declarative and would shrink with a generated API client (JAN-139).
  - **Worst backend hotspots:** `websocket/chat.py:ws_chat` (complexity 30), the toolbox builders in `agent_toolboxes/`, and `llm_service.py` (1166 lines; its `LLMService.__init__` dropped below the complexity limit when the per-provider model-name resolution moved into `_model_name` and the GitHub Models / Azure OpenAI clients into `services/llm_provider_clients.py`, JAN-340). The former `models.py` (1620 lines) is now the `models/` package of domain modules, each within the 600-line core default (JAN-136).
- **Other jobs:**
  - `migration-tests`: the Alembic chain on PostgreSQL (`tests/test_postgres_migrations.py`). The SQLite migration tests are part of the backend suite, which is selected whenever this job is;
  - `e2e-tests`: Playwright against a real backend, split into 4 shards (`e2e-tests (1/4)` … `(4/4)`, JAN-345). Each shard is its own runner with its own backend on a fresh database, passes `--shard=N/4` through `task test:e2e:frontend -- …`, and uploads a blob report; `e2e-report` merges the blobs into one HTML report (artifact `e2e-report`) and also runs after failed shards. Sharding is safe because specs never depend on state other specs left (JAN-336);
  - `docs-build`: the documentation site, plus the backend tests that read the docs when `backend-quality` does not run;
  - `semgrep` and `dependency-audit`: the security scans, called from `semgrep.yml` and `dependency-audit.yml` (see [Authentication and Security](auth-security.md));
  - `pr-title` (`pr-title.yml`): conventional-commit titles for squash merges. It also runs when a title is edited, without re-running CI.

### CI job selection

On a pull request, the `changes` job runs `scripts/ci_paths.py` (JAN-309, JAN-313). It lists the PR's changed files through the GitHub API (the merge-base diff, renamed files under both names, every page), maps each path to the jobs that can be affected by it, and writes the selection and the reason for each job to the job summary. Each other job runs only when selected; a skipped job is an intentional "not affected", not a failure. The nightly run selects every CI job, so `main` keeps complete validation within a day of a merge; it skips itself (every job skipped) when `main` is still at the commit the last completed nightly run checked. The security scans keep their own schedule on `main`.

| Changed path | Selected jobs |
| --- | --- |
| `src/janus/**` | backend, e2e, semgrep, openapi |
| `models/` (the ORM package), `models_knowledge.py`, `database.py`, `config.py`, `runtime.py`, `time_utils.py`, `services/database_copy_service.py` (what the migrations and the PostgreSQL tests import) | the above plus migrations |
| `alembic/**`, `alembic.ini` | backend, migrations, e2e |
| `tests/**` | backend (the two migration test files and `conftest.py` also migrations) |
| `scripts/**` | backend, semgrep (`dependency_audit.py` also dependency audit; `export_openapi.py` also openapi; `ci_paths.py` everything) |
| `frontend/src/**` | frontend, e2e, semgrep (`lib/domainChanges.ts`, read by a backend test, also backend; `generated/**` also openapi) |
| `frontend/e2e/**`, `playwright.config.ts` | e2e |
| other `frontend/**` tooling, assets and config | frontend, e2e (`package.json` and `package-lock.json` also dependency audit and openapi) |
| `modules/**` | backend (manifests and lockfiles also dependency audit) |
| root `package.json` / `package-lock.json` | dependency audit |
| `docs/**`, `TECH_SPEC.md` | docs |
| `.semgrep/**`, `.semgrepignore`, `semgrep.yml` | semgrep (`.semgrep/**` and `semgrep.yml` also backend) |
| `dependency-audit.yml` | dependency audit, backend |
| `hatch_build.py` | backend (the wheel smoke test) |
| `pyproject.toml`, `uv.lock` | everything, unless narrowed (below) |
| `ci.yml`, `Taskfile.yml`, `.python-version`, `.gitignore` | everything |
| other `.github/workflows/*.yml` | backend (`tests/test_workflows.py` checks timeouts, concurrency and selection wiring) |
| agent definitions (`.claude/`, `.codex/`, `.agents/`, `.gemini/`), Markdown outside `docs/`, other `.github/` metadata, `ios/`, release metadata, `prek.toml`, `.jscpd*.json` | no suite (only `pr-title`) |
| any path no rule matches | everything |

The rules are ordered and the first match wins. `tests/test_ci_paths.py` checks that every tracked file matches a rule, that every file Semgrep scans selects Semgrep, and that every backend test that reads the docs tree is in `DOCS_TESTS`, which the docs job runs.

`pyproject.toml` and `uv.lock` are compared table by table at the merge base and the PR head:

- **Narrowed:** a change confined to `[tool.*]` configuration selects only the jobs that read it. `tool.janus.semgrep` adds Semgrep, `tool.janus.dependency_audit` adds the dependency audit, `tool.pytest` adds migrations, and `tool.hatch` (wheel contents) selects backend. A change to `project.version` alone, or a lockfile change to Janus's own version alone (a release bump), selects backend.
- **Not narrowed:** dependencies, dependency groups, `tool.uv`, new tables, and anything unreadable keep the full selection.

Detection fails closed. An API error, a missing PR context, or a listing whose count does not match the PR's `changed_files` (including PRs at the API's 3000-file limit) fails the `changes` job and with it the run. A narrowing lookup that fails keeps the full selection.

There is no required status check, because branch protection and rulesets need GitHub Pro for a private repository (checked 2026-10-05). So no aggregate gate job is added; reviewers read the individual checks.

### Draft pull requests

A draft pull request runs only the cheap checks, so the iterations before a PR is ready cost little GitHub Actions time. `ci.yml` passes the PR's draft flag to `ci_paths.py select --draft`, which then:

- defers `migration-tests`, `e2e-tests` and `semgrep` (`DRAFT_SKIPPED`; the job summary marks them "skipped, draft"), and
- sets the `full` output to `false`, which skips the backend tests with coverage, the changed-lines coverage check, and the frontend tests with coverage inside `backend-quality` and `frontend-quality`.

Lint, formatting, type checks, import boundaries, dead-code scans, the Uberspace install check, the frontend build, the docs build, the OpenAPI drift check and the dependency audit still run when selected. Marking the PR ready for review (`gh pr ready`) triggers a full run (`ready_for_review`), and every later push to the non-draft PR runs the full selection. A PR is merge-ready only after a full run is green on its final commit; a green draft run is not enough.

The `main` ruleset enforces this: it requires the `ci-result` and `pr-title` checks, and the branch must be up to date with `main`. `ci-result` is the last job of `ci.yml`. It needs every other job, fails when any of them failed or was cancelled (jobs the path selection skipped count as passed), and fails a pull-request run whose `full` output is false, so a draft run never satisfies the rule. Required reviews are off (a solo maintainer cannot approve their own PRs); open review threads must be resolved and CodeQL must pass. Release-please's release PR is opened with the Actions token, whose pull-request events start no workflows, so `release-please.yml` dispatches `ci.yml` and `pr-title.yml` on the release branch after creating or updating it (`pr-title.yml` then reads the title of the open PR for that branch).

Agents open pull requests as drafts and mark them ready once the change is validated and the docs pass is pushed (CLAUDE.md, AGENTS.md).

### CI run bounds

- **Cancellation (JAN-310):** a newer push to the same pull request cancels its older CI run (`concurrency` keyed by workflow and PR number). The PR title check does the same in its own group. Nightly and dispatched runs get a group per commit and are never cancelled. Deploys keep the serialized, non-cancelling `deploy-uberspace-production` group.
- **Uploads:** report uploads use `!cancelled()`, so a superseded run stops at once, and CI and security artifacts are kept for 14 days. That is a storage change, not a minute saving.
- **Timeouts:** every runner job has a `timeout-minutes`, set from the measured durations of 2026-09-28 to 2026-10-05 with headroom:

  | Job | Median (max) measured | Timeout |
  | --- | --- | --- |
  | `backend-quality` | 6.5 (16.9) min | 20 |
  | `e2e-tests` | 2.6 (3.4) min; by 2026-11 every full run hit the 10 min bound, and locally the suite takes 13 min on one worker. Since JAN-345, 4 shards of 2.3–3.2 min each locally | 10 per shard |
  | `e2e-report` (JAN-345, not yet measured in CI) | an install and a merge | 5 |
  | `migration-tests` | 1.3 (7.2) min | 10 |
  | `frontend-quality` | 1.2 (1.6) min | 10 |
  | `docs-build` | 0.4 min, with one hung run of 28.9 min | 5 |
  | `openapi-check` (JAN-139, not yet measured in CI) | about 0.5 min locally after installs | 10 |
  | `semgrep`, `dependency-audit` | 1.3, 0.5 min | 10 |
  | `changes`, `pr-title` | seconds | 5, 2 |
  | deploy `deploy` / `validate` / `soak` | 2.3 / 0.4 / 15.5 min | 20 / 10 / 25 |
  | Release Please, release artifacts, failure report, capability checks, nightly probe, quality report | under 1 min | 5–20 |
  | structural review `bundle` (JAN-271) | not yet measured on Actions; 3 s locally | 10 |

  `tests/test_workflows.py` fails when a runner job has no timeout.

### CI overhead decisions (JAN-314)

Each candidate is kept only if it saves runner minutes.

- **Adopted:**
  - `playwright install --only-shell chromium`. Headless runs already use `chromium-headless-shell`, so the full browser download (part of the 27 s install step) is skipped. The test count and assertions are unchanged.
  - `migration-tests` runs only the PostgreSQL tests. The SQLite migration tests (about 33 s of its 44 s test step) already run in `backend-quality`, and every selection that includes migrations includes the backend (`tests/test_ci_paths.py`). That cuts the job from 2 rounded minutes to 1.
  - Report artifacts are kept 14 days and skipped on cancelled runs (storage, not minutes).
  - The PR title check moved to `pr-title.yml`, so editing a title refreshes it without re-running CI.
  - **No CI run per push to `main` (2026-10-17).** In the baseline week, CI on `main` pushes was 63 runs and about 900 of 2,929 rounded minutes, repeating checks the PRs had just passed. A nightly scheduled run replaces it and skips itself when `main` has not moved.
  - **Draft pull requests run only the cheap checks (2026-10-17).** The baseline week had 135 PR runs for about 20 PRs, about 1,980 minutes, most of them on pushes made while a PR was still being iterated on. The test suites now run once a PR is ready for review ([Draft pull requests](#draft-pull-requests)).
- **Rejected:**
  - **Sharing one frontend build with `e2e-tests`:** it saves about 12 s, but adds an upload and download, makes `e2e-tests` wait for `frontend-quality`, and still needs a build when only the backend is selected.
  - **Folding the title check into `changes`:** a title edit would then re-run CI, or leave its check stale.
  - **Folding `docs-build` into `changes`:** a docs failure would read as a selection failure.
  - **Browser or dependency caching:** setup already takes seconds.

### CI cost report

`scripts/actions_cost_report.py` (JAN-308) reads run and job history (`actions: read`; `contents: read` for changed files) and never anything else.

- **Commands:**
  - `collect` writes a snapshot: every run in a period, with each job attempt's name, conclusion, runner labels, and job and step timestamps. It keeps no logs.
  - `report` turns a snapshot into compact JSON and Markdown.
  - `workload` freezes the latest pull request updates with their selected jobs.
- **Report:** per workflow and event, it counts attempts, complete successful runs, failed and cancelled runs, and re-run attempts separately. It gives the medians of three numbers:
  - **execution:** the sum of the job durations;
  - **rounded estimate:** each job rounded up to a whole minute, as GitHub rounds hosted runners. It is an estimate, never invoice usage;
  - **wall time:** first job start to last job end.

  It also gives per-job medians with queue and environment wait, and the slowest steps of `backend-quality` and `e2e-tests`.
- **Exclusions:** skipped jobs and jobs cancelled before a runner picked them up are excluded.
- **Weekly run:** the weekly `Quality report` workflow runs `collect` and `report` over the last seven days against the frozen workload model in `.github/ci-cost/workload.json`, and appends the Markdown to its summary.
- **Report schema** (`schema: 1`):
  - `period`, `collected_at`, `runner_classes`, and `totals`;
  - `workflows`, per workflow and event: attempts, outcome counts, medians, rounded minutes over all attempts and over attempts that were not complete, and per-job medians;
  - `complete_runs`: links to the last 20 complete CI runs;
  - `top_steps`;
  - with `--workload`, the frozen workload priced under both policies; with `--baseline`, a before-and-after table.

**Baseline (measured 2026-10-05; the raw report is not kept in the repository).** Runs created 2026-09-28 to 2026-10-05, all on `ubuntu-latest` hosted runners:

| Workflow (event) | Attempts | Complete | Failed | Re-runs | Median execution | Median rounded | Median wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CI (pull request) | 135 | 105 | 28 | 5 | 12.0 min | 16 min | 6.5 min |
| CI (push to `main`) | 63 | 58 | 4 | 1 | 12.4 min | 15 min | 6.9 min |
| Semgrep (pull request) | 15 | 12 | 3 | 1 | 1.3 min | 2 min | 1.3 min |
| Dependency audit (pull request) | 15 | 15 | 0 | 1 | 0.5 min | 1 min | 0.5 min |

- **Week totals:** 2929 rounded minutes (estimate) over 239 attempts. PR CI attempts that were not complete account for 236 of them.
- **Per-job medians (rounded):** `backend-quality` 7 (its coverage step alone 363 s), `e2e-tests` 3, `frontend-quality` 2, `migration-tests` 2, `docs-build` 1, `pr-title` 1.
- **Deploys:** each soak holds a runner for 15.5 minutes (one sample in the window).
- **Frozen workload:** the latest 20 completed PR updates (`workload.json`, with run links and categories):

  | Selected jobs | Weight |
  | --- | --- |
  | every job but the dependency audit | 0.35 |
  | every job | 0.40 |
  | backend, docs, Semgrep | 0.10 |
  | backend only | 0.05 |
  | backend, e2e, docs, Semgrep | 0.05 |
  | backend, frontend, e2e, docs, Semgrep | 0.05 |

  The window was a CI-tooling sprint (JAN-259 to JAN-268), whose PRs changed `Taskfile.yml`, `ci.yml`, and Python dependencies. At baseline job costs, path selection alone prices the workload at 358 instead of 380 rounded minutes (−5.8%).
- **First measured post-change run (PR #503, one sample, 2026-10-05):**
  - The backend coverage step took 286 s against the 363 s baseline median (−21%, against −51% on a local 4-core machine). `backend-quality` took 5.5 minutes, so 6 rounded instead of 7.
  - The PostgreSQL-only `migration-tests` took 66 s; container start alone is 24 s, so it still rounds to 2 minutes.
  - At these costs the frozen workload prices at 338 rounded minutes against the 380 baseline (−11%). That misses the 40% stretch target: 75% of the frozen updates are full-stack, and the new `changes` job costs a rounded minute on every update.
  - The workload model does not count cancelled superseded runs, or the scans skipped on docs-only and agent-only PRs. Those savings, and medians over at least 10 post-change runs, come from the weekly report against `.github/ci-cost/`.


### Weekly quality report

`.github/workflows/quality-report.yml` (JAN-270) runs every Monday at 06:37 UTC and on dispatch. The logic is `scripts/quality_report.py` (`tests/test_quality_report.py`); the workflow only calls it, and `task quality:report` runs the same locally (`ONLY=complexity,size` for a quick subset) and, like the workflow, ends with the gate, so it exits non-zero when a gated check failed or errored. Runs are serialized per ref (`concurrency` group `quality-report-<ref>`, never cancelled), so overlapping main runs queue instead of racing on the history.

- **Checks and status.** One run collects nine checks. Each gets one status: `pass` or `fail` (measured against a gate), `measured` (report only), `error` (the tool ran but produced no usable result), or `unavailable` (the tool or its input is missing, or the check was not selected). A pytest exit code other than 0 (all passed) or 1 (tests failed), such as an interrupted run, an internal or usage error, no tests collected, or a timeout, makes `tests` and `coverage` `error` even when a JUnit file exists, because it describes a partial run. Error and unavailable checks are listed as such in the summary and contribute no numbers.

  | Check | Tool and scope | Gate |
  | --- | --- | --- |
  | `tests` | `uv run pytest` over `tests/`, JUnit totals | no failed or errored test |
  | `coverage` | the same run, `src/janus` statements and branches | the floors in `[tool.janus.coverage]` |
  | `complexity` | Ruff C901 with `--ignore-noqa` and `max-complexity = 0`, so every `src/janus` function is measured | report only (CI gates new findings) |
  | `size` | non-blank lines of `src/janus/**/*.py` (budget 500) and production `frontend/src/**/*.ts(x)` (budget 400) | report only (CI gates the budgets) |
  | `duplication` | jscpd 5.4.0 with `.jscpd.json` and `.jscpd.tests.json` | production percentage at most the `.jscpd.json` threshold (1%) |
  | `types` | mypy on the `[tool.mypy]` files | no error |
  | `semgrep` | `scripts/semgrep_scan.py`, open findings after the exceptions | no open ERROR finding |
  | `dependencies` | `scripts/dependency_audit.py`, open findings after the exceptions | no open blocking finding |
  | `deployment` | the last 30 days of the `ops-metrics` branch (JAN-269, [deploy and availability metrics](deployment-operations.md#deploy-and-availability-metrics)), summarized by `scripts/ops_metrics.py`: deploys, success rate, median duration, sampled availability | report only; `unavailable` until the first deploy or nightly probe has written that branch |

- **Job summary.** Commit and ref, the check table, every measurement with the previous main value, the change and its direction (better, worse, same) and the baseline, the top 10 hotspots, the tool versions, and the scope of each check. Hotspots are files ranked by `max function complexity / 15 + non-blank lines / module budget + duplicated lines / 50`. Each term is 1.0 at its limit, so a file above 1 exceeds at least one limit. Frontend complexity is not measured; frontend files enter the ranking through size and duplication only.
- **Fixed schema `janus.quality-report/1`.** Top-level keys are exactly `schema`, `generated_at`, `commit`, `ref`, `run`, `tools`, `scope`, `checks` (exactly the nine checks above, each `status`, `detail`, `measurements`), `hotspots` (at most 10: `path`, `score`, `max_complexity`, `worst_function`, `lines`, `duplicated_lines`), `previous` (commit and time of the compared main report, or null), `delta` (`previous`, `current`, `change`, `direction` per metric), `baselines`, and `regressions`. Metric names come from the fixed `METRICS` table, each with its unit and whether higher is better. `validate()` checks every level strictly (exact keys of `run`, `scope`, each check, each hotspot, `previous` and each `delta` entry; strings, numbers (never booleans) and the allowed `status` and `direction` values) and rejects a report that differs in any of this, before it is written by `finalize`, gated, or published.
- **Durable history.** Artifacts (`quality-report`, kept 90 days) are a convenience, not the record. The `history` job (`contents: write`, only on `refs/heads/main`, also after a failed gate) appends the finalized report as one line of `reports/quality-report.jsonl` to the orphan branch `quality-history`, through the same append-only helper as the deploy metrics (`scripts/history_branch.py`). The branch is never merged, rewritten, or force-pushed. Reports from other refs are never published, so a branch run cannot move a baseline. `publish` recomputes `previous`, `delta`, `baselines` and `regressions` against the history tip it appends to (again on every push retry), so a report finalized before another main run published never lands with stale values. Only a definite missing branch (`git ls-remote --exit-code` exit 2, or nothing listed) reads as an empty history; any other `ls-remote` failure aborts `finalize` and `publish` instead of resetting the baselines or starting a new orphan branch. Export: `git fetch origin quality-history && git show origin/quality-history:reports/quality-report.jsonl`.
- **Consumers (the [monthly structural review](#monthly-structural-review), JAN-271).** `scripts/structural_review.py bundle` reads the newest main reports this way. Read the report from the history branch, not from artifacts: `task quality:history COUNT=4` (`scripts/quality_report.py latest --count 4`) prints the newest main reports as JSON Lines, oldest first, one `janus.quality-report/1` object per line with sorted keys. A consumer should check `schema`, treat `error` and `unavailable` checks as "no data" rather than zero, read trends from `delta` and the sequence of reports, and take its candidates from `hotspots` (repository-relative paths, `worst_function` as `name:line`). The newest report of a single run is also in the `quality-report` artifact as `quality-report/quality-report.json`, next to `summary.md` (the job summary) and the raw tool outputs under `work/`.
- **Delta and baselines.** `finalize` compares with the newest main report in that history. Baselines ratchet: a metric's baseline starts at its first main measurement and afterwards only moves in its better direction. A value worse than the baseline is listed under `regressions` and flagged in the summary. Nothing in the report lowers a baseline or edits a gate floor (`[tool.janus.coverage]`, the `.jscpd.json` threshold, the complexity and size budgets); those change only through a reviewed commit, as the Quality Gates above say.
- **Failure.** The last step (`gate`) fails the job when a gated check is `fail` or `error`, after the report has been written, uploaded, and, on main, published. That keeps the previous behavior of the duplication gate and extends it to the other gates. An `unavailable` check does not fail the job.
- **Pinned tools.** jscpd 5.4.0 (in the script), Semgrep and its rule commit (`semgrep_scan.py`), pip-audit (`dependency_audit.py`), Ruff, mypy, pytest, and coverage from `uv.lock`, Python 3.12 and Node.js 22.22.2 in the workflow. The report records the versions it actually ran.
- **Baseline measurement (2026-10-11, main at `b733265`, one local run on a shared, heavily loaded machine):**
  - tests: 2752, of which 10 skipped. 2 failed under load (`test_database_migrations.py::test_dashboard_endpoints_upgrade_legacy_database_when_startup_was_skipped` and `test_main.py::test_single_instance_lock_blocks_second_process`); both pass when run alone. The 1430 s duration reflects that load;
  - coverage: 89.84% statements, 76.48% branches (floors 88% and 72%);
  - complexity: 2010 functions, 13 above 15, maximum 30 (`websocket/chat.py:ws_chat`), mean 2.77;
  - size: 13 of 201 backend modules above 500 non-blank lines, and 12 of 149 frontend modules above 400; the largest is `models.py` with 1243;
  - duplication: production 0.64% (25 clones), tests 2.4% (88 clones);
  - types: 0 mypy errors in 28 files;
  - dependencies: 0 blocking, 1 reported;
  - Semgrep: no blocking finding beyond the accepted exceptions. That includes the two added for this script: fixed tool argument lists, and parsing its own JUnit file. The local run hit rule timeouts on `models.py` and `Dashboard.tsx`, which the report marks as a failed check, as the CI gate does;
  - deployment: unavailable, because the `ops-metrics` branch (JAN-269, merged the same day) has no record yet;
  - top hotspots: `llm_service.py`, `services/topic_service.py`, `frontend/src/types.ts`, `services/local_planner_service.py`, and `websocket/chat.py`.

### Monthly structural review

A monthly, read-only review of the code's structure (JAN-271), run by a coding agent (for example Claude Code or Codex) from the bundle described below. It reuses the [weekly quality report](#weekly-quality-report) as its measurements instead of a second metrics pipeline. The logic is `scripts/structural_review.py` (`tests/test_structural_review.py`); it uses only git and local files, never an LLM, an issue tracker, or the network beyond `git fetch`.

- **Execution path.**
  1. `.github/workflows/structural-review.yml` runs `bundle` at 07:41 UTC on the 2nd of each month, and on dispatch with an optional `base` commit. It has `contents: read` only, uses no secret, and writes nothing but its artifact (`structural-review`, 90 days) and job summary. `task quality:review-bundle` does the same locally (`BASE=<sha>` to override the window).
  2. **Blocker: GitHub Actions has no agent integration for this repository** (no LLM credentials, no issue tracker access). The job summary says so in its "Agent review: blocked in CI" section instead of pretending a review ran. The review itself runs in a Claude Code or Codex session, which has the repository and Linear access (used read-only here): ask `code-quality-auditor` for Mode 3 on the artifact or a local bundle.
  3. The agent writes `review.json`; `task quality:review-check FILE=review.json` validates it and renders `structural-review/review.md` for the caller.
  4. When the caller asks, `task quality:review-record FILE=review.json` appends the checked review to `reports/structural-review.jsonl` on the `quality-history` branch, through `scripts/history_branch.py` like the quality reports. It refuses a window whose head is not on `origin/main`, a window already recorded (one record per window), and a window whose head does not descend from every recorded head (a review completed out of order), so the ledger only moves forward. Recording is what moves the next window's start.
- **Window.** The bundle states the commit range (`base`, `head`) and the time range (their commit times) explicitly. `base` is the furthest-descendant recorded review head that is an ancestor of the head, whatever the ledger order (`base_source: ledger`); without one, the last first-parent commit at least 31 days before the head (`days`), or the root commit (`root`). An explicit `--base` must be a hex sha and an ancestor of the head (`explicit`). A ledger that cannot be read (anything but a definitely missing branch) aborts the run instead of picking a different window; a quality history that cannot be read only marks the quality input `error`.
- **Bundle (`janus.structural-review-bundle/1`, automatic metrics only).** `bundle.json` holds the window; the newest four main quality reports (`quality-reports.jsonl`, summarized with check statuses, regressions and hotspots; `unavailable` before the first main report, `error` when unreadable, never zeros); the first-parent commits and changed files of `git diff base..head`, each mapped to its nearest `CODEMAP.md`; every CODEMAP with its blob and whether the window changed it; and per-router metrics for `src/janus/api/*.py` at the head: non-blank lines, routes, top-level helpers, and database calls (`db.`/`session.` `query`, `execute`, `add`, `commit`, ...) inside route handlers, the automatic proxy for business logic in routers. `diff.patch` is the patch of `src`, `frontend/src`, `scripts`, `alembic`, `Taskfile.yml` and `pyproject.toml`, cut at a line end after 1.5 MB (the bundle and the prompt say when). `prompt.md` gives the reviewer the window, the bundle digest, and the `review.json` skeleton; `summary.md` is the job summary.
- **Review (`janus.structural-review/1`, qualitative only).** Exactly `schema`, `reviewed_at`, `reviewer` (`claude-code`, `codex` or `human`), `bundle_sha256` (the SHA-256 of the bundle, so a review cannot silently cite other metrics), `window` (equal to the bundle's commit and time range), `findings`, `blockers`, and `notes`. Each finding has exactly `id` (`SR-<n>`, unique), `title`, `category` (`responsibilities`, `router-logic`, `api-contract`, `domain-coupling`), `priority` (`urgent`, `high`, `medium`, `low`, as in Linear), `path` and `line` (a file, not a directory, and a line that exist at the head commit; an empty path, `.` or `..` is rejected), `evidence`, `action` (`cleanup`, `structural-check`, `follow-up`), and `reconciliation` (`existing` with its issue identifier, `proposed`, or `unchecked`). An `unchecked` reconciliation without a blocker is rejected: a missing capability must be reported. A review with anything that looks like a secret or an e-mail address (`scripts/ops_metrics.py`'s guard) is rejected before it is rendered or recorded. The rendered report lists the automatic metrics first and the findings separately, sorted by priority.
- **No automatic actions.** Nothing in this path changes code, opens or merges a PR, or creates, edits, or comments on issues. Findings are reconciled against existing Linear issues read-only; `proposed` ones are a list in the report, and the person who asked files the ones they accept (`pr-shepherd` Mode 3 or `spontaneous-implementation`). API contract drift against the frontend client stays with JAN-139.
- **Baseline (2026-10-12, main at `87baa36`, local run, no review recorded yet):** window `5ae00a4..87baa36` (2026-09-10 to 2026-10-12, `days`); 199 first-parent commits, 894 files, +130854/−16674; the 4.2 MB patch is cut at 1.5 MB; quality reports `unavailable` (the `quality-history` branch had no main report yet); 18 CODEMAPs, 17 changed in the window; 28 routers with 190 routes, 39 top-level helpers, and 61 database calls in route handlers across 13 modules, led by `api/auth.py` (23), `api/planner.py` (14), and `api/skills.py` (11). The bundle took 3 s.

## Cross-Cutting Architecture Rules

- FastAPI routers should stay thin. Request, session, and auth wiring belongs in routers; domain behavior belongs in services.
- **Import boundaries (JAN-263):** four import-linter contracts in `pyproject.toml` (`[tool.importlinter]`) enforce these boundaries. `task lint:imports` and CI check them, and `tests/test_import_contracts.py` fails when a new top-level module is missing from the first contract.
  - **Only the composition roots wire the HTTP and WebSocket layers.** The roots are `main.py`, `app_factory.py`, and `cli.py`, which builds the app to serve it. No other module may import `api/`, `websocket/`, `app_factory`, or `main`, directly or indirectly.
  - **The data layer does not depend on behavior.** `models` (the package and its domain modules), the `models_*` modules, `database`, and `schemas/` never import services, toolboxes, the runtime, `llm_service`, workers, or connectors, directly or indirectly.
  - **Services do not reach into the agent toolboxes or the runtime.** No direct import of `agent_toolboxes/`, `agent_tools`, the agent runtime (`agent_runtime`, `agent_task_runner`, `delegation_runtime`, `runtime_trigger_handler`, `runtime_adapter`), `unattended_execution`, or `trigger_runtime`. Indirect paths through another core service (for example `trigger_definition_service`, which uses the tool registry) are allowed.
    - Declared exceptions: `dashboard_service` imports `runtime_adapter` lazily, to record the tool-less briefing and digest step (JAN-164); `tool_approval` imports `runtime_adapter` and `agent_toolboxes.shared`, because the chat approval gate (JAN-254) wraps confirmation-required tools inside the runtime step and records refusals in its `ToolCallLog`.
  - **Planner connectors are leaf adapters.** `connectors/` never imports services, toolboxes, the tool registry, the runtime, or workers.
  - Services may use workers (the WhatsApp services supervise their connector thread). That is not a boundary today, and neither is any other pair not listed here.
- Janus-owned planner items are the canonical writable planner records. Imported planner providers remain read-only context in the MVP.
- Database access should use the shared session dependency in request paths and explicit `finally` cleanup in long-running/background code.
- Background work is thread-based until a later architecture explicitly splits it.
- Documentation is part of the product surface. User-facing Markdown in `docs/docs/` is served through the backend and rendered in the dashboard.
- The repository maintains hierarchical `CODEMAP.md` files and child technical specs so contributors can navigate without broad ad-hoc searches.
