# Migrate issue tracking from Linear to GitHub Issues

**Status:** Planned
**Linear:** JAN-393
**Depends on:** the GitHub integration (`services/github_service.py`); a Projects v2 board in `thomasniebler/janus`; an idle self-evolution loop before its own migration step

## Goal

Agent sessions, pull requests and code already live on GitHub. Linear is a second system every agent, workflow and doc has to know about, and a session that only has the repository cannot read or update an issue. Moving issues to GitHub puts work, PRs and review in one place.

The trade: GitHub keeps priority, estimates and status on a Projects v2 board, not on the issue. Cycles become milestones or an iteration field, epics become parent issues with sub-issues, and `JAN-123` ids become `#123`.

## Where Linear is wired in

- **Product:** `services/linear_service.py`, `services/linear_queries.py`, `api/linear.py`, the `linear` toolbox (`agent_toolboxes/linear.py`), the Linear credentials in `models/settings.py` and the `LinearConfig` settings page.
- **Self-evolution loop:** it files Linear issues and uses them as provenance (`services/self_evolution_intake.py`, `self_evolution_checks.py`, `self_evolution_provenance.py`, `self_evolution_service.py`, `models_self_evolution.py`, `agent_toolboxes/self_evolution.py`).
- **Delivery:** the `**Linear:**` line of every feature spec ([Delivery artifacts](../tech-spec/delivery-artifacts.md)), `CLAUDE.md`.
- **History:** about 250 distinct `JAN-nnn` ids in code, tests, docs, commits and PR titles.

## Plan

**Phase 0: prepare GitHub.**
Labels for type, epic and priority; issue templates; a Projects v2 board with Status, Priority and Size fields; a token with Issues and Projects read/write.

**Phase 1: export and import script.**
Read Linear through its API and create GitHub issues with the `JAN-nnn` prefix in the title, body, labels, parent/child links, comments, attachments (copied or linked) and a link back. It needs a dry-run mode that writes the plan as JSON, a committed mapping file (`JAN-123` → `#456`) that makes re-runs idempotent, and serial creation with backoff for rate limits.

**Phase 2: pilot.**
Import one epic with its children, check links, labels and board fields by hand, fix the script, delete the pilot issues and re-run.

**Phase 3: dual running.**
Import all open issues. Linear becomes read-only by convention, and new work goes to GitHub. Update `CLAUDE.md`. Branches and commits name the GitHub number, plus the old `JAN-nnn` where one exists.

**Phase 4: switch the code**, one small PR at a time:

1. Delivery docs and the spec status block (`**Linear:**` becomes an issue link).
2. A GitHub Issues toolbox (list, read, create, update, comment) with the same fences as the `linear` toolbox, including prompt-injection handling of issue text.
3. Self-evolution intake, checks and provenance read GitHub issues. This changes the stored provenance, so it needs a migration and a spec-first update of [Background workers and runtime](../tech-spec/background-workers-runtime.md).
4. Remove the Linear settings, router, toolbox and settings page, and update every `CODEMAP.md` and spec that names them.

**Phase 5: cutover.**
A final delta import, archive the Linear team after an export is stored outside Linear, and remove the production credentials.

**Rollback.**
Phases 0 to 3 leave Linear untouched. Each phase 4 PR reverts on its own; keep the `linear` toolbox until the GitHub one has run for two weeks. Only phase 5 is hard to undo.

## Open questions

- **Keep `JAN-nnn` ids?** Proposed: yes, as a title prefix plus the mapping file, so no history is rewritten.
- **What to import?** Proposed: open issues with comments; closed and cancelled ones only as a generated index page.
- **Where do epics live?** Proposed: parent issues with sub-issues, if GitHub's depth and count limits fit the current epic structure.
- **Same repository?** Proposed: yes. The repository is public, so anyone can open an issue there. Issue text must stay untrusted input, and the self-evolution loop must not act on issues it did not file.
- **When to migrate the self-evolution loop?** Last, with no work in flight, and with `SCOPE_FENCE` and `DAILY_CAPS` tested against GitHub-sourced issues.
- **Priority and estimates:** agents that read an issue's priority today must read the board field instead.
