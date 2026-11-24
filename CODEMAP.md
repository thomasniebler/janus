# Repository Code Map

Start here when you need to understand Janus. This map points to the main submodules and tells you which narrower `CODEMAP.md` to open before reading implementation files.

## How to use this map

1. Pick the submodule that matches the task.
2. Open the linked `CODEMAP.md` for that submodule before reading source files.
3. Follow child-map links only as far as needed for the current task.

## Top-level submodules

| Submodule | Responsibility | Read next |
| --- | --- | --- |
| `src/` | Python package source for the FastAPI app, Typer CLI, domain services, integrations, and background workers | [`src/CODEMAP.md`](src/CODEMAP.md) |
| `frontend/` | Vite/React dashboard, typed API client, TanStack Query usage, and UI composition | [`frontend/CODEMAP.md`](frontend/CODEMAP.md) |
| `tests/` | Backend and integration-focused regression coverage for auth, email processing, status, reminders, and migrations | [`tests/CODEMAP.md`](tests/CODEMAP.md) |
| `docs/` | Zensical-backed documentation source, technical-spec child docs, navigation config, and generated site output | [`docs/CODEMAP.md`](docs/CODEMAP.md) |
| `ios/` | Native SwiftUI iOS client scaffold for Janus API access | [`ios/CODEMAP.md`](ios/CODEMAP.md) |
| `alembic/` | Migration environment and schema history for the SQLite database | [`alembic/CODEMAP.md`](alembic/CODEMAP.md) |
| `modules/` | Optional companion apps bundled as Janus submodules (currently `todo-chat`, a Tauri 2 + React desktop companion) | `modules/todo-chat/README.md` |
| `scripts/` | Standalone scripts for local startup/diagnostics (`janus-up.sh`), installing the Todo Chat companion (`install-todo-chat.sh`), deployment target capability checks (`deployment_capabilities.py`, run on the host by the `Deployment capability checks` workflow), post-release validation (`release_validation.py` on the runner, `release_host_checks.py` on the host, streamed over SSH by `release_host_checks.sh`, which retries a dropped connection), the pre-release database snapshot (`pre_release_snapshot.py`), filing release failures in Linear (`linear_release_issue.py`), all run by `Deploy to Uberspace`/`Report release failure`, the backend coverage floors (`coverage_gate.py`, run by `task coverage`), the dependency vulnerability audit (`dependency_audit.py`, `task audit:deps`), the Semgrep security scan (`semgrep_scan.py`, `task security:semgrep`), the CI job selection by changed paths (`ci_paths.py`, run by the `changes` job in `ci.yml`), the offline OpenAPI export and drift check (`export_openapi.py`, `task openapi:export`/`openapi:check`, run by the `openapi-check` job, JAN-139), the GitHub Actions cost and timing report (`actions_cost_report.py`) and the consolidated quality report with its delta against the last main report (`quality_report.py`, `task quality:report`), both run by the weekly `Quality report` workflow, the monthly structural review input and the check and ledger of the `code-quality-auditor` review (`structural_review.py`, run by the `Structural review` workflow and `task quality:review-*`), the release version check with deploy and sampled-availability metrics (`ops_metrics.py`, run by `Deploy to Uberspace` and `Nightly Health Check` and `task ops:*`) on the append-only `ops-metrics` history branch; `history_branch.py` appends to it and to the `quality-history` branch | Read directly |
| `.claude/` | Claude Code `skills/` symlinks to the shared `.agents/skills/` procedures | [`.claude/CODEMAP.md`](.claude/CODEMAP.md) |
| `.agents/` | Repository-scoped agent skills (Codex, Claude Code) for reusable workflows and helper scripts | [`.agents/CODEMAP.md`](.agents/CODEMAP.md) |
| `.github/` | GitHub Actions workflows and repository metadata | [`.github/CODEMAP.md`](.github/CODEMAP.md) |
| `.semgrep/` | Janus-specific Semgrep rules (`janus.yml`: API routes and WebSocket handlers without an auth dependency, unscoped lookups of user-owned rows) and their annotated `semgrep --test` fixture (`janus.py`); `.semgrepignore` at the root lists what the scan skips (JAN-267) | Read directly |
| repository root | Cross-cutting project metadata, developer commands, packaging, and architecture notes | Read the root orchestration files below |

## Root orchestration files

| File | Responsibility |
| --- | --- |
| `README.md` | Project overview and contributor setup |
| `LICENSE` | MIT license for the whole repository |
| `AGENTS.md` | Coding-agent instructions: documentation-housekeeping rules (this map network), navigation workflow, and build/test/lint commands |
| `CLAUDE.md` | Claude Code guidance for this repository; keeps the same conventions as `AGENTS.md` |
| `TECH_SPEC.md` | Concise root entrypoint for the technical-spec wiki; detailed requirements live in `docs/docs/tech-spec/` |
| `Taskfile.yml` | Common backend/frontend build, test, coverage (`coverage`, `coverage:diff`, `coverage:frontend`), lint (including `lint:imports`), dependency-audit, Semgrep (`security:semgrep`), duplication, the weekly quality report and its history, and the monthly structural review (`quality:duplication`, `quality:report`, `quality:history`, `quality:review-bundle`, `quality:review-check`, `quality:review-record`), the offline OpenAPI export, frontend type generation and drift check (`openapi:export`, `openapi:types`, `openapi:generate`, `openapi:check`, JAN-139), and docs commands |
| `.jscpd.json`, `.jscpd.tests.json` | jscpd duplication settings for production code (gated at 1%) and for test code (report only), JAN-265 |
| `pyproject.toml` | Python package metadata, dependencies, and tool configuration: Ruff (rule selection, the mccabe complexity limit, bugbear immutable calls), the strict mypy scope (`[tool.mypy]`), the backend coverage floors (`[tool.coverage.*]`, `[tool.janus.coverage]`), the import-linter contracts (`[tool.importlinter]`), and the accepted dependency-audit and Semgrep findings (`[tool.janus.dependency_audit]`, `[tool.janus.semgrep]`) |
| `release-please-config.json`, `.release-please-manifest.json` | Release Please manifest config and current version; the config also bumps the `janus` entry in `uv.lock` on each release PR (JAN-290) |
| `package.json` | Root JavaScript tooling, including Tailwind asset builds used outside the Vite app |

## Common task-to-map shortcuts

- API route, worker, data model, or service change -> `src/CODEMAP.md`, then `src/janus/CODEMAP.md`
- Dashboard page or component change -> `frontend/CODEMAP.md`, then `frontend/src/CODEMAP.md`
- Reusable agent skills -> `.agents/CODEMAP.md` (Claude Code symlinks in `.claude/CODEMAP.md`)
- Documentation rendering or content update -> `docs/CODEMAP.md`
- Schema/history/regression around database upgrades -> `alembic/CODEMAP.md` and `tests/CODEMAP.md`
