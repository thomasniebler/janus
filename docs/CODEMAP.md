# Documentation Code Map

Scope: documentation sources and Zensical configuration under `docs/`. These pages are also served in-app and read by Janus's `docs` toolbox.

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `zensical.toml` | Navigation and theme configuration |
| `docs/` | User-facing pages (index, quickstart, configuration, architecture, database, triggers, tools, inbox, desktop widget); `docs/changelog.md` is a symlink to the root `CHANGELOG.md` |
| `docs/tools.md` | One section per tool-registry entry; each entry's `docs_ref` points here |
| `docs/tech-spec/` | Technical-spec child docs linked from root `TECH_SPEC.md` |
| `docs/feature-specs/` | Feature specifications |
| `docs/features/planned/` | Planned-feature specs and implementation plans |
| `docs/roadmap/` | Roadmap and phase planning |
| `screenshots/` | UI screenshots referenced by docs pages |

Generated output (`site`) is not the source of truth.

## Where to look

- In-app docs rendering -> `zensical.toml`, `src/janus/api/docs.py`, `src/janus/services/docs_service.py`, `frontend/src/pages/Documentation.tsx`
- New or renamed agent tool -> add a section to `docs/tools.md`
- Architecture change -> `../TECH_SPEC.md`, then `docs/tech-spec/`
- Setup docs -> `docs/quickstart.md`, `docs/configuration.md`
