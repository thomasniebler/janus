# WhatsApp Neonize Foundation

**Status:** Shipped; its stub adapter and pull-based history sync are Superseded by [WhatsApp Neonize Live Connector](whatsapp-neonize-live-connector.md)
**Linear:** JAN-149 (parent cluster JAN-148)
**Depends on:** Background workers runtime tech spec (thread-per-connection workers), data model & migrations tech spec, `neonize` package


## Context

- Linear issue: `JAN-149`
- Follow-up implementation issue: `JAN-150`
- Parent feature cluster: `JAN-148 Add WhatsApp access in Janus via Neonize`
- Scope type: docs-only feature specification for the first implementation track

## Problem

Janus is currently email-centric. Its ingestion model, dashboard payloads, and message handling assumptions are built around IMAP accounts, bounded recent email context, and thread-based background workers. Adding WhatsApp access needs a foundation track that fits those existing patterns without over-promising a full chat-platform implementation in the first pass.

The initial WhatsApp work must establish:

1. how a WhatsApp account is configured in Janus,
2. how recent WhatsApp history is ingested safely on first connect,
3. how WhatsApp messages and conversations are persisted and serialized for later dashboard and chat surfaces,
4. how the runtime fits the current thread-based worker model.

This track must use [`krypton-byte/neonize`](https://github.com/krypton-byte/neonize) as the WhatsApp access layer unless a concrete blocker is discovered during implementation.

## Goals

1. Introduce a WhatsApp integration foundation that follows Janus account-configuration, setup-session, and background-worker patterns.
2. Limit initial WhatsApp history ingestion to a bounded recent window instead of attempting a full account import.
3. Make that recent-history window configurable, with a default of the last 3 days.
4. Define the persistence shape needed to store WhatsApp accounts, conversations, messages, sync cursors, and ingestion metadata.
5. Define stable backend serialization shapes that future dashboard and message surfaces can consume.
6. Keep the first track compatible with Janus's existing thread-based runtime and local-first storage model.

## Non-goals

1. Full WhatsApp history import.
2. Full WhatsApp send/reply flows.
3. Rich media download, indexing, transcription, or attachment previews beyond metadata capture.
4. End-user dashboard UI beyond the backend and serialization prerequisites needed for a later surface.
5. Cross-platform conversation unification between email and WhatsApp in v1.
6. Replacing Neonize with another provider based on preference alone.

## Foundation scope boundary

This track is limited to four areas:

1. account configuration and credential/session prerequisites,
2. bounded recent-history ingestion on initial connect plus incremental sync scaffolding,
3. persistence and serialization shapes for WhatsApp data,
4. backend prerequisites for later dashboard and message handling work.

Anything beyond those four areas should be planned as a follow-up track under `JAN-148`.

## Setup and configuration model

WhatsApp onboarding should separate transient setup state from the durable account record.

### Durable account model

Completed setup should still reuse Janus's existing integration-account pattern as closely as possible:

- one user may configure zero or more WhatsApp accounts,
- account secrets or session artifacts must be stored encrypted at rest through the existing Janus security model,
- account records must track ownership, connection status, last successful sync, last sync error, and whether the integration is enabled,
- setup should capture the configurable initial-history window in days, defaulting to `3`.

Required configuration fields:

- `user_id`
- `provider = whatsapp_neonize`
- `display_name`
- `is_enabled`
- `initial_history_days` with default `3`
- encrypted Neonize session/auth payload
- sync status fields such as `last_synced_at`, `last_sync_error`, and any provider cursor/checkpoint needed for incremental sync

### Setup-session lifecycle

Janus should introduce a dedicated user-scoped `WhatsAppSetupSession` record for onboarding and reconnect flows instead of writing partial state directly into `WhatsAppAccount`.

Setup sessions should:

- store `display_name`, `initial_history_days`, and optional `account_id` for reconnect flows,
- track wizard status, pairing state, fallback availability, and human-readable status/error messaging,
- hold transient pairing artifacts and fallback-imported session material until setup is completed or cancelled,
- expire automatically after 15 minutes of inactivity or terminal abandonment,
- be reusable for both first-time account creation and reconnecting a degraded or disconnected account.

The durable `WhatsAppAccount` should only be created or updated when the user completes the wizard successfully.

### Settings > WhatsApp wizard

The primary v1 setup UX should live inside the existing `Settings > WhatsApp` panel rather than on a separate route.

The wizard flow is:

1. `Intro + prerequisites`
   - collect `display_name` and `initial_history_days`
   - explain local Neonize prerequisites and what Janus stores locally
2. `Pair in Janus`
   - start a live Neonize pairing attempt from Janus
   - render current pairing instructions or QR/pairing state from the backend
   - poll until paired, failed, expired, cancelled, or unsupported
3. `Fallback import`
   - expose manual `session_data` or `session_database_path` import only when pairing is unsupported, fails for a host/runtime reason, or the user explicitly opens advanced recovery
4. `Finish`
   - finalize setup into a durable `WhatsAppAccount`
   - persist encrypted session material
   - auto-run the initial bounded sync using `initial_history_days`
   - return the user to the normal account-card/status view

For already healthy accounts, the settings panel should continue to show the normal account list and account actions below the wizard entrypoint.

## Ingestion model

### Initial connect

On first successful account connection, Janus should ingest only conversations and messages whose provider timestamps fall within the configured `initial_history_days` window. The default is the last 3 days, and the setting must be user-configurable before or during account setup.

This bounded initial sync mirrors Janus's existing IMAP-first-sync behavior: prefer recent useful context, avoid expensive archive backfills, and keep onboarding predictable on a local deployment.

### Incremental sync

After the bounded initial sync, Janus should switch to incremental polling or event-following behavior supported by Neonize. The exact Neonize transport details may vary during implementation, but the Janus contract is:

1. sync work runs in a dedicated background thread or supervised thread-compatible worker,
2. incremental state is persisted so restarts do not require another recent-history backfill,
3. sync failures are recorded without corrupting existing message history.

### Message selection rules

The foundation track should ingest:

- direct messages and group messages that Neonize exposes through its stable API surface,
- provider message metadata needed for ordering, dedupe, sender attribution, and future display,
- text content and normalized snippets when available.

The foundation track should not require:

- exhaustive historical backfill outside the configured recent window,
- guaranteed parity for every advanced WhatsApp event type,
- media-body persistence beyond attachment metadata placeholders.

## Persistence shape

Implementation should reuse Janus persistence conventions: user-scoped ownership, explicit provider metadata, durable sync checkpoints, and serialization-friendly records.

### Core records

Expected durable records:

1. `WhatsAppAccount`
2. `WhatsAppConversation`
3. `WhatsAppMessage`
4. `WhatsAppSyncState` or equivalent account-scoped checkpoint fields

Expected transient records:

1. `WhatsAppSetupSession`

### `WhatsAppAccount`

Should persist:

- owning `user_id`
- provider key
- display label
- encrypted Neonize session/auth state
- enabled/disabled state
- configured `initial_history_days`
- connection health fields
- last successful sync timestamp
- last sync error summary

### `WhatsAppSetupSession`

Should persist:

- owning `user_id`
- optional target `account_id` for reconnect flows
- requested `display_name`
- requested `initial_history_days`
- setup status and pairing state enums
- provider capability/support indicators and explanatory messages
- transient pairing payload or manual fallback session material
- created/updated/expires-at timestamps
- terminal error/cancel metadata when relevant

### `WhatsAppConversation`

Should persist:

- owning `user_id`
- parent `whatsapp_account_id`
- stable provider conversation/chat ID
- conversation type (`direct` or `group`)
- display title
- participant summary or normalized participant identifiers
- latest message timestamp
- unread or unseen counts if Neonize exposes them reliably
- created/updated timestamps

### `WhatsAppMessage`

Should persist:

- owning `user_id`
- parent `whatsapp_account_id`
- parent `whatsapp_conversation_id`
- stable provider message ID
- provider timestamp
- direction (`incoming` or `outgoing`) when derivable
- sender identifier and sender display name when available
- message text/body
- snippet/preview text for dashboard-friendly serialization
- raw message type/category
- attachment/media metadata summary
- reply-to or quoted-message references when available
- ingestion timestamps and dedupe fields

## Serialization shape

Janus should define backend response models that align with existing dashboard/message serialization patterns instead of exposing Neonize-native payloads directly.

Minimum future-facing shapes:

1. account summary payload for settings and status surfaces,
2. conversation summary payload for dashboard/message lists,
3. message payload for a later conversation-detail surface,
4. setup-session payloads for the in-app wizard lifecycle.

### Account summary

Should include:

- local account ID
- display name
- enabled/connected status
- `initial_history_days`
- `last_synced_at`
- current health/error summary

### Conversation summary

Should include:

- local conversation ID
- account ID
- provider conversation ID
- title
- conversation type
- latest message snippet
- latest message timestamp
- unread count if supported

### Message payload

Should include:

- local message ID
- conversation ID
- provider message ID
- sender display data
- body/snippet
- timestamp
- direction
- attachment metadata summary

### Setup-session payloads

The backend contract should add:

1. `POST /api/whatsapp/setup-sessions`
   - create a setup session from `display_name`, `initial_history_days`, and optional reconnect `account_id`
2. `GET /api/whatsapp/setup-sessions/{id}`
   - return the current user-scoped setup-session state for polling
3. `POST /api/whatsapp/setup-sessions/{id}/start-pairing`
   - begin live pairing
4. `POST /api/whatsapp/setup-sessions/{id}/cancel`
   - stop pairing and mark the session cancelled
5. `POST /api/whatsapp/setup-sessions/{id}/fallback-import`
   - attach manual `session_data` or `session_database_path`
6. `POST /api/whatsapp/setup-sessions/{id}/complete`
   - finalize the session into a `WhatsAppAccount`, run the initial sync, and return account plus sync results

The setup-session payload should expose:

- local setup-session ID
- wizard status
- pairing state
- whether live pairing is supported in the current runtime
- whether fallback import should be shown
- pairing instructions, QR payload, or equivalent provider guidance when available
- terminal error or success messaging
- optional target `account_id`

These shapes are prerequisites for later dashboard widgets and message-center work, but this track does not require shipping those UI surfaces yet.

## Runtime and worker model

WhatsApp ingestion must fit Janus's current runtime constraints:

- use thread-based workers rather than introducing a new async worker subsystem,
- close database sessions explicitly in long-running worker loops,
- persist incremental checkpoints so restarts are safe,
- treat provider failures as degraded integration state rather than fatal app startup failures.

If Neonize requires a runtime assumption that conflicts with Janus's thread-based worker model, that conflict is a concrete blocker and must be documented before any provider substitution is considered.

Live pairing work should run in a supervised thread-compatible worker or polling loop that updates setup-session state without blocking the request thread.

## Dashboard and message-handling prerequisites

This foundation track should prepare, but not fully deliver, later WhatsApp surfaces.

Prerequisites to define now:

1. conversation summaries should be serializable alongside existing dashboard-oriented data contracts,
2. message records should preserve enough metadata for a future conversation-detail API,
3. account health and sync state should be consumable by system-status or settings surfaces,
4. persistence should preserve provenance so later product work can distinguish WhatsApp data from email data cleanly.

## Assumptions

1. Neonize can provide stable access to recent message history, chat identifiers, timestamps, and sender metadata.
2. Neonize authentication/session material can be stored through Janus's existing encrypted-secret pattern.
3. A thread-based integration worker is operationally acceptable for this provider.
4. The default recent-history window of 3 days is enough to make first-run onboarding useful without triggering large local imports.

## Resolved direction

This track resolves the onboarding UX question as follows:

1. account setup lives in `Settings > WhatsApp`,
2. live in-app pairing is the primary path,
3. manual session import remains available only as fallback or advanced recovery,
4. onboarding state uses a dedicated setup-session lifecycle,
5. reconnecting an existing account reuses the same wizard,
6. completing setup immediately runs the bounded initial sync with the configured history window.

## Open questions

1. Does Neonize expose a stable incremental checkpoint primitive, or will Janus need to derive one from timestamps/message IDs?
2. Which message/event types from Neonize are sufficiently stable for v1 ingestion: text only, or selected media/system events as well?
3. What concrete pairing artifact should Janus persist from Neonize on success: exported session payload, database path, or both?
4. Are group participant snapshots available cheaply enough to persist on each sync, or should Janus store only a normalized summary in this track?

## Rollout risks

### Provider/runtime mismatch

Neonize may depend on runtime behavior that does not fit Janus's current thread-based worker model.

Mitigation: treat runtime incompatibility as an explicit implementation gate and document it before expanding scope or changing provider strategy.

### Unbounded onboarding

WhatsApp histories can be very large, and an accidental full-history import would be expensive on local deployments.

Mitigation: make `initial_history_days` mandatory, default it to `3`, and keep the first sync strictly window-bounded.

### Schema drift from email assumptions

Janus data flows are currently email-shaped, so forcing WhatsApp records into email-specific fields would create brittle follow-up work.

Mitigation: use distinct WhatsApp account/conversation/message models with shared Janus serialization conventions instead of reusing email tables directly.

### Media and event complexity

WhatsApp message types can exceed the initial display and storage needs.

Mitigation: store attachment/event metadata summaries in the foundation track and defer rich media handling to later work.

## Test implications

Implementation follow-up should include automated coverage for:

1. account configuration validation and encrypted storage of Neonize session material,
2. initial sync respecting `initial_history_days` and defaulting to 3 days,
3. incremental sync resuming from persisted checkpoints without repeating the initial backfill,
4. durable persistence and dedupe of conversations/messages across repeated sync runs,
5. serialization contracts for account summaries, conversation summaries, and message payloads,
6. migration safety for existing local databases that add WhatsApp tables and account metadata.

## Review checklist

1. The spec explicitly commits to Neonize unless a concrete blocker is discovered.
2. Initial history ingestion is bounded, configurable in days, and defaults to 3 days.
3. The scope stops at configuration, bounded ingestion, persistence, serialization, and dashboard/message prerequisites.
4. The document does not promise full history import or full messaging UX in this track.
5. The runtime model stays aligned with Janus's existing thread-based worker architecture.
