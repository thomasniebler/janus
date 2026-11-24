# Plan-driven Janus runtime

**Status:** Shipped (every scope item has landed: the single-agent runtime, trigger-first execution, the removal of the legacy agent-definition model, delegation wait/resume with durable re-pickup (JAN-207), the delegation REST and chat UI (JAN-208), the Agent Tasks page (JAN-204), brain-selected task-scoped toolsets (scope item 4, JAN-300), and the bounded adaptive character state (scope item 5, JAN-301); the close-out docs pass, JAN-205, is done. Optional improvements are listed under "Future follow-ups" and do not block this spec)
**Linear:** JAN-157 (architecture migration, tracks this spec's scope); JAN-176 (shipped trigger-ingress follow-up); JAN-192 (shipped trigger-driven execution and fired-trigger history); JAN-169 (shipped: legacy `AgentDefinition` runtime and schema removed; all sub-issues, JAN-200 to JAN-205, JAN-207, JAN-208 and JAN-209, done; the umbrella is Done in Linear); JAN-205 (done: close-out docs pass for this spec); JAN-204 (shipped: Agent Tasks page); JAN-207 (shipped: delegation wait/resume and durable re-pickup, folding in the remaining scope of JAN-138); JAN-208 (shipped: delegation REST and chat UI); JAN-301 (shipped: bounded adaptive character state with audit and rollback, scope item 5); JAN-300 (shipped: brain-selected, task-scoped toolsets, scope item 4); JAN-201 (ungated memory/topic/planner tools; character-sourced prompts and flat skills); JAN-193 (shipped: trigger runs may use the `confirmation="recommended"` memory/topics/dashboard/delegation tools, unsupervised); JAN-194 (shipped: a failed trigger run is recorded `failed`/`exhausted`, never `completed`); JAN-195 (shipped: owner-scoped `trigger-history` split out of `events`; the system event log is chat-only); JAN-226 (trigger-first single agent, parent of the shipped JAN-227–JAN-232): JAN-227 (agent definitions, skill assignment, handoffs removed), JAN-228 (trigger definitions), JAN-229 (tool policy and documented refusals), JAN-230 (character additional/memory instructions), JAN-231 (built-in briefing/digest/mail triggers), JAN-232 (`mail.received` event processing)
**Depends on:** nothing open. The durable trigger worker (`runtime_trigger_worker_enabled`, on by default) and its application-provided handler; recurring schedules and the `mail.received` event exist as trigger definitions (JAN-228/JAN-232). Other external event providers are a future follow-up, not a dependency.

## Goal

Move Janus from several independently configured runtime agents toward one
plan-driven Janus brain that creates task contexts, discovers tools from a
central registry, adapts its character within an autonomy envelope, and
resumes work from scheduled or event-based triggers.

## Scope

The runtime will eventually provide:

1. one general reasoning loop with task-specific prompts and constraints,
2. durable plans and resumable plan steps,
3. a central registry for tool identity, metadata, factories, documentation,
   and permission requirements,
4. dynamic, task-scoped toolsets selected by the brain and approved by policy
   (shipped, JAN-300: see "Brain-selected toolsets" below),
5. a stable character constitution plus bounded adaptive character state,
6. one trigger ingress for manual, scheduled, and external events,
7. frontend views for tasks, plans, tool availability, approvals, triggers, and
   character controls.

## Trigger-first single agent (updated 2026-09-27, JAN-226)

This increment finishes the cutover. Where the historical notes below still
mention `AgentDefinition`, `resolve_runtime_agent_definition()`,
`build_agent_system_prompt()`, handoffs, agent-management tools, or
`confirmed_tool_ids=frozenset()`, they describe the state *before* this
increment and are superseded by this section.

- **Legacy model removed (JAN-169, JAN-227).** `AgentDefinition`, skill
  assignment, `AgentHandoffRun`, `/api/agents`, the handoff and
  agent-management toolboxes, and `GET /api/conversations/{id}/handoffs` are
  gone (migration `d5e8f1a2b3c4`). No call site resolves a capability-profile
  row any more; `enabled_when` reads only real connection state, admin status,
  and plan-step identity. `events` and `trigger-history` are ungated; `triggers` is always granted;
  `rules` is granted with a connected mailbox. The skill-library tools remain
  as the admin-only `skills` toolbox. Chat, every trigger run, and every
  delegated child render the same prompt: the user's character (incl. its
  additional instructions, JAN-230) plus the full skill library, with
  run guidance appended for unattended runs.
- **Trigger definitions (JAN-228).** `TriggerDefinition` stores an objective,
  pre-selected `tool_ids`, and a manual / recurring-schedule (daily or
  interval, IANA timezone) / event activation. The durable worker's new
  `scheduler` hook calls `run_due_schedules()` each poll (only the latest
  missed occurrence runs after downtime), `emit_event()` fans an event out to
  subscribed definitions, and every activation is an ordinary `RuntimeTrigger`
  row carrying `definition_id` and a raw `output`. Definition runs use the new
  `automation` task mode, fenced by the definition's tools plus `core` (at
  the time never the locked `triggers` and `events`; unlocked by JAN-272/274).
- **Tool policy and documented refusals (JAN-229).** A per-user "block without
  confirmation" policy (`tool_policy.py`, `tool_policies`) replaces the
  hard-coded `confirmed_tool_ids=frozenset()` for unattended runs; the default
  blocks `confirmation="required"` entries. Blocked tools are still offered,
  answer `BLOCKED`, are logged, and are listed as refused in the run result;
  raising tools answer `ERROR` (also in chat). Tools the user selected on a
  definition they authored count as confirmed; system and agent-authored
  definitions and ad-hoc agent triggers get no such exception. The registry
  split read and write capabilities (`mail`/`mail.label`/`mail.send`/`mail.processing`,
  `planner.local`/`planner.local.write`, `rules`/`rules.write`).
  *(Replaced by the per-tool unattended switch, JAN-255: one "Unattended:
  Allowed / Off" switch per tool, `tool_policies.unattended_allowed`, with the
  same default, now also deciding what ad-hoc triggers and delegated tasks
  may use; see [Tool permissions](tool-permissions.md).)*
- **Built-in triggers (JAN-231).** Morning briefing, Afternoon digest, and
  Mail processing are seeded per user with `prepare`/`finalize` hooks. The
  old briefing/digest generation paths and the briefing cache are removed;
  `/ws/briefing`, `get_daily_briefing`, `janus brief`, and `janus digest` read
  or run the built-ins (`trigger_runtime.run_definition_inline`).
- **`mail.received` (JAN-232).** After regex rules, a new email the owner has
  subscribed to is stored with a provisional priority and processed by an
  unattended run using the `mail.processing` tools instead of the JSON
  extractors; the synchronous classifier remains the fallback.

The authoritative behavior is in
[Background Workers and Runtime](../tech-spec/background-workers-runtime.md)
and [LLM and Agent Safety](../tech-spec/llm-agent-safety.md); the user guide
is [Runtime triggers](../runtime-triggers.md).

## Shipped state and future follow-ups

The cutover is complete: there is one runtime, no `AgentDefinition`
fallback, and every top-level non-chat activation is a trigger run (delegated
child tasks are the one exception: they run as `RuntimeDelegation` rows in the
`delegated` task mode, not as `RuntimeTrigger` rows). Every scope item has
shipped and nothing below blocks this spec:

| Item | Issue | State |
| --- | --- | --- |
| Tasks/Plans frontend surface: owner-scoped `GET /api/runtime/tasks/` list/detail over trigger runs, definition runs and delegated tasks, and the `/tasks` **Agent Tasks** page with plan steps and the recorded tool trace | JAN-204 | Shipped |
| Delegation block-and-resume (parent parks in the `WAIT` node) and durable re-pickup of interrupted delegations | JAN-207 | Shipped (trigger runs wait; chat keeps polling, see "Waiting on children") |
| Delegation REST and chat UI | JAN-208 | Shipped |
| Brain-selected, task-scoped toolsets approved by policy, beyond the per-mode allowlist and the unattended switch (scope item 4) | JAN-300 | Shipped (see "Brain-selected toolsets") |
| Adaptive character state persisted with audit and rollback (scope item 5) | JAN-301 | Shipped (Janus proposes from chat, the owner approves; see "Character and autonomy") |
| Close-out docs pass | JAN-205 | Done |

### Future follow-ups (not blockers)

These were deliberately left out of the shipped increments. None is required
for this spec to be complete, and none has a committed date:

- **Same-reply tool widening** (from JAN-300): a reply's toolset is fixed once
  the LangChain agent starts, so `request_tools` takes effect from the next
  message, not inside the same reply.
- **Showing a message's tool selection after a reload** (from JAN-300): the
  `runtime_tool_selections` row exists, but no API reads it yet, so the
  selection card is only visible live.
- **Persisting the trait baselines** (from JAN-301): the numeric trait
  baselines and `CharacterRuntime`'s delta-bounded proposals and exploration
  stay in-memory contracts; only the owner-approved `CharacterState` is
  persisted.

## Brain-selected toolsets (JAN-300, scope item 4)

Shipped 2026-10-12. Before a step runs, the brain picks which of the step's
**already authorized** toolboxes the task needs; policy then intersects that
pick with the authorization, so selection only ever narrows. Implementation:
`src/janus/toolset_selection.py`, `RuntimeAdapter.prepare_step(toolset=...)`;
details in [Background Workers and Runtime](../tech-spec/background-workers-runtime.md#brain-selected-toolsets-jan-300)
and [LLM and Agent Safety](../tech-spec/llm-agent-safety.md#registry-backed-task-scoped-authorization).

- **Where it runs.** Every chat message, and every unattended step that names
  no tools of its own: ad-hoc triggers (incl. Janus follow-ups, JAN-257) and
  delegated children. Saved definition runs (incl. the built-ins) keep their
  author's `tool_ids`, and an explicit `required_tool_ids` request is already
  task-scoped; neither is narrowed. `briefing`/`digest`/`ingestion` steps are
  audit-only and unchanged.
- **How it selects.** One utility-model call with a strict JSON contract
  (`{"tool_ids": [...], "reason": "..."}`, no other keys) over registry
  metadata (id, name, operation, first sentence of the description) of the
  step's authorized entries, task text as delimited data, objective capped at
  4,000 characters, bounded by `tool_selection_timeout_seconds` (default 10),
  which is also the provider client's own request timeout (no retries); with
  four selection calls still in flight, a further step skips selection at once.
  A step with at most three toolboxes beyond the anchors makes no call.
- **Policy approves.** `toolset_selection.narrow()` runs inside
  `prepare_step` right after `ToolRegistry.authorize()` and before the
  confirmation gate: ids that are not authorized (unknown, off the mode
  allowlist, switched off, `LOCKED_UNATTENDED`, outside a definition's tools or
  a delegation grant, unconnected) are trimmed and recorded; `core` and `docs`
  stay as anchors; the confirmation gate, refusal stubs, withheld tool names
  and the prompt-injection guard run unchanged on the narrowed entries, and a
  delegated child's grant is clamped to the narrowed set.
- **Fail safe.** No utility model, an error, a timeout, malformed or empty
  output, or a pick with no authorized id offers the full authorized set
  (today's behaviour), recorded as `source="fallback"` with the reason.
- **Persisted.** `runtime_tool_selections` (migration `b8d4f2a6c1e9`) stores
  the effective choice per `(user, task, step)`. A retried step, or a
  continuation step of the same task after a delegation wait, reuses it
  (`source="persisted"`, no new model call), re-trimmed against the current
  authorization.
- **Trace.** Chat's `__TOOL_SELECTION__` frame carries `toolset` (source,
  reason, tool ids, trimmed, widened, authorized count); the chat UI shows a
  *Tool selection* note when the set was narrowed. Unattended step results get
  a `Tool selection: ...` line, which the Agent Tasks page (JAN-204) decodes
  into the step's trace (`RuntimeStepTrace.tool_selection`) and shows next to
  the authorized and invoked tools; no parallel store or view was added.
- **Widening (chat).** A conversation keeps what earlier messages selected.
  `request_tools(tool_ids, reason)` (offered only when something authorized was
  left out) adds authorized-but-unselected toolboxes for the rest of the
  conversation; it refuses anything else and refuses after a suspected prompt
  injection. Explicitly naming a tool in a message also lets the selector pick it.
- **Measured prompt size** of a typical chat turn (non-admin user with a
  mailbox, no other integrations, empty skill library, message "Did anything
  important come in from my landlord this week?", selection `["mail"]`;
  system prompt + tool schemas + message, `cl100k_base` tokens): **before
  8,872 tokens** (63 tools, 31,624 characters of tool schemas); **after 1,568
  tokens** (8 tools: `core`, `mail`, `docs`, `request_tools`; 4,060 characters
  of schemas). The selection request itself is 731 input tokens plus a short
  JSON answer, sent once per message, while the tool schemas are re-sent on
  every agent-loop round of the turn.

**Future follow-ups (not blockers; see "Future follow-ups" above):** widening
inside the same reply (a reply's toolset is fixed once the LangChain agent
starts, so `request_tools` takes effect from the next message); showing a chat
message's selection after a reload (the `runtime_tool_selections` row exists,
but no API reads it yet); and per-user tuning of the selector.

## Cutover history (2026-09-25)

The sections below keep the order in which the cutover landed. Anything about
agent definitions, handoffs, or `build_tool_set()` is history; the section
above is authoritative.

Trigger-driven work now really executes (JAN-192). `trigger_runtime.py` was
authorization-only under JAN-165 — it authorized a step's toolset and then
always failed the step, because no human-absent execution mechanism existed.
`agent_task_runner.run_agent_task()` is that mechanism: a synchronous,
explicitly bounded tool loop built on the same
`create_tool_calling_agent`/`AgentExecutor` machinery as chat, minus the
streaming and the websocket. A trigger that authorizes and executes is now
honestly reported `completed`, and the step's `result` records the automation's
answer plus the trace of tools that actually ran. A run whose step fails is
recorded on the trigger row too (JAN-194): `failed` and retried from a fresh
plan for a potentially transient failure, or `exhausted` on the spot for a
policy denial or malformed payload, with the reason in `last_error`. Two fences keep unsupervised
execution safe, and neither may be weakened without a deliberate decision:

**Behavior change (JAN-193, 2026-09-25): trigger runs now perform
unsupervised writes.** JAN-192 held the trigger allowlist to the read-only
`confirmation="never"` set. JAN-193 decided — choosing "accept unsupervised
writes, narrowed" over building a deferred-approval surface — to also admit
the `confirmation="recommended"` toolboxes `memory`, `topics`, `dashboard`
and `delegation` (the successor to `handoffs`). `_CONFIRMATION_GATED_LEVELS`
deliberately stays `{"required"}`, so those writes happen with no human
review; the only record is the fired-trigger history's list of invoked tools.
The fences that remain, none of which may be weakened without a further
deliberate decision:

1. `TASK_MODE_ALLOWED_TOOL_IDS["trigger"]` is `core`, `trigger-history`,
   `calendar`, `jira`, `memory`, `topics`, `dashboard`, `delegation` — nothing
   else. `triggers` stays excluded for the independent JAN-184 reason
   (unbounded self-scheduling feedback loop); `events` (the system event log,
   which has no per-user column) is withheld from every unattended mode
   (JAN-195), leaving the owner-scoped `trigger-history` for self-checks; `handoffs` stays excluded because
   delegation supersedes it and its `enabled_when` requires a conversation a
   trigger never has.
   *(Replaced by the per-tool unattended switch, JAN-255: there is no fixed
   `trigger` allowlist any more; an ad-hoc run is offered every tool -- the
   locks on `triggers` and `events` were lifted by JAN-274 and JAN-272, which
   added a chain guard and owner-scoped event reads -- and the owner's switch decides what may
   run, by default everything but the `confirmation="required"` entries.)*
2. `TASK_MODE_WITHHELD_TOOL_NAMES["trigger"]` removes individual tools from
   the built toolset: `delete_fact` (memory write but not delete) and
   `set_dashboard_widget_prompt` / `clear_dashboard_widget_prompt` (a
   persisted prompt override durably steers later LLM runs).
3. `trigger_runtime` passed `confirmed_tool_ids=frozenset()`, so any
   `confirmation="required"` entry was denied rather than auto-approved.
   *(Superseded by JAN-229: the owner's tool policy now decides, with the
   same default.)*
4. A trigger's delegated children stay on the read-only `delegated`
   allowlist, clamped to the parent's grant, so delegation cannot pass the
   trigger's write access on. *(Since JAN-255: the parent's grant narrowed to
   read-only, unlocked tools, `tool_policy.delegable_tool_ids`.)*

A deferred-approval surface (a trigger *requests* a consequential action and
a human approves it later) remains the principled route to the `required`
tier and was explicitly not built here.

Denials are fatal only when the trigger payload named specific `tool_ids`; for
the default fallback sweep of the offered tools they are recorded in the step
result instead, so an unconnected calendar does not fail every trigger.

The `RuntimeAdapter` (`runtime_adapter.py`) is the live production entrypoint
for interactive chat (`websocket/chat.py`), mail-ingestion classification
(`processor.py`), briefing/digest generation (`services/dashboard_service.py`),
and trigger-driven execution (`trigger_runtime.py`, JAN-165/JAN-192). Each inbound
message/task now gets a fresh, per-message/per-step authorized toolset via
`ToolRegistry.authorize()` — never a toolset cached for the lifetime of a
conversation or job — with a stable, auditable denial reason for every
requested-but-not-selected tool and a pre-execution confirmation gate for
tools marked `confirmation="required"`. The frontend surfaces denied tools and
pending confirmations from the `__TOOL_SELECTION__` event (JAN-167, shipped).

The capability-intersection authorization tier was decorative until JAN-171:
`granted_capabilities` used to be the union of the requested tools' own
declared `required_capabilities`, so `missing_capability` could never fire.
`capability_profile.user_connected_capabilities()` now derives real,
per-user capability grants from actual connection state (an active
`EmailAccount` for "mail", a calendar-capable `PlannerAccount` for
"calendar", a task-tracker-capable one for "jira"/"dashboard") and
instance-wide integration config (`Settings.github_token`/`linear_api_key`
for "github"/"linear"), replacing the self-granting helper at every call
site. `enabled_when` for mail/calendar/jira/dashboard moved off
`AgentDefinition.mail_access`/`calendar_access`/`jira_access` onto the same
real connection signal; GitHub/Linear tools stay always visible (they're
single, instance-wide integrations, not a per-user connection) with the
capability tier as their real gate. The agent-definition flags themselves were
removed with the model in JAN-227.

### Single-agent prompt and tool defaults (JAN-201)

Per an explicit product decision, `memory_access`, `topic_access`, and
`planner_task_read_access`/`planner_task_write_access` are **not** relocated
to any settings model: the single Janus agent always gets memory, topic, and
local-planner (read and write) tools, exactly like `github`/`linear`. Their
registry entries have no `enabled_when` gate and their capabilities are in
`ALWAYS_GRANTED_CAPABILITIES`; confirmation levels are unchanged
(`planner.local` stays `confirmation="required"`, so unattended trigger runs
still cannot use it). Chat's per-turn memory-fact suggestion now always runs
and is best-effort: a failure is logged and never fails the streamed reply.

Chat (`websocket/chat.py`), mail-ingestion guidance (`processor.py`), and
briefing generation (`services/dashboard_service.py`, `/ws/briefing`, `janus
brief`, the `get_daily_briefing` tool) no longer source prompt content from an
`AgentDefinition`. The user's persisted constitution
(`character_service.get_character_constitution()`, the same data `GET/PUT
/api/character` edits, or the safe default) is rendered with
`CharacterConstitution.render_system_prompt()`:

* chat: `agent_runtime.build_janus_system_prompt()` = character + shared
  guardrails + untrusted-content warning + a tool-availability note + skills;
* mail triage: `build_janus_mail_ingestion_guidance()` = character + skills,
  cached per user per processor;
* briefing: the character is prepended (brace-escaped) to the built-in
  briefing template, so the planner-summary contract is kept. Skills are not
  added to briefings (they never were).

Skills are one flat, always-on library: `agent_runtime.load_skill_library()`
returns every `AgentSkill` row regardless of `agent_definition_skills`
attachment. `Settings.mail_ingestion_agent_definition_id` and
`dashboard_widget_preferences.briefing_agent_definition_id` are no longer read
or exposed by the API (old clients sending them are ignored), and the
Settings UI no longer offers classification/briefing agent pickers; the
columns and the join table were dropped by migration `d5e8f1a2b3c4`
(JAN-202/JAN-227), and `resolve_runtime_agent_definition()` and
`build_agent_system_prompt()` are gone. The briefing path never applies an
agent's `default_model`.

Not yet built: see "Remaining work" above.

The first graph increment is transport- and persistence-independent. It defines
the stable retrieve, generate, select, execute, wait, complete, and failure
nodes as typed transitions. Model-generated plans are data interpreted by this
graph; they do not replace its topology.

The runtime-runner increment adds `PlanDrivenRuntime` as the orchestration
boundary around those transitions. It receives plan retrieval, plan generation,
and step execution as injected adapters, so persistence, LLM planning, and tool
execution remain outside the graph and runner contracts. `run()` advances a
task until it reaches `WAIT`, `COMPLETE`, or `FAILED`; `resume()` continues only
from a waiting state after an external event or persisted state update. The
runner records which steps it attempted for the current advancement, but does
not itself persist plans, call an LLM, authorize tools, or expose a new API.

The character increment adds `janus.character` as the bounded runtime contract
and adds a user-owned persisted constitution exposed through the authenticated
`GET/PUT /api/character` endpoints. `CharacterConstitution` holds the versioned identity, core values,
non-negotiables, normalized trait baselines, per-trait maximum deltas, and an
exploration rate. `CharacterRuntime` creates a baseline `CharacterState`,
applies brain or exploration proposals only to known traits, and returns
accepted dimensions plus stable denial reasons for unknown dimensions, invalid
values, or delta bounding. A state must match the constitution ID and version;
constitution fields and state objects are immutable dataclasses.

The API persists constitution fields in the user's single `character_profiles`
row and increments its version on each update. If no row exists, `GET
/api/character` returns a safe default. Payload validation bounds values before
persistence: rule lists contain 1–12 entries, there are at most 20 traits, and
trait baselines, maximum deltas, and exploration rate are all in `0.0–1.0`;
trait names are normalized and case-insensitively unique.

This increment did not persist adaptive state, write audit events, or add a
settings/configuration key. The rendered baseline prompt is now the system
prompt source for chat, mail triage, and briefing (JAN-201, above); the
persisted adaptive state, its audit trail, and rollback followed in JAN-301
(see "Character and autonomy").

## Plan-driven delegation (updated 2026-09-24)

JAN-200 replaces cross-agent handoffs (`handoff_to_agent`,
`list_available_agents`, `list_agent_sessions`, backed by
`agent_handoff_runs`) with **delegation to a bounded child task inside the same
runtime**. There is one Janus brain, so the thing a task hands work to is not a
second named agent but a *child task*: its own `TaskContext`, its own
single-step `Plan` advanced by `PlanDrivenRuntime`, its own persisted
`PlanRuntimeState`, executed by `agent_task_runner.run_agent_task()` -- the
same unattended execution path triggers use, not the legacy
`agent_tools.build_tool_set()` + streaming chat loop.

This phase was built alongside the legacy handoff runtime; JAN-202, JAN-203,
and JAN-227 then removed the legacy toolbox, the `agent_handoff_runs` table,
and its `AgentDefinition` foreign keys.

### Persistence: `runtime_delegations`

One row per delegation records **task identity, not agent identity**:
`parent_task_id` / `parent_step_id` (the task that asked, e.g.
`chat-conversation-42`) and a unique `child_task_id`
(`delegation-<delegation_id>`), which is also the `task_id` of the child's
`PlanRuntimeState` snapshot. The row also stores the owner (`user_id`, the
authenticated user, never a model argument), an optional `conversation_id`
back-link (nullable, `SET NULL` on delete, so it has no `AgentDefinition`
dependency at all), `depth`, the `objective`, the model's requested
`requested_tool_ids`, the policy-computed `allowed_tool_ids` (see fences
below), and a lifecycle modelled on `runtime_triggers`: `queued` -> `running`
-> `completed` | `failed`, with a private `claim_token`, `claimed_at`,
`completed_at`, `result`, and `error`. Completion and failure require the
claim token, so a stale executor cannot finalize a delegation that was
already expired.

### Toolbox: `delegation`

A new registry entry `delegation` (successor to `handoffs`) builds three
owner-scoped tools:

- `delegate_task(objective, tool_ids=None)` enqueues a child task and returns
  immediately with its `delegation_id`, status, and the tool IDs the child is
  allowed to use.
- `get_delegated_task(delegation_id)` returns status plus the child's result
  or error. Another user's delegation reads as not found, not forbidden.
- `list_delegated_tasks(limit)` lists the current parent task's delegations,
  newest first (the successor to `list_agent_sessions`).

`list_available_agents` has no successor: there is no set of named agents to
choose between. The child's reach is expressed as tool IDs instead, and
`delegate_task` reports the effective set.

The child runs on a small process-local thread pool, so a chat turn is not
blocked for up to `AGENT_TASK_MAX_SECONDS`; the parent consumes the result by
polling `get_delegated_task` / `list_delegated_tasks`, or, in a trigger run,
by waiting for it with `wait_for_delegated_tasks` (JAN-207, below).

### Safety fences

Delegation lets a model create unattended work, so it inherits both trigger
fences and adds two of its own. None may be weakened without a deliberate
decision:

1. **Read-only, never wider than the parent.** Children run in the
   `delegated` task mode. Their `allowed_tool_ids` are the tools *actually
   authorized for the parent's current step* (carried on
   `ToolBuildContext.authorized_tool_ids`, set by `RuntimeAdapter.prepare_step`
   after its confirmation gate), narrowed to read-only (`operation="read"`),
   unlocked entries (`tool_policy.delegable_tool_ids`, JAN-255). A child can
   therefore never reach a tool its parent did not have, and never a writing
   tool even if its parent did. Explicitly requested `tool_ids` outside that
   set are rejected at `delegate_task` time; at execution
   `tool_policy.unattended_tool_sets` narrows the stored set to read-only,
   unlocked tools again. *(Before JAN-255 this was a fixed `delegated`
   allowlist, the `confirmation="never"` set of the original JAN-192 trigger
   allowlist; the read-only rule now also admits `mail`, `planner.local`,
   `web_search`, and `rules` reads when the parent had them.)*
2. **No self-authorization.** The child is confirmed only for the tools the
   owner allows unattended and never inherits a definition's selection, so a
   tool switched *Off* (by default every `confirmation="required"` entry) is
   denied, never auto-approved -- a chat user's per-call approval (JAN-254)
   does not propagate to the child. The owner's *current* switch applies, so
   turning a tool off also applies to already-queued children.
3. **Depth cap.** `delegation` is not read-only, so a child never gets it and
   cannot spawn a further child at all. A trigger run is itself
   depth 0, so its child is depth 1 — within the cap. `MAX_DELEGATION_DEPTH = 1` is enforced in the
   delegation service as well -- the toolbox computes depth 2 when built for a
   `delegated` step, which the service refuses -- so widening an allowlist by
   mistake still cannot create a chain.
4. **Fan-out cap.** Following the `MAX_ACTIVE_TRIGGERS_PER_OWNER` backlog-cap
   pattern, `delegate_task` refuses to exceed
   `MAX_ACTIVE_DELEGATIONS_PER_OWNER` (4) `queued`/`running` delegations per
   owner. It is a separate, smaller constant than the trigger cap (25) because
   every active delegation is a concurrent LLM run rather than a future
   schedule entry; it matches the executor's worker count.

Delegation is `operation="write"`, `risk_level="medium"`,
`confirmation="recommended"` in the registry: it creates durable rows and
spends LLM budget, but every tool the child can reach is read-only.

### Waiting on children (JAN-207, updated 2026-10-11)

A trigger run can now park until the children it delegated finish, then
resume with their results persisted in its own plan state. The design
decisions, all taken conservatively:

1. **Only trigger runs wait; chat keeps polling.** A trigger run is already a
   persisted `PlanDrivenRuntime` plan (`DurablePlanRuntimeTriggerHandler`),
   so it has a plan state that can sit in the `WAIT` node. A chat turn is not
   (it is a per-message `prepare_step` plus a streaming loop with a human
   watching), so it keeps polling `get_delegated_task`; making chat a
   plan-driven run is not part of this change. Built-in runs (Morning
   briefing, Afternoon digest, Mail processing) do not wait either: their
   `finalize` hook delivers the run's answer, and delivering a half-finished
   answer would be wrong.
2. **An explicit tool, one wait per run.** The `delegation` toolbox gains
   `wait_for_delegated_tasks(delegation_ids)`, offered only to trigger-driven
   steps (`ToolBuildContext.trigger_id` is set). It accepts only delegations
   this same step created (owner, `parent_task_id`, and `parent_step_id` must
   all match; anything else reads as not found), at most
   `MAX_ACTIVE_DELEGATIONS_PER_OWNER` of them. It answers immediately and
   tells the model to finish its answer; the run parks *after* the step
   finishes. Only the run's first step may wait: the continuation step that
   resumes it cannot wait again (the tool refuses), so a run waits at most
   once and cannot build a chain of waits.
3. **How the plan parks.** The step that asked to wait completes with its
   answer. The plan then gets one `waiting` step per awaited delegation
   (`<step_id>::await::<delegation_id>`, new `PlanStepStatus.WAITING`) and one
   pending continuation step (`<step_id>::continue`) that depends on the
   original step and every await step and asks for the same tools.
   `select_step` finds nothing runnable and the graph enters `WAIT`; the
   whole state is persisted under the trigger's task ID like any other run.
   A plan whose step limit (`TaskContext.max_steps`) cannot hold the extra
   steps fails the step instead of parking.
4. **The trigger row reads `waiting`.** A parked run's `runtime_triggers` row
   moves from `running` to a new `waiting` status (no claim token, not
   claimable by the worker). `waiting` counts as active for the per-owner
   backlog cap and can be cancelled like a queued run; cancelling it means it
   is never resumed, while its children still finish.
5. **Release.** Once every awaited delegation is terminal (`completed` or
   `failed`, including a delegation that failed as interrupted, below), the
   row moves atomically from `waiting` back to `queued`. This is checked when
   a child finishes and on every poll of the `DurableTriggerWorker`, so a
   restart, or a child that finished before its parent parked, is still
   picked up. A resume is an ordinary claim, so it counts as one attempt.
6. **Deterministic resume.** When the worker runs the row again,
   `DurablePlanRuntimeTriggerHandler` loads the `WAIT` state and, before
   `PlanDrivenRuntime.resume()`, resolves every await step from its
   delegation row: the step becomes `completed` with the child's result, or
   with a line saying the child failed and why. A failed child never fails
   the parent: the continuation step is told and reports it honestly. The
   continuation's objective is rebuilt from the original objective, the
   parked step's answer, and those results, so the resumed step sees exactly
   what was persisted, and the trigger history shows the whole sequence.
7. **Retries are unchanged.** If the continuation itself fails, the run is
   `failed` and its retry starts from a fresh plan, as every trigger retry
   does (JAN-194), which delegates again.

### Durable re-pickup of interrupted delegations (JAN-207)

Delegations still run on a bounded process-local pool, now behind a small
`BackgroundTaskRunner` (`background_tasks.py`, JAN-138). What makes them
durable is the database row plus the `DurableTriggerWorker` poll, not the
pool:

- **Exactly one automatic re-run.** A delegation still `queued`/`running`
  past its lease (`AGENT_TASK_MAX_SECONDS` plus a margin, measured from its
  claim, or from when it was queued) was interrupted. The first time, it is
  put back to `queued` (`requeue_count` 0 -> 1, `requeued_at` set, claim
  token and claim time cleared). The second time, it is marked `failed` with
  a reason saying it was interrupted again after its one automatic re-run.
  Failures of the child itself (a provider error, a denied tool) are never
  re-run.
- **Claim-token handover.** Requeueing clears the claim token, so the
  executor that lost the row can no longer record a result; only the new
  claim's token can. A late result from the old run is dropped and logged.
- **Who re-dispatches.** Every `DurableTriggerWorker` poll requeues or fails
  expired delegations (all owners) and submits every claimable `queued` row
  to the runner. The runner deduplicates by `delegation:<id>`, so a row
  already waiting in this process's pool is not submitted twice, and the
  atomic claim stops a second executor anyway. Reading, listing, or creating
  delegations still applies the same requeue-or-fail rule lazily for the
  owner. With the trigger worker turned off, a requeued delegation is not
  re-run; it fails after one more lease.
- **Fences hold across the re-run.** A re-run executes the same row: the
  same stored, clamped `allowed_tool_ids`, the `delegated` task mode with no
  pre-confirmed tools (the owner's current unattended switch decides), the
  same depth, and no new row, so the per-owner active cap is unchanged (a
  requeued delegation still counts as active).

This is not a distributed queue: one Janus process, one pool, one SQLite
database, as everywhere else.

### Resolved follow-ups

- *(Resolved in JAN-208: the owner-scoped, paginated
  `GET /api/conversations/{id}/delegations` lists a conversation's delegated
  tasks newest first -- 404, not 403, for another user's conversation -- and
  the chat (`BriefingChat`, all three shells) shows them above the transcript
  with status, objective, granted tools, and result or error. It succeeds the
  `GET /api/conversations/{id}/handoffs` view removed in JAN-227.)*
- *(Resolved in JAN-227: the child no longer resolves an `AgentDefinition`;
  it renders the user's character plus the skill library like every other
  unattended run, and is confirmed only for what the owner allows unattended.)*

## Runtime boundaries

The brain may propose plans, subtasks, tool activations, and character
adaptations. It may not grant itself permissions. Effective access is the
intersection of the requested task toolset, agent/task capabilities, user and
tenant policy, resource authorization, and the autonomy envelope.

The graph remains stable and interprets model-generated plans. The model does
not freely rewrite graph topology or bypass authorization, approval, audit, or
persistence nodes.

## Task-scoped tool selection

The registry's task-selection boundary is implemented as an explicit library
contract for plan-execution integration. A request carries a task identifier,
model-proposed tool IDs, a non-model task allowlist, and granted capabilities.
The effective selection is the intersection of those requests with the
allowlist, capabilities, and the current context (real connection state,
admin status, plan-step identity). Unknown, non-allowlisted, disabled, or
under-capable tools are denied with stable reasons (`unknown_tool`,
`not_allowlisted`, `disabled_by_context`, or `missing_capability`) and are
never passed to a builder, except that unattended runs offer `BLOCKED`
refusal stubs for some denials (JAN-229). Since JAN-300 the brain's own pick
(`prepare_step(toolset=...)`) is intersected with this selection and can only
remove entries (see "Brain-selected toolsets"). The current slice
returns those denials to its caller; it does not persist an audit event or
provide a public task API.

Building an authorized selection does not grant confirmation or execution
authority; those checks remain immediately before consequential tool
execution (see "Trigger-first single agent" above for what is wired in
today; `missing_capability` is now a real denial, not a decorative one).

### Self-service trigger tools

The `triggers` registry entry lets Janus schedule, inspect, and cancel its own
runtime follow-ups instead of requiring a human HTTP call to
`/api/runtime/triggers`. It wraps the existing durable trigger machinery and
adds no new persistence or dispatch path. The entry requires the `triggers`
capability, is `mixed`/`medium` risk with `recommended` confirmation, and is
always available (JAN-227; it was gated by the removed `trigger_access`
agent-definition flag).

Every operation is scoped to `owner_id = user.id` inside the trigger service,
and the owner is taken from the authenticated tool context rather than from a
model argument. A trigger belonging to another user reads as missing rather
than forbidden, so trigger IDs cannot be probed.

Two boundaries keep self-scheduling bounded:

- `chat` allowlists `triggers`. Unattended runs were locked out of it
  (`tool_policy.LOCKED_UNATTENDED`, JAN-184 through JAN-255) because a trigger
  able to enqueue further triggers is the classic unbounded feedback loop.
  Since JAN-274 they may use it (default *Allowed*, switchable), bounded by a
  chain guard in the toolbox: each follow-up records its parent run and depth
  (`RuntimeTrigger.parent_trigger_id`/`chain_depth`), and the toolbox refuses
  depth > 2, more than 3 follow-ups per run, more than 10 unattended-scheduled
  follow-ups per owner per day, and anything due in under 15 minutes; it only
  creates one-time follow-ups. Delegated children never get it.
- Creation refuses to extend an owner's backlog past
  `MAX_ACTIVE_TRIGGERS_PER_OWNER` claimable (`queued`, `running`, or
  `failed`-but-retryable) triggers, which bounds a chat turn looping on
  trigger creation.

Cancellation moves a `queued` trigger, or a `failed` trigger still waiting on
a retry, to the terminal `cancelled` status, which is never claimable. A
`running` trigger holds a live worker claim and cannot be cancelled, and an
already-terminal trigger (`completed`, `exhausted`, or already `cancelled`)
has nothing left to cancel.

## Character and autonomy

Janus has a stable constitution and user-controlled character profile persisted
by the character API.
Within an explicit autonomy envelope, it may adapt expression, initiative,
planning style, and exploration preferences. Adaptive changes are scoped,
versioned, and cannot change authority or safety requirements. In the current
character increment, the envelope is limited to the in-memory trait contracts:
each proposal is restricted to a known normalized trait and its configured
maximum delta, while controlled exploration is rate-gated and uses the same
bounded path. The rendered system prompt explicitly keeps adaptive tendencies
below the constitution, safety rules, authorization, and user controls.

**Persisted adaptive state (JAN-301, shipped).** The owner-facing envelope is
a small typed `CharacterState`: `tone`, `verbosity`, `proactivity` (closed
value sets) and a free-text `current_focus` (one line, at most 200
characters). The owner sets the envelope (allowed values and defaults per
dimension, and whether a focus may be set) with the character profile
(`character_profiles.state_envelope`); values outside it are refused on every
path. Janus proposes, the owner approves (as for automations, JAN-235): Janus
can only file a proposal from a chat turn through the chat-only
`character.propose` toolbox (in `tool_policy.LOCKED_UNATTENDED`), and the
owner approves or rejects it on Settings → Character; owner edits apply
directly. Unattended runs (triggers, automations, delegated tasks) never
change or propose the state, and the agent never changes the constitution.
Every change is an audit row (actor: the owner, or Janus with the chat turn
id; previous and new state; time), any earlier state can be restored, and a
reset restores the envelope defaults (`/api/character/state*`). The read-only
`character` toolbox (`get_character_state`) lets Janus explain its current
state. The persisted state is rendered into every system prompt (chat,
trigger runs, delegated tasks, the briefing context, mail-ingestion guidance,
the preview); the focus only as an escaped `<character_focus>` data block.
The numeric trait baselines and `CharacterRuntime`'s delta-bounded proposals
and exploration stay in-memory contracts: they are not persisted or adjusted
at runtime (persisting the baselines is a future follow-up, not a blocker). See [background-workers-runtime.md](../tech-spec/background-workers-runtime.md#adaptive-character-state-jan-301).

## Trigger and reliability requirements

All trigger categories use one transport-independent envelope: `manual`,
`scheduled`, and external `event` deliveries carry a non-blank `trigger_id`,
`idempotency_key`, `task_id`, and `source`, a `payload` mapping, and a
timezone-aware `created_at`. The envelope is immutable at the top level and
copies the incoming payload into a read-only mapping; payload contents remain
untrusted data. The `kind` field identifies the ingress category but does not
change dispatch policy.

The authenticated runtime-trigger ingress validates identity by kind. Manual
requests provide an explicit idempotency key. Scheduled requests provide a
schedule ID and timezone-aware occurrence time, from which Janus derives the
canonical idempotency key and defers worker claims until that time. External
event requests provide an event ID, source, and timezone-aware occurrence
time; Janus namespaces the canonical identity by source. Identity fields from
another trigger kind are rejected instead of being silently ignored.

The Settings **Runtime** tab can submit one scheduled occurrence or one
external-event occurrence with this metadata. Selecting **Scheduled** does not
create a recurring schedule, and selecting **Event** does not subscribe Janus
to a provider: a scheduler, integration, or user must submit each occurrence.

The current process-local dispatcher claims each `(task_id, idempotency_key)`
pair before invoking the task-runtime handoff. Its observable outcomes are:

- `completed`: the handler returned successfully; later deliveries for the
  same pair are `duplicate`;
- `in_progress`: another delivery currently owns the pair's claim;
- `failed`: the handler raised; the claim is released and a later delivery may
  retry; and
- `duplicate`: the pair completed earlier in this dispatcher.

This protects against concurrent execution of the same idempotency claim, not
against concurrent execution of every trigger for a task. `trigger_id` is an
audit identity and is not the deduplication key. The handler receives the
envelope and is responsible for entering the normal task, authorization, and
policy path; scheduled delivery does not directly authorize or invoke tools.

The durable trigger increment adds a `runtime_triggers` record for each
accepted envelope. It stores the trigger and task identities, kind, source,
JSON payload, lifecycle status, claim/completion timestamps, and the latest
failure. A unique `(task_id, idempotency_key)` constraint makes enqueue
idempotent across requests and process restarts. Claims are atomic for queued,
retryable, and expired-running records and return a private ownership token;
completion and failure require that token, preventing a stale worker from
finalizing a claim after another worker has reclaimed it. Failures remain
retryable, except that a handler raising `TerminalTriggerError` exhausts the
row immediately (JAN-194); completion is terminal.

The durable store is intentionally a persistence/service slice, not a worker
or scheduler. The current in-memory dispatcher remains the compatibility
ingress and does not yet claim through this table, so cross-process execution
coordination, automatic backoff, task-wide serialization, scheduling, and
auditable/resumable trigger history remain requirements for the runtime
integration slice. The migration is additive and safe to roll out before those
consumers exist. An opt-in `DurableTriggerDispatcher` now demonstrates the
intended handoff boundary: it persists and claims a trigger, invokes the
runtime callback outside the database transaction, and records success/failure
using the claim token. If another worker reclaims the lease before a terminal
transition, the dispatcher returns a failed result reporting the lost claim;
it does not report false durable completion.

## Security requirements

- Unknown or incompletely described tools fail closed.
- Tool authorization occurs at selection time and immediately before
  execution.
- Credentials remain outside model context and tools are lazily initialized.
- Confirmation tokens are scoped to the exact action, arguments, user, task,
  and expiry.
- Tool, plan, character, and trigger changes are recorded with version and
  actor information.
- Tool results and event payloads are untrusted data and cannot override the
  runtime constitution or policy.

## Test requirements

Each increment must add meaningful tests for registry contracts, permission
matrices, graph resume and replan behavior, stale toolset rejection, trigger
deduplication, character adaptation bounds, frontend usability paths, and
security regressions. The agent-definition tests were removed with the model
(JAN-227); chat, trigger-definition, tool-policy, built-in-trigger, and
mail-processing tests are the baseline now.

## Non-goals

- allowing the model to self-authorize tools or permissions,
- making the graph an unrestricted model-generated program,
- removing user confirmation for consequential external actions (a tool the
  user selects on a trigger definition they wrote *is* that confirmation;
  nothing Janus or the system authored gets it implicitly),
- eagerly initializing every available integration,
- removing existing data before replacement behavior is proven (the
  agent-definition removal in JAN-227 followed the character, delegation, and
  trigger-definition replacements).
