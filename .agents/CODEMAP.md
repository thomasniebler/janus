# Agent Skills Code Map

Scope: repository-scoped agent skills (Codex, Claude Code) under `.agents/skills/`.

## Responsibilities

- provide reusable Janus workflows that coding agents can invoke explicitly or implicitly
- keep repository navigation, PR-update, deployment-admin, and CODEMAP setup workflows close to the repo
- preserve scripts used by skills, such as GitHub PR review-thread inspection

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `skills/analyze-submodule-map/SKILL.md` | Understand a repository submodule by following the CODEMAP hierarchy before reading implementation files |
| `skills/pr-review-threads/SKILL.md` | Inspect unresolved or all PR review threads through the GitHub GraphQL API via `gh` |
| `skills/pr-review-threads/pr_review_threads.py` | Helper script for the PR review-thread skill |
| `skills/janus-deployment-admin/SKILL.md` | Administer a live Janus deployment over SSH without committing host details or secrets |
| `skills/setup-codemap/SKILL.md` | Set up or refresh a hierarchical CODEMAP system |
| `skills/update-pr/SKILL.md` | Update an existing PR by number, including review fixes and validation |
| `skills/combine-dependabot-prs/SKILL.md` | Standard procedure for combining the open Dependabot PRs into one validated PR and closing the originals (also exposed to Claude Code via the `.claude/skills/` symlink) |

## Read-next guidance

- Repository navigation -> `skills/analyze-submodule-map/SKILL.md`
- Existing PR review comments -> `skills/pr-review-threads/SKILL.md`
- Production administration -> `skills/janus-deployment-admin/SKILL.md`
- CODEMAP maintenance -> `skills/setup-codemap/SKILL.md`
- PR update workflow -> `skills/update-pr/SKILL.md`
- Combining Dependabot / dependency-bump PRs -> `skills/combine-dependabot-prs/SKILL.md`
