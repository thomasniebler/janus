# Claude Code Configuration Code Map

Scope: project-scoped Claude Code skills under `.claude/skills/`.

## Responsibilities

- expose the shared repository skills in `.agents/skills/` to Claude Code sessions
- avoid mixing Claude Code configuration with GitHub Actions metadata

## File map

| Path | Responsibility |
| --- | --- |
| `skills/combine-dependabot-prs` | Symlink to `.agents/skills/combine-dependabot-prs/` so Claude Code and Codex share one copy of the Dependabot-combining procedure |

## Read-next guidance

- Combining Dependabot / dependency-bump PRs -> `skills/combine-dependabot-prs/SKILL.md`
- Other shared skills -> `../.agents/CODEMAP.md`
