# Agent guide for Janus

Janus is a privacy-first, local-first personal AI agent: a FastAPI backend with a Typer CLI (`src/janus/`), a React frontend (`frontend/`), and a SwiftUI iOS prototype (`ios/`), backed by SQLite. Architecture lives in [`TECH_SPEC.md`](TECH_SPEC.md) and its children under `docs/docs/tech-spec/`; this file only covers how to work in the repo.

## Finding your way

- Start at the root [`CODEMAP.md`](CODEMAP.md) and follow the nearest child `CODEMAP.md` for the area you change. The `analyze-submodule-map` skill (`.agents/skills/`) is the default way to learn a submodule.
- Search the whole repo only when a map is missing or insufficient.

## Commands

Python uses `uv`, the frontend `npm` in `frontend/`, hooks use `prek`. Prefer `task` targets over hand-rolled commands (`task --list` shows all).

```bash
uv sync --extra documents --extra whatsapp --extra desktop   # all extras, so every test runs
uv run prek install

task run            # backend on :8080
task test           # pytest; single test: uv run pytest tests/test_auth.py::test_password_hashing -v
task lint           # ruff
task typecheck      # strict mypy scope from pyproject.toml
task lint:imports   # import-linter contracts
task lint:dead      # vulture, deptry, knip
task format
task coverage       # backend coverage floors; task coverage:diff for changed lines
task openapi:generate   # after an API change: schema + frontend/src/generated/api.d.ts
task openapi:check      # drift check (CI)
task docs           # build the docs site (Zensical)
task codemap:check  # CODEMAP size and stale-path check
task security:semgrep  # Semgrep with the rules in .semgrep/
task audit:deps     # pip-audit + npm audit
task coverage:frontend  # Vitest with V8 coverage floors
task quality:report # every quality check in one JSON report (also quality:history, quality:duplication)
task quality:review-bundle  # monthly structural review input (then quality:review-check / quality:review-record)
task ops:verify-version  # hosted health endpoint reports the expected version (also ops:probe, ops:metrics, ops:export)

cd frontend && npm run dev     # :5173, proxies the API
cd frontend && npm run build
cd frontend && npm run lint

uv run alembic revision --autogenerate -m "description"
```

## Documentation is part of every change

- **CODEMAPs:** adding, removing, renaming or moving a file, module or directory updates every `CODEMAP.md` that names it, in the same change. Maps say where to look, not how things work; follow the `maintain-codemap` skill.
- **Spec-first:** a change to architecture, a service boundary, a schema or an integration updates the relevant `docs/docs/tech-spec/` page first, then `TECH_SPEC.md` if its index changes.
- **User docs:** a change to setup, configuration, an integration or a visible workflow updates the user guide pages under `docs/docs/` in the same PR. Planned work follows [`delivery-artifacts.md`](docs/docs/tech-spec/delivery-artifacts.md).
- **Tool docs:** every toolbox's `docs_ref` points at a heading in `docs/docs/tools.md`; renaming a heading breaks `tests/test_docs_tools.py`.
- Docs describe the current state only: no ticket history or "used to" prose. A stale doc is a defect, like a failing test.
- On a housekeeping pass, walk every CODEMAP and doc page against the code and fix what drifted.

## Code conventions

- Backend imports are package-rooted (`from janus ...`).
- Routers stay thin; domain logic lives in services. Auth via `Depends(security.get_current_user)` (`get_user_or_none` for optional auth, `get_websocket_user_or_none` for sockets).
- DB sessions come from `database.get_db`; background code closes its sessions in `finally`.
- Background work is thread-based (daemon threads), not async workers.
- Mail rule regexes are security-sensitive: keep `validate_regex()` and `EmailProcessor._safe_search()` intact and extend `tests/test_redos.py` when rule evaluation changes.
- Credentials are Fernet-encrypted at rest (`.janus_key`).
- Unattended (trigger, automation, delegated) tool use is fenced by `tool_policy.py`. Read `docs/docs/tech-spec/llm-agent-safety.md` and `background-workers-runtime.md` before changing what an unattended run can do.
- Frontend: TanStack Query for server state, no global store; `components/features/` own their data, `pages/` compose them.

## Tests

- Bug fixes ship with a regression test for the original failure, including upgrades against existing databases.
- New behavior ships with meaningful tests. Aim for roughly 90–95% coverage over time, but never at the expense of behavioral relevance.
- UI: every new page, route, form or flow gets a Playwright test under `frontend/e2e/`; every component under `components/ui/`, `features/` and `layout*/` has a `*.stories.tsx` covering its meaningful states.

## Delivery

- Work on a branch; `main` is protected by a hook. Scope, plan, implement, validate (`task test`, `task lint`), review your own diff, update docs, then push and open a PR.
- PR titles are conventional commits (`feat(scope): …`, `fix: …`, `chore: …`); release-please derives versions and release notes from them.
- **CI budget:** open PRs as drafts (`gh pr create --draft`); drafts run only cheap checks. Mark ready (`gh pr ready`) once validated; a PR is merge-ready only after a full green run on its final commit. `main` gets a nightly run. See [`architecture-overview.md#draft-pull-requests`](docs/docs/tech-spec/architecture-overview.md#draft-pull-requests).
- **Dependabot:** never merge bumps one by one; combine them with the `combine-dependabot-prs` skill.
- **Production host:** there is no SSH from agent sessions. Add a read-only probe to `scripts/deployment_capabilities.py` and dispatch the `Deployment capability checks` workflow; never add free-form remote commands to workflows.
- Once a task is actionable, keep questions to the user to a minimum; push + PR is the default way to finish.
- If validation hits an unrelated, pre-existing failure that makes the result untrustworthy, fix that blocker first or file it right away as a blocking follow-up.
- Finished work is pushed and has a PR. If that is impossible, say so explicitly and name the local branch and commit.

## Quality gates and history

- **Gates only ratchet.** Never lower a coverage floor, a quality baseline or any other gate to get CI green; that only happens in a reviewed commit made for that purpose.
- **Weekly quality report** (`scripts/quality_report.py`, schema `janus.quality-report/1`): add a metric to its `METRICS` table and to the docs together, never ad hoc. A check that could not run is reported as `error` or `unavailable`, never dropped.
- **Monthly structural review:** the bundle (`scripts/structural_review.py`) is read-only; the review itself runs in an agent session and produces `review.json` checked by `task quality:review-check`. The review never changes code, merges, or files issues; the person who asked files the proposed ones. `task quality:review-record` appends a checked review of `main` once per window.
- **History branches** (`ops-metrics`, `quality-history`) are append-only through `scripts/history_branch.py`: never rewrite, force-push or merge them, and never write secrets or e-mail addresses to them. Keep such logic in tested scripts under `scripts/` and the workflow YAML thin.
- A deploy is done only when the health endpoint reports the expected release version, not on HTTP 200 alone.
- Details: [`architecture-overview.md#weekly-quality-report`](docs/docs/tech-spec/architecture-overview.md#weekly-quality-report), [`#monthly-structural-review`](docs/docs/tech-spec/architecture-overview.md#monthly-structural-review), [`deployment-operations.md#deploy-and-availability-metrics`](docs/docs/tech-spec/deployment-operations.md#deploy-and-availability-metrics).

## Issues

Bugs and feature requests go to GitHub Issues; changes come in as pull requests against `main`.
