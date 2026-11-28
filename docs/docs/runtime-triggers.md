# Triggers and automations

Everything Janus does outside chat is a **trigger**: the morning briefing, the
afternoon digest, processing each new email, and every automation you write.
A trigger runs with nobody watching. It speaks as your character profile, sees your
memory index and skill library, and records what it did so you can check afterwards.

You manage triggers on **Settings → Triggers**.

## Runs and definitions

- A **trigger definition** is a saved automation: what to do, which tools it
  may use, and when it runs. Definitions are listed under **Trigger
  definitions**.
- A **trigger** (or run) is one execution. Every activation of a definition
  (a schedule tick, an event, **Run now**) creates one run. Janus's one-time
  follow-ups and API calls create runs too.

Runs execute one at a time on a background worker. It is on by default; the
Triggers page shows whether it is **running**, **enabled but not running**,
or **disabled by configuration** (`runtime_trigger_worker_enabled`, see
[Configuration](configuration.md)).

## Creating a definition

Click **New trigger** and fill in:

| Field | What it means |
| --- | --- |
| **Name** | Shown on the card and on every run. |
| **Objective** | The prompt: what Janus should do on each run. |
| **Tools** | The toolboxes the run may use (*Tools this trigger may use*). Only these, plus *Core tools*, are offered. |
| **Model** | Optional. *Default model* follows Settings → AI & Models. Pick a provider and model to use another one. If that model cannot be used, the run fails instead of falling back. |
| **Run limits** | Optional. Tool-calling rounds per run (default 8, up to 500) and seconds per run (default 180, up to 24 h). Each can be left at the default, set, or switched to **No limit**. A run that hits a limit fails and is retried. |
| **Activation** | When it runs (below). |

### Activation

- **Manual**: runs only when you press **Run now**.
- **Recurring schedule**:
    - **Daily at a time**, optionally on selected weekdays, in a timezone you choose.
    - **Every N minutes**, from 5 minutes to 7 days.

    If Janus was down when a run was due, only the most recent missed
    occurrence runs once it is back. A briefing missed for three days fires
    once.
- **Event**: one run per event. Pick a **Subsystem**, then one of its events.
  The editor lists the payload fields each run receives.

**Run now** works for every activation kind.

## Event catalog

| Event | Fires when | Payload fields |
| --- | --- | --- |
| `mail.received` | A new email was stored and no SPAM/IGNORE rule filtered it out. | `email_id`, `account`, `from`, `subject`, `received_at`, `priority_from_rule`, `body` |
| `whatsapp.direct_message_received` | Someone sent you a message in a one-to-one chat. | WhatsApp fields (below) |
| `whatsapp.group_message_received` | Someone sent a message in a group you are in. | WhatsApp fields |
| `whatsapp.group_mention` | Someone @-mentioned you, or replied to you, in a group. The message also fires *New group message*. | WhatsApp fields |
| `planner.task_created` | The planner sync saw a Google Tasks or Jira task for the first time. | Task fields (below) |
| `planner.task_changed` | A synced task's title, status or due date changed. | Task fields + `changes` |
| `planner.calendar_event_created` | The planner sync saw a calendar event for the first time. | Calendar fields (below) |
| `planner.calendar_event_changed` | A synced event's title, start, end or location changed. | Calendar fields + `changes` |
| `webhook.received` | One of your webhook URLs received a correctly signed JSON request. | `webhook`, `webhook_id`, `event_header`, `received_at`, `body` |
| `telegram.message_received` | You sent a message to your Janus bot from your linked chat. | `message_id`, `bot`, `sender`, `received_at`, `body` |

- **WhatsApp fields:** `message_id`, `account`, `chat`, `chat_id`, `sender`,
  `sender_id`, `received_at`, `message_type`, `body`, `attachment`.
- **Task fields:** `task_id`, `account`, `provider`, `external_id`, `title`,
  `status`, `due_date`.
- **Calendar fields:** `calendar_event_id`, `account`, `provider`,
  `external_id`, `summary`, `start_time`, `end_time`, `location`.
- `body` holds the first 4,000 characters. `changes` lists what changed as
  `field: old -> new` lines.

What each subsystem counts:

- **WhatsApp:** only live messages from other people. Your own messages, the
  history replayed when you pair, Status updates, broadcasts, channels,
  reactions, edits and deletions never fire.
- **Planner:** what the 30-minute planner sync finds. An account's first sync
  is a baseline and fires nothing. The sync covers today and tomorrow, so
  *New calendar event* fires when an event enters that window. Deleted items
  fire nothing.
- **Telegram:** only messages from the chat you linked. Everyone else is
  ignored.

A definition that still points at an event Janus no longer publishes is
flagged in the editor and never fires.

## Narrowing an event trigger

Two optional settings keep an event trigger quiet and cheap:

- **Only when**: conditions on the payload fields. All of them must hold.
  Each condition is *is one of*, *is not one of*, or *contains*
  (case-insensitive). Examples: `chat_id` *is one of* your family group,
  `from` *contains* `@work.example`, `webhook` *is one of* `github`. For
  WhatsApp chats and senders the editor suggests the chats Janus has seen.
- **Max runs per hour**: at most this many runs in any rolling hour. Empty
  means no limit. New triggers on busy events start with a default:
  10 for *New group message* and *Mentioned in a group*, 30 for webhooks and
  Telegram.

An event that a condition rules out, or that is over the limit, starts no run
and costs no model call. The card counts the skipped events and shows when
the last one was.

Mail that no enabled `mail.received` definition accepts is sorted by the
classic one-shot classifier instead, so it never waits for a run that will not
come.

## Built-in triggers

Janus creates these definitions for every user. You can edit their objective,
tools, model and schedule, or disable them. You cannot delete them or change
how they are activated. Janus never overwrites your edits.

| Built-in | Key | Runs | Default | What it does |
| --- | --- | --- | --- | --- |
| **Morning briefing** | `builtin.briefing` | Daily at 06:30 | On | Writes today's briefing from your schedule, tasks, reminders, topics and mail. The dashboard shows it. |
| **Afternoon digest** | `builtin.digest` | Daily at 16:00 | Follows your digest notification settings | Summarizes the `DIGEST` mail since the last digest, grouped by topic. With no new digest mail it finishes without a model call. |
| **Mail processing** | `builtin.mail` | On `mail.received` | On | Files each new email and records its reminders, topics and planner suggestions. |
| **Secretary** | `builtin.secretary` | Every 60 min, within your waking hours (default 08:00 to 21:00) | On | Looks ahead across calendar, planner, mail, WhatsApp, topics and memory. Does the safe parts itself and starts a conversation when you should decide something. Skips the hour without a model call when nothing changed. |
| **Knowledge extraction** | `builtin.knowledge` | Every 30 min | Off | Extracts people, organizations, projects and their relations from newly ingested text. |
| **Wiki synthesis** | `builtin.wiki` | Every 60 min | Off | Keeps wiki pages for active topics and frequent entities up to date. |
| **Memory review** | `builtin.memory_review` | Daily at 03:17 UTC | On | Checks the facts Janus remembered against the conversations they came from, fixes or archives wrong ones, merges duplicates, and picks the facts for the [memory index](knowledge.md#the-memory-index). |

Notes:

- The briefing and the Secretary start in UTC. The first time you open the
  dashboard or the Triggers page, they switch once to your browser's timezone,
  as long as you have not edited them.
- The digest's time and on/off state stay in sync with your digest
  notification settings.
- Set the Secretary's waking hours and WhatsApp alerts on **Settings → Daily
  Briefing**. Switch knowledge extraction and wiki synthesis on from the
  Memory page (see [Knowledge](knowledge.md)).
- Knowledge extraction, wiki synthesis and the memory review share one
  monthly token budget. With a cloud utility model, the memory review sends
  the source conversations of the facts it checks to that provider; switch
  it off on this page if you do not want that.
- `janus brief` and `janus digest` run the briefing and digest right away and
  print the result. The dashboard's refresh button reruns the briefing.

!!! warning "One run per email"
    With Mail processing enabled, every new email is a full Janus run,
    including each message of a mailbox's first sync. With a paid model
    provider, that costs money. Disable Mail processing to go back to the
    one-shot classifier. See [Inbox](inbox.md) for how mail is sorted.

## Running, history and Agent Tasks

Each definition card has an enabled toggle, **Run now**, **Edit**, **Runs**
(its run history) and **Delete**. Switching a definition off, or deleting it,
cancels its queued and retry-waiting runs. A run already in progress finishes.

**Recent runs**, at the bottom of the page, lists every run. Filter it by
status, kind (manual, scheduled, event), definition (or *Ad-hoc runs*) and
time range, and page with **Newer** / **Older**.

A run card shows its status, times, attempt count, any scheduled retry, the
error, and a **Result** block: the model used, the tools authorized and
invoked, **Refused tool calls**, **Failed tool calls**, and Janus's answer.

| Status | Meaning |
| --- | --- |
| `queued` | Waiting for its time, the worker, or a retry window. |
| `running` | Claimed by the worker. |
| `waiting` | Paused until the child tasks it delegated have finished. |
| `completed` | Finished. |
| `failed` | Failed and will be retried. |
| `exhausted` | Retries stopped. |
| `cancelled` | Withdrawn before it ran. |

**Plan steps and tool trace** on a run card opens it on the **Agent Tasks**
page (`/tasks`). That page lists everything Janus ran without you: trigger
runs, automation runs and delegated tasks, newest first, filterable by source
and status. Open a task to see each plan step with its tools, refusals,
failures, suspected prompt injections and answer, plus links to its
definition, its parent run, and the tasks it delegated.

## Retries

A failed run is retried automatically, from scratch, up to
`runtime_trigger_max_attempts` attempts (default 3). By default the retry
starts on the next worker poll. A positive
`runtime_trigger_retry_backoff_seconds` adds an exponential delay between
attempts.

A failure that a retry cannot fix (a malformed request, or a one-time trigger
that named a tool it may not use) goes straight to `exhausted`.

## Automations Janus proposes

When you describe a recurring job in chat ("remind me every Monday to review
open pull requests"), Janus can draft a trigger definition with the
[Propose automations](tools.md#propose-automations) toolbox. Janus proposes,
you approve.

- A draft is saved **disabled**. It never runs, is never triggered by an
  event, and cannot be run manually until you approve it.
- It appears under **Suggested automations** on Settings → Triggers. A count
  badge shows on **Settings** in the navigation and on the **Triggers** tab.
  It is also listed in the [action inbox](inbox.md).
- The card shows the name, the schedule or event, the full objective exactly
  as it will run, Janus's reason (*Janus says*) and every tool.
- A tool whose Unattended switch is Off gets its own **Allow** checkbox,
  unticked by default. Approving keeps a ticked tool and drops an unticked
  one.
- **Drafted after reading … content** marks a draft written after Janus read
  outside content (mail, web pages, WhatsApp and so on). A red warning shows
  when something read in that turn looked like a prompt injection. Read the
  objective carefully.

Your choices:

- **Approve** (with **Enable now**, on by default) makes it an ordinary
  definition.
- **Edit then approve** opens the trigger editor. Saving is your approval, and
  you may add any tool there.
- **Reject** takes an optional reason that Janus sees. Janus does not suggest
  the same automation again for 30 days.
- **History** lists recent decisions.

To change an approved automation, Janus drafts a **replacement**. Its card
shows the changes against the approved one, which keeps running until you
approve the replacement. Janus never edits, enables, runs or deletes an active
definition itself.

### The approval fingerprint

Approving stores a fingerprint of the definition's content: objective, tools,
activation, schedule, timezone, event, model, and the event filter and rate
limit. The tools you approved count as allowed only while the content still
matches that fingerprint.

When **you** edit an approved definition, the fingerprint is renewed, so your
edits keep the approval. If anything else changed it, its runs fall back to
your Unattended switches, and the mismatch is logged.

### Proposal limits

| Limit | Value |
| --- | --- |
| Suggestions awaiting review | 5 |
| New suggestions | 3 per 24 hours, 1 per chat turn |
| Objective length | 4,000 characters |
| Shortest repeat interval | 60 minutes |
| Tools Janus may pick | Core, Trigger history, Read mail, Relabel mail, Calendar, Jira, Read local planner, Dashboard, Memory, Topics |

Every other tool can only be added by you while reviewing. Drafting works in
chat only: no trigger, automation or delegated task can propose one.

## One-time follow-ups

With the [Trigger tools](tools.md#trigger-tools), Janus can schedule its own
one-time follow-up ("check again tomorrow") from chat or from a trigger run.
It can also list and cancel its pending triggers.

- A follow-up is due at most 365 days ahead, and you can have at most 25
  active triggers at once.
- Each follow-up appears as a definition with the *By Janus* and *One-time
  follow-up* badges. It runs once and needs no approval. You can **Cancel
  follow-up** or delete it, but not edit or rerun it.
- A follow-up gets only the tools your Unattended switches allow. It cannot
  delete memory facts or change dashboard widget prompts.

### The follow-up chain guard

A follow-up that a trigger run schedules is bounded in code, whatever the
objective says:

- **Two levels deep at most.** A follow-up of a follow-up cannot schedule a
  third. A follow-up you schedule in chat starts a new chain.
- **Three follow-ups per run**, cancelled ones included.
- **Ten per day** from all your trigger runs together. Chat follow-ups don't
  count.
- **At least 15 minutes ahead.**
- **One-time only.** A run can never create a recurring trigger.
- A run may cancel only Janus's own follow-ups, never a run of a definition
  you saved.

A refused request is listed under **Failed tool calls**. To stop trigger runs
from scheduling anything, switch *Trigger tools* Off for unattended runs.

## Delegation

With the [Task delegation tools](tools.md#task-delegation-tools), Janus can
hand a sub-question to a background **child task**, from chat or from a run.

- A child task is **read-only**. It gets only read tools, and only ones the
  parent had.
- A child cannot delegate further (depth 1). You can have at most 4 child
  tasks active at once.
- A trigger run can wait for its children: it pauses as `waiting` and
  continues automatically with their results. A run waits at most once, and
  the built-in triggers never wait.
- A child cut off by a restart is started again once; if that fails too, it is
  reported as failed.

Children appear on the Agent Tasks page. Children started from a chat are also
listed in that chat under **Delegated tasks**.

## Webhooks and Telegram

- **Webhooks:** create a webhook under **Webhooks** on Settings → Triggers.
  Outside services send signed JSON to its URL, and each accepted request
  fires `webhook.received`. Filter on `webhook` to react to one sender only.
- **Telegram:** link your own bot on Settings → Connections → Messaging. A message you
  send it fires `telegram.message_received`. A trigger with
  [Telegram alerts](tools.md#telegram-alerts) can answer you there.

Setup and signing are in [Integrations](integrations.md).

## What unattended runs may do

Nobody can approve a tool call during a run. Instead, each toolbox has an
**Unattended: Allowed / Off** switch on Settings → Tools & Safety. The
toolboxes with the biggest effects (sending mail, GitHub, Linear and so on)
default to Off. A tool that is Off answers `BLOCKED` and is listed under
**Refused tool calls**. Tools you pick on a definition you wrote, or on an
approved proposal, are allowed for that definition even when they are Off.
Some tools are chat-only and never run unattended. See
[How permissions work](tools.md#how-permissions-work) for the full rules.
