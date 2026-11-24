# System Status

**Status:** Shipped
**Linear:** none (shipped before Linear tracking); later extensions: JAN-197 (running version in `/api/status/health`), JAN-241 (knowledge search component)
**Depends on:** IMAP monitor heartbeats, planner sync (`last_sync_at`/`last_sync_error`), background LLM probe, event log (incident history)


## Goal

Add an authenticated status overview page to Janus, inspired by GitHub Status, so users can quickly see whether core subsystems are healthy and what recently degraded or recovered.

## V1 scope

V1 covers three top-level service groups:

- **Mail Sync**
- **Planner Sync**
- **LLM**

Each group shows nested per-account or per-integration details beneath the top-level service summary.

## Access model

The status page is part of the authenticated Janus dashboard. It is not a public status site in v1.

## UX model

The page should provide:

1. A top-level overall status banner.
2. A service card for each primary group.
3. Nested component rows for configured accounts or integrations.
4. A recent incident history section showing failures, degradations, and recoveries.

## Status vocabulary

V1 uses these normalized states:

- **operational**: The component is healthy and fresh.
- **degraded**: The component is reachable but stale, partially available, or awaiting a fresh success signal.
- **outage**: The component has a confirmed failure.
- **unknown**: No configured component exists yet, or Janus does not have enough signal to classify it.

## Aggregate rules

- **Overall status** is derived from the worst active service state.
- If any top-level service is in `outage`, the overall page is `outage`.
- Otherwise, if any top-level service is in `degraded`, the overall page is `degraded`.
- Otherwise, if at least one top-level service is `operational`, the overall page is `operational`.
- If no top-level service has a live signal yet, the overall page is `unknown`.

## Service-specific health rules

### Mail Sync

- Uses existing IMAP monitor state, heartbeat freshness, and worker-thread supervision.
- A mail component is `operational` when it is active, marked running, and its heartbeat is fresh.
- A mail component is `degraded` when it is active but missing a recent heartbeat or waiting for its first healthy signal.
- A mail component is `outage` when the monitor reports an error.

### Planner Sync

- Uses persisted `last_sync_at` and `last_sync_error`.
- A planner component is `operational` when it has a recent successful sync and no current sync error.
- A planner component is `degraded` when it is stale or awaiting its first success.
- A planner component is `outage` when it has a sync failure and no recent healthy sync to fall back on.

### LLM

- Janus runs an active background probe against the currently configured LLM backend.
- The latest probe result is persisted so the status page does not depend on an interactive user action.
- The LLM component is `operational` after a recent successful probe.
- The LLM component is `degraded` when the probe result is stale or missing.
- The LLM component is `outage` when the most recent probe failed.

### Knowledge search (JAN-241)

- One component, **Memory and topic search**, whose subtitle is the configured embedding model (`provider/model`) or `embeddings off`.
- `operational` when embeddings are off (the message names the keyword search in use, and why vector search would be unavailable, if it would be), or when embeddings are configured and working (the message says when some facts or topics are still waiting to be embedded; it never shows counts, because every signed-in user can read the status page).
- `degraded` when an embedding model is configured but vector search is unavailable (no `enable_load_extension`, sqlite-vec failed to load, or a database without a vector index such as PostgreSQL), or when the knowledge-index worker's last sweep failed.
- It is computed on request from a capability probe and in-memory worker state; it records no incidents.

## Incident history

V1 stores recent incidents in the existing event log rather than introducing a separate incident table.

- Status transitions should emit structured status events.
- Failures and degradations create incident entries.
- Recoveries create resolved incident entries.
- The status page shows recent status events in reverse chronological order.

## Backend contract

Expose a normalized authenticated endpoint that returns:

- `overall_status`
- `overall_message`
- `generated_at`
- `services[]`
  - `id`
  - `name`
  - `status`
  - `summary`
  - `updated_at`
  - `components[]`
- `recent_incidents[]`

## Frontend requirements

- Add a dedicated `/status` dashboard route and navigation item.
- Poll the status summary endpoint on an interval.
- Render service state with compact, high-signal cards and badges.
- Show timestamps and nested details without requiring a modal or drill-in for v1.

## Future follow-ups

- Public status page.
- User-triggered maintenance windows.
- Longer incident timelines and uptime percentages.
- Manual incident annotations.
