# Delivery Artifacts

Documentation ships with the change it describes, like tests and migrations do. A PR is not review-ready while its docs are stale.

## What a change must update

| Change | Update in the same PR |
|---|---|
| Adds, removes, renames or moves a file, module or directory | every `CODEMAP.md` that names it |
| Architecture, a service boundary, a schema or an integration | the matching page under `docs/docs/tech-spec/` first, then the index in `TECH_SPEC.md` if it changed |
| Setup, configuration, an integration or a visible workflow | the user guide pages: [quickstart](../quickstart.md), [configuration](../configuration.md), and the page for the feature |
| A toolbox | its section in [`tools.md`](../tools.md); its `docs_ref` must resolve (`tests/test_docs_tools.py`) |
| Ships or drops planned work | its page under `feature-specs/` (see below) |

Pages describe the current state. Ticket numbers, "used to" prose and release history belong in Linear, commits and the [changelog](../changelog.md), not in the docs.

## Feature specs

`docs/docs/feature-specs/` holds specs for **unshipped** work only. Each page opens with:

```markdown
**Status:** Planned | In Progress
**Linear:** JAN-123
**Depends on:** <specs or systems, or "None">
```

- `Linear` is a plain issue ID; the workspace is private. Linear is the source of truth for progress; the page does not mirror it.
- When the feature ships, fold its behavior into the user guide and the tech spec, then delete the spec in the same PR.
- When the work is dropped, delete the spec.

## Checks

- `tests/test_docs_links.py` resolves the links in `TECH_SPEC.md` and the tech spec.
- `tests/test_docs_tools.py` resolves every tool `docs_ref`.
- `task docs` builds the site; `task codemap:check` checks the maps.
