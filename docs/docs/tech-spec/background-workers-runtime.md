# Background Workers and Runtime Model

Janus is one agent per user. Its identity is the user's **character profile** (`character.py`, `character_profiles`) plus the shared skill library (`agent_skills`). Everything it does outside chat is a **trigger** (one run, a `runtime_triggers` row), usually of a saved **trigger definition** (`trigger_definitions`). There are no agent definitions and no handoffs between agents. The safety rules these mechanics enforce are specified in [LLM and agent safety](llm-agent-safety.md).

## Plan-driven runtime

Every unit of agent work, in chat or unattended, goes through the same pipeline.

| Module | Role |
| --- | --- |
| `plan_graph.py` | `Plan`, `PlanStep`, `PlanGraph`; plan statuses `draft`, `active`, `waiting`, `completed`, `failed`; step statuses `pending`, `running`, `waiting`, `completed`, `failed` |
| `agent_runtime.py` | `TaskContext`, `PlanDrivenRuntime` (`run`/`resume`), system prompt assembly (`build_janus_system_prompt`) |
| `plan_state.py` | persists a plan graph per owner and task in `plan_runtime_states` with optimistic revisions; decodes step results for history views |
| `runtime_adapter.py` | `RuntimeAdapter.prepare_step()`: the one place a step's toolset is authorized and built |
| `unattended_execution.py` | authorizes and runs an unattended step (trigger, automation, delegated), formats its result |
| `agent_task_runner.py` | the bounded synchronous tool loop for unattended steps |
| `toolset_selection.py` | the utility-model pick that narrows a step's toolset |

A chat turn is one plan step (`step_id` `chat-conversation-<id>::message-<n>`). A trigger run is a one-step plan whose task id is the trigger's. A delegated child is its own one-step plan under a child task id.

**System prompt.** Every surface renders `build_janus_system_prompt(character, skill_library, character_state=..., memory_index=...)`: the constitution (with its `additional_instructions`), the current adaptive character state, the shared tool-availability rules, the untrusted-content policy, the memory guidance (`MEMORY_GUIDANCE`), the memory index and the skill index. The always-injected layer stays small and works as an index, following Anthropic's "The components of agent memory" (2026):

- **Memory guidance** says what memory is *for* (a future session relies on it without the user repeating it), asks Janus to look in memory before answering something that depends on the user's preferences, people, projects or decisions, and to write a fact when asked or when something is clearly worth remembering. It sets no fixed rules about what to write.
- **Memory index** (`services/memory_curation.py`, `index_facts`): at most 15 active facts (`INDEX_MAX_FACTS`) and 2,000 characters (`INDEX_MAX_CHARS`), each line `- [fact <id>] <summary>`. Pinned facts come first, then facts the [Memory review](#memory-review) promoted after confirming them; hidden facts never appear. It is rendered inside an escaped `<memory_index>` data block.
- **Skill index**: one line per skill (`- **<name>**: <description>`, 300 characters). Janus loads a skill's full text with `read_skill(name)`, a `core` tool and therefore offered on every surface and in every task mode. The mail classifier is one tool-less model call, so `build_janus_mail_ingestion_guidance` still renders the skills' full text.

 Chat adds `CHAT_PROGRESS_UPDATES` (a short plan before the first tool call and one line per round). Unattended callers append run guidance (nobody is present; report `BLOCKED` and `ERROR` answers plainly). `/ws/chat` renders the prompt per message, so an approved character change applies from the next message.

## Tool registry

`tool_registry.py` defines `ToolRegistryEntry`; `agent_tools.py` builds `DEFAULT_TOOL_REGISTRY` from the toolbox modules in `agent_toolboxes/`. An entry is a whole toolbox; its id is what policies, definitions and the UI refer to.

| Field | Meaning |
| --- | --- |
| `id` | toolbox id, e.g. `mail.send` |
| `operation` | `read`, `write`, `mixed` or `admin`; only `read` entries are delegable |
| `risk_level` | `low`, `medium`, `high` (shown in the UI and on approval cards) |
| `confirmation` | `never`, `recommended` or `required`; `required` means an approval card in chat and *Off* by default unattended |
| `required_capabilities` | capabilities the step must hold (from `capability_profile`) |
| `enabled_when` | a predicate over real state: connected integrations, admin, plan-step identity, task mode, operator settings |
| `reads_untrusted_content` | output is spotlighted and scanned for prompt injection |
| `docs_ref` | the section of the user page `tools.md` that explains it; cited by refusals |

**Capabilities.** `capability_profile.user_connected_capabilities()` derives capabilities from real state: an active mail account grants the mail capabilities, an enabled WhatsApp account grants `whatsapp`, a configured web search backend grants `web_search`, and so on. `core`, `memory`, `topics`, `knowledge`, `wiki`, `planner.local`, `triggers`, `skills` and `mcp` are always granted.

**Toolboxes.** The `required` entries (approval in chat, *Off* unattended by default) are marked.

| Group | Entries |
| --- | --- |
| Core and history | `core`, `events`, `trigger-history`, `docs` |
| Mail | `mail`, `mail.label`, `mail.processing`, `mail.send` (required), `rules`, `rules.write` (required) |
| Planner | `calendar`, `jira`, `planner.local`, `planner.local.write` (required), `planner.suggestions`, `planner.suggestions.review`, `planner.suggest` |
| Microsoft and Azure | `microsoft.mail`, `microsoft.calendar`, `microsoft.teams`, `microsoft.mail.send` (required), `microsoft.teams.send` (required), `azure.resources` |
| Messaging | `whatsapp`, `whatsapp.send` (required), `whatsapp.notify_me`, `telegram.notify`, `conversations.start` |
| Knowledge | `memory`, `topics`, `knowledge`, `wiki`, `wiki.propose` (required), `character`, `character.propose` |
| Web and integrations | `web_search`, `web_fetch` (required), `github` (required), `linear` (required), `dashboard` |
| Automation | `triggers`, `triggers.propose`, `delegation`, `skills` (required, admin) |
| Chat UI | `ui_actions`, `ui_blocks`, `ui_introspection`, `images` |
| Operator opt-in | `shell` (required, admin), `coding_agents` (required, admin), `self_evolution` (required, own switch) |
| Per user, at run time | `mcp.<server>.<tool>` (remote MCP), `a2a.<id>` (remote agents) |

**Managed registry.** Chat builds its registry per message with `agent_tools.build_managed_registry(db, user)`: the default registry plus the user's remote MCP tools (enabled server and tool, last discovery `ready`; `agent_toolboxes/remote_mcp.py`) and switched-on remote agents with a fetched card (`agent_toolboxes/remote_agents.py`). Remote entries are `reads_untrusted_content` and enabled only in chat, unless the owner chose *Unattended: Allow everything*. A remote MCP tool is `confirmation="never"` only when the server marks it `readOnlyHint: true`; every other remote entry is `required`.

## Task modes

| Mode | Used by | Allowed entries |
| --- | --- | --- |
| `chat` | `/ws/chat` | fixed list `TASK_MODE_ALLOWED_TOOL_IDS["chat"]` plus the user's remote ids |
| `briefing`, `digest` | audit-only step recorded while building briefing context | fixed lists |
| `ingestion` | audit-only step of the synchronous mail classifier | fixed list |
| `trigger` | ad-hoc API runs, Janus follow-ups | every entry the tool policy offers |
| `automation` | definition runs, built-ins included | the definition's `tool_ids` plus `core`, minus locked entries |
| `delegated` | child tasks | the stored parent grant, read-only entries only |

`runtime_adapter.KNOWN_TASK_MODES` is the union. The unattended modes have no fixed list; `tool_policy.unattended_tool_sets` computes theirs.

## Step authorization

`RuntimeAdapter.prepare_step()` turns a request into a `PreparedStep` (tools, denials, the turn trace, the effective toolset). In order:

1. **Authorize.** `ToolRegistry.authorize()` intersects the requested ids with the allowed ids, `enabled_when` and the granted capabilities. Denials carry a stable reason: `unknown_tool`, `not_allowlisted`, `disabled_by_context`, `missing_capability`.
2. **Narrow.** A toolset choice (below) keeps only authorized entries it names, plus the anchors `core` and `docs`.
3. **Confirm.** Entries with `confirmation="required"` must be in `confirmed_tool_ids`, else they are denied `confirmation_required`. Entries in `blocked_tool_ids` are refused whatever their level. In chat, an `approval_gate` builds the unconfirmed `required` entries anyway and wraps each tool so the call waits for the user.
4. **Withhold names.** `TASK_MODE_WITHHELD_TOOL_NAMES["trigger"]` drops `delete_fact`, `set_dashboard_widget_prompt` and `clear_dashboard_widget_prompt` from ad-hoc trigger runs.
5. **Build and wrap.** Each tool is wrapped (outermost first): approval gate, error guard, trace. The trace records every successful call in the step's `TurnTrace`, spotlights output of `reads_untrusted_content` entries, scans it, and after a detection refuses consequential tools for the rest of the step.
6. **Stubs.** Unattended steps pass `refusal_stubs=True`: an entry denied `confirmation_required`, `missing_capability` or `disabled_by_context` is still offered with its real name and arguments, but answers `BLOCKED: <tool> was not run because <reason>. Nothing was changed. See the docs: <docs_ref>.`, logs a `WARNING` and is recorded in the step's `ToolCallLog`. Unknown and not-allowlisted entries get no stub.
7. **Error guard.** With `guard_tool_errors=True` (chat and unattended) a raising tool answers `ERROR: <tool> failed: <detail>` instead of aborting.

`unattended_execution.format_step_result` appends `Refused tool calls:`, `Failed tool calls:`, `Suspected prompt injection:` and `Tool selection:` lines to a stored result, so a run cannot silently claim an action it was refused.

## Brain-selected toolsets

`toolset_selection.py` lets the utility model pick the toolboxes a task needs, which shrinks the prompt (about 63 tools to 8 on a typical chat turn).

- **Selector.** `choose_toolset()` makes one utility-model call (one line per candidate plus the task in `<task>` tags) on a daemon thread, bounded by `JANUS_TOOL_SELECTION_TIMEOUT_SECONDS` (default 10) on a client with no retries. At most four selections run at once; when all slots are busy the step falls back immediately.
- **Parsing.** Only `{"tool_ids": [...], "reason": "..."}` is accepted (at most 40 ids). Errors, timeouts, malformed or empty answers, or no utility model give `source="fallback"`: the full authorized set. Three or fewer candidates give `source="unnarrowed"` without a call. `JANUS_TOOL_SELECTION_ENABLED=false` turns selection off.
- **Untrusted input.** `narrow()` runs after authorization and before confirmation, so the pick can only shrink the authorized set; unauthorized ids are trimmed and recorded. It narrows the grant a delegated child inherits too.
- **Where.** Every chat message; ad-hoc `trigger` runs; delegated children whose step names no tools. Definition runs keep their configured tools.
- **Persistence.** `runtime_tool_selections` (unique per user, task and step) via `services/toolset_store.py`. A retried step, a re-picked-up child or a continuation step reuses the stored pick (`source="persisted"`), re-trimmed against current policy.
- **Widening.** Chat carries the previous pick forward and adds `request_tools(tool_ids, reason)`, which accepts only authorized-but-unselected ids, stores them, and offers them from the next message. It answers `BLOCKED` after a suspected injection. Unattended runs never get it.
- **Trace.** `__TOOL_SELECTION__` carries the choice; the Agent Tasks page shows the `Tool selection:` line of each step.

## Tool policy mechanics

`tool_policy.py` decides what an unattended run may use and what chat runs without asking. Storage: `tool_policies` (one optional override row per user and entry: `unattended_allowed`, `chat_mode`) and `tool_policy_modes` (per user: `unattended_mode`, `chat_mode`, each `per_tool` or `allow_all`).

**Unattended.** `unattended_tool_sets(db, user_id, entries, task_mode=, narrowed_to=, definition=)` returns `(offered, confirmed)`:

- *offered* = every entry not locked (`locked_reason`: `LOCKED_UNATTENDED`, plus every `mcp.*`/`a2a.*` id), intersected with `narrowed_to` (a definition's tools plus `core`, or a child's stored grant), and for `delegated` only `operation="read"` entries;
- *confirmed* = offered ∩ (entries the user allows ∪ a user-authored definition's `tool_ids` ∪ an approved Janus-drafted definition's `tool_ids` while its approval fingerprint matches);
- the adapter gets `allowed_tool_ids=offered`, `confirmed_tool_ids=confirmed`, `blocked_tool_ids=offered − confirmed`.

The default switch value is *Allowed* unless the entry is `confirmation="required"` (`default_unattended_allowed`). `PUT /api/tools/policies/{tool_id}` with `{"unattended_allowed": true|false|null}` overrides it (`null` resets); a locked entry answers `422`. `SWITCH_GOVERNED` entries (`self_evolution`) have no override: their allowance is their own feature switch, and a definition naming them never overrides it.

With `unattended_mode = "allow_all"`, *offered* also includes `images` (`ALLOW_ALL_UNLOCKS`) and the remote ids (the run then builds the managed registry and sets `ToolBuildContext.unattended_allow_all`), and *confirmed* is every offered entry except a switch-governed one whose switch is off. Delegated narrowing, withheld names, the chain guard and injection escalation still apply.

**Chat.** `chat_preconfirmed_tool_ids` = every non-`required` entry, plus `required` entries set to *Always allow* (`chat_mode = "always"`), or everything while the user's chat mode is `allow_all`. `chat_withheld_tool_ids` = `required` entries set to *Off in chat*; they are removed from the request, so they are neither built nor offered. An approval card's **Always allow** answer stores `always` for that entry.

## Chat

`websocket/chat.py` owns one conversation per socket. Per inbound message it:

1. parses the frame (`services/page_context.parse_message_frame`): plain text, or `{"type":"message","text":...,"page_context":{...}}`;
2. renders the system prompt and builds the managed registry and `RuntimeAdapter` fresh (nothing is cached for the socket);
3. drops *Off in chat* entries, asks the selector for a toolset (off the event loop), and calls `prepare_step(approval_gate=..., guard_tool_errors=True)`;
4. renders the page snapshot into the model's copy of the message as an untrusted `<page_context>` block (never stored);
5. streams `__TOOL_SELECTION__`, `__START__`, the reply and the side frames;
6. commits the assistant message, its tool calls and UI blocks before `__END__`; then the title of a new conversation and the post-turn memory suggestion commit separately.

Details:

- **Sessions.** The connection's session must not hold a write transaction while tools run: mutating tools write through `run_isolated_tool_mutation` (`agent_toolboxes/shared.py`) on a second session. A conversation deleted mid-turn rolls the turn back and ends the socket with `__ERROR__:This conversation was deleted.`
- **Inbound reader.** `ChatInbox` owns `receive_text()` for the whole connection: approval, page-action and introspection answers resolve pending futures; everything else queues as the next message (at most 8; one more closes the socket with 1008).
- **Tool events.** `services/tool_call_events.py` turns LangChain tool events into `__TOOL_EVENT__` frames (`call_id`, `tool_id`, `tool_name`, `phase`, redacted `args`, `result_preview` cut to 4 KB, `duration_ms`, `step_id`, `approval`). Argument keys that look like secrets, and whole `Bearer …`/`Basic …` values, are replaced with `[redacted]`. The last frame of each call is stored as a `conversation_tool_calls` row whether or not the client opted in.
- **Approval gate.** `services/tool_approval.py` (`ToolApprovalBroker`). The tool's worker thread blocks while the event loop sends `__TOOL_APPROVAL__` (tool, reason, redacted args, timeout) and awaits a per-call future. Answers: `approve`, `always`, `deny`. Deny, no answer within `JANUS_CHAT_TOOL_APPROVAL_TIMEOUT_SECONDS` (300), a disconnect, or a client without `tool_events=1` answer `BLOCKED` and are stored in `approval` (`denied`, `timeout`, `disconnected`, `unavailable`). An approval covers exactly one call with the arguments shown. A reconnect never replays a pending call.
- **Turn steps.** `services/plan_step_events.py` turns each model round that called tools into a step and streams `__PLAN_EVENT__` frames (`step_id`, `index`, `status`, `tool_names`). Steps are live only, never stored.
- **Page actions.** `ui_actions` tools (`navigate`, `set_filter`, `open_item`, `highlight`, `scroll_to`, `prefill_form`) send `__UI_ACTION__` and wait up to `JANUS_CHAT_UI_ACTION_TIMEOUT_SECONDS` (10) for the client's `ui_action_result`. The answer is the client's result. Server allowlists live in `services/ui_actions.py`.
- **Introspection.** `ui_introspection` tools send `__UI_INTROSPECT__` and wait for the page outline or one element's styles; `services/ui_introspection.py` re-sanitizes the answer (200 nodes, 24 KB).
- **UI blocks.** `render_ui` validates a block against the catalog in `services/ui_blocks.py` and streams `__UI_BLOCK__`; blocks are stored in `conversation_ui_blocks`.
- **Images.** `generate_image` calls the admin-configured provider (`services/image_generation.py`), stores the file under the per-user image directory (`services/image_store.py`, quota `JANUS_IMAGE_STORAGE_LIMIT_MB`) and adds an `image` block.
- **Memory suggestion.** After the reply the utility model may propose one short durable fact (`suggest_memory_fact`), stored with `source=agent`, `verification=unverified` and the conversation as its source, so the [Memory review](#memory-review) can check it. It is skipped when the turn had a suspected injection, and when Janus itself called `add_memory_fact`, `update_fact` or `group_facts` in the turn (a second, paraphrased copy would only duplicate it). The default rules (`DEFAULT_MEMORY_INSTRUCTIONS`) say what memory is for rather than listing what to store.
- **Models.** The chat model comes from the chat surface choice, the title and memory suggestion from the utility model. An explicit choice that cannot be built closes the socket with the error; there is no silent fallback.

## Triggers and the durable worker

**Lifecycle.** `runtime_triggers.status`: `queued → running → completed | failed | exhausted`, plus `waiting` (parked on delegated children) and `cancelled`. `trigger_service.py` owns the transitions.

- **Enqueue.** `enqueue_trigger` dedupes on `(task_id, idempotency_key)`; a cross-owner collision answers `409` without exposing the row. At most 25 claimable rows per owner (`MAX_ACTIVE_TRIGGERS_PER_OWNER`).
- **Claim.** Atomic for `queued`, retry-due `failed`, and `running` rows whose lease (300 s) expired. A claim stores a private token; completion and failure must match it, so a stale worker cannot finalize a reclaimed run.
- **Lease heartbeat.** While a handler runs, `trigger_lease.lease_heartbeat` renews the claim every third of the lease on its own session, so a long run is not reclaimed. A crashed worker stops renewing.
- **Retries.** A handler failure records `failed`; the row is retried up to `JANUS_RUNTIME_TRIGGER_MAX_ATTEMPTS` (3) with optional exponential backoff (`JANUS_RUNTIME_TRIGGER_RETRY_BACKOFF_SECONDS`, 0), then becomes `exhausted`. A `TerminalTriggerError` skips the remaining attempts.
- **Waiting.** A handler that returns a waiting plan parks the row `waiting` (not claimable, token cleared); `release_waiting_trigger` moves it back to `queued`.
- **Scheduled rows.** A row keeps its occurrence time as `created_at`; scheduled rows are claimable only once that time has passed.

**Worker.** `DurableTriggerWorker` (label `runtime-triggers`) polls every 5 s. Each poll first runs the scheduler tick (`startup._run_trigger_scheduler_tick`: seed missing built-ins for every user, enqueue due schedules, then delegation maintenance), then claims up to 10 runnable rows oldest first and runs each. Sessions are phase-scoped: selection, claim and each transition use short sessions; the handler runs outside any transaction. `DurableTriggerDispatcher` runs one envelope the same way for an inline caller. `JANUS_RUNTIME_TRIGGER_WORKER_ENABLED=false` leaves runs queued; `GET /api/runtime/triggers/worker` reports `{enabled, running}`.

**Ingress adapters.** `trigger_adapters.py`: `ScheduledTriggerSpec` derives the idempotency key from `(schedule_id, due_at)`, `EventTriggerSpec` from `(source, event_id)`. The payload contract is narrow: `objective` (required), `task_mode`, `max_steps` (≤ 100). A payload never carries tool policy or authorization.

**Handler.** `trigger_runtime.build_runtime_trigger_handler` (registered by `startup`):

1. resolves the definition from `RuntimeTrigger.definition_id`, never from the payload; a definition run uses `automation`, everything else `trigger`;
2. runs the built-in's `prepare` hook, which may add context or return `skip_output` (complete without a model call);
3. authorizes the step through `unattended_execution` with the policy sets above and the run's `trigger_id` threaded into `ToolBuildContext`;
4. runs `agent_task_runner.run_agent_task()`: at most 8 tool-calling rounds and 180 s by default; a definition may override with `max_tool_calls`/`max_run_seconds` (`0` = no limit). Hitting a limit raises rather than storing LangChain's "Agent stopped" placeholder;
5. stores the plan state, the raw answer on `RuntimeTrigger.output`, and runs the built-in's `finalize` hook (a failure is logged, the run stays completed).

A failed plan raises after its state is persisted, so the row's status follows the plan: `TerminalTriggerRunFailedError` (an explicitly requested tool denied for an immutable reason, missing owner, malformed payload, unusable explicit model) exhausts at once; anything else is retried. For ad-hoc runs a denial is fatal only when the payload named the tool; definition runs never fail up front on a denial, they rely on refusal stubs.

**History.** `GET /api/runtime/triggers/` pages runs (filters: status, kind, definition, time range) with each step's `result`/`step_error` decoded from plan state; the raw payload is never returned. `GET /api/runtime/tasks/` (Agent Tasks) lists trigger, automation and delegated runs with step traces. The `trigger-history` toolbox reads the same owner-scoped data.

## Trigger definitions

`trigger_definition_service.py` owns validation, scheduling, event fan-out and enqueueing for `trigger_definitions`.

- **Fields.** `name`, `objective`, `tool_ids` (validated against the registry), `kind` (`manual`, `schedule`, `event`), `schedule`, IANA `timezone`, `event_type`, `event_filter`, `max_runs_per_hour`, `enabled`, `origin` (`user`, `system`, `agent`), `builtin_key`, `llm_provider`/`llm_model`, `max_tool_calls`/`max_run_seconds`, `next_run_at`, `last_run_at`, and the proposal columns below.
- **Schedules.** `{"type":"daily","time":"HH:MM","days":[0-6]}` in the definition's timezone, or `{"type":"interval","minutes":N}` with 5 ≤ N ≤ 10080. `once` exists only for Janus follow-ups.
- **Scheduling is pull-based.** `run_due_schedules()` enqueues each enabled schedule whose `next_run_at` passed and advances it from now, so after downtime only the latest missed occurrence runs.
- **Every activation is a durable run.** `enqueue_definition_run()` uses a fresh task id per occurrence (`trigger-def-<id>-<hash>`) and the occurrence key (`manual:<ts>`, `schedule:<ts>`, `event:<type>:<id>`, `inline:<ts>`) as idempotency key.
- **Run now and inline.** `POST /api/runtime/definitions/{id}/run` queues a manual run (also for a disabled definition). `run_definition_inline()` claims and runs one in the calling thread; the dashboard briefing, `janus brief` and `janus digest` use it, and it works with the worker off.
- **Disable and delete.** Disabling or deleting cancels the definition's queued, waiting and retry-pending runs in the same transaction; delete also unlinks its runs (`definition_id = NULL`), because SQLite reuses ids. Built-ins cannot be deleted, only disabled or edited.
- **Model choice.** A definition's model runs through `ModelChoice`; an unusable explicit choice is terminal, never a fallback. Each result starts with a `Model: <provider>/<model>` line.

### Events and filters

`trigger_events.py` is the single catalog. Producers emit catalog members only; `emit_event()` rejects anything else, and adding an event means adding the member, its `EventSpec` (label, description, payload fields) and the producer call in one change. `GET /api/runtime/definitions/event-catalog` serves it to the editor.

| Event | Producer |
| --- | --- |
| `mail.received` | `processor.py`, for mail not filed as SPAM or IGNORE |
| `whatsapp.direct_message_received`, `whatsapp.group_message_received`, `whatsapp.group_mention` | `whatsapp_trigger_events.py`, for new live incoming messages with content |
| `planner.task_created`, `planner.task_changed`, `planner.calendar_event_created`, `planner.calendar_event_changed` | `planner_trigger_events.py`, after a committed sync |
| `webhook.received` | `services/webhook_service.py`, after signature verification |
| `telegram.message_received` | `workers/telegram.py`, messages from the linked chat |

`emit_event(db, owner_id, event_type, event_id, data)` enqueues one run per enabled, runnable definition of that owner subscribed to the type. The key `event:<type>:<event_id>` makes re-emission idempotent. Before enqueueing, `trigger_event_filters.admit()` checks the definition's `event_filter` (conditions `{field, op: in|not_in|contains, values}` on catalogued payload fields, all must match, case-insensitive, on the raw payload) and `max_runs_per_hour` (rolling hour). A dropped event increments `rate_limited_count`. New definitions get the event's default limit (10 for WhatsApp group events). The event data is HTML-escaped into an untrusted `<event_data>` block (12,000 characters) and stored under `payload.event`. The durable queue is the event bus; there is no other.

### Janus follow-ups

`create_scheduled_trigger` (the `triggers` toolbox) creates a one-time `agent`-origin definition (`schedule={"type":"once",...}`, empty `tool_ids`, `proposal_status = NULL`) and enqueues its single run. Follow-ups run without approval in `trigger` mode with the default model. They accept only `enabled=false` as an edit, and cannot be run again. An unattended run's follow-ups are bounded by the chain guard (`RuntimeTrigger.parent_trigger_id`, `chain_depth`; limits in [safety](llm-agent-safety.md#follow-up-chain-guard)). The schedule horizon is one year.

### Janus-started conversations

`start_conversation(title, message)` (`conversations.start`, `trigger` and `automation` modes only) creates a new conversation for the run's owner whose first message is Janus's (`conversations.origin = "janus"`, `unread = true`, `origin_trigger_id`). `services/conversation_service.py` fails closed without a durable run, allows one per run and five per owner per rolling day (counted again inside the write transaction), caps title and message, and refuses URLs, links and images. The web app marks such conversations **Started by Janus**; the action inbox lists them while unread.

### Proposals

**Automations.** From chat, `triggers.propose` (`agent_toolboxes/automation_proposals.py`) drafts definitions with `origin="agent"`, `proposal_status="proposed"`, `enabled=false`. `trigger_proposal_service.py` enforces the limits (5 open, 3 per day, 1 per turn, 4,000-character objective, 60-minute interval floor, proposable tools only, fingerprint dedupe including 30 days of rejections). Drafts are inert: the scheduler, `emit_event`, edits, run-now and delete skip or refuse them, and the runtime fails a run of a definition that is not `NULL` or `approved`. Only `POST /api/runtime/definitions/{id}/approve` (user session) writes the approval: `approved_by_user_id`, `approved_at` and `approval_fingerprint` (`trigger_approval.py`). A replacement draft names `supersedes_id`; approving it supersedes and disables the old one. Statuses: `proposed`, `approved`, `rejected`, `withdrawn`, `superseded`. The draft records which untrusted sources the turn read and whether it saw a suspected injection; the review card shows both.

**Other proposal flows**, all approved by the user in the app: wiki edits (`wiki.propose`), character state changes (`character.propose`), planner suggestions (`planner.suggest`, the mail tools and the secretary), skill suggestions (`services/skill_proposer.py`, from repeated tool sequences) and knowledge-graph merge suggestions. The action inbox (`services/inbox_service.py`) lists what waits.

## Built-in triggers

`builtin_triggers.py` seeds these per user (`origin="system"`) on every scheduler tick and before inline runs. It only creates missing ones and never overwrites edits.

| Key | Name | Seed | Tools | Hooks |
| --- | --- | --- | --- | --- |
| `builtin.briefing` | Morning briefing | daily 06:30 | core, calendar, jira, planner.local, topics, memory, mail | `prepare`: planner summary and widget instructions as `<prepared_context>`; `finalize`: mark topics briefed |
| `builtin.digest` | Afternoon digest | daily 16:00 (from the user's digest preference when set) | core, mail, topics, memory, planner.local, mail.processing | `prepare`: DIGEST mail since the last digest (newest 50) with topic threads, or `skip_output`; `finalize`: record the digest, notify |
| `builtin.mail` | Mail processing | event `mail.received` | core, mail, mail.processing, rules, memory, topics, planner.local, calendar | `prepare`: pending suggestions as context |
| `builtin.secretary` | Secretary | every 60 min | core, calendar, jira, planner.local, topics, memory, mail, whatsapp, knowledge, wiki, web_search, triggers, planner.suggest, conversations.start, whatsapp.notify_me | see below |
| `builtin.knowledge` | Knowledge extraction | every 30 min, off | none | `prepare`: entity and relation extraction, always `skip_output` |
| `builtin.wiki` | Wiki synthesis | every 60 min, off | none | `prepare`: create, refresh and prune wiki pages, always `skip_output` |
| `builtin.memory_review` | Memory review | daily 03:17 UTC, **on** | none | `prepare`: verify, merge and promote memory facts (see [Memory review](#memory-review)), always `skip_output` |

Built-ins are seeded in UTC. `adopt_client_timezone()` moves a still-unedited briefing and secretary to the browser's timezone the first time one is reported. Knowledge extraction, wiki synthesis and the memory review make direct model calls within one shared per-user monthly token budget. Self-evolution self-checks are not seeded: each is a one-time definition with `builtin_key = "selfevo.check:<id>"` whose `prepare` runs the check without a model.

### Proactive secretary

The secretary is an ordinary hourly definition whose `prepare` hook (`services/secretary_service.py`) decides in code whether the hour costs a model call:

1. `skip_output` outside the owner's waking hours (`secretary_active_start_hour`/`secretary_active_end_hour`, default 8 to 21, in the definition's timezone);
2. `skip_output` when the last run that thought is younger than six hours and nothing changed since: new mail, WhatsApp messages, reminders, planner events, or a changed fingerprint of calendar events and tasks (stored by `finalize`);
3. otherwise context: the clock, change counts, the last four findings, and the outcome of each conversation it started in the last 14 days, all in `<prepared_context>` blocks.

The objective tells it to end with `NOTHING_TO_REPORT` when nothing deserves attention. Delivery is `start_conversation` or, for urgent items, `notify_me_on_whatsapp`: recipient fixed to the owner's configured number, one per run, three per day, 600 characters, no links.

## Delegation

A chat or trigger step's `delegate_task(objective, tool_ids)` (`delegation` toolbox) creates a `runtime_delegations` row and a child task.

- **Execution.** Children run on `delegation_runtime.DELEGATION_RUNNER`, a `ThreadPoolTaskRunner` of `MAX_ACTIVE_DELEGATIONS_PER_OWNER` (4) workers, through `unattended_execution` in `delegated` mode with the same prompt, policy and refusal machinery as triggers. The child inherits the parent's model choice.
- **Reach.** Requested tools must be authorized for the parent, read-only and unlocked (`tool_policy.delegable_tool_ids`); the stored grant is narrowed again when the child runs. Depth is 1 (`delegation` is not read-only, so a child never gets it). At most four active per owner.
- **Results.** Chat polls `get_delegated_task`/`list_delegated_tasks`. A trigger's first step may call `wait_for_delegated_tasks` once (up to four ids it created); the run parks `waiting` (`delegation_wait.py`) and a continuation step runs when every child is done, re-authorized afresh.
- **Re-pickup.** Delegation maintenance on each scheduler tick requeues an interrupted child once (`MAX_DELEGATION_REQUEUES`), clearing its claim token; a second interruption fails it. Lease: 300 s.
- **Views.** `GET /api/conversations/{id}/delegations` and the Agent Tasks page.

`background_tasks.py` is the minimal runner abstraction (`submit`, `status`, idempotent by key). It is not durable; durability comes from the rows and the worker's maintenance tick.

## Adaptive character state

`character_state_service.py` holds a typed state per user (`character_states`): `tone`, `verbosity`, `proactivity`, `current_focus` (one line, ≤ 200 characters). The owner sets an envelope (`character_profiles.state_envelope`: allowed values, defaults, whether a focus may be set); every write is checked against it. Janus proposes changes only from chat (`propose_character_state`, one per turn, five open), into `character_state_proposals`; the owner approves on Settings → Character. Every change writes a `character_state_changes` row (actor, action `edit`/`proposal`/`rollback`/`reset`/`envelope`, before and after, revision), and any earlier state can be restored. The constitution itself is never changed by Janus.

## Self-evolution loop

An opt-in, admin-only loop (`services/self_evolution_*.py`, `agent_toolboxes/self_evolution.py`): file a Linear issue, fire a Claude Code routine, merge the resulting PR and the release PR, then self-check production. Each step is a row in `self_evolution_actions`, reserved against daily caps before any outbound call. The gates (scope fence, merge provenance, CI, caps, revert) are specified in [safety](llm-agent-safety.md#self-evolution-gates).

## Mail ingestion

Each mail worker (`workers/email.py`) does a bounded initial sync (default 100 messages per account), then listens with IMAP IDLE and a heartbeat (`imap_service.py`); Outlook mailboxes poll Microsoft Graph (`graph_mail_service.py`). Each new message goes to `EmailProcessor.process_email` (`processor.py`):

1. store the email for its account's owner;
2. apply the owner's regex rules (`re2`); a SPAM or IGNORE rule is final;
3. if the trigger worker is enabled and the owner has an enabled `mail.received` definition whose filter admits this mail, emit `mail.received`: the Mail processing run decides through `mail.processing` (`set_email_priority`, `create_reminder`, `track_email_topic`, `suggest_planner_item`), which write only Janus-local state;
4. otherwise run the synchronous path: a constrained JSON classifier guided by the character profile, plus the reminder, topic and planner-suggestion extractors;
5. stage attachments as knowledge sources (not for SPAM/IGNORE).

Categories: IMPORTANT (kept unread, may notify), DIGEST (marked read on the server, goes to the digest), IGNORE, SPAM. Each Janus-assigned category stores reasoning and a confidence. A user relabel (`PATCH /api/emails/{id}/priority`) saves an owner-scoped rule for similar mail. With the event path active, every new mail is one agent run.

## Planner sync and events

The `planner` worker runs `PlannerService.sync_all` every 30 minutes over Google Calendar, Google Tasks, Microsoft Calendar and Jira connectors (`connectors/`); `models.CONNECTED_PLANNER_PROVIDERS` names these four; the enum keeps `ado` and `o365` only so old rows load, and the planner API and `janus planner add` refuse them. `capability_profile` maps calendar providers to the `calendar` capability and Jira to `jira`. Synced datetimes are stored as naive UTC. Each synced task and event is compared with its stored row; new and changed items emit `planner.*` events after the sync commits. An account's first (baseline) sync emits nothing; deletions emit nothing. Sync errors land in `last_sync_error`. Local tasks and events (`services/local_planner_service.py`) are the writable planner.

## Messaging connectors

- **WhatsApp** (`whatsapp_neonize.py`, optional `whatsapp` extra). One supervised thread per enabled account holds a Neonize connection. Messages arrive as live events or a one-time history push after pairing; there is no pull API. Each event handler uses its own session. Pairing runs a separate short-lived, unsupervised connector per setup session:
  - Every pairing attempt opens a new session store, `whatsapp_sessions/setup-<id>-<random>.db`. Neonize logs in as the first device stored in an existing file instead of offering a QR code, so a reused file would resume a stale, possibly unlinked device.
  - A `LoggedOutEv` or `ConnectFailureEv` during pairing marks the setup session `FAILED` with the reason and stops the client, so the wizard reports the failure instead of waiting for a QR code. In account mode `ConnectFailureEv` marks the account `DEGRADED`.
  - A setup connector ends with the client's `stop()`, not `disconnect()`: in Neonize `disconnect()` leaves the blocking `connect()` running, so the pairing thread would never end. Account connectors are supervised and keep their own shutdown path.
  - Event translation lives in `whatsapp_translate.py`.

  A failed native import is probed once per process and never retried, because a re-import can crash the process.
- **Telegram** (`workers/telegram.py`). One worker per linked bot long-polls `getUpdates` (25 s), reloads the bot row each poll, and emits `telegram.message_received` for messages from the linked chat. `telegram.notify` sends only to that chat.

## Knowledge and memory workers

- **Knowledge index** (`services/knowledge_index.py`). Every 60 s: ingest up to 50 knowledge sources (staged attachments and uploads parsed with markitdown when the `documents` extra is installed; mail, chats, events and tasks rebuilt as sources), then embed stale facts, topics, chunks and entities. It does nothing without an embedding provider or without sqlite-vec. Memory and topic writes also embed inline after commit. `janus knowledge backfill` and `janus knowledge reembed` run the same passes from the CLI.
- **Memory compaction** runs on a background thread started from the Memory page; it merges overlapping facts and archives the originals (`memory_curation.apply_compactions`, shared with the memory review).
- **Memory review** is the `builtin.memory_review` trigger, below.
- **Topics** are captured from mail, corroborated by several mails before promotion, and read by the briefing, the digest and the topic tools.

## Memory review

`services/memory_review.py` is the `prepare` hook of `builtin.memory_review`: the offline pass the article calls *dreaming*. It runs daily, on by default, with no tools and no agent step: direct calls to the utility model (or the definition's model) within the knowledge budget, stopping before a call that would exceed it.

1. **Verify.** Up to 8 due facts (`FACTS_PER_RUN`) are checked against their source conversation, the ground truth. Due means: active, `source` `agent` or `compaction`, a source conversation the user owns, and `review_due_at` empty or past. Facts the user wrote or edited themselves are `confirmed` without a quote and never due. Order: unverified first, then by `access_count`, then by `last_accessed_at`. The transcript is the conversation's numbered messages (2,000 characters each); above 12,000 characters only the messages sharing the most words with the fact, and their neighbours, are kept. Fact and transcript are HTML-escaped inside `<fact>` and `<transcript>` with a do-not-follow sentence. The model answers a verdict, a quote and whether the fact is volatile:

   | Verdict | Effect |
   | --- | --- |
   | `confirmed` | `verification=confirmed`, the quote stored as `evidence_quote` |
   | `corrected` | summary and detail rewritten from the transcript, `verification=corrected`, quote stored, re-embedded |
   | `contradicted` | archived, `verification=contradicted`, removed from the index |
   | `unsupported` | kept, `verification=unsupported`, never promoted |

   The quote must appear in the transcript (whitespace and case ignored, at most 500 characters); a `confirmed` or `corrected` answer without such a quote counts as `unsupported`. A checked fact is due again after 90 days (`REVIEW_STABLE`), 14 days when volatile (`REVIEW_VOLATILE`), 30 days when unsupported, each halved when Janus read the fact in the last 7 days. An unusable answer leaves the fact due again in a day.
2. **Merge.** When an active fact changed in the last day, the 100 most recently updated active facts go through `suggest_memory_compactions`, as the on-demand compaction. A merged fact keeps the newest source conversation, `confirmed` (with the sources' quotes) when every source was confirmed or corrected, the summed access count, the promotion of any source, and the sources' pin (pinned wins).
3. **Promote.** When the run changed something, or no fact is promoted yet, the model picks at most `15 − pinned` facts from up to 80 confirmed or corrected, unpinned facts (`promoted`). These form the memory index with the user's pins.

`access_count` is bumped whenever Janus's `memory` tools return a fact, and `last_accessed_at` also when the Memory page lists it (raw SQL, no `updated_at` change). The run's output is one summary line.

## Live UI refresh

Every background writer (trigger runs, delegations, mail ingestion, planner sync, connectors, knowledge workers) commits through ORM sessions, so `domain_changes.py` publishes the owner's changed domains to `/ws/changes` without any extra code. See [Backend and API](backend-api.md#wschanges).
