# Tool permissions

**Status:** In progress (part A: unattended switch, shipped; chat approval step: JAN-254, shipped; per-tool *In chat* switch: JAN-306, shipped; *Always allow* and the blanket *Allow everything* / *Allow all* modes: JAN-359, shipped; "Allow for this conversation": not adopted (owner decision); iOS approval card: not built)
**Linear:** JAN-255; chat approval: JAN-254; *In chat* switch: JAN-306; *Always allow* and blanket modes: JAN-359; unlocking `events` and `triggers`: JAN-272, JAN-274 (shipped)
**Depends on:** Tool registry (`tool_registry.py`, `agent_tools.py`), runtime adapter and task modes (background workers runtime tech spec), tool policy (JAN-229), trigger definitions (JAN-228), delegation (JAN-200), LLM and agent safety tech spec

## Problem

Seven separate checks decide whether Janus may use a tool. The owner can see
one of them, and even that one is hard to read.

| # | Check | Where it lives | Visible? |
| --- | --- | --- | --- |
| 1 | Per-mode allowlist (`chat`, `trigger`, `delegated`, `automation`, `briefing`, `digest`, `ingestion`) | `TASK_MODE_ALLOWED_TOOL_IDS` in `runtime_adapter.py` | No |
| 2 | Withheld tool names per mode (a trigger may not delete memory facts) and tools withheld from every unattended run (`events`) | `TASK_MODE_WITHHELD_TOOL_NAMES`, `UNATTENDED_WITHHELD_TOOL_IDS` | No |
| 3 | Connection state (mail, calendar, and so on) | `enabled_when`, `capability_profile.py` | Indirectly |
| 4 | Registry confirmation level `never` / `recommended` / `required` | `agent_tools.py` | Shown as text |
| 5 | "Block without confirmation" (unattended only) | `tool_policy.py`, Settings → Tools & Connections | Yes |
| 6 | Tools picked on a user-authored trigger definition count as confirmed | `tool_policy.unattended_confirmed_tool_ids` | Partly |
| 7 | A delegated child task gets its parent's tools, narrowed to a read-only list | `agent_toolboxes/delegation.py` | No |

This causes three concrete problems:

- **The visible switch often does nothing.** Unblocking a tool (5) has no
  effect on an ad-hoc trigger or a delegated task when the hidden allowlist
  (1) leaves it out. For example, `web_search` can never run in an ad-hoc
  trigger, but it can run in a saved automation.
- **The label is a double negative.** "Block without confirmation: off" is
  the only way to say "allowed".
- **Chat never asked first** (fixed by JAN-254). `websocket/chat.py`
  pre-confirmed the whole catalog
  (`confirmed_tool_ids=frozenset(catalog_tool_ids)`), so
  `confirmation="required"` was metadata the UI displayed, not a gate.
  *Send mail*, *GitHub*, *Linear*, planner writes, rule writes, skill edits,
  and `propose_wiki_edit` all ran on the model's judgement alone.

## Goal

The owner can see and change, per tool, what Janus may do in chat and in
runs with nobody watching. That page is the whole answer: no hidden
allowlist can contradict it. The only exceptions are a few fixed safety
limits, shown on the page with their reason.

## Design

### The table (Settings → Tools & Connections)

There is one row per registry entry (toolbox), with these columns:

| Tool | What it does | In chat | Unattended |
| --- | --- | --- | --- |
| Send mail | Sends email from your accounts (write, high risk) | Ask every time ▾ | Off ▾ |
| Calendar | Reads your calendar | Allowed | Allowed ▾ |
| Tracked topics | Reads and updates topics | Allowed | Allowed ▾ |
| Schedule triggers | Schedules one-off runs | Allowed | Allowed ▾ (bounded by the chain guard, JAN-274) |
| (a locked tool) | Any `LOCKED_UNATTENDED` entry; today the chat-only `triggers.propose`, `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, and `character.propose` | Allowed | 🔒 Never: <reason> |

- **In chat:** a `confirmation="required"` tool is *Ask every time* (the
  default, JAN-254's per-call Approve / Deny) or *Off in chat* (withheld
  from chat entirely); every other tool is *Allowed* and has no switch
  (part B below, JAN-306). No setting lets a `required` tool run in chat
  without the user approving that call.
- **Unattended:** *Allowed* or *Off*. This one switch covers every
  agent-driven run with nobody watching:
  - ad-hoc triggers Janus schedules (`trigger` mode);
  - saved trigger definitions, including the built-in briefing, digest, and
    mail processing (`automation` mode);
  - delegated child tasks (`delegated` mode), within the limits below.
- Each row shows its default and a "reset" action when changed, like today.
- A row whose connection is missing says so ("Connect Jira to use it")
  instead of pretending a switch matters.
- Switching a **write** tool (`operation` `write`, `mixed`, or `admin`) to
  *Allowed* for unattended runs asks for confirmation first, in an in-app
  dialog rather than the browser's (JAN-275): "Janus will be able to
  <description> with nobody watching. Every run still records which tools it
  used."

### Fixed safety limits

These stay in code. An entry in `tool_policy.LOCKED_UNATTENDED` is shown as a
locked row, with the reason; `triggers.propose`, `ui_actions`, `ui_blocks`, `images`, `ui_introspection`, and `character.propose` (JAN-301) are locked today:

- **`triggers` and `events` are no longer locked (JAN-272, JAN-274).** Part A
  shipped with both locked: a run that can schedule further runs is an
  unbounded loop (JAN-184), and the event log had no owner column (JAN-195).
  The owner asked for both to be solved rather than unlocked. `events` now
  reads only the run owner's rows unattended (`models.Event.user_id`), and
  `triggers` enforces a chain guard in the toolbox (parent run and depth on
  every follow-up; depth at most 2, at most 3 follow-ups per run, at most 10
  unattended-scheduled follow-ups per owner per day, at least 15 minutes
  ahead, one-time only, unattended cancel limited to Janus's own
  follow-ups). Both are normal rows: *Allowed* by default (`events` is
  `confirmation="never"`, `triggers` `"recommended"`) and switchable. The
  ticket title asked for "always allowed"; default-on but switchable was
  chosen so that every tool keeps one consistent, user-visible control. See
  the background workers runtime spec for the guard's details.
- **`triggers.propose` is never available unattended.** Drafting
  automations for the user to approve is chat-only (JAN-235).
- **`ui_actions` is never available unattended.** Page actions (JAN-251)
  navigate and filter the page the user has open in the web UI; with nobody
  watching there is no page.
- **`ui_blocks` is never available unattended.** Chat UI blocks (JAN-252)
  are rendered by the web chat; an unattended run has no chat to show them in.
- **`images` is never available unattended.** Image generation (JAN-253)
  costs money and sends the prompt to a cloud provider, and its picture is
  shown in the web chat; it runs only in a chat turn the user is watching.
- **`ui_introspection` is never available unattended.** Reading the outline
  and styles of the user's open page (JAN-283) needs a human's live web
  client; with nobody watching there is no page, and the outline must never
  leave a chat turn the user is watching.
- **Delegated child tasks** get only read-only (`operation="read"`) tools,
  narrowed to what their parent had, and never `delegation` or `triggers`. The one-level
  depth cap and the fan-out cap stay.
- **Withheld destructive tools** keep their current per-mode lists
  (`delete_fact`, `set_dashboard_widget_prompt`,
  `clear_dashboard_widget_prompt` in `trigger` mode). They are listed under
  their toolbox's row ("Unattended runs can add and update memory facts but
  not delete them").
- **System pipelines** are not agent choices and keep their fixed lists: the
  mail-ingestion classifier (`ingestion`) and the recorded briefing and
  digest steps (`briefing`, `digest`, which make no tool calls today). The
  page footnote names them.

### Trigger definitions

- On a definition **the user authored** (`origin=user`), picking a tool
  allows it for that definition even when the tool is *Off* for unattended
  runs (owner decision, 2026-09-29). The picker marks such tools: "Off for
  unattended runs; allowed here because you picked it."
- A definition Janus drafted (JAN-235) counts like a user-authored one only
  once the user approved it on Settings → Triggers, and only while its content
  still matches what was approved (or last saved by the user); see
  [Agent-proposed automations](agent-proposed-automations.md).
- Built-in definitions, unapproved Janus drafts, and Janus's one-time
  follow-ups get only their tools that are *Allowed* unattended. A tool they list that is *Off* stays offered
  and answers `BLOCKED`, and the refusal is recorded, as today.

### How a run's toolset is computed

For an unattended run, two sets are computed. The run may *see* the first
set of tools and may *execute* the second:

```
offered(run) = all tools
             ∩ (definition.tool_ids      if the run is a definition run)
             ∩ (parent tools ∩ read-only if the run is a delegated child)
             − LOCKED_UNATTENDED   (today: {triggers.propose, ui_actions, ui_blocks, images, ui_introspection})

confirmed(run) = offered(run)
               ∩ ( {t : unattended(t) = Allowed}
                   ∪ (definition.tool_ids if the definition is user-authored) )
```

- `offered(run)` is the run's allowlist (`allowed_tool_ids` for the runtime
  adapter). The withheld tool names are removed from it.
- `confirmed(run)` is what the run may execute (`confirmed_tool_ids`). The
  adapter's `blocked_tool_ids` becomes `offered − confirmed`, so an *Off*
  tool is gated whatever its registry confirmation level.
- A tool in `offered` but not in `confirmed` is one switched *Off*. It stays
  offered as a `BLOCKED` stub (the adapter's `confirmation_required` denial,
  which is stubbed), and the refusal is recorded, as in JAN-229 today.
- Locked tools never reach `offered`, so they are denied `not_allowlisted`.
  Connection state is left to the runtime adapter: a disconnected tool is
  denied `missing_capability` or `disabled_by_context`, as today.

For an ad-hoc trigger with no definition, `offered` is every tool except
any locked one. A run whose step names no tools (the usual case for an ad-hoc
trigger or a delegated child) requests only `confirmed`, so switched-off
toolboxes are left out rather than offered as a pile of refusal stubs. A
definition run's default request is its own `offered` set, so an *Off* tool
the definition lists still answers `BLOCKED`, as does any *Off* tool a step
names explicitly.

`TASK_MODE_ALLOWED_TOOL_IDS["trigger"]`, `["delegated"]` and
`["automation"]` are removed. They are replaced by one function,
`tool_policy.unattended_tool_sets(db, user, mode, definition, parent)` (returning `offered` and `confirmed`), which
`unattended_execution` calls. `chat`, `briefing`, `digest` and `ingestion`
keep their entries.

### Defaults (no silent change for saved automations)

`unattended` defaults to *Allowed* for every tool whose registry
confirmation is not `required`, and *Off* for the `required` ones. This is
exactly today's rule for saved automations, which is where most unattended
work happens, so saved automations run with the same tools as before.

**Behavior change (accepted by the owner, 2026-09-29):** ad-hoc triggers and
delegated tasks follow the same defaults, so they gain tools their hidden
allowlists left out.

- Ad-hoc triggers gain `mail` (read), `mail.label`, `mail.processing`,
  `planner.local` (read), `web_search`, and `rules` (read).
- Delegated tasks gain `mail`, `planner.local`, `web_search`, and `rules`,
  all read-only.

`web_search` was kept chat-only for ad-hoc triggers on purpose (JAN-211). The
owner decided (2026-09-29) that it follows the same default as the other
read tools, *Allowed*, as it already was in saved automations. One switch per
tool, rather than one per surface, was also confirmed.

### Part B: "Ask every time" in chat

The approval step itself shipped with JAN-254 (decision 2026-09-28: per
call, never a standing grant). What is built:

- **Who asks:** every tool whose registry confirmation is `required`. The
  others run without asking, as before.
- **Flow:** when the model calls such a tool, the call pauses. Chat streams a
  `__TOOL_APPROVAL__` frame with the tool, its redacted arguments, and the
  registry's description of it. The web UI shows an inline approval card
  (Approve / Deny) in the sidebar chat, on `/chat`, and in all three shells.
  The answer comes back over `/ws/chat` as
  `{"type": "tool_approval", "call_id", "decision"}`.
  - Approve runs that one call, with the arguments the card showed.
  - Deny answers the model `BLOCKED: <tool> was not run because the user
    declined.` (the tool-policy refusal shape) and records it on the
    message's tool call (`approval: "denied"`).
  - No answer within `chat_tool_approval_timeout_seconds` (default 5
    minutes) or a disconnect counts as Deny (`timeout`, `disconnected`). A
    reconnect never replays or approves a pending call.
- **Clients without the card** (anything not opening
  `/ws/chat?tool_events=1`, today the iOS scaffold) get `BLOCKED`
  (`unavailable`) for these tools; they never run unasked.
- The protocol is specified under "Chat tool approval" in the
  [background workers runtime spec](../tech-spec/background-workers-runtime.md).

**Per-tool *In chat* switch (JAN-306, shipped).** Each `required` row has
an *In chat* select next to *Unattended*:

- ***Ask every time*** (default, no override stored): the tool is offered
  and every call goes through the approval card above.
- ***Off in chat*** (`tool_policies.chat_mode = "off"`): the tool is
  withheld from chat entirely. Chat does not request it, so it is not built,
  not offered to the model, not listed in the turn's `__TOOL_SELECTION__`,
  and not stubbed as a `BLOCKED` tool (unlike an *Off* unattended tool, which
  stays offered as a refusal stub). A delegated child of a chat turn cannot
  reach it either, since the child's grant is narrowed to the parent's.
- ***Always allow*** (`tool_policies.chat_mode = "always"`, JAN-359,
  owner decision reversing JAN-306's "no third value"): the tool is offered
  and runs without an approval card. The approval card's **Always allow**
  button approves its call and stores this value for the tool (the
  decision is recorded as `approved`). Chat's pre-confirmed set is
  `tool_policy.chat_preconfirmed_tool_ids`: every non-`required` entry,
  plus `required` entries switched *Always allow*.
- Non-`required` tools have no *In chat* switch (shown as *Allowed*); `PUT`
  with a `chat_mode` for one answers `422`.
- The switch is per user (a `tool_policies` row is keyed on user and tool),
  read fresh on every chat message, and has no effect on unattended runs,
  which follow only the *Unattended* switch.

**Blanket modes (JAN-359, shipped).** One `tool_policy_modes` row per user
sits above the per-tool switches (no row: both `per_tool`):

- ***Unattended: Allow everything*** (`unattended_mode = "allow_all"`):
  `unattended_tool_sets` confirms every offered entry, whatever its
  *Unattended* switch says, and `tool_policy.unattended_locked_reason` lifts
  the locks on `images` and on remote MCP tools
  (`tool_policy.ALLOW_ALL_UNLOCKS`). The unattended runtime then builds the
  managed registry (with the owner's offered remote tools) and passes
  `unattended_allow_all=True` to `prepare_step`, which is the only way those
  entries' `enabled_when` lets them build outside chat. What stays: the
  page-bound and propose locks (`ui_actions`, `ui_blocks`,
  `ui_introspection`, `triggers.propose`, `character.propose`), the
  self-evolution switch (JAN-320), the delegated child's read-only,
  parent-narrowed grant (`delegable_tool_ids` still uses the fixed locks),
  the trigger chain guard, withheld tool names, and injection escalation.
  An unattended `generate_image` stores the picture with
  `conversation_id = NULL` and answers its URL.
- ***In chat: Allow all*** (`chat_mode = "allow_all"`): every requested
  `required` entry is pre-confirmed in chat; entries switched *Off in chat*
  stay withheld.
- Turning either mode on asks for confirmation in Settings and is logged as a
  `WARNING` event (`tool_policy.modes_changed`).

**"Allow for this conversation": not adopted (owner decision, JAN-306).**
A conversation-scoped grant on the approval card (so repeated calls of the
same tool in one conversation stop asking) would weaken JAN-254's strict
per-call approval: after one approve, later calls with different arguments
would run unseen. The owner decided not to build it; every `required` call
keeps its own card. Reopening it needs a new owner decision; the earlier
sketch (grant keyed on conversation and tool, never persisted across
conversations, visible and revocable on the card, logged on every use)
would be the starting point.

Not built yet:

- The iOS approval card.

### Data and API

- **Data:** a `tool_policies` row is an override; no row means the default.
  - Part A replaces `block_without_confirmation` with `unattended_allowed`
    (boolean). The migration fills it with the inverse of the old column
    (blocked becomes not allowed) and drops the old column. It must work on
    SQLite and PostgreSQL.
  - Part B (JAN-306) adds a nullable `chat_mode` string: `NULL` means the
    default (*Ask every time*), `"off"` withholds the tool from chat, and
    `"ask"` is the explicit form of the default. `unattended_allowed`
    becomes nullable in the same migration, so a row can override only
    one of the two switches; a row with both `NULL` is deleted.
- **API:** `GET /api/tools/policies` returns, per tool:
  - `tool_id`, `unattended_allowed`, `default_unattended_allowed`,
    `overridden`;
  - `locked_reason` (or `null`), and `withheld_unattended_tools` (tool names
    unattended runs never get from this toolbox);
  - part B adds `chat_mode` (`"allowed"` for a tool without the switch,
    otherwise `"ask"`, `"always"` (JAN-359), or `"off"`) and
    `default_chat_mode` (`"allowed"` or `"ask"`); `overridden` is true when
    either switch is overridden;
  - JAN-359 adds `effective_unattended_allowed` and `effective_chat_asks`:
    what applies once the blanket modes are taken into account.

  `GET /api/tools/registry` carries the same fields on each built-in, next to
  its description, `operation`, `risk_level`, and connection state.
  `PUT /api/tools/policies/{tool_id}` takes `{"unattended_allowed": bool |
  null, "chat_mode": "ask" | "always" | "off" | null}`; each field is optional (an
  omitted field is left unchanged) and `null` resets that switch. Changing
  a locked tool's unattended switch answers `422`, as does setting
  `chat_mode` on a tool that is not `confirmation="required"`. The old
  `block_without_confirmation` field is removed from the API.
  `GET`/`PUT /api/tools/policy-modes` (JAN-359) read and change
  `{"unattended_mode": "per_tool" | "allow_all", "chat_mode": "per_tool" |
  "allow_all"}`; an omitted field is left unchanged.

## Delivery

Two PRs, both under JAN-255:

1. **Unattended switch.**
   - Covers the table with the *Unattended* column and the locked rows, the
     `unattended_tool_sets` function that replaces the three mode allowlists,
     the migration, the API, and the note in the definition picker.
   - Tests: the pinned allowlist tests (`test_tool_registry`,
     `test_delegation`, `test_trigger_runtime`, `test_agent_tools`) become
     tests of the computed sets; a new test pins today's automation defaults
     as unchanged, and another checks that an Off tool answers `BLOCKED` and
     is recorded. Playwright covers switching a tool, the write-tool warning,
     and reset. Stories cover the table with locked, disconnected, and
     changed rows.
2. **"Ask every time" in chat (part B).**
   - The approval frame, card, and timeout shipped with JAN-254; the *In
     chat* column with JAN-306. "Allow for this conversation" was not
     adopted (see part B).
   - Tests: websocket tests for approve, deny, and timeout (JAN-254); for
     the *In chat* switch, that an *Off in chat* tool is withheld (not
     stubbed) from the chat toolset, that the default stays per-call
     approval, that another user's setting never applies, and that the
     switch does not change unattended runs (JAN-306). Playwright covers
     approving, denying, and switching a tool off in chat. `*.stories.tsx`
     covers the card's pending, approved, denied, and timed-out states and
     the table with a tool off in chat.

Docs in the same PRs:

- `llm-agent-safety.md` and `background-workers-runtime.md`: the model above
  replaces the per-mode allowlist narrative.
- `backend-api.md`: the API.
- `data-model-migrations.md`: the columns.
- `frontend.md`: the table and the approval card.
- `configuration.md`: the user-facing text.
- This page's Status becomes Shipped with PR 2.

## Out of scope

- Per-surface unattended columns (briefing versus triggers versus
  delegated). One switch is the decision; the table leaves room to split it
  later.
- Per-tool-function switches inside a toolbox. The withheld-names list stays
  the only function-level limit.
- Remote MCP tools in unattended runs: their policy intersection (JAN-158)
  is unchanged. Chat offers them since JAN-343 (read-only-annotated tools run
  without asking, all others ask per call, and *In chat* works for those).
  Their *Unattended* switch is locked (`tool_policy.locked_reason`) until
  JAN-344 opens them to unattended runs per tool; only *Unattended: Allow
  everything* (JAN-359) opens them all at once.
