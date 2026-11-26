# Proactive secretary

**Status:** Shipped (phase 1; everything under "Out of scope" is planned, not built)
**Linear:** not yet filed (create the issue and link it here before the PR is marked ready)
**Depends on:** Trigger runtime and built-in definitions (JAN-231), Janus-started conversations ([JAN-319](agent-started-conversations.md)), WhatsApp send ([JAN-360](whatsapp-neonize-live-connector.md)), tool permissions / Unattended switch ([JAN-255](tool-permissions.md)), unattended chain guard (JAN-274), prompt-injection guardrails (JAN-127)

## Problem

Janus answers well and runs the jobs it was given, but it does not think ahead.
Everything outside chat is a trigger with a fixed objective (briefing, digest,
mail filing, knowledge, wiki), and the one tool that can reach the user
unprompted (`start_conversation`, JAN-319) has no caller that decides on its own
when something is worth saying. The user has to start every line of thought.
The goal is a *secretary*: something that looks at the user's world on its own,
works out what is coming or going wrong, does the safe parts, and reaches the
user only when it matters.

The adaptive `proactivity` and the constitution's `exploration_rate` do not
help here. `proactivity` only changes how readily Janus suggests next steps
*inside a reply*, and `exploration_rate` only nudges numeric style traits
(`character.py`), never what Janus thinks about or when.

## Behavior

### The Secretary trigger

A new built-in definition, **Secretary** (`builtin.secretary`), runs hourly. It
is an ordinary trigger definition the user can edit (objective, tools,
schedule, model) or disable, like the other built-ins. It is **enabled** after
seeding (the owner chose this over opt-in; the docs state the data flow to a
cloud model).

Before each run, code (not the model) decides whether to spend a model call:

1. **Waking hours.** Outside the owner's active window (default 08:00 to 21:00,
   in the definition's timezone) the run completes with a short note and no
   model call.
2. **Change gate.** The run counts what is new or changed since the last run
   that actually thought (skipped hours complete too and are looked past): new
   mail, new incoming WhatsApp messages, changed reminders, changed planner
   events, and any change in the synced calendar events, synced tasks, or
   Janus's local tasks (compared by content, because synced events carry no
   change timestamp). If nothing changed and that run was less than six hours ago, the run
   completes without a model call. A quiet inbox therefore costs nothing. The
   price is that something that becomes urgent only through the clock can be
   noticed up to six hours late unless an earlier run scheduled a follow-up.

When it does run, the objective is deliberately open: think like a chief of
staff. What is coming in the next days that needs preparation? What is stuck,
overdue, or silently waiting on the user? What connects across mail, calendar,
topics and memory that the user has not noticed? What is worth looking into
unasked? The run may:

- **act on the safe parts** with its tools: create reminders, track topics,
  propose planner tasks and events with `propose_planner_item` (registry entry
  `planner.suggest`: a pending suggestion the user approves or dismisses, with
  the same dedupe as mail suggestions; never send mail or messages to other
  people);
- **ask or report** by starting a conversation when the user must decide or
  would want to read something (`start_conversation`, five a day, JAN-319);
- **interrupt** through WhatsApp only for something time-critical (see below);
- **stay silent** otherwise and finish with `NOTHING_TO_REPORT`.

### It remembers and it learns what lands

`prepare` hands the run two blocks of context besides the planner summary:

- **Its own recent runs** (the last few outputs, trimmed), so it does not repeat
  itself or lose a train of thought between hours.
- **Pending planner suggestions** (the newest 40, as a scanned
  `<prepared_context>` block, added by `builtin_triggers._prepare_secretary`),
  so it updates a waiting suggestion instead of proposing it again.
- **Engagement feedback** for the conversations earlier Secretary runs started
  in the last 14 days: title, whether the owner opened it, and whether the
  owner replied. The objective tells it to drop topics the owner ignored twice
  and to do more of what got a reply. No new rating UI: opening and replying
  already are the signal.

### WhatsApp as an interrupt channel

A new tool `notify_me_on_whatsapp(message)` (registry entry `whatsapp.notify_me`)
sends one short text to **the owner's own number**, configured in the owner's
notification settings. It is deliberately not `send_whatsapp_message`: that
tool speaks for the user to other people and is `confirmation="required"`.

Fences, all in code:

- **Owner only.** The target is the owner's configured number and an enabled
  account of the owner; the tool takes no recipient or conversation argument.
- **Opt-in by setting.** The entry is enabled only in unattended modes and only
  when the owner turned on *Secretary WhatsApp alerts* and set a number.
- **Bounded.** One message per run, three per owner per rolling day (counted on
  the stored outgoing message's `origin_trigger_id`), at most 600 characters,
  plain text.
- **No exfiltration.** No URLs, links, or images (same check as JAN-319).
  Blocked after a suspected prompt injection in the same run
  (`INJECTION_ESCALATED_TOOL_NAMES`).
- **Honest failure.** A missing connection or a send error returns an error to
  the run, which then falls back to `start_conversation`; nothing is silently
  dropped.

### Settings

`NotificationPreference` gains `secretary_active_start_hour` (default 8),
`secretary_active_end_hour` (default 21), `secretary_whatsapp_enabled` (default
off) and `secretary_whatsapp_number`. They are part of the existing notification
settings API and shown on Settings → Daily Briefing → Secretary. The number is
validated as an international number and stored as `+digits`; an end hour that
is not after the start hour is refused.

## Design decisions

1. **A schedule trigger with a code gate, not a new runtime.** Briefing, digest
   and mail already prove the pattern (`prepare`/`finalize`, `skip_output`).
   Waking hours and the change gate live in `prepare`, so a skipped hour never
   calls a model and needs no scheduler change (intervals have no window
   today).
2. **Feedback from existing signals.** `Conversation.unread` and the owner's
   replies already say whether a message landed. A separate rating table would
   add UI and a second source of truth.
3. **A narrow WhatsApp tool instead of loosening `whatsapp.send`.** A fixed
   recipient removes the reason `whatsapp.send` is `required`, so the Unattended
   switch can stay at its default for this entry without weakening the rule for
   messages to other people.
4. **Hooks feed and deliver; the model decides.** The Secretary's tools are
   read and low-risk write tools. Nothing in it sends mail or contacts third
   parties, and `LOCKED_UNATTENDED` entries stay locked.

## Cost and privacy

An hourly run with a cloud model sends planner, mail and memory excerpts to the
provider. The change gate and waking hours bound that; the trigger can be
disabled or pointed at a local model on Settings → Triggers. Local models
(Ollama) are noticeably weaker at open-ended judgment, so expect more
`NOTHING_TO_REPORT` or shallower findings from them.

## Out of scope (planned next)

- **Action inbox for the desktop and phone.** A small authenticated endpoint
  listing Janus-started conversations and proposals with approve/dismiss
  actions, so an `eww` popup, a desktop notification action, or the iOS app can
  render buttons. iOS push (APNs) belongs here.
- **Delegated research.** Letting a Secretary run hand a bounded read-only
  question to a delegated child (`delegation`) and fold the answer in.
- **An idea backlog and a periodic reflection** on what the owner engaged with.
- **Reworking `exploration_rate`**, which currently has no effect the user can
  notice.

See [Background workers and runtime](../tech-spec/background-workers-runtime.md#proactive-secretary)
for the technical contract and [Runtime triggers](../runtime-triggers.md) for the user-facing docs.
