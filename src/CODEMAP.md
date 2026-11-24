# Source Code Map

Scope: Python package sources under `src/`.

## Responsibilities

- package the Janus backend and CLI code
- hold the application entrypoints, services, models, and integrations
- separate generated package metadata from maintained source

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `janus/` | Maintained application source for the FastAPI app, CLI, services, and integrations |
| `janus.egg-info/` | Generated package metadata from editable/install builds; not a primary source area |

## Read-next guidance

- API, worker, or business-logic change -> `janus/CODEMAP.md`
- Packaging metadata issue -> inspect `pyproject.toml` at the repository root before `janus.egg-info/`
