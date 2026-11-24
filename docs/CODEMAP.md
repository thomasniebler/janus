# Documentation Code Map

Scope: documentation sources and Zensical configuration under `docs/`.

## Responsibilities

- define the in-app documentation content served by the backend and read by Janus's `docs` toolbox
- configure navigation and theme settings for Zensical
- maintain the focused child technical specifications exposed through in-app docs
- archive periodic comprehensive codebase review reports
- hold generated static site output when docs are built locally

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `zensical.toml` | Navigation structure and theme/site configuration for docs rendering |
| `docs/index.md` | Documentation landing page |
| `docs/changelog.md` | Symlink to the root `CHANGELOG.md` (release-please), published as the Changelog page |
| `docs/quickstart.md` | Five-minute quickstart setup guide |
| `docs/configuration.md` | Configuration reference (env vars, `.env`, precedence, integrations, and the per-tool Unattended and In chat switches) |
| `docs/architecture.md` | System architecture and module-overview reference |
| `docs/database.md` | Database backend configuration (SQLite default, optional PostgreSQL) |
| `docs/desktop-widget.md` | Desktop widget API/CLI docs for status-bar integrations (Waybar, Sway) |
| `docs/runtime-triggers.md` | User guide to triggers: trigger definitions, the built-in briefing/digest/mail-processing triggers, `mail.received` processing, the per-tool Unattended switch and refusals, suggested automations from Janus and their review (JAN-235), run history, and worker settings |
| `docs/tools.md` | User reference for every tool-registry entry (one heading per entry, the target of each entry's `docs_ref`, e.g. `tools.md#send-mail`), how the Unattended switch works, the In chat "Ask every time"/"Always allow"/"Off in chat" switch for tools that ask before every call and the blanket "Allow everything"/"Allow all" modes (JAN-306, JAN-359), and what each `BLOCKED` answer means; `tests/test_docs_tools.py` fails if an entry has no section (JAN-297) |
| `docs/tech-spec/` | Focused child technical specifications linked from root `TECH_SPEC.md` and docs navigation |
| `docs/feature-specs/` | Detailed feature specifications shipped as docs content |
| `docs/roadmap/` | Roadmap and phase-planning documents |
| `docs/features/planned/` | Planned feature specs and implementation plans, including the Linear → GitHub Issues migration plan (JAN-393) |
| `docs/reviews/` | Archived point-in-time comprehensive codebase review reports; served by `/api/docs` but never offered to Janus's `docs` toolbox |
| `screenshots/` | UI screenshot assets referenced by docs pages |
| `site/` | Generated documentation output; not the source of truth |

## Read-next guidance

- In-app docs navigation or rendering issue -> `zensical.toml`, then `src/janus/api/docs.py`, `src/janus/services/docs_service.py`, and `frontend/src/pages/Documentation.tsx`
- New or renamed agent tool -> add its section to `docs/tools.md` and point the registry entry's `docs_ref` at it
- Architecture or technical requirement change -> root `../TECH_SPEC.md`, then the relevant `docs/tech-spec/` child spec
- Feature-spec content change -> `docs/feature-specs/`
- Roadmap or planned-work doc change -> `docs/roadmap/` or `docs/features/planned/`
- Setup/onboarding doc change -> `docs/quickstart.md` or `docs/configuration.md`
