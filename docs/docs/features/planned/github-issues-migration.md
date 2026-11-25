# Plan: Migrate Issue Tracking from Linear to GitHub Issues

**Status:** Planned
**Linear:** JAN-393
**Depends on:** None

## Why

Agent sessions, pull requests and code already live on GitHub. Linear is a second
system that every agent, workflow and doc has to know about, and a session that only
has the repository cannot read or update an issue. Moving issues to GitHub gives one
place for work, PRs and review.

## What would be lost

Be honest about the trade before starting. GitHub Issues has no equivalent for:

| Linear feature | GitHub replacement | Gap |
|---|---|---|
| Priority (Urgent/High/Medium/Low) | Projects v2 single-select field | Not on the issue itself |
| Estimates (S/M/L) | Projects v2 field | Not on the issue itself |
| Cycles | Milestones or a Projects v2 iteration field | Weaker burndown |
| Epics as parent issues | Sub-issues (parent/child) | Works, but with limits on depth and count |
| Status workflow | Open/Closed plus a Projects v2 status field | Two places to keep in sync |
| Issue IDs `JAN-123` | `#123` | IDs change, see below |

## Where Linear is wired in today

The plan has to touch all of these.

**In the product**

- `services/linear_service.py`, `services/linear_queries.py`, `api/linear.py`, `agent_toolboxes/linear.py`: the Linear integration and the agent's Linear toolbox.
- `models/settings.py` and the Settings `LinearConfig` page: the stored Linear credentials.
- Self-evolution loop: `services/self_evolution_intake.py`, `self_evolution_checks.py`, `self_evolution_provenance.py`, `self_evolution_service.py`, `models_self_evolution.py`, `agent_toolboxes/self_evolution.py`.
- Policy and prompts: `tool_policy.py`, `capability_profile.py`, `trigger_proposal_service.py`, `services/tool_approval.py`, `prompt_injection.py`, `runtime_adapter.py`.
- GitHub side already exists: `services/github_service.py` (issues, PRs, check runs).

**In the delivery process**

- The agent definitions, the Gemini skills and the release-failure workflow that filed Linear issues were removed when the repository went public; failed releases now show only as failed workflow runs.
- `scripts/structural_review.py`: the monthly review files findings in Linear.
- The "Linear" line in the planned-doc status block (`docs/docs/tech-spec/delivery-artifacts.md`).

**In history**

- About 210 distinct `JAN-nnn` identifiers appear in code, tests, docs, commit messages and PR titles.

## Decisions to make before starting

1. **Keep `JAN-nnn` identifiers?** Recommended: yes. Put `JAN-123` at the start of every imported title and keep a committed mapping file (`JAN-123` → `#456`). Existing references stay searchable and no history is rewritten.
2. **What to import?** Recommended: all open issues (Backlog, Todo, In Progress) with comments; completed and cancelled ones only as a generated index page with title, link and status. Importing about 400 closed issues adds noise and API calls for little value.
3. **Where do epics live?** Recommended: parent issues with sub-issues, matching how Linear is used today. Use a Projects v2 board for status, priority and estimate fields.
4. **Same repository or a separate one?** Recommended: the same repository (`thomasniebler/janus`). Agent sessions are already scoped to it, and issue/PR cross-links just work.
5. **Keep the in-app Linear integration?** Recommended: keep it working during the migration, then replace the agent toolbox with a GitHub Issues toolbox. Remove Linear only after the cutover is stable.

## Phases

### Phase 0: Prepare GitHub (S)

- Create labels for type (`bug`, `feature`, `improvement`), epic marker, and priority.
- Add issue templates and a Projects v2 board with Status, Priority and Size fields.
- Check the GitHub App or token permissions: Issues read/write, Projects read/write.
- Note: cloud agent sessions cannot use the GitHub MCP `search_*` tools. Agents should search with `gh api search/issues`, as the session instructions say.

### Phase 1: Export and import script (M)

- Script that reads Linear through its API and writes GitHub issues: title with `JAN-nnn` prefix, body, labels, parent/child links, comments, and a link back to the original.
- Dry-run mode that writes the full plan as JSON and creates nothing.
- Idempotent: the mapping file records what exists, so a re-run creates no duplicates.
- Respect rate limits and secondary limits; create issues serially with backoff.
- Output: the mapping file and the closed-issue index page.

### Phase 2: Pilot (S)

- Import one epic (suggest JAN-364 and its children) into the real repository.
- Check parent/child links, labels, board fields and cross-references by hand.
- Fix the script, then delete the pilot issues and re-run.

### Phase 3: Dual running (M)

- Import everything open. Mark Linear read-only by team convention: new work goes to GitHub only.
- Update `CLAUDE.md` and the agent definitions to say where to file and look up issues.
- Branch names and commit messages reference the GitHub issue number, plus the old `JAN-nnn` when one exists.

### Phase 4: Switch the code (L)

Do this in separate PRs, smallest risk first:

1. Delivery docs and skills (`CLAUDE.md`, `AGENTS.md`, status block convention).
2. `structural_review.py` → file a GitHub issue.
3. A `github_issues` agent toolbox (list, read, create, update, comment) with the same policy fences as the Linear toolbox, including the prompt-injection handling of issue text.
4. Self-evolution intake, checks and provenance: replace the Linear issue as the source with a GitHub issue. This changes the stored provenance model, so it needs a migration and spec-first updates to `docs/docs/tech-spec/`.
5. Remove the Linear settings, API router, toolbox and `LinearConfig` page. Update every `CODEMAP.md` and spec that mentions them.

### Phase 5: Cutover and decommission (S)

- Final delta import of anything changed in Linear.
- Archive the Linear team. Keep the workspace exported for a period.
- Remove the credentials from production settings.

## Estimate

About 8 to 12 working days with agents, mostly in Phase 4. It can run beside the metrics epic, but Phase 4 item 4 should wait until the self-evolution loop has no work in flight.

## Rollback

- Phases 0 to 3 are reversible: Linear stays intact and nothing in it is deleted.
- Phase 4: each PR is independent and revertible. Keep the Linear toolbox until the GitHub one has run for two weeks.
- Phase 5 is the only step that is hard to reverse. Do not start it before an export is stored outside Linear.

## Risks

- **Sub-issue limits and API gaps.** Check current GitHub limits for sub-issue depth and count against the epic structure before the pilot.
- **Priority and estimates live outside the issue.** Agents that read `priority` today must read the board field instead.
- **Self-evolution loop.** It treats a Linear issue as its input and as provenance. A half-migrated loop could act on stale or duplicated issues, so migrate it last and test the fences (`SCOPE_FENCE`, `DAILY_CAPS`) against GitHub-sourced issues.
- **Issue text is untrusted.** The same prompt-injection rules that apply to Linear text must apply to GitHub issue text, which anyone with access to the repository can write.
- **Lost context.** Linear attachments and images do not transfer automatically. The import script must copy or link them.
