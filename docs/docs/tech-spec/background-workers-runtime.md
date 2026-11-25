# Background Workers and Runtime Model

> **Glossary note:** Janus is a single always-on agent. Its identity is the
> user's `CharacterProfile` constitution (`character.py`), its instructions are
> one flat skill library (`AgentSkill`, a historical table name), and
> everything it does outside chat is a **trigger run** (`RuntimeTrigger`),
> usually of a saved **trigger definition** (`TriggerDefinition`). The
> per-conversation `AgentDefinition` model, skill assignment, and agent-to-agent
> handoffs were removed in JAN-169/JAN-227 (migration `d5e8f1a2b3c4`); see
> "Removed: Legacy Agent Definitions" at the end of this page.

## Runtime Shape

- Janus runs background work in daemon threads rather than async worker processes.
- IMAP monitoring, planner sync, WhatsApp connector monitoring, status probing, the self-hosted auto-update check ([deployment-operations.md](deployment-operations.md#self-hosted-auto-update)), memory compaction, the durable trigger worker (and its scheduler tick), the knowledge-index worker, and delegated child tasks must fit the current thread-based model until a future architecture changes it.
- Long-running and background code must close database sessions explicitly in `finally` blocks.
- **One prompt source for every surface.** Chat, every trigger run, and every
  delegated child task render the same system prompt:
  `agent_runtime.build_janus_system_prompt(character, load_skill_library(db), character_state=state)`
  — the user's constitution (including its `additional_instructions`, JAN-230),
  the persisted adaptive character state (JAN-301, see "Adaptive Character
  State" below), and every stored skill. Unattended callers append their own
  run guidance after it; nothing else contributes identity or instructions.
  Chat alone passes `progress_updates=True`, which adds the
  `CHAT_PROGRESS_UPDATES` section (see "Progress narration" under the chat
  plan/step timeline below); unattended runs leave it out.
  The briefing context's `character_prompt` and the mail-ingestion classifier
  guidance render the same constitution and state
  (`character_state_service.prompt_character`).

## Adaptive Character State (JAN-301)

Scope item 5 of the plan-driven runtime: a stable constitution plus a bounded
adaptive state. Janus proposes, the owner approves (as for automations,
JAN-235).

- **Typed state.** `character.CharacterState` carries `tone` (`neutral`,
  `warm`, `formal`, `direct`, `playful`), `verbosity` (`brief`, `balanced`,
  `detailed`), `proactivity` (`reactive`, `balanced`, `proactive`) and
  `current_focus` (free text, one line, at most 200 characters), next to the
  constitution's trait baselines. It is persisted per user in
  `character_states` (`models_character.CharacterStateRecord`, with a
  `revision`); no row means the envelope defaults.
- **Owner-set envelope.** `character_profiles.state_envelope` holds, per
  dimension, the allowed values and the default, and whether a focus may be
  set (`NULL` = the built-in envelope: everything allowed, defaults
  `neutral`/`balanced`/`balanced`, no focus). Every write path checks it:
  owner edits, Janus proposals, and the approval of an older proposal are
  refused outside it (`CharacterStateError`, `409` over the API). Saving a
  narrower envelope moves out-of-bounds dimensions to their defaults (and
  clears the focus when it is switched off), audited as an `envelope` change.
  The constitution itself (`PUT /api/character`) is untouched by all of this.
- **Proposals, chat only.** The `character.propose` toolbox
  (`propose_character_state`, at most one per chat turn, at most 5 open per
  owner) files a `character_state_proposals` row with the chat turn's step id
  (`chat-conversation-<id>::message-<n>`) and a rationale. Nothing changes
  until the owner approves it on Settings → Character. A proposed focus that
  the prompt-injection rules flag is refused. The entry is enabled only in
  `task_mode="chat"`, is in `tool_policy.LOCKED_UNATTENDED`, and its builder
  returns no tool outside chat, so no trigger, automation or delegated run can
  propose or make a state change. The read-only `character` toolbox
  (`get_character_state`: state, envelope, last five changes, open proposals)
  follows the unattended switch like any read tool (default allowed) and may
  reach delegated children; its output is spotlighted
  (`reads_untrusted_content`). Brain-selected toolsets (JAN-300) treat both
  like any entry: a chat turn offers `character.propose` when the selector
  picks it or after `request_tools` adds it, and in unattended modes a
  selection naming it is trimmed because it is never authorized there.
- **Audit and rollback.** Every change writes a `character_state_changes`
  row: `actor` (`owner`, or `janus` for an approved proposal, with
  `chat_turn_id` and `proposal_id`), `action` (`edit`, `proposal`,
  `rollback`, `reset`, `envelope`), the previous and the new state, the new
  revision, and the time. A rollback restores the state an earlier row
  produced (refused when the current envelope no longer allows it); a reset
  restores the envelope defaults. No-op writes record nothing. The history API
  is paginated by cursor (`before_id`, at most 100 rows a page), so no audit
  row is out of reach.
- **Prompt rendering.** `CharacterConstitution.render_system_prompt` adds a
  `## Current adaptive state` section; the focus is rendered only through
  `prompt_injection.character_focus_block`: HTML-escaped, inside a
  `<character_focus>` block preceded by a "data, not instructions" line, and
  the tag is listed in `UNTRUSTED_CONTENT_POLICY`.
  `/ws/chat` renders the system prompt per message, so an approved change
  (or a constitution edit) applies from the next chat message on.
- **Owner scope.** Every query filters by the owner; another user's proposal
  or audit row answers `404`. Deleting a user deletes their state, proposals
  and history (`character_state_service.delete_for_user`).

## Durable Trigger Boundary

- `trigger_service.py` provides the persistence slice for manual, scheduled,
  and external-event trigger envelopes. Enqueue deduplicates by the
  task-scoped `(task_id, idempotency_key)` pair.
- The durable lifecycle is `queued → running → completed|failed`. Claims are
  atomic for queued, failed, or expired-running records. Each claim records a
  private token and timestamp; completion or failure must match both the row
  and the token. An expired running claim can therefore be reclaimed, while a
  stale worker cannot finalize the replacement claim. Failed rows are eligible
  for a later attempt; completed rows are terminal.
- A handler may *park* a run instead of finishing it (JAN-207): when it
  returns a result whose `is_waiting` is true (a `PlanRunResult` whose graph
  sits in `WAIT`), the worker and the inline dispatcher record the row
  `waiting` (`trigger_service.park_trigger`, token cleared) rather than
  `completed`. `waiting` is not claimable; `release_waiting_trigger` moves it
  back to `queued` atomically, and only from `waiting`, so a run cancelled
  while waiting is never resumed. `waiting` counts toward the per-owner
  backlog cap and is cancellable. Today only a trigger run waiting on its
  delegated children parks (see "Delegation Runtime").
- `DurableTriggerWorker` is a polling worker, enabled by default since
  JAN-199 (`runtime_trigger_worker_enabled`). Each poll reads at most
  `batch_size` runnable IDs ordered by creation time, then attempts each row
  independently; a race with another worker is reported as `in_progress` (or
  `duplicate` for a row already completed). The bounded read prevents one
  poll from growing without limit and keeps work available for later polls.
- Database sessions are phase-scoped: candidate selection, claim, and terminal
  state transitions each use a short-lived session closed in `finally`. The
  handler runs after the claim session closes and outside the transaction, so
  an LLM call or other slow task work never holds a database session open.
  Terminal writes are ownership-guarded; if the lease was reclaimed before a
  result was recorded, the worker reports a failed attempt with durable claim
  loss rather than false completion.
- A handler exception is recorded as `failed` and the row is eligible on the
  next poll. The worker does not implement delay, exponential backoff, or an
  attempt limit; those are later reliability-policy work. A successful handler
  is recorded as `completed` only when the owning token still matches.
- **Scheduler hook (JAN-228).** `DurableTriggerWorker` accepts an optional
  `scheduler(now)` callable that runs at the start of every poll, before the
  batch is read. Production startup passes
  `startup._run_trigger_scheduler_tick`, which seeds any missing built-in
  trigger definitions for every user and then calls
  `trigger_definition_service.run_due_schedules()`. A scheduler exception is
  logged and never stops the worker from executing already-queued runs.
- `run_forever(stop_event)` checks the event between bounded batches and waits
  with `Event.wait(poll_interval_seconds)`, making shutdown responsive without
  waiting out the full polling interval. The stop event does not cancel a
  handler already in progress; that handler must return before the worker can
  finish its current item.
- `DurableTriggerDispatcher` is a one-envelope adapter with the same session
  and ownership boundaries. It is not the long-running worker path, but
  `trigger_runtime.run_definition_inline()` uses it to run one definition
  synchronously for a waiting caller (see "Trigger Definitions" below).
- `RuntimeTrigger.definition_id` (nullable, `SET NULL` on delete) links a run
  to the saved definition it came from; ad-hoc API runs have none. Since
  JAN-257 the agent's `create_scheduled_trigger` also creates a definition
  (see "Janus follow-ups" below). SQLite does not enforce the `SET NULL`
  (no `PRAGMA foreign_keys`), so readers treat an id whose definition is gone
  as unlinked. `RuntimeTrigger.definition_name` (JAN-204) snapshots the
  definition's name when `enqueue_trigger` inserts the run, so a run keeps
  its provenance after its definition is deleted: the Agent Tasks page
  (`services/runtime_task_service.py`) still lists it as a definition run under
  that name, with no link, and its `source=definition` filter still matches
  it. `RuntimeTrigger.output` stores the
  model's raw final answer for a completed run, without the audit header the
  plan-state `result` carries.
- `ScheduledTriggerSpec` and `EventTriggerSpec` are deterministic ingress
  adapters for scheduler and provider integrations. A scheduled occurrence
  derives its idempotency key from `(schedule_id, due_at)`; an external event
  derives it from `(source, event_id)`. Re-delivery therefore produces the same
  trigger identity, while different providers may safely reuse an event ID.
  Both adapters require an owner, timezone-aware timestamps, and the narrow
  objective/task-mode/step-limit context. Their payload is still untrusted
  event data and cannot provide tool policy or authorization.
- A durable row preserves the envelope's occurrence timestamp as
  `created_at`. Only queued scheduled rows are claimable when that timestamp
  is not in the future, so a one-time scheduled occurrence submitted ahead of
  time stays queued until it is due. Manual and event rows remain immediately
  eligible even when a provider supplies an event timestamp ahead of the local
  clock.
- `PlanRuntimeTriggerHandler` is the guarded durable-trigger-to-plan-runtime
  adapter. Its narrow payload contract reads `objective` (required, a
  non-blank string), `task_mode` (optional, default `"default"`, otherwise a
  non-blank string), and `max_steps` (optional, default `100`, an integer;
  booleans are rejected). The adapter maps these fields, together with the
  envelope's `task_id`, into `TaskContext`; `TaskContext` also rejects a step
  limit below `1`. Payload data is untrusted, and malformed context fails
  closed before the runtime is called. Other payload fields are not treated as
  runtime policy or tool-selection input.
- `PlanRuntimeTriggerHandler`/`DurablePlanRuntimeTriggerHandler` themselves do
  not authorize tools; they only validate the trigger context and delegate
  advancement to `PlanDrivenRuntime.run()`/`resume()`. Tool authorization is a
  separate concern, injected as the runtime's `execute_step` callable -- see
  "Runtime Adapter and Tool Authorization" below for the production wiring
  (`trigger_runtime.build_runtime_trigger_handler`) that fills that seam.
- The current in-memory `TriggerDispatcher` remains a compatibility-only
  dispatcher. The durable worker can now be started by application startup,
  when `runtime_trigger_worker_enabled` is true (the default since JAN-199);
  startup registers the built-in runtime handler with
  `configure_runtime_trigger_handler()` and fails closed if the worker is
  enabled without a handler. The worker remains single-process; a separate schedule
  store and multi-process deployment are later reliability work.
- The worker's handler is injected and transport-neutral. The new adapter
  connects a claimed envelope to one in-memory `PlanDrivenRuntime.run()` call,
  while `DurablePlanRuntimeTriggerHandler` persists the resulting owner-scoped
  graph state and resumes a waiting cursor on a later trigger. A worker restart
  is safe for trigger claims; optimistic plan-state revisions prevent a stale
  concurrent worker from overwriting a newer cursor.

## Runtime Trigger Worker Startup

- `runtime_trigger_worker_enabled` defaults to `true` and is available as
  `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED` or the matching TOML setting. Setting
  it to `false` skips the trigger-runtime import and leaves accepted triggers
  `queued`.
- `startup.runtime_trigger_worker_status()` reports `enabled` (the setting)
  and `running` (worker started in this process and its supervised thread is
  alive). The authenticated `GET /api/runtime/triggers/worker` endpoint
  exposes it so the Settings → Triggers panel shows the real worker state
  instead of static copy.
- `runtime_trigger_max_attempts` defaults to `3`; after the final failed
  attempt the trigger becomes terminal `exhausted` and is not polled again.
  `runtime_trigger_retry_backoff_seconds` defaults to `0`; a positive value
  applies exponential delays (`base`, `2*base`, ...) between failed attempts.
  Both settings are available through the corresponding `JANUS_*` variables or
  TOML keys and apply only to the durable trigger worker.
- Startup registers the worker with the existing supervised worker manager
  under `runtime-triggers`; shutdown signals its cooperative stop event and
  prevents a supervisor restart during orderly application shutdown.
- An application integration must call `configure_runtime_trigger_handler()`
  before startup when enabling the worker. The factory is intentionally
  injected so the plan runtime, tool registry, and authorization policy remain
  application-owned rather than being guessed by process bootstrap.

## Trigger Definitions (JAN-228)

A `TriggerDefinition` (`trigger_definitions` table, migration `e6f9a3b4c5d7`)
is the reusable half of an automation; `trigger_definition_service.py` owns
validation, scheduling, event fan-out, and enqueueing.

- **Fields:** owner `user_id`, `name`, `objective` (the prompt), `tool_ids`
  (registry capability ids, validated against `DEFAULT_TOOL_REGISTRY`),
  `kind` (`manual` | `schedule` | `event`), `schedule`, IANA `timezone`
  (default `UTC`), `event_type`, `enabled`, `origin` (`user` | `system` |
  `agent`), `builtin_key` (unique per user; `None` unless Janus seeded it),
  optional `llm_provider`/`llm_model` (JAN-233; validated by
  `validate_model_choice()`, `NULL` = default backend), `next_run_at`,
  `last_run_at`.
- **Schedules:** `{"type": "daily", "time": "HH:MM", "days": [0-6]}` (0 =
  Monday; empty or omitted = every day), evaluated in the definition's
  timezone, or `{"type": "interval", "minutes": N}` with `5 ≤ N ≤ 10080`.
  `compute_next_run()` returns the first occurrence strictly after a
  reference time; `next_run_at` is recomputed on every create/update and is
  `None` while disabled or for non-schedule kinds.
- **Events (JAN-210):** `trigger_events.py` is the single event catalog: a
  `TriggerSubsystem` enum (`mail`, `whatsapp`, `planner`), a `TriggerEvent`
  enum whose values are `<subsystem>.<event>` (`mail.received`, see "Mail
  Ingestion" below; the WhatsApp and planner events, see "WhatsApp and planner
  trigger events" below), and per member a label, a description, and the
  payload fields its producer sends (`EventSpec.payload`). Producers publish
  catalog members, not strings (`processor.py` calls
  `emit_event(db, owner, TriggerEvent.MAIL_RECEIVED, ...)`);
  `emit_event()` raises `ValueError` for anything outside the catalog. The API
  schemas type `event_type` as `TriggerEvent`, so an unknown value is a `422`,
  and `normalize_definition_fields()` re-checks it via `parse_event()` for
  non-API callers and stores the plain string value.
  `GET /api/runtime/definitions/event-catalog` serves the catalog grouped by
  subsystem for the editor's dependent dropdowns. Only emitted events may be
  catalogued: adding one means adding the enum member, its `EventSpec`, and
  the producer's `emit_event()` call in the same change
  (`tests/test_mail_processing_tools.py` asserts the emitted payload matches
  the catalogued fields). There is no separate in-process event bus: the
  durable trigger queue is the bus. Before it enqueues, `emit_event()` asks
  `trigger_event_filters.admit()` per definition (JAN-363): the definition's
  `event_filter` (conditions `{field, op: in|not_in|contains, values}` on the
  event's catalogued payload fields, all must match, case-insensitive, matched
  on the raw payload before it is escaped) and its `max_runs_per_hour` (event
  runs of that definition with `created_at` in the last rolling hour). An
  event over the limit increments `rate_limited_count` and sets
  `last_rate_limited_at` (committed), so drops stay visible. A new definition
  (user or Janus draft) whose `max_runs_per_hour` is not given gets the
  event's `EventSpec.default_max_runs_per_hour` (10 for the two WhatsApp group
  events, otherwise none); an explicit `null` means no limit. Filter and limit
  are cleared on non-event definitions. `emit_event()`'s occurrence key is
  `event:<type>:<event_id>`, so re-emitting the same id never enqueues a
  second run. `emit_event()` runs in the producer's own
  (already background, e.g. IMAP) thread and only inserts one deduplicated
  `RuntimeTrigger` row per subscribed definition and event id; the polling
  `DurableTriggerWorker` executes them asynchronously, with retries and
  history. Definitions were validated against the same set since JAN-228, so
  no stored value needed a data migration.
- **Every activation is an ordinary durable run.** `enqueue_definition_run()`
  builds an envelope via `build_definition_envelope()` and calls
  `trigger_service.enqueue_trigger(..., definition_id=...)`, so claims,
  retries, `exhausted`, and history are exactly the durable trigger machinery
  above. The payload carries `objective` (plus any event context),
  `task_mode="automation"`, `max_steps=100`, and `definition_id`. Each
  occurrence gets its own task id (`trigger-def-<id>-<hash>`, derived from the
  definition id and an occurrence key) because a completed plan-state task id
  is never re-run; the occurrence key (`manual:<ts>`, `schedule:<ts>`,
  `event:<type>:<event id>`, `inline:<ts>`) is also the idempotency key, so a
  redelivered occurrence dedupes.
- **Scheduling is pull-based.** `run_due_schedules()` (the worker's scheduler
  hook) enqueues each enabled schedule definition whose `next_run_at` has
  passed and advances `next_run_at` from *now*, so after downtime only the
  most recent missed occurrence runs (a briefing missed for three days fires
  once).
- **Events are push-based.** `emit_event(db, owner_id, event_type, event_id,
  data)` enqueues one run per enabled definition of that owner subscribed to
  the event type. The event data is HTML-escaped and rendered into the
  objective as a delimited, explicitly untrusted `<event_data>` block (capped
  at 12,000 characters), so event text cannot close the block; it is also
  stored under `payload.event`.
- **Run now / inline:** `run_definition_now()` queues an immediate manual run
  of any kind (`POST /api/runtime/definitions/{id}/run`).
  `trigger_runtime.run_definition_inline()` runs one definition in the calling
  thread through `DurableTriggerDispatcher` — an atomically claimed durable
  row, so the background worker can never run it a second time, and it works
  even when the worker is disabled. The dashboard briefing, the
  `get_daily_briefing(force_refresh=True)` chat tool, and the `janus brief` /
  `janus digest` CLI commands use it.
- **Switching off and deleting stop pending runs (JAN-224).** When
  `update_definition` turns a definition off, it calls `cancel_pending_runs`
  (one conditional `UPDATE` over the `queued`, `waiting` and retry-pending
  `failed` runs) in the same transaction, so the worker never executes a run
  of a trigger the user switched off. `delete_definition` does the same before
  deleting, for every definition, so no run outlives its definition as an
  unlabelled ad-hoc trigger. A run the worker already claimed is left alone.
  `delete_definition` also sets `definition_id` to `NULL` on the definition's
  runs itself: SQLite does not enforce the column's `ON DELETE SET NULL`, and
  it reuses a freed id, so the next definition would otherwise inherit the
  deleted one's run history (and be resolved for its leftover runs).
  Switching a definition back on does not revive cancelled runs, and **Run
  now** still queues a run of a disabled definition.
- **Definition runs are recognized from the database, never the payload.**
  `definition_for_trigger()` resolves `RuntimeTrigger.definition_id`; only then
  does `build_runtime_trigger_handler` use the `automation` task mode (ad-hoc
  runs and Janus follow-ups keep the `trigger` mode, which also drops the withheld tool names).
- **Execution of a definition run** (`trigger_runtime._execute_authorized_step`):
  1. a built-in's `prepare` hook may append fresh context to the objective or
     return `skip_output`, which completes the run without an LLM call;
  2. the step is authorized with `allowed_tool_ids = definition.tool_ids ∪
     {"core"}` (minus any `LOCKED_UNATTENDED` entry, i.e. the chat-only `triggers.propose`, `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, and `character.propose`), with the
     owner's unattended switch as the confirmation gate (see "Unattended Tool
     Switch and Refusals"), and with the run's `trigger_id` passed through to
     the toolboxes (`ToolBuildContext.trigger_id`, JAN-274);
  3. the step runs on the definition's model choice
     (`ModelChoice(definition.llm_provider, definition.llm_model)`, passed to
     the executor through a context variable). An unusable explicit choice
     raises `ModelUnavailableError`, a terminal error: the run is `exhausted`
     with that message and never falls back to the default model;
  4. the raw answer is stored on `RuntimeTrigger.output` (the plan-state
     result shown in the run card starts with a `Model: …` line);
  5. a built-in's `finalize` hook delivers it. A `finalize` failure is logged
     as an `ERROR` event and does not undo the completed run.
- **Built-ins cannot be deleted** (`409`), only disabled or edited, and their
  `kind` cannot change. Editing a definition never changes its `origin`.

### Janus follow-ups as one-time definitions (JAN-257)

Every `create_scheduled_trigger` call that is not a dedupe hit creates, in the
same isolated session, a `TriggerDefinition` with `origin="agent"`,
`kind="schedule"`, `schedule={"type": "once", "at": <UTC ISO>}`, empty
`tool_ids`, and a name derived from the objective ("Follow-up: …"), then
enqueues the unchanged `ScheduledTriggerSpec` envelope with that
`definition_id` in the same insert. Product decision (2026-09-29): one-shots
run **without approval**, exactly as before, and keep `proposal_status = NULL`;
JAN-235's approval flow (below) governs every other trigger definition.

- `trigger_definition_service.is_agent_follow_up()` identifies them. The
  runtime treats them as ad-hoc for execution: `trigger` task mode (the
  owner's Unattended switch minus the trigger-withheld tools, with
  `allowed_tool_ids=None`), no definition passed to the confirmation gate, and
  the default model. The definition is for visibility only. A follow-up run
  may itself schedule follow-ups, within the chain guard below.
- `once` is accepted only for `origin="agent"` (`validate_schedule(...,
  allow_once=True)`); the API's `TriggerSchedule` schema does not offer it.
  `compute_next_run` returns `None`, so `run_due_schedules` never enqueues
  them: their single run is queued at creation.
- `update_definition` accepts only `enabled=false`, which cancels the run
  (`cancel_pending_runs`, a conditional `UPDATE` over queued/retrying rows);
  content edits and re-enabling raise `TriggerDefinitionError` (422).
  `run_definition_now` refuses them (409). `delete_definition` cancels the
  queued run before deleting. `cancel_trigger` disables the definition.
- The backlog cap, horizon, and re-ask dedupe are unchanged; a dedupe hit
  returns the existing run and creates no second definition.

### Unattended self-scheduling chain guard (JAN-274)

`triggers` is available to unattended runs (`trigger` and `automation`
modes; never `delegated`, whose children get read-only tools only). It is a
normal entry on the Unattended switch, *Allowed* by default
(`confirmation="recommended"`) and switchable per user. What keeps a run that
schedules runs from looping is enforced in the toolbox
(`agent_toolboxes/triggers.py`), not in the prompt:

- **Lineage.** `RuntimeTrigger.parent_trigger_id` and `chain_depth` (migration
  `a3e5c7d9f1b2`). `trigger_runtime` passes the executing run's `trigger_id`
  through `execute_unattended_step_detailed(trigger_id=)` →
  `RuntimeAdapter.prepare_step(trigger_id=)` → `ToolBuildContext.trigger_id`,
  and the registry builder hands it and the step's `task_mode` to
  `build_trigger_tools`. A follow-up scheduled in an unattended mode is
  enqueued (`trigger_service.enqueue_trigger(parent_trigger_id=,
  chain_depth=)`) as a child of that run, one level deeper. Roots -- chat
  follow-ups, API triggers, definition schedules and events, and every row
  that existed before the migration -- have depth 0 and no parent.
- **Refusals** (a `ValueError`/`PermissionError` from the tool, which the
  unattended guard reports to the model as `ERROR: …` and the run's result
  lists under "Failed tool calls"):
  - depth: a follow-up deeper than `MAX_UNATTENDED_CHAIN_DEPTH` (2);
  - fan-out: a run's fourth follow-up (`MAX_FOLLOW_UPS_PER_RUN` = 3,
    cancelled ones included, so cancel-and-reschedule does not reset it);
  - rate: the owner's eleventh follow-up scheduled by unattended runs within
    a rolling day (`MAX_UNATTENDED_FOLLOW_UPS_PER_DAY` = 10,
    `trigger_service.count_unattended_follow_ups_since`; when a follow-up was
    scheduled is read from its one-time definition's `created_at`, because a
    scheduled row's own `created_at` holds its due time). Chat follow-ups do
    not use this budget;
  - delay: a follow-up due sooner than `MIN_UNATTENDED_FOLLOW_UP_DELAY`
    (15 minutes);
  - no parent: an unattended step without a `trigger_id`, or whose
    `trigger_id` is not one of the owner's runs, schedules nothing (fail
    closed).
- **No recurring schedules.** The toolbox only creates one-time follow-ups;
  it cannot create or edit a recurring definition (the chat-only proposal
  flow, JAN-235, owns that), so nothing an unattended run schedules outlives
  its chain.
- **Cancel scope.** In an unattended mode `cancel_trigger` only cancels
  Janus's own follow-ups (`source == "agent-tool"`); a run of a user-saved
  definition or an API/event trigger answers a `PermissionError`. Chat keeps
  the full owner-scoped cancel.
- The per-owner backlog cap (`MAX_ACTIVE_TRIGGERS_PER_OWNER`, 25 claimable
  rows) still applies to every surface on top of these limits.

Default-on but switchable, rather than always on: the guard makes the tool
safe, and the switch keeps the user's per-tool control consistent with every
other `confirmation="recommended"` toolbox.

### Janus-started conversations (JAN-319)

An unattended run can open a **new chat conversation** for its owner whose
first message is Janus's, so something that needs the user's attention or an
answer lands where the user talks to Janus instead of only in a run result.

- **Tool and entry.** `start_conversation(title, message)` in
  `agent_toolboxes/conversations.py`, registry entry `conversations.start`
  ("Start conversations": `operation="write"`, `risk_level="low"`,
  `confirmation="recommended"`, no required capability, `docs_ref`
  `tools.md#start-conversations`). `enabled_when` admits only the `trigger`
  and `automation` task modes, and the entry is in no
  `TASK_MODE_ALLOWED_TOOL_IDS` list, so chat (Janus is already talking to the
  user), `briefing`/`digest`/`ingestion`, and `delegated` children (read-only
  tools only; the entry is `write`) never get it.
- **Policy.** Not `confirmation="required"`: it writes nothing outside the
  owner's own Janus and is bounded below, so like `triggers` and `memory` it
  is a normal Unattended-switch entry, *Allowed* by default and switchable
  per user. Switched *Off*, it is offered and answers `BLOCKED`. Ad-hoc
  triggers and Janus follow-ups (JAN-257, `trigger` mode with the owner's
  allowed tools) get it while the switch allows it; a saved definition gets
  it only when its `tool_ids` list it (a user-authored definition that lists
  it overrides an *Off* switch, as for any tool). Not in
  `LOCKED_UNATTENDED`: the whole point is unattended use.
- **Owner scope.** The conversation's `user_id` is the run's user
  (`ToolContext.user`), never a model argument. The tool takes no
  conversation id, so it can only create a new conversation for that owner,
  never append to an existing one or to another user's.
- **Run identity and limits** (`services/conversation_service.py`, checked
  before anything is written; a refusal is a `ValueError`/`PermissionError`
  the unattended guard reports as `ERROR: …` under "Failed tool calls"):
  - fail closed without a durable run: no `ToolBuildContext.trigger_id`, or one
    that is not the owner's `RuntimeTrigger`, starts nothing;
  - `MAX_JANUS_CONVERSATIONS_PER_RUN` = 1, counted by
    `conversations.origin_trigger_id`;
  - `MAX_JANUS_CONVERSATIONS_PER_DAY` = 5 per owner in a rolling day, counted
    over `origin = "janus"` rows by `created_at`. Deleting such a
    conversation frees its slot. This is the cap that also bounds a chain:
    each run of a follow-up chain (JAN-274, at most two levels and three
    follow-ups per run) may start one conversation, all from the same daily
    budget. The tool schedules nothing itself, so the chain guard is
    unchanged. Concurrent starts for one owner are serialized: the service
    first takes `SELECT ... FOR UPDATE` on the owner's `users` row
    (`owner_lock_statement`; a real row lock on PostgreSQL, omitted by
    SQLAlchemy on SQLite), and both limits are counted again after the new
    row is flushed, inside the write transaction, with the insert rolled
    back if either is now exceeded. On SQLite that flush takes the
    database's single write lock, held through the recount and the commit,
    so a second transaction's recount always sees the first one's row;
    two runs that pass the first check at the same time cannot both stay
    over a limit;
  - title 1-120 characters (whitespace collapsed to one line), message
    1-4000 characters.
- **No exfiltration.** The text is stored only in the owner's database and
  shown only to the owner. Because the chat renders assistant messages as
  Markdown, a remote image would be fetched the moment the conversation is
  opened (a zero-click channel for whatever the run read) and a link could
  carry the same data on a click. So the title and message are refused when
  they contain a URL scheme (`https:`, `data:`, `mailto:`, any `scheme://`),
  a protocol-relative `//host` link, a `www.` host, or Markdown image syntax
  (`![`). That pattern runs on the raw text and again on its canonical form,
  with HTML entities (`&#58;`, `&colon;`, repeatedly, so double encoding
  too) and CommonMark backslash escapes (`https\://`) resolved, so an
  escape a Markdown renderer would undo cannot hide a scheme. In addition,
  the text is parsed with a CommonMark parser (`markdown-it-py`), and every
  resolved link destination -- inline, reference-style, or autolink -- must
  be an app path (`/…`, never `//host` or `/\host`, checked after
  percent-decoding and dropping the tabs and newlines a URL parser ignores)
  or an in-page `#anchor`; any image is refused. App paths
  (`/settings?tab=triggers`) stay allowed.
- **Prompt injection.** `start_conversation` is in
  `INJECTION_ESCALATED_TOOL_NAMES`, and the tool checks the turn trace
  itself: once something read in the run looked like an injection, it answers
  `BLOCKED` and writes nothing, so injected text cannot be planted in the
  user's chat history. The opening message is stored as an `assistant`
  message, and a later chat turn in that conversation runs under chat's
  normal rules (approval card for `confirmation="required"` tools).
- **Schema** (migration `d7f9b1c3e5a7`, additive): `conversations.origin`
  (`user`/`janus`, `models.ConversationOrigin`, server default `user`),
  `conversations.origin_trigger_id` (nullable, indexed), and
  `conversations.unread` (boolean, server default false). Only
  `start_janus_conversation` sets `origin = "janus"` and `unread = true`.
- **Surfacing.** `GET /api/conversations/` returns `origin` and `unread`;
  `GET /api/conversations/{id}` clears `unread` when the owner opens it.
  `Conversation` is tracked in `domain_changes` as the `conversations`
  domain, so the commit refreshes the list and badge in every open tab over
  `/ws/changes`. The web chat marks such a conversation **Started by Janus**
  and **New** while unread, the shared navigation's Chat entry carries the
  unread count, and a non-server install also gets a desktop notification
  (`NotificationService.notify_conversation`). Every create is logged
  (`JanusConversations`, owner-scoped event).

### Agent-proposed definitions (JAN-235)

Apart from the one-time follow-ups above, every `origin="agent"` definition is
one Janus drafted from chat under "Janus proposes, the user approves". Full
design and threat model: [Agent-proposed automations](../feature-specs/agent-proposed-automations.md).

- **Columns** (migration `b7d2e9f4a1c3`, all nullable, index on
  `(user_id, proposal_status)`): `proposal_status`
  (`models.TriggerProposalStatus`: `proposed` / `approved` / `rejected` /
  `withdrawn` / `superseded`), `proposal_rationale`, `proposed_at`,
  `proposal_source` (`{"conversation_id", "untrusted_context": [...], "injection_suspected"}`),
  `supersedes_id` (FK to `trigger_definitions`, `ON DELETE SET NULL`),
  `approved_at`, `approved_by_user_id` (FK to `users`), `approval_fingerprint`
  (`String(64)`), `rejected_at`, `rejection_reason`. `NULL` status on every
  user, system, and follow-up row.
- **`trigger_approval.py`** (models-only, read by the definition service,
  `tool_policy`, and `trigger_runtime`): `approval_fingerprint()` is a SHA-256
  over the canonical JSON of `objective`, sorted `tool_ids`, `kind`,
  `schedule`, `timezone`, `event_type`, `llm_provider`, `llm_model`;
  `is_runnable()` / `runnable_filter()` accept `NULL` or `approved`;
  `has_valid_approval()` requires `origin="agent"`, `approved`,
  `approved_by_user_id == user_id`, `approved_at`, and a matching fingerprint.
- **Inert until approved.** Drafts are stored `enabled=false`.
  `update_definition`, `run_definition_now`, and `delete_definition` raise
  `TriggerDefinitionConflict` (`409`) for any non-`approved` proposal (a
  rejected row is kept so it keeps suppressing re-proposals);
  `list_owned_definitions`, `run_due_schedules`, and `emit_event` filter with
  `runnable_filter()`; `trigger_runtime` fails a run whose definition is not
  runnable with a terminal error.
- **Confirmation.** `tool_policy.unattended_tool_sets()` confirms an agent
  definition's `tool_ids` only when `has_valid_approval()` holds; an `approved`
  row whose fingerprint does not match logs `trigger_proposal.fingerprint_mismatch`
  (`WARNING`) and gets plain policy. An owner edit through `PATCH`
  (`update_definition(..., actor_user_id=owner)`) re-records the fingerprint,
  `approved_at`, and `approved_by_user_id`; any other caller changing the
  content leaves the stale approval in place.
- **`trigger_proposal_service.py`.** Agent side (`create_proposal`,
  `revise_proposal`, `withdraw_proposal`, `list_proposals`) never writes an
  approval field. User side (`approve_proposal`, `reject_proposal`) is called
  only by the API. Limits, counted per owner from the database: 5 open, 3 per
  rolling 24 hours, 4,000-character objective, 60-minute interval floor, no
  `once` schedules, tools only from `PROPOSABLE_TOOL_IDS` (`core`,
  `trigger-history`, `mail`, `mail.label`, `calendar`, `jira`,
  `planner.local`, `dashboard`, `memory`, `topics`), and a dedupe on the
  fingerprint against open and approved proposals, the owner's own
  definitions, and rejections from the last 30 days (the refusal quotes the
  rejection reason). Limit refusals raise `ProposalLimitError` and log
  `trigger_proposal.limit_refused`. Approval validates the user's edits with
  the ordinary rules (any registry tool, 5-minute interval floor).
- **Supersede.** `supersedes_id` must name one of the owner's `approved` agent
  definitions. A new draft for the same target withdraws the older open one.
  Approving the replacement marks the target `superseded`, disables it, and
  cancels its pending runs in the same transaction.
- **Toolbox** `triggers.propose` (`agent_toolboxes/automation_proposals.py`,
  `confirmation="recommended"`): `propose_trigger_definition`,
  `revise_proposal`, `list_my_proposals`, `withdraw_proposal`, one creation per
  chat turn (a per-toolset counter). It is only in
  `TASK_MODE_ALLOWED_TOOL_IDS["chat"]` and is in
  `tool_policy.LOCKED_UNATTENDED`, so no `trigger`, `automation`, or delegated
  run is offered it.
- **Provenance from the shared turn trace.** The proposal tools read the
  step's `TurnTrace` (JAN-127, `ToolContext.turn_trace`): `untrusted_tool_ids()`
  (successful calls of entries flagged `reads_untrusted_content`) fills
  `proposal_source.untrusted_context`, and `injection_suspected()` sets
  `proposal_source.injection_suspected`. Neither blocks the draft (resolved
  question 6): `triggers.propose` is deliberately not in
  `runtime_adapter.INJECTION_ESCALATED_TOOL_NAMES`, because a draft is inert
  until the user approves it; instead the review card shows a prompt-injection
  warning. `create_scheduled_trigger`, whose follow-ups run without approval,
  stays escalated.
- **API** (`api/trigger_definitions.py`): `GET .../proposals?status=` (with
  `blocked_tool_ids`, `untrusted_context`, `next_occurrence`, `supersedes`
  from `trigger_proposal_service.review_data`), `GET .../proposals/count`,
  `POST .../{id}/approve` (optional edits plus `enable`, `409` unless
  `proposed`), `POST .../{id}/reject` (optional `reason`). Non-proposals answer
  `404` there.
- **Audit.** `trigger_proposal.created`, `.revised`, `.withdrawn`, `.approved`,
  `.rejected`, `.superseded`, `.limit_refused`, and `.fingerprint_mismatch`
  go through `event_logger.log` with definition id, owner, actor, and old/new
  status.


### Proactive secretary

The **Secretary** (`builtin.secretary`) is the trigger that thinks ahead
without being asked. It adds no runtime: it is an ordinary schedule definition
(hourly interval, `origin="system"`, enabled after seeding) whose `prepare`
hook decides in code whether the hour costs a model call. Product behavior and
rationale: [`feature-specs/proactive-secretary.md`](../feature-specs/proactive-secretary.md).

- **Hook** (`services/secretary_service.py`, called from
  `builtin_triggers._prepare_secretary`). `prepare` returns a `SecretaryPlan`:
  1. `skip_output` outside the owner's waking hours (`NotificationPreference`
     `secretary_active_start_hour`/`secretary_active_end_hour`, default 8 to
     21, on the definition's timezone clock; `adopt_client_timezone` moves an
     unedited secretary off UTC together with the briefing);
  2. `skip_output` when the newest run that actually thought (skipped runs complete too and are
     looked past, recognised by their skip-note output) is younger than
     `REFRESH_AFTER` (6 h) and `changes_since` finds nothing new or changed
     since it: mail on the owner's accounts (`received_at`), incoming WhatsApp
     messages, reminders (owned through their source mail), planner events, **and**
     a content fingerprint of the synced calendar events (from today on), synced
     tasks, and Janus's local tasks (`planner_fingerprint`; the sync upserts in
     place and `CalendarEvent` has no change timestamp, so only content can say
     a meeting appeared or moved). The fingerprint of the last run that thought
     is stored on `NotificationPreference.secretary_planner_fingerprint` by the
     built-in's `finalize` hook, never by a skipped run; no stored value counts
     as changed (the first run after an upgrade thinks once);
  3. otherwise `context`: the clock and window, the change counts, the last
     `HISTORY_RUNS` (4) findings (runs that ended `NOTHING_TO_REPORT` are left
     out), and the outcome of each Janus-started conversation from this
     definition's runs in the last 14 days (`replied`, `opened, no reply`, or
     `ignored (still unread)`, derived from `Conversation.unread` and the
     owner's `ConversationMessage`s). Both blocks are wrapped with
     `prompt_injection.prepared_context_block`: past outputs and titles were
     written from outside content.
- **Seeded tools.** `core`, `calendar`, `jira`, `planner.local`, `topics`,
  `memory`, `mail`, `whatsapp`, `knowledge`, `wiki`, `web_search`, `triggers`
  (self follow-ups under the JAN-274 chain guard), `conversations.start`, and
  `whatsapp.notify_me`. No `mail.send`, `whatsapp.send`, `shell`,
  `web_fetch`, or `delegation`; the usual Unattended switch and
  `LOCKED_UNATTENDED` fences apply unchanged.
- **Quiet by default.** The objective tells the run to finish with exactly
  `NOTHING_TO_REPORT` and call no delivery tool when nothing deserves
  attention. Delivery goes through `start_conversation` (JAN-319 caps:
  one per run, five per owner per day) and, for time-critical items,
  `notify_me_on_whatsapp`.
- **`notify_me_on_whatsapp`** (`agent_toolboxes/whatsapp.py`, registry entry
  `whatsapp.notify_me`: `operation="write"`, `risk_level="low"`,
  `confirmation="recommended"`, required capability `whatsapp`, `docs_ref`
  `tools.md#notify-me-on-whatsapp`). The only argument is `message`; the
  recipient is `NotificationPreference.secretary_whatsapp_number` (stored as
  `+digits`, validated by `phone_to_jid`), sent from the owner's lowest-id
  enabled account over `WhatsAppService.send_text`, so it cannot reach anyone
  else and needs no per-call confirmation. Fences, all in code:
  - `enabled_when`: `trigger`/`automation` task modes only (never chat or
    `delegated`), a connected WhatsApp account, and the owner's
    `secretary_whatsapp_enabled` with a number set, so a disabled channel is
    not even offered;
  - fail closed without a durable run (`ToolBuildContext.trigger_id`);
  - at most `MAX_WHATSAPP_ALERTS_PER_RUN` (1) per run and
    `MAX_WHATSAPP_ALERTS_PER_DAY` (3) per owner in a rolling day, counted on
    outgoing `whatsapp_messages.origin_trigger_id` (set after the send);
  - at most 600 characters, and the JAN-319 no-URL/link/image check
    (`conversation_service.refuse_external_references`);
  - in `runtime_adapter.INJECTION_ESCALATED_TOOL_NAMES`, and the tool checks
    the turn trace itself: after a suspected injection it answers `BLOCKED`;
  - a missing connection or send failure is raised as an error that tells the
    run to fall back to `start_conversation`; nothing is stored then.
- **Settings.** `GET/PATCH /api/emails/notifications/preferences` carry
  `secretary_active_start_hour` (0-23), `secretary_active_end_hour` (1-24),
  `secretary_whatsapp_enabled`, and `secretary_whatsapp_number` (the fingerprint column is not
  an API field). A PATCH is
  `422` for an end that is not after the start or a number that is not
  international. Migration `c9e3a5b7d1f2` adds the four columns and
  `whatsapp_messages.origin_trigger_id`.
- **Known limits.** Unchanged inputs are re-checked only every six hours, so
  something that becomes urgent purely by the clock (an appointment now 20
  hours away) can be noticed up to six hours late unless a run scheduled a
  follow-up. The waking-hours window uses the definition's timezone, which is
  UTC until a browser reports one.

## Built-in Triggers (JAN-231, JAN-232)

`builtin_triggers.py` seeds its definitions per user with `origin="system"`.
`ensure_builtin_definitions()` runs on every scheduler tick (for all users),
when a user lists definitions, and before the dashboard/CLI briefing and
digest; it only creates *missing* built-ins and never overwrites edits. Seeded
tool ids are filtered to ids the registry knows.

| Key | Name | Activation (seed) | Seeded tools | Hooks |
| --- | --- | --- | --- | --- |
| `builtin.briefing` | Morning briefing | daily 06:30, enabled; seeded in UTC, then moved once to the browser's timezone (see below) | `core`, `calendar`, `jira`, `planner.local`, `topics`, `memory`, `mail` | `prepare`: today's planner summary (`DashboardService.get_briefing_generation_context`) plus the dashboard briefing widget's prompt override; `finalize`: marks the surfaced topics briefed |
| `builtin.digest` | Afternoon digest | daily (UTC), time and enabled flag seeded from the user's `NotificationPreference.daily_digest_hour/minute`/`daily_digest_enabled` and re-synced whenever those fields change through `PATCH /api/emails/notifications/preferences`; 16:00 UTC and **disabled** when the user has no notification preference row | `core`, `mail`, `topics`, `memory`, `planner.local`, `mail.processing` | `prepare`: `DIGEST` emails received since `max(last_daily_digest_at, now − digest_lookback_hours)` filtered by the notification filters and then capped at the newest 50 eligible ones (filtering first, so filtered-out mail cannot crowd eligible mail out before `last_daily_digest_at` advances), rendered as untrusted email content with topic threads — `skip_output` when there are none; `finalize`: sets `last_daily_digest_at`, marks topics digested, sends the desktop digest notification when `notify_digest` is on |
| `builtin.mail` | Mail processing | event `mail.received`, enabled | `core`, `mail`, `mail.processing`, `rules`, `memory`, `topics`, `planner.local`, `calendar` | none |
| `builtin.secretary` | Secretary | hourly interval, enabled; seeded in UTC, then moved once to the browser's timezone | `core`, `calendar`, `jira`, `planner.local`, `topics`, `memory`, `mail`, `whatsapp`, `knowledge`, `wiki`, `web_search`, `triggers`, `conversations.start`, `whatsapp.notify_me` | `prepare`: waking-hours gate, change gate, own history and conversation outcomes (see [Proactive secretary](#proactive-secretary)); `finalize`: remembers the planner fingerprint after a run that thought |
| `builtin.wiki` | Wiki synthesis (JAN-244) | hourly, **disabled** (opt-in) | none | `prepare`: `wiki_synthesis.run_synthesis` creates missing pages (active or pinned topics, entities with at least 5 mentions), removes pages whose subject is gone (user-edited ones are kept, unlinked), checks up to 200 pages (least recently checked first), clears Janus-written text from pages whose sources are all gone, and rewrites up to 5 stale pages (inputs hash changed; inputs are the passages plus up to 5 active memory facts about the subject, numbered after the passages and cited with `fact_id`, JAN-334) with direct model calls within the monthly budget shared with extraction; a page the user edited gets a pending proposal instead; always `skip_output` |
| `builtin.knowledge` | Knowledge extraction (JAN-243) | every 30 minutes, **disabled** (opt-in) | none | `prepare`: `knowledge_extraction.run_extraction` makes up to 6 direct JSON calls (8 pending chunks each, newest sources first) to the definition's model or the utility model, within the user's monthly token budget, writes entities/mentions/relations through `knowledge_graph`, and always returns `skip_output` (no agent step, no tools) |

- **Briefing timezone:** built-ins are usually seeded by the worker before
  Janus knows where the user is. `adopt_client_timezone()` moves a
  still-unedited, UTC-seeded briefing to the browser's IANA timezone the first
  time one is reported (`/ws/briefing?timezone=...` or
  `GET /api/runtime/definitions/?timezone=...`); once the user has edited the
  definition it is never changed behind their back. The digest and mail
  built-ins keep UTC.
- **Dashboard briefing:** `/ws/briefing` sends the newest *completed* briefing
  run from today (today in the definition's timezone, via
  `builtin_triggers.todays_briefing`), or runs the definition inline when
  there is none yet or the client passes `?refresh=1` (the dashboard refresh
  button). The text is sent in one message; it is no longer token-streamed.
- **Removed with this cutover:** the email-callback digest scheduler
  (`_maybe_send_scheduled_digest`), the generated-briefing cache
  (`briefing_cache_entries`, dropped by migration `f7a0b4c5d6e8`), and
  `LLMService.generate_briefing` / `stream_briefing` / `generate_digest`.
  After seeding, the notification preference's digest hour/enabled fields no
  longer schedule anything; the digest's schedule lives on its definition.
- **Self-evolution self-checks (JAN-321)** are not seeded: each is a one-time
  agent-origin definition with `builtin_key = "selfevo.check:<action id>"`,
  which `builtin_for()` maps to a `prepare` hook running
  `self_evolution_checks.run_self_check` and returning `skip_output` (no model
  call). See the next section.

## Autonomous Self-Evolution Loop (JAN-320)

An opt-in loop in which Janus develops itself with **no human approval step**
(owner decision): file a Linear issue -> fire a Claude Code Routine -> merge
the feature PR -> merge the release-please PR -> self-check production. This
departs from "Janus proposes, the user approves" (JAN-235) for this loop only;
every other fence in this document is unchanged. Safety is enforced in code by
the services, never by the prompt. Feature spec:
[Autonomous Self-Evolution Loop](../feature-specs/autonomous-self-evolution-loop.md).

- **Modules.** `services/self_evolution_service.py` (settings, switch, daily
  caps with row reservation, the `SCOPE_FENCE` list, the audit trail,
  GitHub/Linear/HTTP seams), `services/self_evolution_intake.py` (JAN-324
  `file_issue` with open-issue title dedupe; JAN-323 `start_job`),
  `services/self_evolution_merge.py` (JAN-322 merge gates, release ordering,
  GraphQL revert PRs), `services/self_evolution_checks.py` (JAN-321
  scheduling, running and failure handling), the `self_evolution` toolbox
  (`agent_toolboxes/self_evolution.py`), `api/self_evolution.py`, and the
  `models_self_evolution.py` tables `self_evolution_settings` /
  `self_evolution_actions`.
- **Switch.** Per user, default off; only an admin's switch counts. It gates
  three layers: every service entry point (`require_enabled`), the
  `self_evolution` capability (`capability_profile`, also needs `github` and
  `linear`), and the unattended allowance (`tool_policy.SWITCH_GOVERNED`: the
  entry is allowed unattended exactly while the switch is on; `set_policy`
  refuses it like a locked entry; a definition naming it never overrides an off
  switch). The entry is `confirmation="required"`, `operation="write"`,
  `reads_untrusted_content=True`: chat asks per call, delegated children never
  get it, and a suspected prompt injection blocks it for the rest of the step.
  It is not in `trigger_proposal_service.PROPOSABLE_TOOL_IDS`.
- **Caps.** `DAILY_CAPS` per rolling 24 h, counted from
  `self_evolution_actions` (refused/duplicate rows excluded, failed included).
  An attempt inserts a `pending` row first and is refused when its rank among
  the window's counted rows (by id) exceeds the cap.
- **Merge gates** (`check_gates`, run right before each merge; the merge call
  pins the checked head SHA): open, not draft, same-repository branch, base =
  default branch, `mergeable` true and `mergeable_state` `clean`/`has_hooks`,
  every check run completed with `success`/`skipped`/`neutral` and every
  commit status `success`, at least one check (feature PRs), every changed file
  listable (< 3000) and none (old or new name) in `SCOPE_FENCE` or naming the
  loop itself. Release PRs additionally: `release-please--` branch, only
  `CHANGELOG.md`/`.release-please-manifest.json`/`pyproject.toml`/`uv.lock`,
  a recorded `feature_merged` row for the named feature, and the release head
  must contain the feature's merge commit (`compare` `ahead`/`identical`);
  release PRs may have no checks at all, because release-please opens them with
  `GITHUB_TOKEN`, which starts no workflow runs.
- **Merge scope** (`self_evolution_provenance.provenance`, after the gates):
  `merge_feature_pr` merges only (a) a PR whose description names, word-bounded,
  the session id or URL of a `job_started` row of the same user from the last
  14 days, launched for the same repository; (b) a Dependabot minor/patch bump: opened by `dependabot[bot]` from
  a `dependabot/` branch, one commit authored by `dependabot[bot]`, and every
  `updated-dependencies:` entry of its message has `update-type`
  `version-update:semver-patch`/`semver-minor` (missing/unparseable metadata
  or any major refuses; Dependabot's weekly `weekly-dependencies` uv/npm
  group PR is one such commit, so a week containing any major bump is
  refused as a whole; GitHub Actions bumps get their own group PR, which the
  `.github/` fence refuses) -- a
  deliberate, owner-requested departure from the "combine Dependabot PRs"
  convention, for the loop only; (c) a revert PR the
  loop opened (`revert_opened` row). All count against the feature-merge cap.
- **Routine trigger.** `POST
  https://api.anthropic.com/v1/claude_code/routines/{trig_id}/fire`, the URL
  built from a constant and the validated `trig_...` id (settings hold no
  URL), headers `Authorization: Bearer <sk-ant-oat01- routine token>`,
  `anthropic-version: 2023-06-01`, `anthropic-beta:
  experimental-cc-routine-2026-04-01`, body `{"text": ...}` capped at 65,536
  characters. The text starts with fixed guardrails (one PR against the default
  branch whose description names the Linear issue and the session URL, never
  merge, the fence). `200` returns `claude_code_session_id`/`_url`, recorded
  on the row (a `200` without a valid id and URL is a failed start that does
  not block a retry); error answers (`{"type": "error", "error": {...}}`, 400-503) are
  recorded with type, message and `Retry-After`, never the token. There is no
  idempotency key, so a fire is never retried.
- **Self-check.** `schedule_self_check` (only for a release the loop merged,
  once per release, 10 min-24 h ahead, backlog cap respected) stores 1-5 HTTP
  checks on a `self_check` row and queues the run with `create_definition`
  (agent origin, one-time, `builtin_key` above) + `ScheduledTriggerSpec` +
  `enqueue_trigger`, like `create_scheduled_trigger`. The run GETs only paths
  on the configured production base URL, without redirects, and is
  idempotent on retries. On failure `handle_failure` sends
  `NotificationService.notify_alert` (desktop; skipped in server mode), the
  row's `ERROR` event, a `system_error` issue via `file_issue`, and a revert PR
  via GraphQL `revertPullRequest` (merged later only through
  `merge_feature_pr`). Issue and revert are skipped if the switch was turned
  off meanwhile.
- **Audit.** Every attempt, refusal and outcome is an action row and an
  owner-scoped event (`source="SelfEvolution"`); `GET
  /api/self-evolution/settings` returns caps, usage, the fence and recent
  actions.

## Runtime Adapter and Tool Authorization

- `runtime_adapter.RuntimeAdapter` (JAN-157/JAN-162) is the single production
  entrypoint that turns an inbound task/message into a `TaskContext`/`PlanStep`
  pair and an authorized, per-message toolset. It wraps the tool registry's
  `authorize()`/`build_authorized`, so every call site gets the same stable
  denial reasons (`unknown_tool`, `not_allowlisted`, `disabled_by_context`,
  `missing_capability`, `confirmation_required`) and the same pre-execution
  confirmation gate.
- The non-model policy input bounds which registry entries a step may even
  request, independent of what an LLM asks for. For the supervised and
  system-pipeline modes (`chat`, `briefing`, `digest`, `ingestion`) it is the
  fixed `TASK_MODE_ALLOWED_TOOL_IDS` entry. The unattended modes (`trigger`,
  `automation`, `delegated`) have no fixed list: `unattended_execution` passes
  the set computed by `tool_policy.unattended_tool_sets` as an explicit
  `allowed_tool_ids` (see "Unattended Tool Switch and Refusals").
  `runtime_adapter.KNOWN_TASK_MODES` is the union of both, for callers that
  validate a mode name (`create_scheduled_trigger`). The second,
  orthogonal gate is `ToolRegistryEntry.enabled_when`, which reads real state
  only: connected integrations (`capability_profile.user_connected_capabilities`
  — an active mail account enables `mail`, `mail.label`, `mail.send`,
  `mail.processing`, `rules`, `rules.write`; an enabled WhatsApp account
  enables `whatsapp`, JAN-347), admin status (`skills`; `shell`
  also needs the operator setting `shell_tool_enabled`, JAN-353; `coding_agents`
  needs `coding_agents_enabled` and an installed agent CLI, JAN-403), and
  plan-step identity (`delegation`, `web_search`, `web_fetch`, `shell`, `coding_agents` need a `task_id`), and task mode (`conversations.start` only in `trigger`/`automation`, JAN-319). `core`,
  `events`, `trigger-history`, `memory`, `topics`, `planner.local`,
  `planner.local.write`, `triggers`, and `docs` are ungated; `core`, `memory`, `topics`, `planner.local`,
  `triggers`, `skills`, and `mcp` are in `ALWAYS_GRANTED_CAPABILITIES`.
- **Remote MCP tools in chat (JAN-343).** Chat builds its `RuntimeAdapter`
  per message on `agent_tools.build_managed_registry(db, user)`, which is
  `DEFAULT_TOOL_REGISTRY` plus the user's offered remote tools (enabled tool,
  enabled server, last discovery `ready`; `agent_toolboxes/remote_mcp.py`).
  It passes `TASK_MODE_ALLOWED_TOOL_IDS["chat"]` plus those `mcp.*` ids as
  `allowed_tool_ids`, both to toolset selection and to `prepare_step`.
  - Remote entries are `confirmation="never"` only when the server marked the
    tool `readOnlyHint: true`, otherwise `required` (approval card, injection
    escalation).
  - All of them are `reads_untrusted_content` and are enabled only in `chat`
    steps.
  - Every unattended path keeps `DEFAULT_TOOL_REGISTRY`, and
    `tool_policy.locked_reason` locks `mcp.*` ids for unattended runs, so
    triggers, automations, and delegated children never get them (JAN-344),
    except under the owner's *Unattended: Allow everything* mode (JAN-359):
    `unattended_execution` then builds `build_managed_registry`,
    `tool_policy.unattended_locked_reason` lifts the lock, and
    `prepare_step(unattended_allow_all=True)` sets
    `ToolBuildContext.unattended_allow_all`, the only flag that lets a remote
    entry's `enabled_when` pass outside `chat`. A delegated child still never
    gets them (`delegable_tool_ids` keeps the fixed locks).
- **WhatsApp (JAN-347).** `whatsapp` (`agent_toolboxes/whatsapp.py`,
  `WHATSAPP_ENTRY`) is a read-only, `confirmation="never"`,
  `reads_untrusted_content` entry with four tools:
  - `list_whatsapp_accounts`
  - `list_whatsapp_conversations`: search on title or participants, unread
    only, since, account
  - `read_whatsapp_messages`: one conversation, since/until, newest first
  - `search_whatsapp_messages`: body, snippet, or sender; optional
    conversation and since

  - **Scoping.** Every query filters on the step user's id and on that
    user's *enabled* accounts. A conversation that is foreign or belongs to
    a disabled account is "not found"; `list_whatsapp_accounts` still lists
    a disabled account, marked disabled.
  - **Limits.** Results are capped at 100 rows (`limit <= 0` returns
    nothing, like `search_emails`), and every text field at 4,000
    characters.
  - **Search.** It escapes `%` and `_`.
  - **Policy.** The entry is allowlisted for `chat` only, like `mail`
    (briefing and digest use is open as JAN-350), and allowed unattended by
    default (read-only, delegable).
  - **Enablement.** It is offered only with the `whatsapp` capability (an
    enabled `WhatsAppAccount`).
- **Send WhatsApp (JAN-360).** `whatsapp.send` (`WHATSAPP_SEND_ENTRY`,
  `operation="write"`, `risk_level="high"`, `confirmation="required"`, like
  `mail.send`) holds `send_whatsapp_message(text, conversation_id=None,
  phone_number=None, account_id=None)`.
  - **Target.** Exactly one of an owned `conversation_id` (direct or group;
    its account must be enabled) or an international `phone_number`
    (`whatsapp_service.phone_to_jid`: digits with optional `+`/`00`, 7-15
    digits, no national `0` prefix, becomes `<digits>@s.whatsapp.net`).
    A phone number needs `account_id` when the user has several enabled
    accounts.
  - **Send.** `WhatsAppService.send_text` calls
    `workers.whatsapp.send_text_message`, which sends over the account's
    live `NeonizeConnector.client` (`client.send_message(jid, text)`) and
    raises `WhatsAppNotConnectedError` when there is none in this process.
    Text only, at most `MAX_SEND_CHARS` (4096) characters; no media,
    reactions, or Status posts.
  - **Storage.** The sent message is upserted as an `outgoing`
    `WhatsAppMessage` (provider id and timestamp from the send response), and
    a phone number without a stored chat gets a new `direct` conversation
    titled `+<digits>`.
  - **Policy.** In the `chat` allowlist and asks per call there; off for
    unattended runs by default, like every `required` entry.
- **Capability split (JAN-229).** Read and write halves of a toolbox are
  separate registry entries so they can carry different confirmation levels:
  `mail` (read, `never`) / `mail.label` (`recommended`) / `mail.send`
  (`required`) / `mail.processing` (`recommended`); `planner.local` (read,
  `never`) / `planner.local.write` (`required`); `rules` (read, `never`) /
  `rules.write` (`required`). `skills` (the admin-only skill-library toolbox,
  `agent_toolboxes/skills.py`, `confirmation="required"`) replaces the removed
  `agent-management` entry, and `handoffs` is gone.
- **Introspection (`events` and `trigger-history`, JAN-185/195):** two
  read-only, ungated entries built from `agent_toolboxes/events.py`.
  `trigger-history` (`list_my_trigger_history`) reads the caller's own
  `runtime_triggers` rows (status, `attempt_count`, `last_error`,
  `completed_at`, plus the executed run's `result`/`step_error` -- never the
  raw payload), filtered by `user_id`; it is allowlisted for `chat` and
  *Allowed* by default for unattended runs, so a run can check its own prior
  outcome before repeating work. `events` (`list_recent_events`) reads the
  event log through the same visibility rule as `GET /api/events`
  (`services/event_service.visible_events_condition`, JAN-272): in a `chat`
  step an admin reads every row and anyone else only rows with their
  `user_id`; in every other mode -- all unattended runs, admin-owned or not,
  and a toolset built outside a plan step -- only the owner's rows, never
  system rows (`user_id` `NULL`) or another user's. It is therefore a normal,
  default-*Allowed* entry on the Unattended switch and is delegable (read-only).
  See "Event ownership" below and `docs/docs/tech-spec/llm-agent-safety.md`.
- **Documentation (`docs`, JAN-297):** a read-only, ungated,
  `confirmation="never"` entry built from `agent_toolboxes/docs.py`
  (`list_docs`, `read_doc`, `search_docs`) over `services/docs_service.py`,
  the same file access and path confinement `/api/docs` uses, minus
  `docs/docs/reviews/`. It needs no capability, is allowlisted for `chat`,
  and is *Allowed* by default (and delegable) for unattended runs. It is not
  `reads_untrusted_content`: the pages are first-party. Each registry entry's
  `docs_ref` names a section of `docs/docs/tools.md`, and a `BLOCKED`
  refusal stub cites it ("See the docs: tools.md#send-mail."). The shared
  system prompt tells Janus to use these tools for questions about Janus
  itself. See `docs/docs/tech-spec/llm-agent-safety.md`.
- **Event ownership (JAN-272).** `models.Event.user_id` (nullable, FK
  `users.id` `ON DELETE SET NULL`, indexed; migration `a3e5c7d9f1b2`) records
  the user an event concerns. `event_logger.log(..., user_id=)` takes it
  explicitly; otherwise it inherits the owner bound by
  `event_logger.owned_by(user_id)` (a `ContextVar`; `bind_owner`/
  `release_owner` for async scopes). Owned scopes: each trigger run
  (`trigger_runtime` handler), each delegated child
  (`delegation_runtime.run_delegation`), each `/ws/chat` connection, each
  mail account's IMAP sync/IDLE thread (`workers/email.initial_sync_worker`),
  each message the processor files (`EmailProcessor.process_email`), each
  planner account's sync (`PlannerService.sync_all`). Explicit owners: auth
  password/session events, mail send failures, relabel fallbacks, memory
  compaction, the dashboard briefing step, mail-processing tool decisions,
  delegation outcomes, digest delivery, refused tool calls, `mark_message_read`,
  MCP registry audit events (the server's owner), and skill-library audit
  events (the acting user). Everything else -- startup, LLM provider init,
  status probes, worker supervision, failed logins -- stays a system row.
  Every row written before the migration is a system row. Deleting a user
  sets their rows to system rows (`DELETE /api/auth/users/{username}`, since
  SQLite does not enforce the FK).
- **Chat (`/ws/chat`, JAN-163):** each inbound message calls
  `RuntimeAdapter.prepare_step()` fresh -- no toolset is cached for the
  conversation or the WebSocket's lifetime. There is no agent selection or
  capability-profile row: the user and their real connections are the whole
  context. The resulting selection (`tools`/`denied`) streams to the client as
  a `__TOOL_SELECTION__:<json>` event immediately before `__START__`.
  Chat is human-supervised, so the unattended switch does not apply; instead
  every `confirmation="required"` tool asks the user per call (JAN-254, see
  "Chat tool approval" below). Chat pre-confirms only its `never` and
  `recommended` entries (`confirmed_tool_ids`) and passes an `approval_gate`
  to `prepare_step`, which builds the `required` entries anyway and wraps
  each of their tools so the call pauses until the user answers. The
  streamed selection lists them with `confirmation: "required"`.
  `confirmed_tool_ids` is `tool_policy.chat_preconfirmed_tool_ids` (JAN-359):
  the non-`required` entries plus `required` entries the user switched
  *Always allow*, or every entry while the user's *In chat* mode is *Allow
  all*. Before
  each message, chat drops from `requested_tool_ids` every `required` entry
  the user switched *Off in chat* (`tool_policy.chat_withheld_tool_ids`,
  JAN-306), so such an entry is not built, not offered, and not stubbed. Chat
  passes `guard_tool_errors=True`: a tool that raises answers `ERROR: ...` to
  the model instead of aborting the turn. The connection's long-lived chat
  session must not hold an open write transaction while tools run: mutating
  tools write through `run_isolated_tool_mutation`
  (`agent_toolboxes/shared.py`) on a second session, so a new conversation
  row is committed (not just flushed) before the turn's tool loop, and the
  turn's message rows stay pending (`autoflush=False`) until the post-turn
  commit. Otherwise the tool's write would wait on the chat transaction's
  lock ("database is locked" on SQLite; on PostgreSQL, a foreign key to the
  uncommitted conversation would block the same way) --
  `tests/test_chat_sqlite_locking.py` pins this on an on-disk SQLite file
  (JAN-189). After the reply, the post-turn
  memory suggester runs with the user's `memory_instructions` (JAN-230) or the
  built-in default rules. The reply is committed before `__END__`; the
  first message's title and the memory suggestion commit after it, so a
  turn is finished on the server only once the socket waits for the next
  message. A conversation deleted while a turn is in flight (from another
  tab, or right after the reply while the title is still generated) makes
  either commit's `UPDATE conversations` match no row: the turn's writes are
  rolled back (no orphaned messages). A commit that updates no conversation
  row (a later message's tail with no title to store) cannot notice that way,
  so after every turn commit, and before a message on a resumed conversation,
  chat checks that the user's conversation row still exists (owner-scoped).
  In every case the socket ends with
  `__ERROR__:This conversation was deleted.` instead of a generic chat error
  (`_commit_turn`/`_require_conversation` in `websocket/chat.py`).
- **Chat tool-call events (`/ws/chat`, JAN-249):** between `__START__` and
  `__END__`, every tool call of the turn streams as
  `__TOOL_EVENT__:<json>` frames, one per phase, built by
  `services/tool_call_events.py` from LangChain's `on_tool_start` /
  `on_tool_end` / `on_tool_error` stream events. The assistant message and
  its tool-call rows are committed *before* `__END__` is sent, so a client
  that reloads the conversation right after the turn sees them; the title
  and the memory suggestion are best-effort and commit afterwards. The envelope:

  | Field | Meaning |
  | --- | --- |
  | `call_id` | LangChain `run_id` of the call; all frames of one call share it |
  | `tool_id` | Registry entry id (`mail`, `memory`, ...) from `PreparedStep.tool_ids_by_name`; `null` if unknown |
  | `tool_name` | The tool the model called (`search_emails`) |
  | `phase` | `start`, then exactly one of `end`, `error`, `blocked` |
  | `args` | The model's arguments, redacted (below); cut JSON *text* instead of an object when over 4 KB |
  | `args_truncated` | Whether any argument (or the whole JSON) was cut |
  | `result_preview` | The tool's answer as the model saw it, plain text, cut to 4 KB; `null` on `start` |
  | `result_truncated` | Whether the preview was cut |
  | `duration_ms` | Wall time from start to end; `null` on `start` |
  | `step_id` | The turn step the call ran in (JAN-316, below); `null` outside a step; live only, not stored |

  - **Phases:** a call whose answer is the runtime's own refusal
    (`BLOCKED: <tool_name> was not run ...`: the tool-policy/connection
    refusal stub or the prompt-injection escalation in `runtime_adapter.py`)
    ends `blocked`; one whose answer is the chat error guard's
    `ERROR: <tool_name> failed: ...`, or that raised, ends `error`. Only these
    exact shapes count, so a tool merely returning text that starts with
    "BLOCKED" stays `end`.
  - **One call, one id:** the runtime wraps each tool (`_guarded` ->
    `_traced` -> the tool) and every wrapper's nested `invoke` emits its own
    LangChain tool events; only the outermost run (no open tool run among its
    `parent_ids`) is reported.
  - **Redaction:** argument keys that, lowercased with non-alphanumerics
    removed, contain `password`, `passwd`, `passphrase`, `secret`, `token`,
    `apikey`, `accesskey`, `privatekey`, `authorization`, `cookie`,
    `credential`, `sessionid` or `bearer`, or equal `auth`, `pwd`, `otp` or
    `pin`, have their value replaced by `[redacted]` at any depth; so does a
    whole string value of the form `Bearer <token>` / `Basic <token>`. String
    values over 1,024 characters are cut. This is a best-effort display
    filter, not a secret scanner: free-text arguments are shown as written.
  - **Untrusted results:** `result_preview` can carry mail, web, or MCP
    content. It is never interpreted by the backend and clients must show it
    as plain text only (never Markdown or HTML).
  - **Opt-in:** only clients that open `/ws/chat?tool_events=1` receive the
    frames, so a client that would show unknown frames as reply text (the iOS
    scaffold) is unaffected. Frames are never part of the stored reply text.
  - **Persistence:** whether or not the client opted in, the last frame of
    each call is stored as a `conversation_tool_calls` row of the assistant
    message (see [data model](data-model-migrations.md)) and returned in
    `GET /api/conversations/{id}` as each message's `tool_calls`, in call
    order (empty for user messages and for replies without tools).
  - **Deprecated markers:** `__TOOL_CALL__:<tool_name>` and
    `__TOOL_RESULT__:<tool_name>` are still streamed to every client (one per
    LangChain tool run, so a wrapped tool repeats them) for one more release.
    The web client ignores them; they will be removed once the iOS client
    reads `__TOOL_EVENT__`. New clients must not depend on them.
  - **Approval:** a frame of a call the user was asked about also carries
    `approval` (`approved`, `denied`, `timeout`, `disconnected`,
    `unavailable`; `null` for every other call), stored on the row.
- **Chat tool approval (`/ws/chat`, JAN-254):** a tool of a
  `confirmation="required"` entry (send mail, GitHub, Linear, planner
  writes, mail-rule writes, skill edits, wiki edit proposals, web page fetches) never runs in chat without the user
  approving that one call.
  `runtime_adapter.prepare_step(approval_gate=...)` builds those entries
  (they are not withheld and not stubbed) and wraps each tool as the
  outermost layer (`gate` -> `_guarded` -> `_traced` -> the tool), so the
  gate's LangChain run is the call JAN-249 reports and its `run_id` is the
  `call_id`. The gate lives in `services/tool_approval.py`
  (`ToolApprovalBroker`); the WebSocket side in `websocket/chat_inbox.py`.
  - **Pause:** LangChain runs a sync tool in a worker thread
    (`run_in_executor`), so the gate blocks that thread on
    `asyncio.run_coroutine_threadsafe(broker.request(...))` while the event
    loop keeps serving the socket. `broker.request` waits (at most 2 s) until
    the call's `start` event has been forwarded, sends one
    `__TOOL_APPROVAL__:<json>` frame, and awaits a per-call
    `asyncio.Future`. A gate that finds itself on the event-loop thread
    refuses instead of deadlocking.
  - **Request frame** `__TOOL_APPROVAL__:{call_id, tool_id, tool_name,
    display_name, risk_level, reason, args, args_truncated,
    timeout_seconds}`: `args` and `args_truncated` use the JAN-249
    redaction and size caps (`tool_call_events.preview_args`); `reason` is
    the registry entry's description; `timeout_seconds` lets the client show
    how long the card stays open.
  - **Answer frame** (client -> server, a text frame):
    `{"type": "tool_approval", "call_id": "...", "decision": "approve" |
    "deny"}`. A reader task (`ChatInbox`) owns `websocket.receive_text()` for
    the whole connection: approval frames resolve the pending future,
    everything else is queued as the next user message, so a message typed
    during a turn still runs after it. The queue holds at most
    `MAX_QUEUED_MESSAGES` (8) messages; one more closes the socket with 1008
    rather than growing memory and the backlog of LLM turns without limit,
    and messages still queued when the socket closes are dropped. The reader
    never blocks on the queue, so approval answers always get through. An
    answer for an unknown, already answered, or other connection's
    `call_id`, or with any other `decision`, is ignored.
  - **Per call, never a standing grant:** the future belongs to one
    `call_id`, the approved call runs with exactly the arguments the card
    showed (unredacted), and the next call to the same tool asks again.
    A conversation-wide "Allow for this conversation" grant was considered
    and not adopted (owner decision, JAN-306): it would let later calls with
    different arguments run unseen.
  - **Per-tool *In chat* switch (JAN-306, JAN-359):** *Ask every time*
    (default), *Always allow* (`tool_policies.chat_mode = "always"`: the
    entry is pre-confirmed and skips the gate), or *Off in chat*
    (`"off"`), set through `PUT /api/tools/policies/{tool_id}`. *Off in chat*
    withholds the entry from the chat request (above), so the model never
    sees its tools and a delegated child of the turn cannot be granted them.
    The user-wide *In chat: Allow all* mode (`tool_policy_modes.chat_mode`)
    pre-confirms every requested entry but never un-withholds an *Off in
    chat* one. Both are per user, read on every message, and ignored by
    unattended runs.
  - **Always allow from the card (JAN-359):** a `{"decision": "always"}`
    answer resolves the call as `approved` and, when the call runs, calls
    the broker's `on_always_allow` hook, which stores *Always allow* for
    that entry (`tool_policy.update_policy` on its own short-lived session),
    so the next message pre-confirms it.
  - **Refusal:** deny, no answer within
    `chat_tool_approval_timeout_seconds` (default 300; env
    `JANUS_CHAT_TOOL_APPROVAL_TIMEOUT_SECONDS`), or a disconnect answers the
    model `BLOCKED: <tool_name> was not run because the user declined.
    Nothing was changed.` (timeout and disconnect name their own reason),
    the tool-policy refusal shape, so the call ends `blocked`. The refusal
    is recorded in the step's `call_log` (`refused`, `user_denied` /
    `user_timeout` / `user_disconnected` / `user_unavailable`) and as a
    `WARNING` event.
  - **Disconnect and reconnect:** when the socket closes, the reader task
    resolves every pending call `disconnected`; the turn finishes without
    sending (sends become no-ops) and is stored with its rows. A new
    connection has a new broker, so a reconnect never replays, re-asks, or
    approves a call of an earlier connection.
  - **Clients that did not opt in:** only `/ws/chat?tool_events=1` clients
    get the frame. For any other client (the iOS scaffold) a required tool
    answers `BLOCKED` (`unavailable`, "this chat client cannot ask the user
    for approval") and is never run silently.
  - **Prompt injection:** after a suspected injection in the step, the gate
    does not ask; the inner `_traced` wrapper refuses the call as before
    (JAN-127).
  - **Persistence:** `conversation_tool_calls.approval` (nullable string)
    holds the decision; it is `null` for calls that needed none.
- **Chat plan/step timeline (`/ws/chat`, JAN-316):** a chat turn is one
  runtime plan step (`PreparedStep.step`, the `step_id` the turn's
  `__TOOL_SELECTION__` frame already names, `chat-conversation-<id>::message-<n>`).
  Inside it, the tool-calling agent works in rounds: each model call that asks
  for tools is one *turn step*, and the tools it asked for run under it. The
  turn steps stream live as `__PLAN_EVENT__:<json>` frames, built by
  `services/plan_step_events.py` (`TurnEventStream`, which also owns the
  JAN-249 `ToolEventTracker`) from LangChain's `on_chat_model_start` /
  `on_chat_model_end` and the tool events, and every `__TOOL_EVENT__` of a
  call made inside a step carries that step's id. The envelope:

  | Field | Meaning |
  | --- | --- |
  | `step_id` | Turn-local id, `step-1`, `step-2`, ...; unique within one turn only |
  | `index` | 1-based position of the step in the turn |
  | `status` | `pending`, `running`, `completed`, `failed` (the `PlanStepStatus` values of `plan_graph.py`) |
  | `tool_names` | The tools the model asked for in this round, in its order; may repeat a name |

  - **Lifecycle:** `pending` when the model's round ends with tool calls
    (the step was generated); `running` right before its first
    `__TOOL_EVENT__` `start`; then exactly one terminal frame, `failed` if
    any of its calls ended `error` or `blocked` (including a denied or
    unanswered approval), otherwise `completed`. The terminal frame follows
    the step's last terminal `__TOOL_EVENT__` once as many calls finished as
    the model asked for; otherwise (a call LangChain answered without a tool
    run, such as an unknown tool name) the next model round or the end of the
    stream closes it. A stream that fails mid-turn closes an open step
    `failed`. A tool call that starts while no step is open (a provider that
    reports no model events) opens an implicit step with empty
    `tool_names`, so every call has a step. The final round that only
    writes the reply is not a step.
  - **Waiting for approval:** not a separate frame. A step is *waiting*
    while it is not terminal and one of its calls has an unanswered
    `__TOOL_APPROVAL__` request (JAN-254); clients derive it from the frames
    they already have, so the gate needs no extra plumbing.
  - **Tool-event `step_id`:** `__TOOL_EVENT__` frames gain an optional
    `step_id` (the turn step the call ran in; `null` for a call outside any
    step). It is not stored: `conversation_tool_calls` rows and
    `GET /api/conversations/{id}` keep the JAN-249 shape.
  - **Why a new frame, not only a field on the tool event:** a step exists
    before any of its calls starts (`pending`) and ends after the last one,
    and a round can ask for several tools at once, so its lifecycle is not
    the lifecycle of any single call. The tool-event `step_id` only links a
    call to its step.
  - **Nested models:** model events while a tool call is open (a tool that
    calls a model internally) are ignored; they are not agent rounds.
  - **Live only:** step boundaries are not persisted. A reloaded or
    reselected conversation shows its tool calls (JAN-249) but no steps;
    the timeline covers the current turn. The durable, cross-run view of
    unattended plan state is the Agent Tasks page (JAN-204,
    `GET /api/runtime/tasks/`), which uses the same `PlanStepStatus` names.
  - **Opt-in:** only `/ws/chat?tool_events=1` clients receive the frames
    (the iOS scaffold is unaffected); they are never part of the stored
    reply text.
  - **Progress narration:** the chat system prompt
    (`agent_runtime.CHAT_PROGRESS_UPDATES`) asks Janus, on a turn that takes
    more than one tool round, to write a short plan before its first tool
    call and one short line after each round (was the result as expected,
    the most interesting finding, what comes next), then the full answer;
    a direct answer or a single lookup skips it. The narration is ordinary
    reply text: it streams to every client (with or without
    `tool_events=1`) and is stored as part of the assistant message.
    `llm_service.stream_agent_chat` separates the text of one agent round
    from the next round's with a blank line (`plan_step_events.RoundTextJoiner`,
    fed by `TurnEventStream`, so a nested model is not a round) (never before the turn's first
    text, never a second one when the round already ended with one), so
    the plan, the progress lines, and the answer stay separate paragraphs.
    Unattended runs do not get the section: their output is a result, not
    a live conversation.
- **Chat page context (`/ws/chat`, JAN-250):** every web-client message
  carries a structured snapshot of the page the user is looking at, so
  Janus can answer "what am I looking at?" and resolve "this email" or
  "these topics". The logic lives in `services/page_context.py`; `chat.py`
  only calls it.
  - **Inbound message frames** (client -> server, text frames). The
    `ChatInbox` reader is unchanged: `tool_approval` frames go to the
    approval broker first, every other frame is queued as the next message
    and parsed by `page_context.parse_message_frame()`:

    | Frame | Meaning |
    | --- | --- |
    | plain text (`hello`) | the message, no page context (iOS scaffold, old clients) |
    | `{"type": "message", "text": "...", "page_context": {...}}` | the message plus a structured snapshot (current web client) |
    | `{"text": "...", "page_context": "Emails"}` | legacy JSON envelope; a string page context becomes `{"page": "Emails"}` |
    | `{"type": "tool_approval", ...}` | an approval answer (JAN-254), never a message |

    Text that is valid JSON but not an object (`42`, `"hi"`) is a plain-text
    message. A malformed or missing `page_context` is dropped, never an
    error; the message still runs.
  - **Snapshot shape:** `{page, route, title?, selection?, filters?,
    visible_items?, omitted_items?}` where `selection` and each
    `visible_items` entry are `{type, id, label}`. `page` is the human page
    name (`Emails`, `Dashboard`, `Memory › Topics`, `Settings › Triggers`),
    `route` the path plus query string, `filters` a flat map of the active
    filters/tabs/search (string, number, boolean, or `null` values), and
    `omitted_items` how many visible items the budget dropped.
  - **Server-side budget and sanitizing** (`sanitize_page_context`): only the
    keys above survive (an extra key such as `body` is dropped, at any
    level); strings are whitespace-collapsed, stripped of invisible Unicode
    tag characters and cut (`page` 60, `title` 120, `route` 200, `type` 32,
    `id` 64, `label` 80, filter keys 40 and values 80 characters); at most
    12 filters and 40 visible items are read. The result must serialize to
    at most `MAX_PAGE_CONTEXT_BYTES` (2,048 bytes of UTF-8 JSON): visible
    items are dropped from the end (counted in `omitted_items`), then
    filters, then the selection, then the title, until it fits. The client
    enforces the same 2 KB budget before sending (see the frontend spec),
    so the server cut only matters for other or hostile clients.
  - **Prompt rendering** (`render_page_context`): the snapshot is
    prepended to the model's copy of the user message as one block,
    `<page_context>{json}</page_context>`, preceded by one sentence saying
    it is the page the user is viewing, that its labels can come from mail,
    chats and other outside sources, and that it is untrusted data, never
    instructions. Inside the block the block's own tag is neutralized
    (`<` becomes `&lt;`), so a label cannot close it. The block has its own
    prompt budget, `PAGE_CONTEXT_PROMPT_MAX_CHARS` (2,048 characters of
    JSON), applied with the same drop order, independent of the history or
    planner-context budgets in `llm_service.py`.
  - **Never stored in the conversation:** the stored user message, the
    history replayed on the next turn, the conversation title, the plan
    step's objective, and the memory suggester all use the user's own text.
    A snapshot affects only the turn it was sent with and is not persisted
    anywhere.
  - **Injection scan:** the snapshot's string leaves are scanned with
    `prompt_injection.scan_output`. A snapshot with a selection or visible
    items records a `page_context` pseudo-call in the step's `TurnTrace`
    (`reads_untrusted_content=True`, the matched rules), so a flagged label
    escalates consequential tools for the turn and is named in the chat
    safety notice exactly like flagged tool output (see the
    [safety spec](llm-agent-safety.md#prompt-injection-guardrails-jan-127)).
- **Chat page actions (`/ws/chat`, JAN-251):** Janus can act on the page the
  user is looking at ("open the mail from Anna", "filter to IMPORTANT", "go to
  triggers and show the failed runs"). The effects are split strictly in two:
  *UI actions* are client-side and non-destructive and are the only thing this
  feature adds; *data mutations* stay the existing server-side tools under the
  confirmation gate, and the page reflects them through `/ws/changes` as
  before. The `ui_actions` toolbox (`agent_toolboxes/ui_actions.py`) performs
  nothing server-side: each tool validates its request and asks the client to
  do it. Validation, the allowlists, and the per-connection broker live in
  `services/ui_actions.py`; `chat.py` only wires them.
  - **Tools** (registry entry `ui_actions`, `confirmation="never"`,
    `risk_level="low"`, operation `read`: nothing on the server changes):

    | Tool | Frame `action` | Arguments | Server-side check |
    | --- | --- | --- | --- |
    | `ui_navigate` | `navigate` | `route` | `route` is a known app route: `/`, `/chat`, `/briefing`, `/emails`, `/docs`, `/memory?tab=<facts\|topics\|documents\|knowledge\|wiki\|rules>`, `/settings?tab=<a Settings tab id>` (Triggers is `runtime`); no other query, no host, no fragment |
    | `ui_set_filter` | `set_filter` | `page`, `key`, `value` | `(page, key)` is allowlisted and `value` is one of its values: `emails.priority` (`all`, `important`, `digest`, `spam`), `topics.status` (`active`, `candidate`, `snoozed`, `resolved`, `ignored`, `all`), `topics.search` (free text, at most 80 characters), `triggers.run_status` (`all`, `queued`, `running`, `waiting`, `completed`, `failed`, `exhausted`, `cancelled`), `triggers.run_kind` (`all`, `manual`, `scheduled`, `event`), `triggers.show_finished` (`true`, `false`) |
    | `ui_open_item` | `open_item` | `type`, `id` | `type` is `email`, `topic`, `calendar_event`, or `trigger_definition`, and `(type, id)` is in this turn's `page_context` (`visible_items` or `selection`) |
    | `ui_highlight` | `highlight` | `type`, `id` | same as `open_item` |
    | `ui_scroll_to` | `scroll_to` | `section` | a known section id: the Dashboard widgets (`briefing`, `topic_focus`, `important_emails`, `whatsapp_messages`, `schedule`, `tasks`) and the Triggers tab's `trigger_definitions` and `trigger_runs` |
    | `ui_prefill_form` | `prefill_form` | `form_id`, `fields` | a known form with known field names, string values of at most 1,000 characters: `topic.create` (`title`, `summary`, `marker_keywords`). The client fills the form; it never submits it |

    The tool descriptions name these values so the model does not guess.
    Anything else answers `ERROR: <tool> failed: <why>` (chat's tool error
    guard), so the call ends `error` and Janus can say what it could not do.
  - **Outbound frame** (server -> client): `__UI_ACTION__:{action_id,
    call_id, action, params, label, timeout_seconds}`. `action_id` is a fresh
    random id per request; `call_id` is the JAN-249 `call_id` of the tool
    call (below); `params` are the validated, normalized arguments (the
    JAN-249 redaction and size helpers are applied, a no-op for the
    allowlisted keys); `label` names the target for the transcript chip (the
    item's snapshot label, the route, `key = value`, the section, or the form
    id); `timeout_seconds` is how long the server waits.
  - **Inbound frame** (client -> server): `{"type": "ui_action_result",
    "action_id": "...", "ok": true|false, "error": "..."}`. The `ChatInbox`
    reader hands it to the connection's `UIActionBroker` (next to the
    approval broker), so it arrives while the reply streams; it is never
    queued as a message. A result for an unknown, already answered, or
    earlier connection's `action_id` is ignored, as is a non-boolean `ok`.
    `error` is cut to 300 characters.
  - **Tool answer = client result:** the tool blocks its worker thread (the
    JAN-254 pattern: `asyncio.run_coroutine_threadsafe` onto the
    connection's loop, a per-action future) until the client answers or
    `chat_ui_action_timeout_seconds` (default 10; env
    `JANUS_CHAT_UI_ACTION_TIMEOUT_SECONDS`) passes. `ok: true` answers
    `Done: <what> ...`; `ok: false` answers `ERROR: <tool> failed: the page
    could not do it: <error>`; a timeout answers `ERROR: ... the page did not
    confirm it in time; assume it did not happen`, never a success; a closed
    socket answers an error too.
  - **`call_id`:** the toolbox is the innermost wrapper, so it cannot see its
    outer LangChain run. The broker therefore pairs each request with the
    call's forwarded JAN-249 `start` event: `chat.py` reports every
    forwarded `start` of a `ui_*` tool (`note_started(call_id, tool_name,
    args)`), and the request claims the oldest unclaimed start of the same
    tool, preferring one with equal arguments, waiting at most 2 s for it
    (else a random id). Unclaimed starts are dropped at the next turn.
  - **Clients that did not opt in:** only `/ws/chat?tool_events=1` clients
    get the frame. For any other client (the iOS scaffold) every tool answers
    `ERROR: ... this client cannot perform page actions` without waiting.
  - **Chat only:** `ui_actions` is in `TASK_MODE_ALLOWED_TOOL_IDS["chat"]`
    only, is in `tool_policy.LOCKED_UNATTENDED` (never offered to `trigger`,
    `automation`, or `delegated` runs, and never selectable for a trigger
    definition), is enabled only in `chat` task mode, and, like every locked
    entry, is left out of `ToolBuildContext.authorized_tool_ids`, so a
    delegated child can never inherit it.
  - **Prompt injection:** the `ui_*` tools are in
    `INJECTION_ESCALATED_TOOL_NAMES`: after a suspected injection in the
    turn (a flagged tool output or a flagged `page_context` label) every page
    action answers the usual `BLOCKED: ... looked like a prompt injection`
    refusal, `navigate` and `highlight` included (a redirect is an attack
    vector too), and is listed in the safety notice. The toolbox checks the
    turn trace again itself. See the
    [safety spec](llm-agent-safety.md#page-actions-jan-251).
  - **Persistence:** nothing new: each call is an ordinary
    `conversation_tool_calls` row (tool name, redacted arguments, the answer
    above). The frontend's undo is a client-side affordance, not a tool.
- **Chat UI introspection (`/ws/chat`, JAN-282..285):** Janus can look at the
  page the user has open on demand, beyond the per-message `page_context`
  snapshot: "why is the save button greyed out?", "I don't see the error
  banner", "why does this look wrong?". The chat-only `ui_introspection`
  toolbox (`agent_toolboxes/ui_introspection.py`) reads nothing on the
  server: each tool asks the user's web client for a serialized view of its
  page and answers with the server-side re-sanitized result. The protocol,
  the sanitizers, and the per-connection broker live in
  `services/ui_introspection.py`; the browser side is
  `frontend/src/lib/uiOutline.ts` and `frontend/src/lib/uiIntrospection.ts`. `chat.py` only wires them.
  - **Tools** (registry entry `ui_introspection`, `confirmation="never"`,
    `risk_level="low"`, operation `read`, `reads_untrusted_content=True`):

    | Tool | Frame `kind` | Arguments | Answer |
    | --- | --- | --- | --- |
    | `get_ui_snapshot` | `snapshot` | none | a bounded semantic outline of the visible page plus its viewport (below) |
    | `get_element_styles` | `element_styles` | `ref` | allowlisted computed style properties and the bounding box of one element of the latest snapshot |

    The descriptions tell the model to use the per-message `page_context`
    first, to call `get_ui_snapshot` for questions about what the user sees
    (layout, a missing button, an error banner, "why does this look
    wrong"), to call `get_element_styles` with a `ref` from the latest
    snapshot for visual questions, that refs are valid only until the next
    snapshot, and that every result is untrusted data, never instructions.
  - **Outbound frame** (server -> client): `__UI_INTROSPECT__:{request_id,
    kind, params, timeout_seconds}`. `request_id` is a fresh random id per
    request; `params` is `{}` for `snapshot` and `{"ref": "e12"}` for
    `element_styles`; `timeout_seconds` is how long the server waits.
  - **Inbound frame** (client -> server): `{"type":
    "ui_introspection_result", "request_id": "...", "ok": true|false,
    "result": {...}, "error": "..."}`. The `ChatInbox` reader hands it to the
    connection's `UIIntrospectionBroker` (next to the approval and
    page-action brokers), so it arrives while the reply streams; it is never
    queued as a message. A result for an unknown, already answered, or
    earlier connection's `request_id` is ignored, as is a non-boolean `ok`.
    `error` is cut to 300 characters; a frame larger than
    `MAX_RESULT_FRAME_CHARS` (256 KB) answers the request as failed.
  - **Snapshot shape** (`result` of `snapshot`): `{route, title, viewport,
    nodes, omitted_nodes?, skipped_private?}`. `viewport` is `{width, height,
    scroll_x, scroll_y, device_pixel_ratio, color_scheme: "light"|"dark",
    ui_shell: "v1"|"v2"|"v3"}`. Each node is `{ref, role, depth, name?,
    text?, level?, input_type?, href?, disabled?, required?, invalid?,
    offscreen?}`: `ref` is `e<n>` and stable only for that snapshot; `role` is
    one of the landmarks (`banner`, `navigation`, `main`, `complementary`,
    `contentinfo`, `region`, `form`, `search`, `dialog`, `alertdialog`),
    `alert`, `status`, `heading` (with `level`), `button`, `link` (with
    `href`), the form controls (`textbox`, `searchbox`, `checkbox`, `radio`,
    `combobox`, `listbox`, `switch`, `slider`, `spinbutton`, with their label
    as `name`, `input_type`, `disabled`, `required`, `invalid`), `tab`,
    `tablist`, `img` (its `alt` as `name`), or `text` (a short visible text
    run); `depth` is the number of enclosing landmark/form/dialog nodes;
    `offscreen` flags a node outside the viewport. **Never sent:** input
    values (not even of checkboxes or selects), password, one-time-code, and
    payment-card fields (left out entirely), `contenteditable` content, the chat panel itself
    (`[data-janus-chat]`), and every `[data-janus-private]` subtree; hidden
    nodes (`display: none`, `visibility: hidden`, `hidden`,
    `aria-hidden="true"`) are skipped.
  - **Styles shape** (`result` of `element_styles`): `{ref, tag, box: {x, y,
    width, height}, styles: {property: value}}`. Only the allowlisted
    properties (`STYLE_PROPERTIES`: display, position, visibility, opacity,
    z-index, overflow(-x/-y), box-sizing, the size and min/max sizes,
    margin, padding, top/right/bottom/left, the flex and grid layout basics,
    gap, color, background-color, border, border-radius, outline, the font
    and text basics, cursor, pointer-events, transform) survive; a value
    containing `url(` is dropped.
  - **Server-side budget and sanitizing** (`sanitize_snapshot`,
    `sanitize_styles`): the client is not trusted to have applied its own
    caps. Only the keys above survive (unknown keys dropped at every level,
    an unknown `role` drops the node, a malformed `ref` drops the node);
    strings are whitespace-collapsed, stripped of invisible Unicode tag
    characters and cut (`name` 120, `text` 200, `title` 120, `route` and
    `href` 200, style values 120 characters; `href` loses its query and
    fragment, and an absolute URL keeps only its origin); numbers are
    clamped; at most `MAX_SNAPSHOT_NODES` (200) nodes are read, and the
    sanitized snapshot must serialize to at most `MAX_SNAPSHOT_BYTES` (24 KB
    of UTF-8 JSON): nodes are dropped from the end (counted in
    `omitted_nodes`) until it fits. A styles answer must name the requested
    `ref`. An answer that does not sanitize answers an error.
  - **Refs:** the broker remembers the refs of the connection's latest
    sanitized snapshot. `get_element_styles` with a malformed ref, or one
    that is not in that snapshot (or before any snapshot), errors without a
    frame ("call get_ui_snapshot first"). The client keeps only the latest
    snapshot's ref -> element map and answers an error for an unknown ref or
    an element that left the page.
  - **Untrusted output:** the entry is `reads_untrusted_content=True`, so the
    runtime adapter's `_traced` wrapper spotlights the answer in an
    `<untrusted_tool_output>` boundary, scans it with
    `prompt_injection.scan_output`, and records the read in the step's
    `TurnTrace`. A flagged outline escalates exactly like flagged mail or web
    output: every `INJECTION_ESCALATED_TOOL_NAMES` tool (all page actions
    included) answers `BLOCKED` for the rest of the turn, and the read is
    named in the chat safety notice (JAN-127).
  - **Tool answer = client result:** the tool blocks its worker thread (the
    JAN-251 pattern: `asyncio.run_coroutine_threadsafe` onto the
    connection's loop, a per-request future) until the client answers or
    `chat_ui_action_timeout_seconds` (default 10, shared with page actions)
    passes. `ok: false` answers `ERROR: <tool> failed: the page could not
    answer: <error>`; a timeout, a closed socket, or an answer that does not
    sanitize answers an error too, never an empty or made-up outline.
  - **Clients that cannot answer:** only `/ws/chat?tool_events=1` clients
    (the web UI) get the frame. For any other client (the iOS scaffold, the
    CLI) both tools answer `ERROR: ... this client cannot show Janus its
    page (only the Janus web UI can)` without waiting.
  - **Chat only:** `ui_introspection` is in
    `TASK_MODE_ALLOWED_TOOL_IDS["chat"]` only, is in
    `tool_policy.LOCKED_UNATTENDED` (never offered to `trigger`,
    `automation`, or `delegated` runs, and never selectable for a trigger
    definition), is enabled only in `chat` task mode, and, like every locked
    entry, is left out of `ToolBuildContext.authorized_tool_ids`, so a
    delegated child can never inherit it.
  - **Persistence:** nothing new: each call is an ordinary
    `conversation_tool_calls` row whose stored result is the capped JAN-249
    preview of the answer. Snapshots are not kept anywhere else.
- **Chat UI blocks (`/ws/chat`, JAN-252):** Janus can answer with
  structured UI as well as Markdown: a summary card, a small table, a chart,
  a checklist, key/value facts, a row of action buttons, or an image (the
  `generate_image` tool adds its own, JAN-253; see below). A block is model output, validated against a fixed
  catalog on both sides and rendered as text only (see the
  [safety spec](llm-agent-safety.md#chat-ui-blocks-jan-252)). The catalog,
  the budgets, validation, and the per-turn collector live in
  `services/ui_blocks.py`; the tool in `agent_toolboxes/ui_blocks.py`;
  `chat.py` only wires them.
  - **Tool:** `render_ui(block)` (registry entry `ui_blocks`,
    `confirmation="never"`, `risk_level="low"`, operation `read`: it only
    shows something). `block` is one catalog object with its `type`; the
    tool validates it, gives it a fresh id (`blk_<12 hex>`), stamps the
    catalog version, keeps it for the assistant message, and answers the
    model `Shown to the user: <type> block <id>. ...` (a short confirmation;
    the model is told not to repeat the block's contents as text).
  - **Catalog, `UI_BLOCK_SCHEMA_VERSION = 1`.** Every block carries `id`,
    `version` (`1`), `type`, and an optional `title` (at most 120
    characters; required for `card`). Unknown keys are rejected at every
    level (`extra="forbid"`). Strings are plain text:

    | `type` | Fields (limits) |
    | --- | --- |
    | `card` | `title` (required), `subtitle?` (200), `body?` (2,000), `fields?` (at most 12 `{label` 60, `value` 300`}`), `links?` (at most 4 links, below) |
    | `table` | `columns` (1-8 strings of at most 60), `rows` (at most 50; each exactly one cell per column; a cell is a string of at most 300, a number, a boolean, or `null`) |
    | `chart` | `kind` (`bar`, `line`, `pie`), `labels` (1-50 strings of at most 40), `series` (1-5 `{name` 60, `values`: one finite number per label`}`), `unit?` (16). A `pie` has exactly one series of non-negative values with a positive sum |
    | `checklist` | `items` (1-50 `{text` 300, `checked` boolean, default `false}`); display only, nothing toggles |
    | `key_value` | `items` (1-30 `{key` 80, `value`: a string of at most 500, a number, or a boolean`}`) |
    | `actions` | `buttons` (1-6 `{label` 40, `tool`, `args` (JSON object, default `{}`), `style` (`primary`, `secondary`, `danger`; default `secondary`)`}`); `tool` must be the name of a tool offered in this turn (not `render_ui`) |
    | `image` | `alt` (required, 300), `url?`, `caption?` (300). `url` may only be a same-origin, authenticated image path, `/api/images/<id>` (`id` 1-64 of `[A-Za-z0-9_-]`); there are no external image URLs. Without `url` the client shows the alt text as a placeholder |

    A link is `{label` (80), `href` (500)`}` where `href` is a same-origin
    path (`/...`, not `//` and without a backslash) or an `https://` URL with
    a host. A `version` other than `1` in the model's block is rejected; an
    `id` the model sends is replaced.
  - **Budgets:** the block's JSON (UTF-8, as streamed and stored) is at most
    `UI_BLOCK_MAX_BYTES` (8,192 bytes); the item limits are in the table; at
    most `MAX_UI_BLOCKS_PER_MESSAGE` (8) blocks per reply. The web client
    mirrors every limit in its zod schemas (`frontend/src/lib/uiBlocks.ts`).
  - **Refusals:** a block that fails validation answers `ERROR: render_ui
    failed: invalid UI block: <the validation errors>` (chat's tool error
    guard), so the model can fix the block and call again, or answer in
    Markdown. Nothing is streamed or stored for it.
  - **Outbound frame** (server -> client, opt-in): `__UI_BLOCK__:<block
    json>`, the validated block exactly as stored. `chat.py` sends a turn's
    new blocks right after the `render_ui` call's terminal `__TOOL_EVENT__`
    frame (and any left over before `__END__`), so a block appears between
    the reply text streamed before and after the call.
  - **Clients that did not opt in:** only `/ws/chat?tool_events=1` clients
    can render blocks. For any other client (the iOS scaffold) `render_ui`
    answers `ERROR: render_ui failed: this client cannot render UI blocks;
    answer in Markdown instead` without storing anything; the tool
    description tells the model to fall back to Markdown.
  - **Persistence:** blocks are stored with the assistant message, like its
    tool calls, as `conversation_ui_blocks` rows (`position`, `block_id`,
    `block_type`, `schema_version`, JSON `payload` = the block), committed
    before `__END__`. `GET /api/conversations/{id}` returns them as each
    message's `ui_blocks` (`{block_id, block_type, schema_version,
    payload}`), so a reloaded conversation renders the same blocks. Blocks
    are not replayed into the model's history (only the reply text is).
  - **Actions stay in the chat turn:** a button never runs anything in the
    browser. Clicking it sends an ordinary user message whose text names the
    button, the tool, and its arguments (`Run "Snooze": call snooze_topic
    with {"topic_id": 4}`), so the tool runs, if the model calls it, inside a
    normal chat turn under the tool policy and the JAN-254 confirmation gate
    (a `confirmation="required"` tool still asks).
  - **Chat only:** `ui_blocks` is in `TASK_MODE_ALLOWED_TOOL_IDS["chat"]`
    only (not `briefing` or `digest`: their output is stored and rendered as
    text), is in `tool_policy.LOCKED_UNATTENDED`, is enabled only in `chat`
    task mode, and is left out of `ToolBuildContext.authorized_tool_ids`, so
    no trigger, automation, or delegated run is ever offered it.
  - **Prompt injection:** after a suspected injection in the turn, an
    `actions` block and any block with an `https://` link answer the usual
    `BLOCKED: render_ui was not run because content read earlier looked like
    a prompt injection` refusal; plain cards, tables, charts, checklists,
    key/value lists, and images still render.
- **Chat image generation (`/ws/chat`, JAN-253):** Janus can make a picture
  with a cloud image model and show it inline. Cloud only (owner decision
  2026-09-28): no local Stable Diffusion/ComfyUI backend. The providers and
  their capability detection live in `services/image_generation.py`; storage,
  the quota, and the owner-scoped lookup in `services/image_store.py`; the
  tool in `agent_toolboxes/images.py`; the endpoint in `api/images.py`.
  `chat.py` only links the turn's images to the assistant message.
  - **Tool:** `generate_image(prompt, aspect?, style?)` (registry entry
    `images`, `confirmation="recommended"`, `risk_level="medium"`, operation
    `write`: it costs money, sends the prompt off-host, and stores a file).
    `prompt` is at most 4,000 characters, `aspect` is `square` (default),
    `landscape`, or `portrait`, and `style` is an optional hint of at most 200
    characters appended to the prompt (`Style: <hint>`). `recommended` means
    it runs in chat without the JAN-254 approval card (only `required` entries
    ask); the call is still a tool chip with its arguments, and the event log
    records it. The tool description says the prompt is sent to the
    configured provider.
  - **Answer and block:** the tool stores the image, then adds an `image`
    block itself to the turn's `UIBlockTurn` (`url` `/api/images/<id>`, `alt`
    = the prompt cut to 300 characters, `caption` `Generated with <provider>
    <model>`), so the picture shows inline right after the call's tool event,
    like a `render_ui` block, without a second tool call. The model is
    answered `Generated and shown to the user: image <id> (block <block id>,
    /api/images/<id>). Do not call render_ui for it again ...`. A client that
    cannot render blocks (did not open `/ws/chat?tool_events=1`) gets
    `ERROR: generate_image failed: this client cannot show images ...` before
    anything is sent to a provider; a reply that already holds
    `MAX_UI_BLOCKS_PER_MESSAGE` blocks is refused the same way.
  - **Providers and capability detection:** `Settings.image_provider`
    (`openai`, `gemini`, `openrouter`; `NULL` = off, the default) and
    `Settings.image_model` (empty = the provider's default: `gpt-image-1`,
    `gemini-2.5-flash-image`, `google/gemini-2.5-flash-image`) choose the
    backend. Each provider reuses the API key and base URL stored for the chat
    provider of the same name (like embeddings, JAN-241); there is no second
    credential. Image generation counts as configured only when a provider is
    chosen *and* its key is stored; `capability_profile` then grants the
    `images` capability. Otherwise the entry is denied `missing_capability`
    (listed in `__TOOL_SELECTION__`'s `denied`) and the model is not offered
    the tool. Ollama and Anthropic cannot generate images and are rejected as
    `image_provider` with `422`.
    - **OpenAI:** `POST {openai_base_url or https://api.openai.com/v1}/images/generations`
      with `{"model", "prompt", "n": 1, "size"}` (`gpt-image-*`: `1024x1024`,
      `1536x1024`, `1024x1536`; `dall-e-3`: `1024x1024`, `1792x1024`,
      `1024x1792`, plus `"response_format": "b64_json"`) and the bearer key;
      the image is `data[0].b64_json`.
    - **Gemini:** `POST https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`
      (`x-goog-api-key`) with the prompt as one text part,
      `generationConfig.responseModalities = ["IMAGE"]`, and
      `generationConfig.imageConfig.aspectRatio` (`1:1`, `16:9`, `9:16`); the
      image is the first `candidates[0].content.parts[].inlineData`.
    - **OpenRouter:** `POST {openrouter_base_url}/chat/completions` with one
      user message, `"modalities": ["image", "text"]`, and
      `image_config.aspect_ratio`; the image is the first
      `choices[0].message.images[].image_url.url` `data:` URL.

    Calls use `httpx` with a 120 s timeout, no retries, and a 20 MB response
    cap. A non-2xx answer, a missing image, or an image that is not PNG,
    JPEG, or WebP (sniffed from its bytes, never taken from the provider)
    answers `ERROR: generate_image failed: <short reason>`; provider error
    text is cut to 200 characters and never contains the key.
  - **Storage:** files, never SQLite blobs:
    `<image_storage_dir>/<user_id>/<image_id>.<png|jpg|webp>`
    (`image_storage_dir` defaults to `<data dir>/images`), written with mode
    `0600`. One `generated_images` row per image (see the
    [data model spec](data-model-migrations.md)); the public id is 32
    URL-safe random characters (`secrets.token_urlsafe(24)`), which fits the
    block's `/api/images/<id>` pattern. The row is written in its own
    short-lived session (like other chat tool writes, JAN-189) with
    `conversation_id` set and `message_id` `NULL`; `chat.py` then sets
    `message_id` to the assistant message for the turn's image blocks before
    the reply is committed (before `__END__`).
  - **Quota:** `image_storage_limit_mb` (default 200 MB per user) caps the
    total `size_bytes` of a user's images. At or over it, `generate_image`
    answers `ERROR: ... image storage is full ...` before calling the
    provider; `image_store.store_image` checks again with the new image's
    size (`used + len(data) > limit` is refused, nothing written) under a
    per-user lock that also covers the write and the commit, so two turns of
    the same user cannot both pass the check (Janus is single-process).
    Deleting conversations frees space.
  - **Retention:** an image lives as long as its conversation.
    `ConversationService.delete_conversation_for_user` deletes the rows (ORM
    cascade from `Conversation`; SQLite does not enforce FK cascades) and
    then their files (`image_store.delete_files`, best effort, paths confined
    to the images directory).
  - **Prompt injection:** `generate_image` is in
    `INJECTION_ESCALATED_TOOL_NAMES`: once anything read in the turn looked
    like a prompt injection it answers the usual `BLOCKED: ... looked like a
    prompt injection` refusal, since an injected mail could otherwise spend
    money or push text to a provider. The toolbox checks the trace again
    itself.
  - **Chat only:** `images` is in `TASK_MODE_ALLOWED_TOOL_IDS["chat"]` only,
    in `tool_policy.LOCKED_UNATTENDED`, enabled only in `chat` task mode, and
    left out of `authorized_tool_ids`, so no trigger, automation, or delegated
    run is offered it.
  - **Audit:** each call is an ordinary `conversation_tool_calls` row
    (JAN-249). The event log gets an `INFO` `ImageGeneration` event per
    stored image and a `WARNING` one per refusal or provider failure, with
    `provider`, `model`, `image_id`, and `prompt_chars`, never the prompt.
- **Briefing/digest:** these are now the built-in trigger definitions above
  (JAN-231), executed as `automation` runs. `DashboardService.prepare_briefing_runtime_step()`
  still authorizes an audit-only `briefing`-mode step while building the
  briefing context; the `briefing`/`digest` task modes remain in the table
  for that audit path.
- **Mail ingestion (`processor.py`, JAN-164/JAN-232):** regex `Rule`s remain
  the fast path. When a `mail.received` definition is subscribed (see "Mail
  Ingestion" below) the email goes to that trigger run instead of the
  synchronous classifier. Otherwise the LLM judgment-call fallback
  additionally authorizes an audit-only `ingestion`-mode step via
  `EmailProcessor._prepare_ingestion_runtime_step()`; `classify_email()` itself
  is unchanged.
- **Triggers (`trigger_runtime.build_runtime_trigger_handler`, JAN-165/JAN-192):**
  the production `TriggerHandler` registered with
  `startup.configure_runtime_trigger_handler()`. For each trigger it builds a
  single-step `Plan` from the trigger's objective and payload `tool_ids`,
  authorizes that step through `RuntimeAdapter` bound to the trigger's
  `owner_id` (in the `automation` mode for a definition run, otherwise
  `trigger`), and then **executes** it via
  `agent_task_runner.run_agent_task()` -- a synchronous tool loop bounded both
  by iteration count (`AGENT_TASK_MAX_ITERATIONS`) and wall clock
  (`AGENT_TASK_MAX_SECONDS`, deliberately well under `DurableTriggerWorker`'s
  300s `lease_seconds`, since a run outliving its lease would be re-claimed
  and executed a second time concurrently) over the same
  `create_tool_calling_agent`/`AgentExecutor` machinery chat uses, without the
  streaming or the websocket binding. The system prompt is the user's Janus
  character plus the skill library (as in chat) followed by the trigger-run
  guidance (no human present; report `BLOCKED`/`ERROR` tool answers instead of
  retrying or pretending). The step's `result` records the automation's
  answer plus the trace of tools that actually ran and any refused/failed
  tool calls (see "Unattended Tool Switch and Refusals"). A provider exception propagates
  and fails the step; a tool exception does not (it answers `ERROR: ...` to
  the model and is listed under "Failed tool calls"). Hitting either ceiling raises
  `AgentTaskLimitError` rather than being recorded as a result: `AgentExecutor`
  *returns* a "Agent stopped due to …" placeholder instead of raising, and
  persisting that would present an unfinished run as a real answer.
- **Ad-hoc trigger safety fences (`trigger` mode; triggers write
  unsupervised since JAN-193).** Runs *without* a definition (queued through
  `POST /api/runtime/triggers/` or the agent's own `create_scheduled_trigger`)
  are offered every registry entry; which of those may run is the owner's
  unattended switch (JAN-255). With the defaults that is the
  `confirmation="never"` reads (`core`, `events`, `trigger-history`, `mail`,
  `calendar`, `jira`, `planner.local`, `web_search`, `knowledge`, `wiki`,
  `rules`, `docs`) **plus** the `confirmation="recommended"` toolboxes `mail.label`,
  `mail.processing`, `memory`, `topics`, `dashboard`, `delegation`, and
  `triggers`, so a trigger run can label mail, create/update memory facts,
  change topic and dashboard widget state, start delegated child tasks, and
  schedule bounded follow-ups with no human review (JAN-193, "accept
  unsupervised writes, narrowed"; JAN-274). Before JAN-255 a fixed `trigger`
  allowlist left out mail, local planner, rules, and web search. The fences:
    1. **Locked tools.** Any entry in `tool_policy.LOCKED_UNATTENDED` is
       never offered and is denied `not_allowlisted`. Only `triggers.propose`
       (drafting automations is chat-only, JAN-235), `ui_actions` (page
       actions need a human's open page, JAN-251), and `ui_blocks` (chat UI
       blocks need a web client to render them, JAN-252), `images` (image
       generation is chat-only, JAN-253), `ui_introspection` (reading
       the user's open page, JAN-283), and `character.propose` (proposing
       adaptive character-state changes, JAN-301) are locked; `events` became
       owner-scoped (JAN-272) and `triggers` got its chain guard (JAN-274),
       before which both were locked (JAN-184, JAN-195). The owner's
       *Unattended: Allow everything* mode (JAN-359) lifts the `images` lock
       (`tool_policy.ALLOW_ALL_UNLOCKS`) and the remote MCP lock; the others
       stay.
    2. **Per-mode tool withholding.** `TASK_MODE_WITHHELD_TOOL_NAMES["trigger"]`
       (derived from `TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY`) drops individual
       tools from the built toolset after authorization: `delete_fact`
       (memory write yes, delete no) and `set_dashboard_widget_prompt` /
       `clear_dashboard_widget_prompt` (a persisted prompt override durably
       steers later LLM runs). Chat and `automation` are not narrowed this
       way. The settings page lists these names under their toolbox
       (`withheld_unattended_tools`).
    3. **Confirmation gate = unattended switch.** The step is prepared with
       `confirmed_tool_ids` = the offered tools the owner allows and
       `blocked_tool_ids` = the rest of the offered tools. By default the
       `confirmation="required"` entries (`mail.send`, `planner.local.write`,
       `github`, `linear`, `web_fetch`, `wiki.propose`, `rules.write`, `skills`, `shell`) are off;
       `self_evolution` follows its own opt-in switch (off by default, JAN-320).
       Nothing is confirmed on the user's behalf for an ad-hoc or
       agent-scheduled run.
    4. **Delegation clamp.** A trigger's children run in the `delegated` mode
       and get only the read-only, unlocked part of the parent's grant
       (`tool_policy.delegable_tool_ids`), so delegation cannot pass the
       trigger's write access (including `triggers`) on. Depth (1) and
       fan-out (4) caps are unchanged.
    5. **Chain guard.** `create_scheduled_trigger` refuses chains that are too
       deep, too wide, too frequent, or due too soon (see "Unattended
       self-scheduling chain guard" above), and `list_recent_events` reads only
       the owner's rows.
  Auditability is after-the-fact: the fired-trigger history records which
  tools each run invoked, refused, or saw fail.
- **Automation (definition-run) fences (JAN-228/229/255).** A definition run
  is offered its own `tool_ids` plus `core` (passed as `allowed_tool_ids`),
  minus any locked entry (`GET /api/tools/registry` sets `locked_reason` on
  it, so the definition editor does not offer it; only `triggers.propose`,
  `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, and `character.propose` are locked, so a user may pick `triggers` or `events` for a definition).
  Connection state is checked by the adapter; the owner's unattended switch
  decides what is confirmed. A definition with `origin="user"` additionally
  counts the tools the user selected on it as confirmed, so a hand-written
  trigger may, for example, send mail even though `mail.send` is off by
  default. Built-in (`system`) definitions get no such exception; an
  `agent`-origin definition gets it only while it carries a valid owner
  approval (JAN-235, see "Agent-proposed definitions" above). The switch's defaults equal the pre-JAN-255 rule for saved
  automations, so their tools did not change. `skills` still needs an admin
  owner and is off by default.
- **Unattended Tool Switch and Refusals (JAN-229, JAN-255).** `tool_policy.py`
  + `models.ToolPolicy` (`tool_policies.unattended_allowed`, one optional
  override row per user and capability):
  - Default: *Allowed* unless the entry is `confirmation="required"`
    (`default_unattended_allowed`). `web_fetch` (JAN-329) is a read-only entry
    that is `required` precisely so it starts *Off*: the URL it opens is an
    outbound channel that could carry data out, so an unattended run gets it
    only when the owner switches it on or selects it on a trigger they wrote.
    It is not locked. `shell` (JAN-353) and `coding_agents` (JAN-403) are
    `required` for the same reason and are also not locked: the owner may
    switch them on for unattended runs or select them on a trigger they
    wrote, and they are never delegable (`operation="admin"`). A user may override any unlocked built-in
    entry on Settings → Tools & Connections (`PUT /api/tools/policies/{tool_id}`
    with `{"unattended_allowed": true|false|null}`; `null` resets to the
    default, a locked entry, i.e. `triggers.propose`, `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, or `character.propose`, answers `422`).
  - `SWITCH_GOVERNED` entries (`self_evolution`, JAN-320) have no override
    row: their allowance is the owner's self-evolution switch. While it is off
    the row carries a `locked_reason` (so no definition editor offers it) and
    the entry is removed from *confirmed* even for a definition that names it;
    `set_policy` always refuses it (`422`).
  - The switch covers every unattended run: ad-hoc triggers (`trigger`),
    definition runs including the built-ins (`automation`), and delegated
    child tasks (`delegated`). Chat does not use it: it asks per call
    for `confirmation="required"` tools instead (JAN-254).
  - `unattended_tool_sets(db, user_id, entries, task_mode=, narrowed_to=,
    definition=)` returns `(offered, confirmed)`. *offered* = every entry
    minus `LOCKED_UNATTENDED`, ∩ `narrowed_to` (a definition's tools plus
    `core`, or a delegated child's stored grant), and for `delegated` ∩
    `operation == "read"` entries. *confirmed* = offered ∩ (the entries the
    user allows ∪ a user-authored definition's `tool_ids` ∪ an agent
    definition's `tool_ids` while `trigger_approval.has_valid_approval()`
    holds, JAN-235). Any other task
    mode gets nothing. The adapter receives `allowed_tool_ids=offered`,
    `confirmed_tool_ids=confirmed`, and `blocked_tool_ids=offered − confirmed`,
    so an *Off* tool is gated whatever its registry confirmation level.
  - **Allow everything (JAN-359).** With the owner's
    `tool_policy_modes.unattended_mode = "allow_all"`, *offered* uses
    `unattended_locked_reason(..., allow_all=True)` (adds `images` and remote
    MCP ids) and *confirmed* is every offered entry except a switch-governed
    one whose own switch is off (`self_evolution`). The `delegated`
    read-only intersection, `narrowed_to`, withheld tool names, the trigger
    chain guard, and injection escalation all still apply.
  - Unattended steps are prepared with `refusal_stubs=True`: a requested tool
    denied for `confirmation_required`, `missing_capability`, or
    `disabled_by_context` (`STUBBED_DENIAL_REASONS`) is still offered to the
    model with its real name, description, and arguments, but calling it
    returns `BLOCKED: <tool> was not run because <reason>. Nothing was
    changed. See the docs: <docs_ref>.` (the docs suffix since JAN-297), logs a `WARNING` event, and is recorded in the step's
    `ToolCallLog`. `unknown_tool`/`not_allowlisted` denials (such as a
    locked or definition-excluded tool) get no stub.
  - With `guard_tool_errors=True` (unattended and chat), a tool that raises
    returns `ERROR: <tool> failed: <detail>` instead of aborting the run.
  - `unattended_execution.format_step_result` adds `Refused tool calls:` and
    `Failed tool calls:` lines to the persisted result, so a run can never
    silently claim an action it was refused.
  - Prompt-injection guard (JAN-127): unattended steps are prepared with
    `scan_objective=True`, so the objective's `<event_data>`/`<email_content>`/
    `<prepared_context>` blocks (the briefing's planner summary and the digest's
    topic threads are wrapped in the last) are scanned before the run starts, and every flagged tool's output
    is spotlighted and scanned. After a detection the run's consequential
    tools (`confirmation="required"` entries, including a user-authored
    definition's own picks, plus `INJECTION_ESCALATED_TOOL_NAMES`) answer
    `BLOCKED` with detail `injection_suspected`, and the result gains a
    `Suspected prompt injection:` line. See
    [Prompt-injection guardrails](llm-agent-safety.md#prompt-injection-guardrails-jan-127).
- **Trigger denial policy.** For an ad-hoc run, a denial is fatal only when the
  payload named specific `tool_ids` -- an automation that asked for something
  it may not have must not silently run with less. When no `tool_ids` were
  given, the request is a best-effort sweep of the offered tools, so denials (e.g.
  `calendar` with no calendar connected) are recorded in the step result
  instead of failing every trigger. For a **definition run, denials never fail
  the step up front**: the definition's tool list is the user's standing
  request, and the refusal stubs plus the recorded result are the audit
  trail. An explicit-request denial's terminality depends on whether the
  reason can change: `unknown_tool`, `not_allowlisted`, and
  `confirmation_required` are immutable and therefore terminal;
  `missing_capability` and `disabled_by_context` can resolve on a later
  attempt (e.g. a connection made), so those fail the step but use the retry
  budget instead (see `_TERMINAL_DENIAL_REASONS` in `unattended_execution.py`).
- **Trigger status follows the plan outcome (JAN-194).**
  `DurableTriggerWorker` decides a row's status by whether the handler raised,
  while `PlanDrivenRuntime` converts a step exception into `fail_step` and
  returns normally. `build_runtime_trigger_handler` therefore raises once the
  failed plan state has been persisted, so a failed run is never recorded
  `completed`, and the exception type says whether a retry can help:
  - `TerminalTriggerRunFailedError` (a `triggers.TerminalTriggerError`): an
    explicitly requested tool was denied for an immutable reason
    (`UnattendedStepDeniedError`, see the denial-policy note above), the owner
    no longer exists, or the payload is malformed
    (`InvalidTriggerPayloadError`). The worker, and `DurableTriggerDispatcher`,
    mark the row `exhausted` on that attempt instead of spending the retry
    budget on a failure that would repeat identically.
  - `TriggerRunFailedError`: anything else, such as a provider outage, a tool
    raising outside the guarded tool wrapper, `AgentTaskLimitError`, missing
    runtime configuration, or an explicitly requested tool denied for a reason
    that could resolve later (`missing_capability`, `disabled_by_context`).
    (The fresh-install "no agent definition is configured" failure, JAN-206,
    no longer exists: runs need no agent definition.) The row goes to
    `failed` and is retried with the configured backoff until
    `runtime_trigger_max_attempts`, then `exhausted`.

  In both cases the plan's failure reason becomes the row's `last_error`. A
  retry re-runs the task from a fresh plan: `DurablePlanRuntimeTriggerHandler`
  still treats a stored `COMPLETE` state as a read-only no-op, but re-runs a
  stored `FAILED` state and overwrites it under the usual revision check. The
  handler is only invoked again once the trigger boundary has decided to retry,
  and returning the stored failure would turn every retry into a no-op.
- **Fired-trigger history (JAN-192).** No new storage was added:
  `serialize_plan_state` already persists each step's `result`/`error` inside
  `PlanRuntimeState.state`, so `plan_state.load_plan_outcomes()` decodes it
  (one batched, owner-scoped query per page) for both surfaces --
  `agent_toolboxes.events.list_my_trigger_history` (the `trigger-history`
  entry; `result`, `step_error`)
  and `GET /api/runtime/triggers/` (the same two fields on
  `RuntimeTriggerResponse`, rendered by the Settings → Triggers
  `TriggerWidget`; since JAN-256 this endpoint returns a `RuntimeTriggerPage`
  (`items`, `total`, `limit` default 25 / max 200, `offset`, `has_more`),
  filterable by `status`, `kind`, `definition_id` (an id or `none` for runs
  without a definition), and a `start`/`end` `created_at` range, and each
  item carries `definition_name` / `definition_origin` from one batched,
  owner-scoped lookup; `GET /api/runtime/definitions/{id}/runs` returns the same
  shape per definition). `step_error` matters because `claim_trigger` clears
  `last_error` on every retry, so an exhausted trigger would otherwise show no
  reason at all. The raw ingress payload remains hidden on both surfaces.

## Brain-selected toolsets (JAN-300)

Plan-driven runtime scope item 4 (`src/janus/toolset_selection.py`). A step's
toolset is the policy-authorized set from "Runtime Adapter and Tool
Authorization", narrowed by a selection the brain makes per task.

- **Contract.** `RuntimeAdapter.prepare_step(toolset=ToolsetChoice)` takes the
  choice as untrusted input. Right after `ToolRegistry.authorize()` (before the
  confirmation gate), `toolset_selection.narrow()` keeps only authorized
  entries the choice names plus the anchors `core` and `docs`; every other id
  goes to `trimmed_tool_ids`. Denials and refusal stubs come from the full
  request and are unchanged; the confirmation gate, `approval_gate`,
  `TASK_MODE_WITHHELD_TOOL_NAMES`, the prompt-injection guard and
  `authorized_tool_ids` (and so a delegated child's grant) all see the narrowed
  entries. A choice with no authorized id, or `tool_ids=None`, keeps the full
  set. `PreparedStep.toolset` is the effective choice.
  `toolset_selection.authorized_candidates()` returns the candidates without
  building anything.
- **Selector.** `choose_toolset()` makes one utility-model call
  (`llm_selector`: system message `toolset_selection.SELECTOR_PROMPT_MARKER`
  + one line per non-anchor candidate + the task inside `<task>` tags, capped at
  4,000 characters) on a daemon thread, waited for at most
  `config.settings.tool_selection_timeout_seconds` (default 10). The call runs
  on its own utility-model client (`toolset_selection.utility_service`, the
  configured utility model with `LLMService(settings_override={"request_timeout": t})`:
  `timeout` and `max_retries=0` for OpenAI/OpenRouter/Gemini,
  `default_request_timeout` and `max_retries=0` for Anthropic, an httpx client
  timeout for Ollama), so a hung provider call really ends. At most
  `MAX_CONCURRENT_SELECTIONS` (4) calls are in flight; when every slot is busy
  the step does not queue but falls back at once ("the selector is busy").
  `parse_selection()` accepts only `{"tool_ids": [str, ...], "reason": str}`
  (one code fence tolerated, at most 40 ids, reason cleaned and capped at 200
  characters). Error, timeout, malformed or empty output, or no utility model
  -> `source="fallback"`, full set. Three or fewer non-anchor candidates ->
  `source="unnarrowed"`, no call. `tool_selection_enabled = false` skips
  selection everywhere (`PreparedStep.toolset` is `None`).
- **Where.** Chat (`websocket/chat._select_chat_toolset`, off the event loop
  via `asyncio.to_thread`) for every message; unattended steps
  (`unattended_execution.select_unattended_toolset`) only for `trigger`-mode
  runs without a definition and `delegated` children whose step names no
  tools. Definition runs (incl. built-ins and approved Janus proposals) and
  explicit `required_tool_ids` are not narrowed.
- **Persistence.** `models.RuntimeToolSelection` (`runtime_tool_selections`,
  unique `(user_id, task_id, step_id)`, migration `b8d4f2a6c1e9`) stores the
  effective choice through a short-lived session (never the chat socket's
  session, JAN-189; `services/toolset_store.py`). `toolset_store.load_for_step()` returns the row of the same step, else
  the task's latest row, as `source="persisted"`: a retried trigger step,
  a re-picked-up delegation, and a continuation step after a delegation wait
  reuse the first choice without a model call and are re-trimmed against the
  current policy. Deleting a conversation deletes its rows.
- **Trace.** Chat's `__TOOL_SELECTION__` adds `toolset` (`source`, `reason`,
  `tool_ids`, `trimmed`, `widened`, `authorized_count`, `narrowed`;
  `null` when selection is off); `features/ToolsetSelectionNote.tsx` shows it
  above the chat when `narrowed`. `format_step_result` adds
  `Tool selection: <source>, <n> of <m> offered (...); trimmed: ... Why: ...`
  to an unattended step's result; `runtime_task_service.parse_step_result`
  decodes it into `RuntimeStepTrace.tool_selection`, shown in the step trace
  on the Agent Tasks page (JAN-204).
- **Chat widening.** Chat carries the previous message's selected ids (when it
  was a real selection) into the next pick and every `widened_tool_ids` of the
  conversation. When something authorized was left out, chat appends
  `request_tools(tool_ids, reason)` (`build_widen_tool`): it accepts only
  authorized-but-unselected ids, stores them on the step's row, answers
  `BLOCKED` after a suspected prompt injection in the turn, and the additions
  are offered from the next message (a reply's toolset is fixed once the agent
  starts). Unattended runs never get it.
- **Cost.** Measured on a typical chat turn: 8,872 tokens of system prompt +
  tool schemas + message before, 1,568 after (63 -> 8 tools), plus one
  731-token selection request per message. See the feature spec for the setup.

## Mail Ingestion

- **Push, not polling:** each IMAP monitor holds a persistent connection with IDLE and fetches new messages as the server announces them; each new message goes through `workers.email.email_callback` → `EmailProcessor.process_email`.
- **Bounded initial mail sync:** Janus only ingests the most recent messages (default 100, `Settings.initial_sync_email_limit`) per IMAP account during the first historical sync; later syncs stay incremental.
- **Classification:** Every email is classified as `IMPORTANT`, `DIGEST`, `IGNORE`, or `SPAM`.
- **Important mail:** `IMPORTANT` mail requires immediate attention and is kept unread on the server.
- **Digest mail:** `DIGEST` mail is non-urgent and informative; it is marked read on the server and included in the daily digest.
- **Spam mail:** `SPAM` is unwanted communication and is moved to an ignored state.
- **Selective synchronization:** Only `DIGEST` emails are marked as read on the IMAP server. `IMPORTANT` emails remain unread until the user interacts with them.
- **Context awareness:** The LLM receives today's calendar and tasks as context when classifying emails.
- **Planner-aware ingestion:** Planner suggestion extraction should consider a bounded recent-mail window, tracked topics, active local tasks/events, pending planner suggestions, and imported planner context.
- **Regex rules first:** user `Rule`s always run first. A rule that files the email as `SPAM`/`IGNORE` is final; nothing downstream sees it.
- **Event-driven processing (`mail.received`, JAN-232):** when the trigger worker is enabled (`runtime_trigger_worker_enabled`) and the owner has at least one enabled `mail.received` event definition (the built-in **Mail processing** is seeded enabled) that would accept this email (JAN-363: `EmailProcessor._mail_event_owner` asks `trigger_event_filters.admit(..., record=False)` with the payload minus `email_id`; mail no definition accepts takes the synchronous path below instead of waiting for a run that never comes; because that pre-check is a read, a concurrent email can still use up the last slot, so when `emit_event()` then enqueues no run the stored row is removed and the email takes the synchronous path too), `EmailProcessor._store_for_trigger_processing` stores the email — with the rule's priority when a rule matched, otherwise a provisional `DIGEST` with reasoning `"Waiting for Janus mail processing"` and `LOW` confidence — and calls `trigger_definition_service.emit_event()`, which enqueues one run per subscribed definition. The event data (email id, account, sender, subject, received time, rule priority, and at most 4,000 characters of body) is embedded as untrusted `<event_data>` context. A provisional email is **not** marked read on the IMAP server until Janus decides (`email_callback` skips it); when `set_email_priority` then files it as `DIGEST`, the tool marks it read through `imap_service.mark_message_read()` — a short-lived connection of its own, because the account's IDLE connection lives on another thread — which leaves the mailbox alone when its UIDVALIDITY no longer matches the stored one.
- **Mail-processing tools (`mail.processing`, `agent_toolboxes/mail_processing.py`):** `set_email_priority` (records priority, reasoning, and confidence; sends the IMPORTANT notification once, subject to notification filters), `create_reminder`, `track_email_topic`, and `suggest_planner_item` (pending suggestions only). They replace the one-shot JSON extractors on this path and keep their persistence and dedupe rules. All writes stay Janus-local and owner-scoped, except that filing a provisional email as `DIGEST` marks it read on the server (the synchronous path's behavior); nothing sends mail.
- **Synchronous fallback:** otherwise (worker disabled, no enabled `mail.received` definition) the constrained JSON classifier plus the reminder/topic/planner JSON extractors run inline, guided by the user's character constitution (JAN-201), exactly as before. There is no per-mailbox "processing agent" setting any more.
- **Cost:** with the event path active, every new email — including each message of an account's initial sync window — is one full agent run.
- **Manual re-classification:** Users can manually change the category of an email via the UI, from the Emails list or an open email (`PATCH /api/emails/{id}/priority`). The inbox list (`GET /api/emails/`) leaves `SPAM` out; `?priority=` lists one category, which is how the Emails page's spam filter reads the spam folder (JAN-219).
- **Classification confidence:** Every Janus-assigned email category must store a confidence estimate (`high`, `medium`, or `low`) alongside the reasoning.
- **AI-driven reasoning for manual tags:** When a user tags an email as `IMPORTANT`, the LLM generates reasoning by analyzing the email body against the user's schedule, tasks, and existing important rules.
- **Learning from corrections:** When a user re-labels an email, Janus applies the correction and saves it right away as an active, owner-scoped rule for similar messages (an exact-sender rule when the sender is known, otherwise a subject rule; JAN-74, JAN-287). This is never silent: the email's reasoning says that a rule was saved, and the rule shows under Memory → Rules, where the user can reorder or delete it.
- **Clarification/review queue:** Rule candidates that need the user's decision live in a persistent clarification queue (`EmailClarificationRequest`, Memory → Rules → *Clarify misunderstandings*). Approving one creates (or re-points) its suggested rule; dismissing one creates nothing. A manual correction does not queue an item, because it is already an explicit decision.
- **Explicit approval:** Users can approve or dismiss clarification items. Approval creates the suggested rule; dismissal keeps the correction on the one email only.

## WhatsApp and planner trigger events

- **WhatsApp (JAN-361).** `NeonizeConnector._ingest` publishes (through
  `whatsapp_trigger_events.publish`) after it has
  committed the message, and only when all three hold: the message came from
  a live `MessageEv` (`live=True`; `HistorySyncEv` backfill never emits), it
  is `INCOMING` (the account's own messages, including `whatsapp.send`
  replies, never emit, so a reaction cannot trigger itself), and the row is
  new (`WhatsAppService.message_exists` is checked before the upsert, so a
  re-delivery or a backfilled message arriving live again does not emit). A
  direct chat emits `whatsapp.direct_message_received`; a group chat emits
  `whatsapp.group_message_received`, plus `whatsapp.group_mention` when the
  message's `contextInfo` (`mentionedJID` or the quoted `participant`, on any
  sub-message, read by `whatsapp_trigger_events.referenced_users`) names one of the account's own
  user ids. The connector reads those (`get_me()` JID and LID user parts) on
  connect; if that fails, mention events stay off and group events still
  fire. Event id: `<account_id>:<provider_message_id>`. A publish failure is
  logged and never loses the stored message, and one failing event does not
  stop the others for that message. Never emitted: chats on `@broadcast`
  (Status updates, broadcast lists) or `@newsletter` (channels), and messages
  without content of their own (`whatsapp_trigger_events.has_content`:
  reactions, edits, deletions/protocol messages, poll votes, pins). A reply
  counts as a mention only when it quotes a message (`contextInfo.stanzaID`).
  A message handled before `ConnectedEv` loads the own identity itself.
- **Planner (JAN-362).** `PlannerService.sync_all` compares each synced task
  (`title`, `status`, `due_date`) and calendar event (`summary`,
  `start_time`, `end_time`, `location`) with its stored row before
  overwriting it (`planner_trigger_events.snapshot`/`changes`/`apply`, one
  field list for comparing and writing; enums by value). Before comparing
  and storing, `planner_trigger_events.to_utc` converts every synced
  datetime to naive UTC (JAN-394): connectors return aware datetimes in the
  event's own offset, and SQLite would otherwise keep their wall time and
  drop the offset. Times in the event payload and `changes` text carry an
  explicit `+00:00` and records new or changed items as
  `PendingPlannerEvent`s. `planner_trigger_events.publish` emits them right
  after the account's sync committed (before the status bookkeeping), so a
  failed sync emits nothing and a failing status log cannot lose them. An
  account whose `last_sync_at` is still `NULL` is on its baseline sync and
  records nothing. Event id: `<account_id>:<external_id>`, plus, for
  `*_changed`, the sync's start time and a hash of the `changes` text. A
  change is detected only by the sync that stores it, so it fires once, and a
  transition that repeats later (Todo -> Done, back, Done again) is a new
  occurrence and fires again. Known limits: the calendar window is today and
  tomorrow, so `calendar_event_created` means "first seen in that window";
  the sync never deletes rows, so deletions and cancellations emit nothing.

## WhatsApp Connector Runtime

- **Persistent per-account connection:** Each enabled `WhatsAppAccount` gets one supervised daemon thread (`whatsapp:{account.id}`) that opens a Neonize client and blocks on its connection for the account's lifetime, mirroring the IMAP monitor's one-thread-per-account model rather than a shared poll loop.
- **Event-driven, not pull-based:** Neonize exposes no fetch-by-date-range API; messages arrive only as live events (`MessageEv`) or a one-time post-pairing backlog push (`HistorySyncEv`). There is no periodic "sync recent history" job — ingestion happens as events are handled.
- **Session-per-callback:** Every event handler opens and closes its own database session; no session is shared between the connector's blocking connect call and its event callbacks.
- **Pairing runs separately from the durable connection:** Live QR pairing during setup runs a short-lived, unsupervised connector instance scoped to one `WhatsAppSetupSession`; on success it disconnects and hands off to the durable per-account worker started when setup completes, rather than keeping the pairing-time connection alive.
- **Setup status follows the pairing thread (JAN-223):** the pairing thread writes only the session's `pairing_state` (`code_ready`, `waiting`, `paired`, `failed`). While a session's `setup_status` is `pairing`, every poll (`GET /api/whatsapp/setup-sessions/{id}`) derives the matching `setup_status` from it, including the thread's final state: `paired` makes the session `ready` (so **Finish setup** enables) and `failed` makes it `failed` with `last_error` set.
- **Live account status:** the connector thread's status writes (`connected`, `degraded`, `disconnected`, `last_sync_error`) publish the `whatsapp` domain change like its message writes, so an open Settings → WhatsApp tab shows them without a reload.
- **No forced resync:** Because there is no pull API, the manual "restart connector" action only re-establishes the persistent connection; it cannot force redelivery of historical messages already missed.
- **Crash handling:** A crashed connector thread is restarted by the standard worker-manager backoff policy and marks the account `degraded` with the captured error, mirroring IMAP monitor crash handling.
- **Permanent native-load failure (JAN-172):** Importing `neonize` loads a Go shared library, and a failed load (e.g. host glibc older than the library needs) cannot be retried safely: Python drops the half-imported package, so a re-import re-runs the native load, which has segfaulted the whole process. `whatsapp_neonize.neonize_import_error()` therefore probes the import at most once per process and caches the result; the connector worker, the client factory, and the setup-time capability/connection checks all read that cache. When it reports a failure, the worker raises `worker_manager.PermanentWorkerError`, which the supervisor records like a crash (account `degraded`, `last_sync_error` = "WhatsApp is not supported on this host: …") but never restarts (`status()` reports `permanently_failed`). A manual restart or re-enable reads the same cached failure without touching the library; recovering needs a fixed host runtime and a Janus restart. Neonize is the optional `whatsapp` extra: when it (or Pillow, which it imports) is not installed, the probe records a `ModuleNotFoundError` as "WhatsApp support is not installed: … Install the optional 'whatsapp' extra" instead, handled the same way.

## Briefing and Chat Runtime

- **Briefing:** The built-in **Morning briefing** trigger (see "Built-in Triggers") produces the summary of the user's day, including events, tasks, and emails. The dashboard shows today's completed run and re-runs it on refresh; `janus brief` runs it inline and prints the output.
- **Topic-aware briefing:** Morning briefings must surface durable tracked topics as first-class threads alongside schedule, reminders, and tasks.
- **Configurable briefing context:** The briefing's main prompt is its definition's `objective` (editable on Settings → Triggers). The dashboard briefing widget's extra instructions and optional email window are appended as context by the `prepare` hook on every run.
- **Bounded email windows:** Daily briefings may optionally include summarized email context for a bounded preset window such as today, yesterday, or the last 24 hours.
- **Interactive chat:** `/ws/chat` provides a stateful WebSocket connection for follow-up questions about user data. There is no agent selection (see "Runtime Adapter and Tool Authorization" above); a conversation resumes by `conversation_id` alone and always speaks as the user's character.
- **Chat and utility models (JAN-233):** each connection builds the chat model from `settings.chat_llm_*` and a separate utility model from `settings.utility_llm_*` for the per-turn conversation title and memory suggestion (memory compaction in `MemoryService` uses the utility model too). An unusable explicit chat model closes the socket with the error; an unusable utility model is logged as an `ERROR` event and those helpers are skipped. Neither falls back to the default model. Delegations spawned from chat record the chat model choice.
- **Rich context:** Chat LLM context includes planner summary and recent emails with sender, subject, and snippet.
- **Topic-aware digest:** The built-in **Afternoon digest** trigger prefers structured topic groupings when linked tracked topics are available, while preserving an other-updates bucket for ungrouped mail; its `finalize` hook sends the desktop digest notification. `janus digest` runs it inline (emails since the last digest; the old `--hours` option is gone).
- **Digest planner upkeep:** The digest run may propose planner items with `suggest_planner_item`, which only creates pending suggestions that stay in the review queue until the user approves them.

## Live UI Refresh From Background Work (JAN-104)

- Trigger runs (claim, completion and failure are ORM bulk updates of `runtime_triggers`), Janus follow-ups and their one-time definitions (JAN-257), delegated tasks, mail ingestion, planner sync, the WhatsApp connector, knowledge extraction (including its token-usage and chunk bookkeeping) and wiki synthesis all write through SQLAlchemy sessions, so their commits publish the owner's domain changes (`trigger_runs`, `trigger_definitions`, `emails`, `planner`, `whatsapp`, `knowledge`, `wiki`, `conversations` for a Janus-started conversation (JAN-319), ...) to `/ws/changes` without any worker-specific code. Publishing is a thread-safe hand-off onto the subscribing socket's event loop; a worker thread never blocks on a browser. See "Domain-Change WebSocket" in [backend-api.md](backend-api.md#domain-change-websocket-jan-104).

## Reminder Extraction

- **Reminder extraction:** Janus analyzes recent emails for concrete event reminders and preparation work that should happen before an event or deadline.
- **Persistent reminder memory:** Derived reminders are stored as active memory in the database so they survive restarts and repeated briefings.
- **Calendar-style representation:** Active reminders are mirrored into Janus-managed calendar-style entries so planner summaries can surface them alongside normal events.
- **Six-week preparation horizon:** Morning briefing and related context should consider the next 4-6 weeks of planner events when deciding what needs preparation now.
- **Non-blocking AI failure:** Reminder extraction must never prevent email ingestion; LLM failures are logged and the mail pipeline continues.
- **Provider scope:** V1 uses Janus-managed local reminder entries rather than writing back to external planner providers.

## Structured Topic Tracking

- **Incoming topic extraction:** After Janus stores and prioritizes a non-spam/non-ignored email, it may derive zero or more user-scoped tracked topics without blocking ingestion if extraction fails.
- **Durable topic model:** Tracked topics persist separately from reminders and durable memory facts, including lifecycle state, confidence, supporting evidence, and user curation signals.
- **Manual topic seeding:** Users can create tracked topics directly with a title, brief description, and marker keywords.
- **Shared topic record:** User-created manual topics use the same durable tracked-topic model as auto-discovered topics.
- **Marker-assisted matching:** Mail ingestion should attempt lightweight keyword-based matching against configured marker keywords.
- **Corroborated promotion:** Auto-discovered topics start as candidates and must be mentioned by a configurable number of distinct emails before they are promoted and surfaced downstream; manual creation or user curation confirms a topic immediately.
- **Configurable scope:** Topic tracking must support admin/default controls and per-user overrides for enablement, extraction scope, minimum confidence, confirmation-mention threshold, and downstream topic count.
- **Shared downstream context:** Dashboard summaries, planner/briefing context, authenticated topic APIs, and topic-enabled chat tools must read the same durable topic model.
- **Manual topic creation:** Topic-enabled chat agents must be able to create or confirm manual tracked topics directly instead of misrouting that intent into durable memory facts.
- **Topic focus dashboard widget:** The dashboard must expose a Topic Focus widget that reads the shared active-topic summary and participates in per-user widget visibility/collapse preferences.

## Knowledge Index Worker (JAN-241)

- **What it does:** `services/knowledge_index.knowledge_index_loop` runs as the supervised `knowledge-index` thread (`worker_manager`, restart policy on), started by `startup.ensure_runtime_initialized()` when `knowledge_index_worker_enabled` is true (the default). Every 60 s it first runs one ingestion pass (`knowledge_sources.run_tick`, below), then calls `sweep()`: for facts, then topics, then chunks, it embeds up to 10 batches of 32 missing or stale rows (one `embed_documents` call per batch). A row whose text hash is unchanged only has its `source_updated_at` refreshed, with no provider call. It is a separate thread rather than part of the trigger worker's scheduler tick, so slow embedding calls never delay trigger claims.
- **Nothing to do without a model or vector index:** with `settings.embedding_provider` unset, or when `VectorIndex` is unavailable (PostgreSQL, or no `enable_load_extension`/sqlite-vec), the sweep returns immediately and never calls a provider.
- **Failures:** a provider error stops that sweep; the last error is kept in memory for system status and logged once per new message. The next tick retries.
- **Inline path:** `MemoryService.create_fact`/`update_fact`/`group_facts` and `TopicService.create_topic`/`update_topic` call `embed_owner()` after their commit. Every other writer (compaction, email topic extraction, snoozes) is covered by the sweep; the keyword index is kept current for every writer by the `after_flush` hook, inside the writer's own transaction.
- **Startup check:** `startup.prepare_knowledge_search()` probes extension loading, sqlite-vec, and FTS5 once per process, logs a warning for anything missing, and creates and backfills missing FTS5 tables. It is never fatal.
- **CLI:** `janus knowledge reembed [--force]` runs the same sweep until nothing is stale; `--force` also rebuilds the keyword tables and recomputes every vector.
- **Ingestion (JAN-242):** each tick processes at most 50 sources. First, staged documents: an attachment (staged by `processor.py` after an email is stored, unless it was filed as SPAM/IGNORE) or an upload that is still `pending` is parsed with markitdown (`knowledge_parsing.py`; without the optional `documents` extra only TXT/MD/CSV are accepted and other documents are skipped at staging); a parse error leaves it pending for one retry, then marks it `failed` and drops its bytes. Mail still waiting for its `mail.received` run (provisional DIGEST, reasoning "Waiting for Janus mail processing") and its attachments are held back until Janus classifies it, so mail that ends up SPAM or IGNORE is never parsed or sent to an embedding provider; if that run never completes, the mail stays out of the knowledge store. Then discovery (`knowledge_discovery.py`): mail (not SPAM/IGNORE), chat conversations, WhatsApp chats, planner tasks, and calendar events with no source yet or changed since their source was built (by `updated_at`, or by text hash for calendar events, which sync edits in place). This covers every write path without ORM hooks. Last, sources whose owner is gone, or whose mail is now SPAM/IGNORE, are deleted with their chunks and vectors. A conversation, WhatsApp chat, planner task, or calendar event owner must match on both id and user, because SQLite reuses a freed id and an id-only match could keep a deleted row's source alive under another user's new row; deleting a local planner task (JAN-221) also deletes its source directly, in the same transaction. Chunking (`knowledge_chunking.py`) is structure-aware, about 1,500 characters with 200 characters of overlap, and keeps every chunk whose ordinal and text are unchanged, so only new text is re-embedded. Staging stops (new files are recorded as `skipped`, "staging backlog full") once 200 MB of bytes wait. Ingestion runs even without an embedding model (chunks are then stored but not embedded); searching chunks arrives in phase 3 (JAN-243).
- **Backfill:** `janus knowledge backfill` runs ingestion passes until nothing is left, for a first import after upgrading.
- **Entity embeddings (JAN-243):** the sweep embeds entities too (`owner_type="entity"`, name and description), after facts, topics, and chunks; deleting or merging an entity deletes its vector.
- **Graph cleanup (JAN-243):** when ingestion rewrites or deletes a chunk, `knowledge_graph.forget_chunks` removes its mentions and relation evidence, deletes relations left without evidence and entities left without mentions or relations, and the rewritten chunk becomes pending for the Knowledge extraction trigger again (`extracted_hash` no longer matches).

## Delegation Runtime (JAN-200)

Plan-driven delegation is the only way one Janus step hands work to another
(agent-to-agent handoffs were removed in JAN-227); the full design and safety
fences are in the
[plan-driven runtime spec](../feature-specs/plan-driven-janus-runtime.md#plan-driven-delegation-updated-2026-09-24).

- **Model inheritance (JAN-233):** a child runs on the parent step's model choice, recorded on the delegation row (`llm_provider`/`llm_model`).
- **Child tasks, not named agents:** A chat or trigger step's `delegate_task` tool (triggers since JAN-193) creates a `runtime_delegations` row linking the parent task to a new child task, which runs as its own single-step `PlanDrivenRuntime` plan in the `delegated` task mode and persists its `PlanRuntimeState` under the child task ID.
- **Background execution:** Children run on a bounded process-local thread pool (`MAX_ACTIVE_DELEGATIONS_PER_OWNER` workers) behind `background_tasks.ThreadPoolTaskRunner`, through `unattended_execution.execute_unattended_step`, the same authorize-and-execute path as trigger-driven work — including its system prompt (the user's character plus the skill library, then run guidance), tool policy, refusal stubs, and tool-error guard.
- **Bounded reach:** A child may only use tools that were authorized for the parent step, are read-only (`operation="read"`), and are not locked (`tool_policy.delegable_tool_ids`; with the defaults that adds `mail`, `planner.local`, `web_search`, and `rules` reads to the pre-JAN-255 set, and the owner-scoped `events` read since JAN-272; `web_fetch` is read-only and so delegable, but *Off* unless the owner allows it, JAN-329; `triggers` is not read-only and is never delegable), and is confirmed only for what the owner allows unattended (never for a definition's selection). It cannot delegate further (depth 1), and an owner may have at most four delegations queued or running.
- **Result consumption:** A chat parent polls `get_delegated_task` / `list_delegated_tasks`. A trigger parent may instead call `wait_for_delegated_tasks` (JAN-207); the human-facing view is JAN-208, below.
- **Waiting parents (JAN-207):** `delegation_wait.py` owns the wait. The trigger handler (`trigger_runtime._execute_authorized_step`) opens a per-step wait sink (`delegation_wait.accepting_waits`, a context variable) around the agent loop, but only for a run's first step and never for a built-in run; the `wait_for_delegated_tasks` tool, built only when `ToolBuildContext.trigger_id` is set, adds IDs of delegations this same step created to it. If the sink is not empty when the step finishes, the step executor returns `agent_runtime.StepAwait`, and `PlanDrivenRuntime` calls `PlanGraph.await_external`: the step completes, one `waiting` step per delegation and one continuation step that depends on them are appended, and the graph enters `WAIT`. The worker parks the row `waiting`. `delegation_wait.release_waiting_parents` moves a `waiting` row to `queued` once every awaited delegation is terminal; it runs after each child finishes (for that parent) and on every worker poll (for everyone). On the resumed claim, `DurablePlanRuntimeTriggerHandler`'s `before_resume` hook (`delegation_wait.resolve_awaited_delegations`) resolves each await step from its delegation row (`PlanGraph.resolve_external`) and rewrites the continuation objective (`PlanGraph.revise_pending_step`) from the original objective, the parked answer, and the children's results or errors; then `PlanDrivenRuntime.resume()` runs the continuation. A child's failure is reported to the continuation, never turned into a parent failure. The continuation gets no wait sink, so a run waits at most once.
- **Durable re-pickup (JAN-207):** `delegation_service.expire_stale_delegations` (now also callable for all owners) treats a `queued`/`running` row past its lease (from `claimed_at` when running, else from `requeued_at` or `created_at`) as interrupted: with `requeue_count` 0 it goes back to `queued` (`requeue_count` 1, `requeued_at` now, claim token and `claimed_at` cleared), otherwise it fails with a reason naming the second interruption. Clearing the token is the claim-token handover: the old executor's `complete_delegation`/`fail_delegation` no longer match. `delegation_runtime.run_delegation_maintenance`, called from the trigger worker's scheduler tick (`startup._run_trigger_scheduler_tick`) on every poll, runs that rule for all owners, submits every claimable `queued` row through the runner (`delegation_runtime.dispatch_delegation`, deduplicated by the key `delegation:<id>`), and releases waiting parents. Owner reads still apply the rule lazily. A re-run is the same row, so its stored clamped allowlist, depth, `delegated` mode with no pre-confirmed tools, and the active cap all hold. Columns `requeue_count` and `requeued_at` were added by migration `f2a7c4e9b1d3`.
- **Human-facing view (JAN-208):** `GET /api/conversations/{id}/delegations` (`ConversationService.list_delegations_for_user`) pages the delegations whose `conversation_id` is that conversation, newest first, for its owner only (another user's conversation answers 404, and rows are filtered on `user_id` too). It applies the stale-lease rule first (requeue once, then fail; JAN-207), like the agent-facing tools, and never returns the claim token or the parent/child task ids. The chat's `DelegatedTasksPanel` shows them; see [frontend.md](frontend.md).

### Background task runner (JAN-138)

`background_tasks.py` is the minimal submit/status abstraction over thread
workers that the durable re-pickup needs, and nothing more:

- `BackgroundTaskRunner` (a `Protocol`): `submit(name, fn, *, key=None) ->
  TaskHandle` and `status(key) -> TaskStatus | None`. A `key` makes a
  submission idempotent while the earlier one is still pending or running;
  `status` reports `pending`, `running`, `succeeded`, or `failed` for the
  latest submission under a key.
- `ThreadPoolTaskRunner` is the only implementation: a bounded
  `ThreadPoolExecutor` that logs (never re-raises) a job's exception and
  marks it `failed`. The delegation pool was migrated onto it
  (`delegation_runtime.DELEGATION_RUNNER`, also the test seam).
- It is **not a distributed or durable queue.** It holds nothing across a
  restart. Durability comes from the rows each job works on plus the
  `DurableTriggerWorker` poll (itself a `WorkerManager`-supervised thread)
  that re-submits whatever a restart left behind; long-lived supervised
  threads stay on `WorkerManager`. A separate durable job runner was
  deliberately not built.

## Removed: Legacy Agent Definitions (JAN-169, JAN-227)

The per-conversation `AgentDefinition` model is gone. Migration `d5e8f1a2b3c4`
drops `agent_definitions`, the `agent_definition_skills` join table,
`agent_handoff_runs`, and every column that referenced `agent_definitions`
(`settings.mail_ingestion_agent_definition_id`,
`dashboard_widget_preferences.briefing_agent_definition_id`,
`agent_memory_facts.source_agent_definition_id`); its downgrade restores the
empty schema only. `agent_skills` stays as the flat skill library.

Removed with it: `/api/agents`, the skill attach/detach routes under
`/api/agents/{id}/skills`, `GET /api/conversations/{id}/handoffs`,
`services/agent_handoff_service.py`, the `handoffs` and `agent-management`
toolboxes (`handoff_to_agent`, `list_available_agents`, agent-definition CRUD
and merge tools), `runtime_adapter.resolve_runtime_agent_definition()`,
`agent_runtime.build_agent_system_prompt()`, `ToolBuildContext.agent_def` /
`allow_handoffs`, and the `disabled_by_agent_context` denial reason (now
`disabled_by_context`). The skill-library tools survive as the admin-only
`skills` toolbox (`agent_toolboxes/skills.py`). Chat and every unattended run
need no agent definition; a fresh install can chat and run triggers
immediately.

The removal is complete: no code path, route, toolbox, or schema element refers
to agent definitions any more (only historical Alembic revisions do). The
JAN-169 cluster's additive follow-up work has shipped too: the Tasks/Plans
surface (JAN-204) is the Agent Tasks page (`services/runtime_task_service.py`,
`GET /api/runtime/tasks/`). Delegation wait/resume and durable re-pickup shipped in JAN-207, the
human-facing delegation view in JAN-208, brain-selected toolsets in JAN-300 and
the bounded adaptive character state in JAN-301. JAN-169 and the
[plan-driven runtime spec](../feature-specs/plan-driven-janus-runtime.md) are
closed out (JAN-205); the optional items left for later (same-reply tool
widening, showing a message's tool selection after a reload, persisting the
trait baselines) are listed under
[Future follow-ups](../feature-specs/plan-driven-janus-runtime.md#future-follow-ups-not-blockers).
