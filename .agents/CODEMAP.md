# Agent Skills Code Map

Scope: repository-scoped agent skills (Codex, Claude Code) under `.agents/skills/`; Claude Code reaches them through symlinks in `../.claude/skills/`.

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `skills/analyze-submodule-map/` | Navigate a submodule via the CODEMAP hierarchy |
| `skills/setup-codemap/` | Set up or refresh the CODEMAP system |
| `skills/maintain-codemap/` | Keep CODEMAPs lean; `skills/maintain-codemap/scripts/check_codemaps.py` checks budgets and dangling paths (`task codemap:check`) |
| `skills/pr-review-threads/` | Inspect PR review threads via `gh` (helper script included) |
| `skills/update-pr/` | Update an existing PR, including review fixes |
| `skills/combine-dependabot-prs/` | Combine open Dependabot PRs into one validated PR |
| `skills/janus-deployment-admin/` | Administer a live deployment over SSH without committing secrets |
