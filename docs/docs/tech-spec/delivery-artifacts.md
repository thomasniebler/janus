# Delivery Artifacts

## Status

Adopted. This spec defines the convention that keeps documentation from drifting
out of sync with shipped behavior, following the docs-staleness gaps identified
in a documentation audit (Google OAuth described as "Planned" after it shipped;
GitHub/Linear integrations and the LLM provider matrix with no user-facing
docs at all).

## Problem

Code changes go through Linear → branch → PR → merge and are enforced by CI
(lint, tests, migrations). Documentation has no equivalent enforcement: a
feature can ship, and the docs that describe it (or fail to describe it) never
get touched again. The result is stale "Planned" pages for shipped features
and shipped integrations with zero documentation.

## Rule: Docs Are Delivery Artifacts

Treat the following as delivery artifacts with the same "must be updated in
the same change" status as tests and migrations, not as optional follow-up:

- the relevant child spec(s) under [`docs/docs/tech-spec/`](.),
- the relevant page(s) under `docs/docs/feature-specs/` or
  `docs/docs/features/planned/`,
- user-facing pages (`docs/docs/index.md`, `docs/docs/quickstart.md`,
  `docs/docs/configuration.md`) when the change affects setup, configuration,
  supported integrations, or visible workflows.

A PR that changes user-visible behavior, adds/removes an integration, or
changes setup/configuration is not review-ready until the artifacts above
reflect the new state. The user-docs pass in the delivery workflow checks this
before a PR is opened.

## Planned-Feature Doc Convention

This convention is mandatory going forward, not retroactive: every page under
`docs/docs/features/planned/` and every feature spec under
`docs/docs/feature-specs/` that is **created or materially edited** from this
point on must open with a status block in this form. The existing corpus
(19 feature-spec pages predate this convention and do not yet carry the
block) is migrated opportunistically — the user-docs pass adds the block to an
existing page the first time it touches that page, rather than in one bulk
pass. Until a given page is touched, treat its absent block as "not yet
migrated," not as a violation.

```markdown
**Status:** Planned | In Progress | Shipped | Superseded
**Linear:** JAN-123
**Depends on:** <other spec(s) or systems this needs, or "None">
```

- **Status** reflects the feature's real state, not the state it was in when
  the doc was first written. When a feature ships, its status must move to
  `Shipped` in the same PR that ships it (or the very next user-docs
  pass) — a page cannot describe an implemented feature as `Planned`.
- **Linear** names the issue (as a plain ID such as `JAN-123`) that is the
  source of truth for live progress, sub-tasks, and current assignee. The
  Linear workspace is private, so the doc gives the ID, not a link. The doc
  captures design intent and dependencies; Linear captures day-to-day status.
  Don't duplicate sprint-level progress notes into the doc — reference instead
  of mirroring, so there is one place progress can go stale.
- **Depends on** names other planned features, tech specs, or external
  systems (e.g. "Google OAuth tech spec", "MCP tool registry", "requires
  `runtime_trigger_worker_enabled` flag") that must land first or that this
  feature's design assumes. This is what lets a reader (or an agent scoping
  a new Linear issue) see blocking relationships without re-deriving them
  from code.
- When a feature is superseded by a later redesign (for example, a legacy
  system kept only as a compatibility layer), mark it `Superseded` and name
  the doc that replaces it, rather than leaving it silently accurate-by-luck.

## What This Does Not Replace

- Linear remains the single source of truth for issue status, assignment,
  and cross-issue blocking relationships. The doc's `Linear` field is a
  pointer, not a mirror — do not copy Linear's status board into markdown.
- `TECH_SPEC.md` ownership rules (spec-first changes, root index maintenance)
  are unchanged; this spec adds the planned-feature/status convention on top
  of them.
