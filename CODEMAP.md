# Repository Code Map

Start here. Pick the area that matches the task, open its `CODEMAP.md`, and follow child maps only as far as needed.

## Top-level areas

| Path | Responsibility | Read next |
| --- | --- | --- |
| `src/` | Python package: FastAPI app, CLI, services, integrations, background workers | [`src/CODEMAP.md`](src/CODEMAP.md) |
| `frontend/` | React/Vite dashboard and typed API client | [`frontend/CODEMAP.md`](frontend/CODEMAP.md) |
| `tests/` | Backend and integration tests | [`tests/CODEMAP.md`](tests/CODEMAP.md) |
| `docs/` | Documentation source, including the technical-spec child docs | [`docs/CODEMAP.md`](docs/CODEMAP.md) |
| `ios/` | Native SwiftUI iOS client scaffold | [`ios/CODEMAP.md`](ios/CODEMAP.md) |
| `alembic/` | Database migration environment and history | [`alembic/CODEMAP.md`](alembic/CODEMAP.md) |
| `modules/` | Optional companion apps (`todo-chat` desktop companion) | `modules/todo-chat/README.md` |
| `scripts/` | Standalone scripts: local startup, deploy and release checks, CI path selection, quality and security reports, OpenAPI export | Read directly; most are wrapped by `Taskfile.yml` tasks |
| `.github/` | GitHub Actions workflows and repository metadata | [`.github/CODEMAP.md`](.github/CODEMAP.md) |
| `.agents/` | Shared agent skills and their helper scripts | [`.agents/CODEMAP.md`](.agents/CODEMAP.md) |
| `.claude/` | Claude Code symlinks to the shared skills | [`.claude/CODEMAP.md`](.claude/CODEMAP.md) |
| `.semgrep/` | Janus-specific Semgrep rules and their test fixture | Read directly |

## Root files

| File | Responsibility |
| --- | --- |
| `README.md` | Project overview and contributor setup |
| `AGENTS.md`, `CLAUDE.md` | Coding-agent instructions and conventions |
| `TECH_SPEC.md` | Entry point to the technical spec in `docs/docs/tech-spec/` |
| `Taskfile.yml` | Developer commands: build, test, lint, coverage, audits, quality reports, OpenAPI, docs |
| `pyproject.toml` | Python package metadata, dependencies, and tool configuration |
| `hatch_build.py` | Wheel build hook that bundles migrations and the built frontend (read by `src/janus/resources.py`) |
| `release-please-config.json`, `.release-please-manifest.json` | Release Please configuration and current version |
| `.jscpd.json`, `.jscpd.tests.json` | Duplication-check settings |
| `package.json` | Root JavaScript tooling (Tailwind builds outside the Vite app) |

## Where to look

- API route, worker, model, or service -> `src/CODEMAP.md`
- Dashboard page or component -> `frontend/CODEMAP.md`
- Docs content or architecture spec -> `docs/CODEMAP.md`
- Database migration -> `alembic/CODEMAP.md`
- CI, deploy, release -> `.github/CODEMAP.md`
