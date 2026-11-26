---
name: maintain-codemap
description: Write, review, prune, or split CODEMAP.md files so they stay lean navigation aids instead of growing into documentation. Use when creating or editing a CODEMAP.md, when a change adds/removes/renames routes, pages, packages or entry points, or when a map feels too long, stale, or duplicated.
---

# Maintain CODEMAP

A CODEMAP answers "where do I look?", not "how does it work?". If a map reader has to
scroll, the map has failed: it should cost far less to read than the code it points to.

## Principles

1. **Structure, not behavior.** Keep purpose, entry points, directory layout, route/page
   tables (path, file, one-line purpose), cross-module contracts, and "where to look for X".
   Move algorithms, edge-case rules, and design rationale to `docs/` or docstrings and link them.
2. **Budget.** Target about 150-250 lines (about 10-15 KB) per submodule map and about 100 lines
   for the root map. Past the budget, split or prune; do not append.
3. **Split hierarchically.** When a submodule map exceeds the budget, add child maps for its
   large packages and leave a one-line pointer in the parent. Parent maps describe, children detail.
4. **Collapse by directory.** One line per directory or file pattern. List individual files only
   for entry points, hotspots, and files whose purpose is not evident from the name.
5. **Single source of truth.** Say each fact in one place and link to it. Never restate another
   map's content; never duplicate what `docs/` already says.
6. **No volatile detail.** Omit counts, version pins, CI/workflow names, build mechanics, and
   exhaustive test lists. Keep a fact only if it prevents a wrong turn.
7. **Maps rot; make drift cheap to catch.** Every path in a map must exist, and every router,
   page, or package should be reachable from a map. Verify mechanically, not by memory.

## Workflow when changing code

- Structural change (new/removed/renamed package, entry point, router, page, job): update the
  affected map in the same change, and the root map only if the submodule index changes.
- Shared contract change: update the owner's map and only the consumer maps that name it.
- Apply the principles while editing: if you are about to add a paragraph, it probably belongs
  in `docs/`; add a link instead.

## Workflow when a map is overloaded

1. Measure: `scripts/check_codemaps.py` reports size and missing paths.
2. Classify each section as navigation, behavior, or volatile.
3. Move behavior to docs (link it), delete volatile detail, collapse per-file lists.
4. If still over budget, split into child maps and link them from the parent.
5. Re-run the check.

## Checks

`python .github/skills/maintain-codemap/scripts/check_codemaps.py [--max-lines 250] [--root-max-lines 100]`

Reports maps over budget and backticked relative paths that do not exist. Wire it into the
repository's normal check task (for example `task check` or a pre-commit hook) so drift fails fast.
