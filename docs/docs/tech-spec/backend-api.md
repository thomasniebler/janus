# Backend and API Architecture

## Service Boundaries

- `AuthService` owns user registration, login, session creation, session revocation, admin checks, and first-run setup behavior.
- `InboxService` (`services/inbox_service.py`, `api/inbox.py`) owns the action inbox: `GET /api/inbox` aggregates what is waiting for the owner from existing sources (unread Janus-started conversations, pending planner suggestions, topic suggestions, email clarifications, proposed trigger definitions), newest first, capped at 50 (20 per source), with an `ETag` (`If-None-Match` returns 304) so a popup can poll cheaply. Titles and summaries are untrusted plain text, cut to 120 and 300 characters with control and bidi characters removed. `POST /api/inbox/{kind}/{id}/{action}` runs `mark_read` (conversations) or `dismiss` (planner, topic, clarification) through the existing services; every other kind/action pair, a foreign id, an item that is no longer pending, or a user-started conversation is a 404. Timestamps carry an explicit UTC offset. Trigger proposals are listed with `risk="needs_web"` and no action. The endpoints accept the web session or a device token ([Auth and security](auth-security.md)); `/api/inbox/tokens` (create, list, revoke) is web-session only.
- `EmailService` owns email account configuration, prioritization rules, and rule persistence.
- `ConversationService` owns chat conversation persistence for the REST history API and `/ws/chat` resume. `GET /api/conversations/{id}` returns each message with its `tool_calls` (JAN-249: `call_id`, `tool_id`, `tool_name`, `phase`, redacted `args`, `args_truncated`, `result_preview`, `result_truncated`, `duration_ms`, and `approval`, the user's chat approval decision for a `confirmation="required"` call (JAN-254), else `null`; empty for user messages) and its `ui_blocks` (JAN-252: `block_id`, `block_type`, `schema_version`, and `payload`, the validated block as streamed in `__UI_BLOCK__`; empty for user messages and replies without blocks), owner-scoped like the rest of the conversation API. Each conversation also carries `origin` (`user`, or `janus` for one an unattended run started with `start_conversation`) and `unread` (`true` until the owner first opens a Janus-started conversation; `GET /api/conversations/{id}` clears it) (JAN-319).
- `GET /api/conversations/{id}/delegations` (JAN-208, `get_current_user`) returns one newest-first page (`DelegationPage`: `items`, `total`, `limit` 1-100 default 20, `offset` 0-1,000,000 so an out-of-range value answers `422` rather than overflowing SQLite, `has_more`) of the tasks Janus delegated from that conversation. Each `DelegationResponse` carries `delegation_id`, `status` (`queued`/`running`/`completed`/`failed`), `objective`, `requested_tool_ids`, `allowed_tool_ids`, `result`, `error`, `depth`, `created_at`, `claimed_at`, and `completed_at`; never the claim token or the internal parent/child task ids. `objective`, `result`, and `error` are model text, rendered as plain text. A missing conversation and another user's answer the same `404`. Before the page is read, stale queued/running rows get the stale-lease rule (`ConversationService.list_delegations_for_user`): requeued once, failed if interrupted again (JAN-207).
- `services/image_store.py` owns generated-image storage (JAN-253): files under `image_storage_dir/<user_id>/`, `generated_images` rows, the per-user quota, owner-scoped lookup (`get_image_for_user`), path confinement, and file removal when a conversation is deleted. `services/image_generation.py` owns the cloud image providers (OpenAI, Gemini, OpenRouter) and their capability detection.
- `EmailProcessor` owns rule evaluation, the hand-off of new mail to `mail.received` trigger definitions, the constrained AI classification fallback, reminder extraction hooks, and non-blocking ingestion behavior.
- `PlannerService` owns planner connector factories and imported-context aggregation for briefings, chat, reminders, and dashboard summaries.
- `LocalPlannerService` owns Janus-managed task/event CRUD, planner suggestion review, lightweight dedupe, and unified local-plus-imported planner reads.
- `LLMService` owns provider-specific model construction, the JSON extractors, chat streaming, the post-turn memory suggester, and health probing. It no longer generates briefings or digests; those are built-in trigger definitions (`builtin_triggers.py`).
- `trigger_definition_service` owns saved trigger definitions (validation, schedules, event fan-out, run enqueueing); `tool_policy` owns the per-user, per-tool unattended switch (JAN-255) and the tool sets of each unattended run.

## API Surface Rules

- Request authentication normally uses `Depends(security.get_current_user)`.
- Routes that can be anonymous use `security.get_user_or_none`; WebSockets use `security.get_websocket_user_or_none`, the same check on a short-lived session that is closed before the socket opens (JAN-336).
- Admin-only routes wrap authenticated users with `admin_required`.
- Routes must verify authenticated ownership for user-scoped data before returning or mutating it.
- Ownership lives in the service layer, not the router (JAN-137). A user-scoped read or mutation goes through a `*_for_user` service method that takes the owner id and filters on it in the same query (for example `EmailService.update_account_for_user`, `delete_rule_for_user`, `update_email_priority_for_user`, `ConversationService.get_conversation_for_user`). The knowledge, planner, and WhatsApp routers follow the same rule (JAN-288): `knowledge_sources.list_sources_for_user` / `delete_source_for_user`, `planner_account_service.list_accounts_for_user` / `get_google_calendar_account_for_user` / `upsert_google_accounts_for_user` / `delete_google_accounts_for_user`, and `WhatsAppService.update_account_for_user` / `delete_account_for_user`. Such methods return `None` / `False` for a row that is missing *or* owned by someone else, and the router maps that to the same `404`, so a foreign id never discloses that it exists. Emails are owned through their account: an email with no account has no owner and answers `404` to every user. Do not add a router that loads a row by id and checks `user_id` itself before calling an unscoped service method; unscoped `update_*`/`delete_*` variants for user-owned rows have been removed so new call sites cannot reach for them.
- The same rule covers services and agent tools that read on a user's behalf, not just routers (JAN-286). Every email read for a user or a run owner starts from `EmailService.query_emails_for_user(user_id)` (or `get_email_for_user` / `get_emails_for_user` / `list_emails_for_user` / `get_important_emails_for_user` on top of it): the dashboard `important_emails` and `all_emails` widgets, the briefing email section, and the `search_emails` / `get_email_detail` / `get_email_details_batch` tools. A foreign email id answers exactly like a missing one. `EmailService.send_email` takes a required `user_id` and only ever sends from that user's own active account: an explicit `account_id` owned by someone else is "not found", and the implicit choice considers only that user's accounts. The unscoped `get_important_emails`, `get_account`, and global outgoing-account resolver were removed, and `get_accounts` / `get_rules` now require a `user_id`.
- Correction-derived rules are owner-scoped too (JAN-287). When a relabel (`PATCH /api/emails/{id}/priority`, `relabel_email(s)`) or an approved clarification turns into a rule, `EmailService` matches existing rules, retires duplicates, computes the next evaluation order, and decides whether to suppress a new suggestion only among the acting user's own rules (every rule has an owner; `rules.user_id` is `NOT NULL`, JAN-338). Another user's rule is never renamed, re-prioritized, reordered, re-activated, or deactivated.
- Rule *evaluation* uses the same scope (JAN-295). `EmailProcessor._evaluate_rules` loads its rules from `EmailService.get_active_rules_for_mail_owner(user_id)` for the receiving account's owner: that owner's active rules, in evaluation order. Another user's rule never classifies incoming mail.
- Background context built on a user's behalf takes that user's id too (JAN-296). `ReminderService.get_active_reminders(user_id)`, `get_preparation_outlook(user_id)`, and `build_email_context(user_id)` only return the owner's reminders (owned through the source email's account, as mail is) and events from the owner's own planner accounts; with no owner they are empty. `PlannerService.get_briefing_payload` / `get_context_data` pass their resolved user, and `EmailService.update_email_priority_for_user` builds its reasoning context for the correcting user.
- Backend APIs should return data shapes stable enough for generated frontend types and runtime validation on critical responses.
- Writable planner endpoints must target Janus-owned local items only; imported planner records remain read-only and must not be mutated through local planner APIs.

## Generated Images API (JAN-253)

- `GET /api/images/{image_id}` (`get_current_user`) streams one image the chat tool `generate_image` stored. The lookup is owner-scoped in the service (`image_store.get_image_for_user`): an id owned by another user, a malformed id (anything but 1-64 of `[A-Za-z0-9_-]`), a row whose file is missing, and a stored path that resolves outside `image_storage_dir` all answer the same `404`. The response carries the row's sniffed `mime_type` (`image/png`, `image/jpeg`, or `image/webp`), `Cache-Control: no-store` (a cached copy on a shared browser profile would outlive the owner's logout and the image's deletion), `X-Content-Type-Options: nosniff`, and `Content-Disposition: inline; filename="janus-image-<id>.<ext>"`, so the chat's download link (`<a download>`) saves it with a sensible name. There is no list or upload endpoint: images are made only by the tool and deleted with their conversation (`DELETE /api/conversations/{id}`), or one at a time with `DELETE /api/images/{image_id}` (JAN-359, owner-scoped through `image_store.delete_image_for_user`, `204`, the same `404` for anything else), which removes the row and the file and is the only way to free an image an unattended run stored without a conversation.

## Self-Evolution API (JAN-320)

- `GET /api/self-evolution/settings` (`get_current_user`) returns the caller's own loop settings -- `enabled`, `routine_trigger_id`, `routine_token_configured`, `github_repo`, `production_base_url` -- plus `daily_caps`, `usage_last_24h`, `scope_fence`, and the 20 newest `recent_actions`. It never returns the token. `enabled` is false for a non-admin whatever the row says.
- `PATCH /api/self-evolution/settings` (`admin_required`) updates the caller's own settings: omitted/`null` leaves a field alone, `""` clears it. `routine_trigger_id` must match `^trig_[A-Za-z0-9]+$` (the fire URL is built in code), `routine_token` must start with `sk-ant-oat01-`, `production_base_url` must be `https://` without credentials, and the repository `owner/name`; violations answer `422`. The loop itself has no HTTP trigger: it runs only through the `self_evolution` toolbox (see [background workers runtime](background-workers-runtime.md#autonomous-self-evolution-loop-jan-320)).

## Runtime Trigger API

- `POST /api/runtime/triggers/` is an authenticated enqueue endpoint and returns
  `202 Accepted`. `GET /api/runtime/triggers/` is an authenticated observability
  endpoint and returns one newest-first `RuntimeTriggerPage` (`items`, `total`,
  `limit` (default 25, max 200), `offset`, `has_more`) of the current user's
  runs, filterable by `status`, `kind`, `definition_id` (an id, or `none` for
  runs without a definition; anything else is a 422), and a `start`
  (inclusive) / `end` (exclusive) `created_at` range (`start >= end` is a 422).
  Each item carries `definition_name` and `definition_origin` (JAN-256). Both
  routes use the authenticated session from `security.get_current_user`.
- The POST body always accepts `task_id`, `kind`, `source` (default `"api"`),
  `objective`, `task_mode` (default `"default"`), and `max_steps` (default
  `100`). Identity fields are kind-specific: manual requests require
  `idempotency_key`; scheduled requests require `schedule_id` and a
  timezone-aware `scheduled_for`; event requests require `event_id` and a
  timezone-aware `occurred_at`. Missing or cross-kind identity fields are
  rejected. Text fields must be non-blank; `max_steps` must be a strict integer
  from `1` through `100`—booleans and strings are rejected rather than coerced.
- Scheduled and event requests use the canonical trigger adapters. Scheduled
  identity is derived from `(schedule_id, scheduled_for)` and event identity
  from `(source, event_id)`, so retries of the same occurrence are stable.
  Future scheduled occurrences remain queued and cannot be claimed before
  `scheduled_for`.
- Idempotency is scoped to `(task_id, idempotency_key)`. Reusing that pair as a
  different authenticated owner returns `409 Conflict` and does not expose the
  existing owner's row. Reusing it as the same owner is an idempotent request
  for the existing trigger record.
- `objective`, `task_mode`, and `max_steps` are task context only. The ingress
  does not accept or honor a client-supplied tool list, tool policy, capability
  grant, or execution approval. Extra payload data is not forwarded to the
  plan runtime.
- Each accepted row is bound to the authenticated user's `user_id`. GET
  filtering is by that owner, so a user cannot observe another user's trigger
  lifecycle records. The optional `limit` query parameter defaults to `25`
  and is bounded to `1–200`; `offset` (default `0`) pages further back.
- Responses are safe lifecycle views containing identifiers, task metadata,
  status, timestamps, and the latest error. They deliberately omit the stored
  JSON `payload` and private claim token/tool policy fields.
- GET responses also carry `result` and `step_error` (JAN-192), decoded from
  the trigger's persisted plan state rather than from `payload`: `result` is
  the automation's summary of what it authorized and actually invoked once
  the trigger has run, and `step_error` is the recorded failure reason, which
  outlives `last_error` across a retry that has re-claimed the row. Both are
  `null` for a trigger that has not executed yet.
- GET responses also carry `definition_id` (the saved definition a run came
  from, or `null` for an ad-hoc run). The raw `RuntimeTrigger.output` column is
  not part of the response; it is read server-side by the dashboard briefing,
  the `get_daily_briefing` tool, and the CLI.
- This API is the ad-hoc ingress (no dashboard form any more; the Settings →
  Triggers page lists every run) and the observability surface for all runs.
  The worker is enabled by default and executes each run with a bounded tool
  loop; there is still no public plan-state mutation endpoint.

## Runtime Task API (JAN-204)

Read-only, authenticated, owner-scoped routes under `/api/runtime/tasks`
(`api/runtime_tasks.py`) behind the Agent Tasks page. They join the three
tables that persist unattended plan-runtime work -- `runtime_triggers`,
`runtime_delegations`, and `plan_runtime_states` -- in
`services/runtime_task_service.py`, whose `list_tasks_for_user`,
`get_trigger_task_for_user`, and `get_delegated_task_for_user` filter by the
owner themselves. No new table: everything comes from what the runtime
already persists, plus the `runtime_triggers.definition_name` snapshot
(migration `d4f7b2c9e1a6`). Chat turns are not tasks here; their steps are the
live turn timeline (JAN-316) and their tool calls `conversation_tool_calls`
(JAN-249).

- `GET /api/runtime/tasks/` returns one newest-first `RuntimeTaskPage`
  (`items`, `total`, `limit` (default 25, `1–100`), `offset`, `has_more`) of
  the caller's trigger runs and delegated tasks, merged by `created_at` with a
  `UNION ALL` so the pagination is exact across both tables. `source` filters
  to `trigger` (ad-hoc runs, no definition), `definition` (runs of a saved
  definition) or `delegated`; `status` to one lifecycle status (`queued`,
  `running`, `waiting` -- a trigger run parked on its delegated children,
  JAN-207 --, `completed`, `failed`, `exhausted`, `cancelled`). Anything else
  is a 422. The page keeps polling while a listed task is queued, running or
  waiting. Each `RuntimeTaskSummary` carries `source`, `ref_id`
  (`trigger_id` or `delegation_id`), `task_id`, a `title` (the definition
  name, else the objective's first line, else the trigger source), `status`,
  timestamps, the plan's `plan_status`, step counts, the in-progress
  `current_step`, `definition_id`/`definition_name`, a delegated task's
  `conversation_id`, and a run's `child_count` (delegations whose
  `parent_task_id` is its `task_id`). A run is a definition run when it was
  enqueued from a definition: `RuntimeTrigger.definition_name`, the name
  snapshotted at enqueue time, outlives the definition, so a deleted
  definition's runs keep `source=definition` and their original name, while
  `definition_id` is `null` (no link) once the definition is gone. A live
  definition's current name wins over the snapshot.
- `GET /api/runtime/tasks/triggers/{trigger_id}` and
  `GET /api/runtime/tasks/delegations/{delegation_id}` return a
  `RuntimeTaskDetail`: the summary plus the plan snapshot's `objective`,
  `task_mode`, `node`, `reason`, and `steps`. Each step has its objective,
  status, dependencies, required tools, `error`, `output` (the model's answer),
  and a `trace` decoded from the audit header
  `unattended_execution.format_step_result` writes at the top of the step
  result: `model`, `authorized_tools`, `invoked_tools`, and the `skipped`,
  `refused`, `failed`, and `suspected_injection` lines, plus `tool_selection`
  (the brain-selected toolset and its reason, JAN-300). That header is the only
  persisted tool trace of an unattended run (there is no per-call table for
  them), so a result without it is returned whole as `output` with no
  `trace`. A trigger run adds `trigger_source`, `attempt_count`, `last_error`,
  its delegated `children`, and as `parent` the run that scheduled it
  (`parent_trigger_id`, JAN-274) when the caller owns it. A delegated task adds
  `allowed_tool_ids`, `delegation_result`/`delegation_error`, and as `parent`
  the caller's newest trigger run with its `parent_task_id`.
- Another user's (or an unknown) id answers `404`, never `403`. The raw trigger
  `payload` and claim tokens are never returned. Listing and detail first
  expire the caller's delegations that outlived their lease
  (`expire_stale_delegations`), so an orphaned child never shows as running
  forever. A plan snapshot that no longer validates is skipped (the task shows
  without steps) rather than failing the page.
- Agent-scheduled triggers may share a `task_id` (it "groups related runs"),
  and plan state is keyed by owner + `task_id`, so such runs show the same,
  latest plan snapshot and children -- the same limitation the trigger
  history's `result` already has.

## Trigger Definition API (JAN-228)

All routes are authenticated and owner-scoped under `/api/runtime/definitions`
(`api/trigger_definitions.py`); another user's definition reads as `404`.

- `GET /` lists the user's definitions, built-ins first then by name, seeding
  any missing built-in (`builtin.briefing`, `builtin.digest`, `builtin.mail`)
  first. The optional `?timezone=<IANA>` (sent by the web UI) moves a
  still-unedited, UTC-seeded Morning briefing to that timezone once.
- `POST /` creates a user-authored definition (`origin="user"`) and returns
  `201`. Body: `name`, `objective`, `tool_ids`, `kind` (`manual` | `schedule` |
  `event`), `schedule` (`{"type":"daily","time":"HH:MM","days":[0-6]}` or
  `{"type":"interval","minutes":N}`, `5–10080`), `timezone` (IANA, default
  `UTC`), `event_type`, `enabled`, and optional `llm_provider`/`llm_model`
  (JAN-233; `null`/empty = default backend). Validation failures (unknown tool
  id, unknown timezone, event type, or provider, malformed schedule) return
  `422`.
- `PATCH /{id}` applies a partial update and re-validates the whole
  definition. The `kind` of a built-in cannot change (`422`); editing never
  changes `origin`. A Janus proposal that is not `approved` answers `409`; the
  owner's edit of an approved one re-records its approval (JAN-235).
- `DELETE /{id}` returns `204`; deleting a built-in or a non-approved proposal
  returns `409` (disable / reject it instead).
- `POST /{id}/run` queues an immediate run of any kind and returns `202` with
  the new `RuntimeTriggerResponse` (`409` for a non-approved proposal).
- `GET /proposals?status=&limit=` (JAN-235) lists the definitions Janus
  drafted, newest first (`TriggerProposalResponse`: the definition plus
  `blocked_tool_ids`, `untrusted_context`, `next_occurrence`, and the
  `supersedes` summary for the review diff). `GET /` lists only `approved`
  ones among them.
- `GET /proposals/count` returns `{"open": N}` for the navigation badge.
- `POST /{id}/approve` approves a `proposed` draft as the owner, with optional
  edits (`name`, `objective`, `tool_ids`, `kind`, `schedule`, `timezone`,
  `event_type`, `llm_provider`, `llm_model`) and `enable` (default `true`);
  `409` unless it is still `proposed`, `422` for invalid edits, `404` for a
  definition that is not a proposal. Approving a replacement supersedes the
  approved definition it names.
- `POST /{id}/reject` with an optional `reason` (shown to Janus).
- `GET /{id}/runs?limit=` (default `20`, `1–100`) returns that definition's
  newest runs with `result`/`step_error` decoded from plan state.
- `GET /event-catalog` (JAN-210) lists the subsystems that publish trigger
  events, each with `subsystem`, `label`, `description`, and `events`
  (`event_type`, `label`, `description`, `payload: [{name, description}]`),
  from `trigger_events.catalog()`: `mail` (`mail.received`), `whatsapp`
  (JAN-361) and `planner` (JAN-362); each event also carries
  `default_max_runs_per_hour` (JAN-363). It
  replaced the flat `GET /event-types`. On create/update `event_type` is the
  `TriggerEvent` enum, so a value outside the catalog is a `422`.
- Event definitions (JAN-363) accept `event_filter` (`null` or a list of
  `{field, op: "in" | "not_in" | "contains", values: [str]}`; `field` must be
  a payload field of the event, at most 10 conditions and 50 values) and
  `max_runs_per_hour` (`null` or 1–1000) on create, update, and proposal
  approval. On `POST`, omitting `max_runs_per_hour` applies the event's
  default; an explicit `null` stores no limit. Invalid values are a `422`.
- Responses (`TriggerDefinitionResponse`) include `id`, `builtin_key`, `name`,
  `objective`, `tool_ids`, `kind`, `schedule`, `timezone`, `event_type`,
  `event_filter`, `max_runs_per_hour`, `rate_limited_count`,
  `last_rate_limited_at`, `enabled`, `origin`, `llm_provider`, `llm_model`, `next_run_at`,
  `last_run_at`, timestamps, and the proposal fields (`proposal_status`,
  `proposal_rationale`, `proposed_at`, `proposal_source`, `supersedes_id`,
  `approved_at`, `rejected_at`, `rejection_reason`; all `null` except on
  Janus drafts).

## Removed Endpoints (JAN-169, JAN-227, JAN-231)

- `/api/agents` (agent-definition CRUD) and
  `GET/POST/DELETE /api/agents/{id}/skills[/{skill_id}]` (skill assignment).
  Skills are one flat library managed through `/api/skills`; `SkillSummary`
  no longer carries `assigned_agent_count`.
- `GET /api/conversations/{id}/handoffs` (succeeded by `GET /api/conversations/{id}/delegations`, JAN-208).
- The streamed `/ws/briefing` token protocol: the socket now sends today's
  briefing text as one message between `__START__` and `__END__` (running
  the briefing definition inline when needed or on `?refresh=1`; an optional
  `?timezone=` is used as for `GET /api/runtime/definitions/`).

## MCP Tool Registry API

- Authenticated `/api/tools/registry` returns stable built-in metadata (each
  built-in also carries, for the current user, `unattended_allowed`,
  `default_unattended_allowed`, `chat_mode`, `default_chat_mode`,
  `locked_reason`, `withheld_unattended_tools`, and `connected`) and only the current user's remote MCP server records.
- `GET /api/tools/policy-modes` / `PUT /api/tools/policy-modes` (JAN-359)
  read and change the user's blanket modes,
  `{"unattended_mode": "per_tool"|"allow_all", "chat_mode":
  "per_tool"|"allow_all"}` (omitted field unchanged, unknown value `422`).
  `unattended_mode = "allow_all"` confirms every switchable tool for
  unattended runs and lifts the `images` and remote MCP locks;
  `chat_mode = "allow_all"` skips every approval card in chat (tools *Off in
  chat* stay withheld). See `tool_policy.py` and
  [Tool permissions](../feature-specs/tool-permissions.md).
- `GET /api/tools/policies` returns one row per built-in capability
  (`tool_id`, `unattended_allowed`, `default_unattended_allowed`,
  `chat_mode`, `default_chat_mode`, `overridden`, `locked_reason`,
  `withheld_unattended_tools`). `chat_mode` is the *In chat* switch
  (JAN-306): `"allowed"` for a tool that is not `confirmation="required"`
  (no switch), otherwise `"ask"` (default, per-call approval), `"always"`
  (runs without asking, JAN-359), or `"off"` (withheld from chat).
  `effective_unattended_allowed` and `effective_chat_asks` (JAN-359) say what
  applies once the blanket modes below are taken into account.
  `PUT /api/tools/policies/{tool_id}` with `{"unattended_allowed":
  true|false|null, "chat_mode": "ask"|"always"|"off"|null}` sets or (with `null`)
  resets either switch; an omitted field is left unchanged. `chat_mode` on a
  tool that is not `confirmation="required"` returns `422`. Changing a locked
  capability (one in `tool_policy.LOCKED_UNATTENDED`; only the chat-only
  `triggers.propose`, `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, and `character.propose` since JAN-272/274 unlocked `triggers` and
  `events`, which are now switchable) returns `422` for `unattended_allowed`
  and an unknown one `404`.
  The switch governs unattended runs only: ad-hoc triggers, trigger
  definitions, and delegated tasks (see
  [LLM and Agent Safety](llm-agent-safety.md#unattended-tool-switch-jan-229-jan-255)). `/api/tools/servers` provides
  owner-scoped create, update, discovery/refresh, disconnect, OAuth-start, and
  per-discovered-tool enablement operations. Create and update accept a
  write-only static bearer token (`access_token`; `clear_access_token` on
  update), exclusive with OAuth; server responses carry `credential_status`
  (`none`/`oauth`/`static`) and never the token (JAN-342).
- Request validation errors (`422`) omit each error's `input` app-wide, so a
  submitted token, password, or key is never echoed back (JAN-342).
- Remote discovery is restricted to the streamable HTTP transport. The service
  validates endpoint DNS/IP safety, requires HTTPS for non-loopback hosts,
  rejects redirects, and applies bounded HTTP and response-body limits.
- OAuth callbacks are the only anonymous route in this surface; short-lived
  state binds the callback to its owner and server, uses S256 PKCE, and returns
  only a Settings redirect. Token material is encrypted and omitted from API
  responses and event details. See the [focused MCP registry spec](../feature-specs/mcp-tool-registry/tech-spec.md).

## Remote A2A Agents API (JAN-404)

- `/api/tools/a2a-agents` (`api/a2a.py`, owner-scoped, cookie session with CSRF):
  - `GET` lists the user's agents.
  - `POST` creates one with `{name, base_url, token?}`, switched off. It fetches the card at once and records a failed fetch in `last_error` (still 201). An unsafe URL is 422.
  - `PATCH /{id}` takes `name`, `base_url`, `enabled`, `token` or `clear_token`. A new URL forgets the card and the token. Switching on without a fetched card is 422.
  - `POST /{id}/refresh` fetches the card again; a failure switches the agent off.
  - `DELETE /{id}` removes the agent and its tool policy.
- The token is write-only (`token_configured` only). Another user's agent is 404.
- The router is mounted before `tools.py`. See [External Agents](../feature-specs/external-agents.md).

## Webhooks API (JAN-406)

- **Management.** `/api/webhooks` (`api/webhooks.py`, owner-scoped, cookie session with CSRF):
  - `GET` lists the user's endpoints (`key_prefix`, counters; never the key or secret).
  - `POST` with `{name}` returns `{webhook, path, secret}`, the only time the URL path and secret are shown. At most 20 per user.
  - `PATCH /{id}` takes `name` and `enabled`.
  - `POST /{id}/rotate-secret` returns the new secret once.
  - `DELETE /{id}`.
- **Delivery.** `POST /api/webhooks/in/{key}` is public, authenticated by its HMAC signature, and exempt from the cookie CSRF check by path prefix.
  - Success: `202 {"accepted": true, "runs": n}`.
  - Errors: 401 unsigned, wrong or stale; 404 unknown or off; 413 over 64 KB; 400 not JSON; 429 over 60 a minute.
  - See [Inbound Webhooks](../feature-specs/inbound-webhooks.md).

## Skill Suggestions API (JAN-407)

- `GET /api/skills/suggestions` (`api/skill_suggestions.py`) returns the signed-in user's suggestions, computed on request without a model call. Each has `key`, `tools`, `turns`, `examples` (chat titles) and `brief`, the goal for `POST /api/skills/design-assist`.
- `POST /api/skills/suggestions/dismiss` with `{key}` hides one for 30 days (204).
- The router is mounted before `skills.py`, so `/api/skills/{skill_id}` does not capture `suggestions`. See [Skill Suggestions](../feature-specs/skill-suggestions.md).

## Telegram API (JAN-405)

`api/telegram.py`, scoped to the signed-in user's single bot. The token is write-only and never returned.

- `GET /api/telegram` returns `connected`, `bot_username`, `linked`, `pairing_code` (while unpaired) and `last_error`.
- `POST /api/telegram/connect` with `{token}` checks it with Telegram's `getMe`, stores it encrypted, issues a new pairing code, and (re)starts the bot's polling worker. A token Telegram rejects is a 422 and nothing is stored.
- `POST /api/telegram/test` sends a test message to the linked chat (204; 422 while unlinked).
- `DELETE /api/telegram` stops the worker and forgets the bot (204).
- See [Telegram Connector](../feature-specs/telegram-connector.md).

## Character API

- `GET /api/character` and `PUT /api/character` require the authenticated user and
  are scoped to that user's single `character_profiles` record.
- The profile persists the versioned constitution fields: `constitution_id`,
  `version`, `name`, `identity`, `core_values`, `non_negotiables`, `traits`,
  `exploration_rate`, and (JAN-230) the optional free-text
  `additional_instructions` and `memory_instructions`. A successful update
  creates version `1` or increments the existing version.
- `POST /api/character/preview` takes the same payload as `PUT` and returns
  `{system_prompt, memory_instructions}`: the exact system prompt chat and
  every trigger run would use for that draft (constitution plus the current
  skill library and guardrails) and the effective memory-suggestion rules. It
  persists nothing.
- When no profile exists, `GET /api/character` returns a safe Janus default with
  version `1`; it does not create a database row.
- Validation bounds are enforced before persistence: `core_values` and
  `non_negotiables` contain 1–12 entries, `traits` contains at most 20 entries,
  each trait baseline and maximum delta is `0.0–1.0`, and exploration rate is
  `0.0–1.0`. Trait names are required, normalized, and unique
  case-insensitively.
- The rendered constitution plus the persisted adaptive state (JAN-301) is the
  system-prompt source for chat, trigger runs, and delegated tasks; the preview
  renders the saved state too.
- **Adaptive character state (JAN-301)**, all owner-scoped (`api/character.py`,
  `services/character_state_service.py`); Janus can only propose from chat:
  - `GET /api/character/state` returns `{state, defaults, envelope, revision,
    updated_at, focus_max_chars}`; `state` is `{tone, verbosity, proactivity,
    current_focus}`.
  - `PUT /api/character/state` applies an owner edit (the whole state);
    `PUT /api/character/state/envelope` stores the bounds and defaults
    (`{tone|verbosity|proactivity: {default, allowed}, focus_allowed}`; a
    default outside `allowed` is `422`) and pulls the state back inside them;
    `POST /api/character/state/reset` restores the defaults;
    `POST /api/character/state/rollback` (`{change_id}`) restores the state an
    earlier audit row produced. Each returns the state view.
  - `GET /api/character/state/history` returns one page of audit rows newest
    first, `{items, next_before_id}` (actor, action, chat turn id, proposal id,
    previous and new state, time); `limit` is 1–100 (default 50) and passing
    `next_before_id` back as `before_id` returns the next older page, so every
    earlier state stays reachable for a rollback;
    `GET /api/character/state/proposals` (optional `status`) lists Janus's
    proposals; `POST .../proposals/{id}/approve` applies one and returns the
    state view, `POST .../proposals/{id}/reject` closes it.
  - A value outside the envelope, or a proposal that was already decided,
    answers `409` with the reason; another user's proposal or audit row
    answers `404`. Focus text is one line of at most 200 characters (`422`
    beyond).

## Planner API Surface

- `GET /api/planner/tasks` and `GET /api/planner/events` return unified planner views that include Janus-owned items and imported read-only context, with provenance metadata.
- `GET/POST/PATCH /api/planner/local/tasks` and `GET/POST/PATCH /api/planner/local/events` manage Janus-owned writable planner items. `DELETE /api/planner/local/tasks/{id}` and `DELETE /api/planner/local/events/{id}` (JAN-221) delete one of the caller's own items (`204`; a foreign, imported, or missing id is `404`; served by `api/planner_items.py` and `services/planner_item_deletion.py`): a deleted task's subtasks move up to its parent, its `PlannerLink` evidence rows are removed, and suggestions that targeted it lose the target (pending update suggestions are dismissed, since nothing is left to update), and the task's own knowledge source with its chunks, vectors and graph evidence is deleted in the same transaction (SQLite can give the freed id to the next task).
- **Connections (OAuth accounts).** Google and Microsoft share one route set and one code path (`services/connect_providers.py` registers each provider's configured check, `begin` and `complete`; `google_connect_service.py` and `microsoft_connect_service.py` implement them alike). The Google client id/secret come from `settings.toml` (`services/google_client.py`), not the database or the planner settings. `GET /api/connections` returns each provider's `configured`/`connected`/`mail_supported`/`mail_ready` state (Connect Microsoft requests only `Calendars.Read.Shared` and `Mail.Read`, the permissions the app registration has approved), the connected address, its single redirect URI (`<base>/api/connections/<provider>/callback`) and config keys. `GET /api/connections/{provider}/auth-url` starts "Connect <provider>" (`400` when not configured). The unauthenticated `GET /api/connections/{provider}/callback` consumes the single-use `OAuthState` (a Google Sign-In state, which has no user, finishes the login instead: `api/auth.finish_google_login`) and redirects to `/settings?tab=connections&connected=<provider>` or `&error=<code>` (`<provider>_denied`, `_invalid_callback`, `_state_invalid`, `_not_configured`, plus the exchange codes such as `google_invalid_grant`, `google_scope_missing`, `google_no_refresh_token`, `google_account_save_failed`, `microsoft_flow_failed`, `microsoft_token_exchange_failed`). `DELETE /api/connections/{provider}` removes the connection, deactivates its mailboxes and deletes the planner sources it backed with their imported rows (also sources that predate connections). `POST /api/connections/{provider}/mailbox` adds a Gmail or Outlook mailbox (`EmailAccount.auth_type` = `google`/`microsoft`, `connection_id`, no password) read by its own monitor: Gmail over IMAP XOAUTH2 (`services/mailbox_oauth_service.py`, `imap_service._login`), Outlook by polling Microsoft Graph every minute (`graph_mail_service.GraphMailMonitor`: a `sync_cursor` instead of UIDs that stops in front of a message the callback failed to store and retries it for three polls, the first sync capped by `initial_sync_email_limit`, a heartbeat every 30 seconds, the loop ending when the mailbox is disconnected, no mark-as-read without `Mail.ReadWrite`); `409` when not connected, the mail scope was not granted, or the address already has a mailbox. Tokens live Fernet-encrypted in `oauth_connections` (one row per user and provider). Served by `api/connections.py`.
- `GET/PUT /api/planner/{provider}/calendars` (`google` or `microsoft`) list and select the calendars to sync through one `CalendarSource` table per provider (Google: `primary` alias, no validation; Outlook: `calendar_id:event_id` external ids, validated, no selection means the default calendar); deselected calendars lose their imported events. `POST /api/planner/sync` syncs the caller's sources now (`api/planner_sources.py`).
- `GET /api/planner/suggestions` returns reviewable planner suggestions, defaulting to pending items.
- `PATCH /api/planner/suggestions/{id}` allows user edits before approval.
- `POST /api/planner/suggestions/{id}/approve` applies a suggestion into a new or existing local planner item.
- `POST /api/planner/suggestions/{id}/dismiss` records explicit rejection without mutating canonical planner data.
- `GET /api/memory/` lists or searches the caller's memory facts: `query`, `include_archived`, `limit`, and `source` (`user`/`agent`/`compaction`, JAN-245; anything else returns `422`). Filters narrow the candidates before semantic/keyword ranking.
- `POST /api/memory/` stores a user-authored fact; a fact with the same summary (ignoring case, `%`/`_` matched literally, JAN-224) is refreshed instead of duplicated. `PATCH /api/memory/{id}` (JAN-224) edits a fact's `summary` and/or `detail`: an omitted field is unchanged, an empty `detail` clears it, a blank summary or an empty body returns `422`, and a missing or foreign fact returns `404`. `DELETE /api/memory/{id}` removes one; `POST /api/memory/compact` and `GET /api/memory/compact/latest` start and read the background compaction run.
- `GET/PATCH /api/planner/settings` (`PATCH` admin only) read and update system settings, including the active LLM provider and per-provider model/key/base-URL fields (Ollama, OpenAI, OpenRouter, Gemini, Anthropic, GitHub Models; Azure OpenAI has `azure_openai_endpoint`/`azure_openai_deployment`/`azure_openai_api_version` and `azure_openai_auth_mode` = `entra` | `api_key`, anything else returns `422`; JAN-340). Keys are write-only and surface as `*_api_key_configured` flags; the provider fields of the response are built by `services/llm_settings.provider_settings_payload`. The per-surface model choices `chat_llm_provider`/`chat_llm_model` and `utility_llm_provider`/`utility_llm_model` (JAN-233) are read and written here too; providers are normalized to lowercase, an unknown provider returns `422`, and `null` or an empty string resets a field to the default backend. The embedding fields (JAN-241) are `embedding_provider` (`ollama`/`openai`/`gemini`; `null`/empty turns embeddings off and clears the model; anything else returns `422`), `embedding_model` (empty = the provider's suggested model; a provider switch without a model resets it), and `embedding_min_similarity` (0–1). Responses also carry `embedding_default_models` and `vector_search_available`. The image fields (JAN-253) are `image_provider` (`openai`/`gemini`/`openrouter`; `null`/empty turns image generation off and clears the model; `ollama`, `anthropic`, or anything else returns `422`) and `image_model` (empty = the provider's default; a provider switch without a model resets it); responses also carry `image_default_models` and `image_storage_limit_mb`.
- `POST /api/planner/test-llm` (admin) tests the selected provider with the unsaved draft settings in the body. A draft base URL without a draft key is refused (`status: error`) rather than sent the stored key (JAN-222).
- `POST /api/planner/llm-models` (admin) lists a provider's models as `{"models": [{"id", "label"}], "error": null}` using draft `api_key`/`base_url` from the body, falling back to stored settings. Provider failures return HTTP 200 with an empty list and a short `error`. Successful listings are cached in-process for five minutes per provider, key hash, and base URL. GitHub Models lists `{origin}/catalog/models` beside its inference base URL. Azure OpenAI (JAN-349) lists `GET {endpoint}/openai/deployments?api-version=2023-03-15-preview` (the deployment ids, labelled `deployment (model)`; `/openai/models` would be the catalog of deployable models) with the draft `auth_mode` from the body (else the stored one): an Entra ID bearer token from the shared credential in `entra` mode, the `api-key` header in `api_key` mode under the usual stored-key policy. The endpoint is validated like the chat client's (resource root, `https://`). An Entra token is only minted for an Azure OpenAI host (`llm_provider_clients.is_entra_endpoint`). Deployments whose `status` is not `succeeded` or whose `model` is not a chat model (the OpenAI non-chat markers) are dropped. A 403 PermissionDenied, a 401, a non-Azure host in Entra mode, or a missing Azure sign-in returns an `error` that says so and asks for the deployment name. For the stored-key policy (here and in `POST /api/planner/test-llm`), Azure's `azure_openai_endpoint` plays the base-URL role; in Azure `entra` auth mode (draft value, else stored) the policy is skipped, since the stored key is never sent. GitHub Models listings drop catalog entries whose `supported_output_modalities` lack `text` (embedding models).

## Knowledge Sources API (JAN-242)

All routes are owner-scoped (`get_current_user`); another user's source answers `404`.

- `POST /api/knowledge/uploads` (multipart `file`) stores a document and parses it before answering (`201`), so the response is the resulting source: `ready`, or `pending` with `status_detail` when parsing failed (the worker retries once, then marks it `failed`). Files over 10 MB return `413`; unsupported types (anything but PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, HTML, EML) return `422`. Only the extracted text is kept.
- `GET /api/knowledge/sources` lists the caller's sources, newest first, with `source_type`, `status`, `limit` (default 100, max 500), and `offset` filters. Each item carries `id`, `source_type`, `owner_id`, `part`, `title`, `mime_type`, `size_bytes`, `status`, `status_detail`, `chunk_count`, `processed_at`, and `created_at`; staged bytes are never returned.
- `DELETE /api/knowledge/sources/{id}` deletes a source with its chunks and their vectors (`204`). Sources built from a Janus row (mail, chats, events, tasks) are rebuilt by the next ingestion pass while that row exists, so the UI offers delete only for uploads and attachments.

## Knowledge Graph API (JAN-243)

All routes are owner-scoped (`get_current_user`).

- `GET /api/knowledge/extraction` returns `enabled` (the built-in Knowledge extraction trigger's flag), `definition_id`, `model` (`provider/model`), `is_cloud` (`services/model_locality.is_cloud_model`: the provider is not Ollama, or Ollama's base URL is not a loopback, private, or LAN host, e.g. hosted Ollama), `monthly_token_budget`, `period`, `tokens_used`, `calls`, and `pending_chunks`. It never writes: the preferences row and, if still missing, the built-in definition are created on the first save (the trigger worker also seeds built-ins on its poll).
- `PUT /api/knowledge/extraction` takes `enabled` and/or `monthly_token_budget` (0 to 1,000,000,000; else `422`) and returns the new status. The cloud warning is the UI's job; the API does not ask.
- `GET /api/knowledge/entities` lists entities with `mention_count` and `relation_count`, most mentioned first, filtered by `entity_type` and `q` (substring of the normalized name), with `limit` (default 100, max 500) and `offset`.
- `GET /api/knowledge/search` runs hybrid retrieval: `q` (required, at most 500 characters), `source_type` and `entity_id` (repeatable filters), `since`/`until` (source date), `limit` (1–20, default 8), and `hops` (0–2, default 1). It returns `hits` (chunk id, text trimmed to 1,200 characters, source id/type/title/date, mentioned entities, and `matched_by`: `query` or `graph via …`) and the `entities` the query matched.
- `GET /api/knowledge/entities/{id}` returns one entity with aliases, description, relations in both directions (predicate, other entity, confidence, supporting chunk count), and the sources mentioning it most; `404` for another user's entity.
- `GET /api/knowledge/graph` (JAN-334, `knowledge_provenance.graph_for_user`) returns `nodes` (active or pinned topics, then entities — those linked to a shown topic first, then the most mentioned — each with `id` `topic:<id>`/`entity:<id>`, `kind`, `ref_id`, `label`, `entity_type`, `mentions`, and `wiki_page_id`) and `edges` (`evidence` topic → entity, weighted by the passages of the topic's email/calendar evidence or of its wiki page's citations that mention the entity; `relation` entity → entity labelled with the predicate). `topics` (default 30) and `entities` (default 60) are limited to 0–150. Only the caller's rows are read; every query is an aggregate or limited (pair weights are `COUNT(DISTINCT chunk)`, a cited evidence passage counts once, a passage cited by several topic pages links each of them).
- `GET /api/knowledge/merge-suggestions` lists pending suggestions with both entities. `POST /api/knowledge/merge-suggestions/{id}/accept` merges the duplicate into the entity and returns it; `POST .../{id}/reject` (`204`) keeps both and blocks the pair. An unknown, foreign, or already resolved suggestion returns `404`.

## Wiki API (JAN-244)

All routes are owner-scoped; a foreign or unknown page or revision answers `404`.

- `GET /api/wiki/pages` lists pages (most recently updated first) with `pending_proposals`, filtered by `subject_type`, `subject_id`, and `q` (title or text). `GET /api/wiki/pages/{id}` adds `body`, `citations`, pending `proposals`, and published `revisions` (newest first).
- `GET /api/wiki/pages/{id}/provenance` (JAN-334, `knowledge_provenance.page_provenance_for_user`) resolves the published page's citations against the caller's rows: `citations` (each `n` with `kind` `passage` or `fact`, `available`, the snapshot `title`/`date`/`source_type`, and either the live chunk `excerpt` (up to 4,000 characters), its `source`, and the mentioned `entity_ids`, or the live memory `fact`), `entities` (mentioned in the cited passages, plus the page's own entity, with the citing markers and their `wiki_page_id`), `facts` (cited facts, then the ones the next synthesis would use), and the `subject`. Another user's page is `404`; a citation to a row the caller does not own resolves to `available: false` with no content.
- `POST /api/wiki/pages` (`subject_type`, `subject_id`) creates the page about a topic or entity (`201`, or the existing page); synthesis writes it.
- `PUT /api/wiki/pages/{id}` publishes a user edit (`body` 1–50,000 characters, optional `title`) and marks the page `user_edited`; citations whose `[n]` marker survives are kept.
- `POST /api/wiki/revisions/{id}/accept` publishes a pending proposal, `/dismiss` (`204`) dismisses it, and `/restore` publishes a copy of an earlier published revision as a user edit.
- `GET/PUT /api/wiki/synthesis` read (never writing) and set the Wiki synthesis switch; the status carries the model, `is_cloud`, and the shared monthly budget's usage.

## In-App Documentation API

- **Seamless integration:** Zensical documentation is rendered directly inside the Janus dashboard.
- **Dynamic content delivery:** `/api/docs/{path:path}` serves Markdown files from `docs/docs`.
- **Navigation parity:** `/api/docs/nav` parses `docs/zensical.toml` to provide the same navigation structure as the standalone Zensical site.
- **Canonical technical spec surfacing:** The repository-root `TECH_SPEC.md` acts as the entrypoint and index for the technical-spec wiki and is exposed through the in-app docs navigation as `tech-spec.md`.
- **Explicit root-doc allowlist:** The docs API may serve selected repository-root Markdown files, but only through an explicit allowlist so the docs surface cannot become a general file browser.
- **Shared file access (JAN-297):** `services/docs_service.py` owns path resolution and confinement (no `..`, absolute-path, or outward-symlink escapes from `docs/docs`; the `tech-spec.md` alias is the only root file). The router and Janus's `docs` agent toolbox both use it; the toolbox additionally skips `docs/docs/reviews/`, which the router still serves.
- **Login required (JAN-299):** the router carries `Depends(security.get_current_user)`, so `/api/docs/*` answers `401` without a session. The frontend `/docs` page already sits behind the login redirect; the tech specs and the security assessments under `reviews/` are not public documentation.
- **Child technical specs:** Focused child specs live under `docs/docs/tech-spec/` so they are normal documentation pages, available through both Zensical navigation and in-app docs.
- **User-docs consistency workflow:** Delivery workflows must run a user-facing docs audit after the final code push so setup, configuration, and feature documentation stay aligned with shipped behavior.

## Stats API (JAN-258)

- `GET /api/stats?days=7|30|90&timezone=<IANA>` (default 30 days, UTC) returns
  the current user's `StatsSummary`: `dates`, then `mail`, `triggers`, `memory`,
  and `messaging`, each with a `per_day` series aligned to `dates` (local
  calendar days in `timezone`) plus totals. Other `days` values or an unknown
  timezone are a 422.
- Everything is owner-scoped: mail through the user's own email accounts,
  runs/definitions/facts/topics/wiki pages/knowledge sources/WhatsApp messages
  by `user_id`, chat messages through the user's conversations. Trigger runs
  are bucketed by `created_at` (the occurrence time); `failed` includes
  `exhausted`, `pending` is queued + running + waiting (JAN-207); `success_rate` is completed /
  (completed + failed), `null` when nothing finished; per-definition
  `avg_duration_seconds` averages `completed_at - claimed_at` of completed runs.
- `approvals` (JAN-371) reports how often the user accepts what Janus proposes,
  each item counted by when it was proposed: `automations` (trigger definitions
  Janus drafted, by `proposed_at`: proposed/approved/rejected/withdrawn/superseded),
  `chat_tools` (`ConversationToolCall.approval`: approved/denied/timeout/disconnected/
  unavailable, through the user's conversations), and `character`
  (`CharacterStateProposal`: proposed/approved/rejected). Each has `totals`,
  `total`, `approval_rate` = approved / (approved + rejected or denied), `null`
  when nothing was decided (timeouts, withdrawals and supersessions are not
  decisions), and `median_hours_to_decision` (proposal to approval or rejection;
  always `null` for chat tools, which record no decision time). Whether the user
  edited a proposal before approving it is not recorded, so it is not reported.
- `llm` (JAN-408) reports the user's model calls and tokens from
  `llm_usage_days`. It has `per_day`, `by_surface` (chat, trigger, delegated,
  other), `by_model`, and `totals`, which include `unreported_calls`, the
  calls whose provider returned no token counts. Rows are per UTC day, so the
  window is the last `days` UTC days, not the local calendar. See [LLM Usage Tracking](../feature-specs/llm-usage-tracking.md).

## Domain-Change WebSocket (JAN-104)

- **Endpoint:** `/ws/changes` (`websocket/changes.py`) authenticates through `security.get_websocket_user_or_none` like `/ws/chat` (cookie or bearer JWT; the `Origin` allowlist applies), closes anonymous connections with 1008, and holds no DB session while open (the handshake's is closed before the socket opens, and each heartbeat re-check opens its own), so a long-lived tab does not pin a pooled connection.
- **Frames (server to client only):** `{"type": "ready"}` once subscribed; `{"type": "changed", "domains": [...]}` after a commit touched one of the user's domains (bursts within 100 ms are coalesced into one frame); `{"type": "ping"}` every 25 s. Before each ping the session is re-validated (user, session generation, revocation), so a logged-out or revoked session is closed with 1008. Inbound frames are ignored.
- **Payload rule:** frames carry domain names only, never row ids or content. Changes are owner-scoped: a user only hears about rows they own, except the global skill library (`skills`), which is broadcast because every user's chat prompt renders it.
- **Centralised emission:** routers and services do not publish anything themselves. `domain_changes.py` hooks SQLAlchemy `Session` events (`before_flush`/`after_flush` for ORM instances, `do_orm_execute` for ORM bulk `update()`/`delete()`), resolves each tracked row's owner (directly from `user_id`, or through the parent row for `EmailRecord`, `ReminderMemory`, `TopicEvidence`, `TaskRecord`, `CalendarEvent` and `MCPDiscoveredTool`), and publishes on `after_commit`; a rollback of the outermost transaction drops what it recorded. REST mutations, chat tools (including their isolated mutation sessions), trigger runs, delegated tasks and background workers therefore all emit the same events. While no browser is subscribed the hooks return immediately.
- **Limits:** the bus is in-process, so events only reach sockets served by the same backend process (Janus runs as one process today; a multi-process deployment would need an external broker). Raw `text()` SQL and Core statements outside an ORM session are not observed.
- **Not the trigger event catalog:** domain changes are UI cache-invalidation hints. They are unrelated to trigger definition event activations such as `mail.received` (`trigger_definition_service.emit_event`) and never start a run.

## System Events API

- **Owner-scoped read access (JAN-272):** `GET /api/events` returns a paginated, newest-first page of the events the caller may read. `models.Event.user_id` records the user an event concerns (`NULL` for system rows and every row written before the column existed). An admin reads every row; any other authenticated user reads only rows with their own `user_id` (`services/event_service.visible_events_condition`). Before JAN-272 every signed-in user read the whole log. `GET /api/events/sources` lists only the sources of rows the caller may read.
- **Query parameters:**
  - `event_type` (optional): exact match on `models.EventType` (enum: `INFO`, `SUCCESS`, `WARNING`, `ERROR`); omitted means no filter.
  - `source` (optional, 1–200 chars): exact match on `models.Event.source`; omitted means no filter.
  - `start` (optional, RFC 3339 datetime): inclusive lower timestamp bound. Treated as UTC if naive; timezone-aware bounds are converted to naive UTC via `time_utils.to_naive_utc` before comparison.
  - `end` (optional, RFC 3339 datetime): exclusive upper timestamp bound. Treated as UTC if naive; timezone-aware bounds are converted to naive UTC before comparison.
  - `limit` (1–200, default 100): page size.
  - `offset` (≥ 0, default 0): pagination offset.
- **Response envelope:** `{items: Event[], total: number, limit: number, offset: number, has_more: boolean}`.
  - `items`: newest-first ordered by `(timestamp desc, id desc)`.
  - `total`: count of all matching records before pagination.
  - `has_more`: true if `offset + items.length < total`.
- **Validation:** If both `start` and `end` are provided and `start >= end` (after UTC normalization), return `422 Unprocessable Entity` with message `"start must be before end"`. Invalid `limit`, `offset`, or `event_type` are rejected at FastAPI validation.
- **Sources filter:** `GET /api/events/sources` returns `string[]` of distinct, alphabetically sorted `models.Event.source` values (excluding `null`), for populating filter dropdowns.
- **Authentication:** Both endpoints require `Depends(security.get_current_user)`.

## System Status API Requirements

- **Authenticated dashboard surface:** The system status page lives inside the authenticated Janus dashboard rather than as a public status site.
- **GitHub-style overview:** The UI must provide an overall status banner, top-level service groups, and nested component details for configured integrations.
- **Initial service coverage:** V1 covers Mail Sync, Planner Sync, and the active LLM backend.
- **Normalized status vocabulary:** Services and nested components use `operational`, `degraded`, `outage`, and `unknown`.
- **Aggregate rules:** The page-level state is derived from the worst current top-level service state.
- **Version and release notes:** `GET /api/status/version?since=<X.Y.Z>` (login required) returns the running version (`importlib.metadata`), the in-app changelog path (`/docs/changelog`; GitHub Pages is disabled, so the docs page is the in-app one: `docs/docs/changelog.md` is a symlink to the root `CHANGELOG.md` that release-please maintains, which `docs_service` serves through its `changelog.md` root alias like `tech-spec.md`), the GitHub release link of the configured `auto_update_repository`, and the release notes after `since` up to the running version, newest first, at most five (`services/changelog_service.py` parses the file once per modification; links release-please appends to each bullet are dropped; no `since` or no newer release means no notes, and an install without `CHANGELOG.md` has none). The frontend remembers the last version each user saw in `localStorage` (`janus.lastSeenVersion.<user id>`, written when the flash is dismissed or times out) and flashes the notes after an update (`WhatsNewFlash`); Settings shows the version as a changelog link (`VersionLabel`).
- **Mail sync health:** Mail status is derived from persisted IMAP monitor state, heartbeat freshness, and supervised worker liveness.
- **Planner sync health:** Planner status is derived from `last_sync_at`, `last_sync_error`, and sync freshness windows.
- **Active LLM probing:** Janus persists the latest LLM health probe result so the page reflects ongoing backend health instead of only user-triggered tests.
- **Incident history:** Recent service degradations, outages, and recoveries are surfaced from structured status events persisted in the existing events log.
- **Frontend refresh:** The status page should poll the status summary API on an interval and render clear stale/error messaging with timestamps.

## Documentation Usability Boundary

Broader work to turn the docs into a clearer end-user manual remains a separate follow-up initiative. Technical-spec surfacing should not make the docs API a broad repository file browser.
