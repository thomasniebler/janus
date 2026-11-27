# Tools and permissions

Janus acts through **toolboxes**. Each toolbox has a registry id (such as
`mail.send`), holds one or more tools, and is one row on **Settings → Tools &
Connections → Built-in Janus tools**. Each toolbox section below is the page
its `docs_ref` points to, so a `BLOCKED` answer names the section that
explains it.

Every toolbox has a **confirmation level**:

- **never**: reads or harmless actions. Runs without asking.
- **recommended**: changes Janus's own records. Runs without asking.
- **required**: the biggest effects. Asks in chat and is Off for unattended
  runs by default.

## How permissions work

Two switches decide what a toolbox may do, one for chat and one for runs
nobody watches. Both are yours alone: other users' settings never apply to
you.

### Unattended switch

Triggers, automations and delegated tasks run with nobody watching, so they
cannot ask you. Each toolbox has an **Unattended: Allowed / Off** switch
instead.

- **Defaults:** toolboxes with confirmation level `required` start Off.
  Everything else starts Allowed. **Reset** returns a toolbox to its default.
- **BLOCKED answer:** a tool whose Unattended switch is Off is still offered
  to a saved trigger that lists it. Calling it answers
  `BLOCKED: ... was not run because the user has switched it off for
  unattended runs`, changes nothing, and is listed under **Refused tool
  calls**.
- **No tool list:** a one-time trigger or delegated task that names no tools
  is offered only the Allowed ones. One that names an Off tool fails before
  anything runs.
- Switching a writing toolbox to Allowed asks you to confirm first.

`BLOCKED` also appears when the integration behind a tool is not connected,
and after something Janus read in the same turn looked like a prompt
injection. Then every tool with outside or lasting effects answers `BLOCKED`
for the rest of that reply or run.

### In chat switch

Toolboxes with confirmation level `required` also have an **In chat** switch:

| Value | Effect |
| --- | --- |
| **Ask every time** (default) | Each call shows an approval card. |
| **Always allow** | Runs without a card. The call still shows as a chip. |
| **Off in chat** | Janus does not see the toolbox in chat at all. |

The **approval card** shows the tool, the reason, and the exact arguments
(secrets shown as `[redacted]`):

- **Approve** runs this one call.
- **Always allow** runs it and switches the toolbox to *Always allow*.
- **Deny** does not run it. Janus is told `BLOCKED` and carries on.
- No answer within 5 minutes (`chat_tool_approval_timeout_seconds`), a closed
  tab or a lost connection also means no run.

A client that cannot show approval cards (the iOS app) gets `BLOCKED` for
these toolboxes. Other toolboxes have no In chat switch and run without
asking.

### Modes: per_tool and allow_all

Above the list sit two modes, one per switch. Both default to `per_tool`,
which follows each toolbox's own switch. Settings asks you to confirm before
`allow_all`.

- **Unattended: Allow everything** confirms every toolbox for unattended runs,
  Off ones included. It also unlocks *Image generation*, *Remote MCP tools*
  and *Remote agents* for those runs.
- **In chat: Allow all** runs every toolbox in chat without an approval card.
  Toolboxes set to *Off in chat* stay off.

### Locked for unattended runs

These toolboxes are chat-only. Their Unattended switch is locked, and no
trigger definition can use them:

- *Propose automations* (`triggers.propose`)
- *Page actions* (`ui_actions`)
- *UI blocks* (`ui_blocks`)
- *Page introspection* (`ui_introspection`)
- *Propose character changes* (`character.propose`)
- *Image generation* (`images`), *Remote MCP tools* and *Remote agents*,
  unless Unattended is set to **Allow everything**

### Definition tool lists and approved proposals

A saved trigger gets the toolboxes selected on it, plus *Core tools*.

- On a **definition you wrote**, selecting a toolbox allows it for that
  definition even when its Unattended switch is Off.
- A **definition Janus proposed** gets the same only after you approve it,
  and only while its approval fingerprint still matches. See
  [Automations Janus proposes](runtime-triggers.md#automations-janus-proposes).
- **Built-in triggers** do not get this. They follow your switches.

### Delegated tasks

A delegated child task gets only read-only toolboxes, and only ones its parent
had. *Allow everything* does not change this. Locked toolboxes, *Shell*,
*Coding agents* and remote tools never reach a child.

### Follow-up chain guard

The *Trigger tools* toolbox is Allowed by default because it guards itself. A
follow-up scheduled by a trigger run is at most two levels deep, at most three
per run, at most ten per day per user, at least 15 minutes ahead, and one-time
only. See [The follow-up chain guard](runtime-triggers.md#the-follow-up-chain-guard).

### Self-evolution has its own switch

*Self-evolution tools* ignore the Unattended switch and *Allow everything*.
They run unattended exactly when the self-evolution loop is switched on, and
never when it is off, not even on a definition that lists them.

## How Janus picks tools

Janus does not offer the model every allowed toolbox. Before each chat
message, each ad-hoc follow-up, and each delegated task, a quick call to the
utility model (Settings → AI & System) picks the toolboxes the request needs.

- **It only narrows.** The pick is limited to what your switches, a
  definition's tool list or a delegation grant already allow. Approval cards
  still appear.
- **It fails safe.** Without a usable utility model, or after a timeout
  (`tool_selection_timeout_seconds`, default 10), Janus offers everything it
  is allowed to use.
- *Core tools* and *Janus documentation* are always offered.
- Saved triggers, built-ins included, always get exactly their own tool list.
- In chat, *Tools picked for this message* shows the choice and why. If a tool
  is missing, ask for it ("use the calendar"). Janus adds it with
  `request_tools` from your next message on.
- An administrator can turn selection off with `tool_selection_enabled =
  false`.

## Toolbox reference

Unless noted, a toolbox is offered in chat and to unattended runs. "Unattended
Off" means its Unattended switch starts Off.

## Core tools

`core` · never. `get_current_datetime`. Always available.

## System introspection tools

`events` · never. `list_recent_events` reads the event log. In chat an admin
sees the whole log; everywhere else Janus reads only your own entries.

## Trigger history

`trigger-history` · never. `list_my_trigger_history` reads the outcomes of
your own trigger runs, so a run can check what earlier runs found.

## Read mail

`mail` · never. Search and read stored mail from your own accounts. Needs a
connected mail account. Mail text is untrusted content.

## Relabel mail

`mail.label` · recommended. Change a message's label (IMPORTANT, DIGEST,
IGNORE, SPAM). Needs a connected mail account.

## Send mail

`mail.send` · required, Unattended Off. `send_email` sends mail from one of
your active accounts. Needs a connected mail account.

## Mail processing

`mail.processing` · recommended. Sets an email's priority and records its
reminders, topics and planner suggestions. Used by the Mail processing
built-in; not offered in chat.

## Read WhatsApp

`whatsapp` · never. List your accounts, list and search chats, and read
messages Janus stored. Read-only; nothing reaches WhatsApp. Needs an enabled
WhatsApp account. Message text is untrusted content.

## Send WhatsApp

`whatsapp.send` · required, Unattended Off. `send_whatsapp_message` sends
plain text (up to 4,096 characters) as you, into a stored chat or to a phone
number. Needs a live WhatsApp connection.

## Notify me on WhatsApp

`whatsapp.notify_me` · recommended. Sends one short alert to **your own**
number: at most one per run and three a day, 600 characters, no links.
Unattended runs only. Needs a connected WhatsApp account and *WhatsApp alerts*
with your number on Settings → Daily Briefing.

## Telegram alerts

`telegram.notify` · recommended. `notify_me_on_telegram` sends one short
message to the Telegram chat you linked: 600 characters, no links, at most 20
a day. Unattended runs only. Needs a linked bot (see
[Integrations](integrations.md)).

## Calendar tools

`calendar` · never. Read events from your connected calendars and reminders
Janus created. Needs a connected calendar.

## Microsoft Outlook mail

`microsoft.mail` · never. List, search and read your Outlook mailbox through
Microsoft Graph. Administrators only; needs the Microsoft sign-in
(`janus microsoft login`).

## Microsoft Outlook calendar

`microsoft.calendar` · never. List your Outlook calendar events.
Administrators only; needs the Microsoft sign-in.

## Microsoft Teams chats

`microsoft.teams` · never. List your Teams chats and read their messages.
Administrators only; needs the Microsoft sign-in.

## Send Microsoft Outlook mail

`microsoft.mail.send` · required, Unattended Off. Send plain-text mail from
your Outlook mailbox. Administrators only; needs the Microsoft sign-in.

## Send Microsoft Teams messages

`microsoft.teams.send` · required, Unattended Off. Post a plain-text message
into one of your Teams chats as you. Administrators only; needs the Microsoft
sign-in.

## Azure resources

`azure.resources` · never. List subscriptions, resource groups and resources
the machine's Azure identity can see. Read-only. Administrators only; needs
`JANUS_AZURE_ENABLED=true`.

## Jira task tools

`jira` · never. Read tasks from your Jira integration. Needs a connected Jira
account.

## Read local planner

`planner.local` · never. Read Janus's own planner tasks. Always available.

## Change local planner

`planner.local.write` · required, Unattended Off. Create, update, complete,
move and reorder local planner tasks.

## Planner suggestions

Three toolboxes:

- `planner.suggestions` · never. List pending suggestions and find
  duplicates.
- `planner.suggestions.review` · recommended. Dismiss suggestions in bulk,
  dismiss duplicates, restore dismissed ones. Never changes the planner.
- `planner.suggest` · recommended. Propose a planner item no single email
  caused (the Secretary uses it). Not offered in chat.

## Dashboard tools

`dashboard` · recommended. Read the briefing and dashboard widgets, change
widgets and widget prompts. Needs a connected calendar or task tracker. Ad-hoc
triggers never get the widget-prompt tools.

## GitHub tools

`github` · required, Unattended Off. List and merge pull requests with the
instance's GitHub token, which an administrator sets up.

## Linear tools

`linear` · required, Unattended Off. List teams and issues; create, update,
comment on and assign issues with the instance's Linear API key, which an
administrator sets up.

## Web search

`web_search` · never. Sends only the short query Janus writes to the
configured search provider (Brave or SearXNG). Results are untrusted content.
Needs a search provider set up by an administrator.

## Web page reader

`web_fetch` · required, Unattended Off. `web_fetch` opens one public `http(s)`
page and returns its readable text (up to 20,000 characters). Private and
local addresses are refused. The approval card shows the exact URL, because
the URL itself reaches that site.

## Memory tools

`memory` · recommended. List, search, add, update, group and delete the facts
Janus remembers about you. Ad-hoc triggers never get `delete_fact`.

## Topic tools

`topics` · recommended. List, search, create and update tracked topics.

## Knowledge search

`knowledge` · never. Search everything Janus ingested, and look up extracted
people, organizations and projects with their relations.

## Read the wiki

`wiki` · never. List and read your wiki pages.

## Propose wiki edits

`wiki.propose` · required, Unattended Off. Propose a new text for a wiki page.
Nothing changes until you accept it.

## Trigger tools

`triggers` · recommended. Schedule, list, inspect and cancel your one-time
follow-ups. Bounded by the
[follow-up chain guard](runtime-triggers.md#the-follow-up-chain-guard).

## Start conversations

`conversations.start` · recommended. Lets a trigger run open a new chat with
you, marked **Started by Janus**. At most one per run and five a day, no
outside links. Unattended runs only; delegated tasks never get it.

## Propose automations

`triggers.propose` · recommended. Draft trigger definitions that stay disabled
until you approve them on Settings → Triggers. Chat only (locked). See
[Automations Janus proposes](runtime-triggers.md#automations-janus-proposes).

## Character state

`character` · never. `get_character_state` reads Janus's adaptive character
state: tone, verbosity, proactivity, focus, their bounds, recent changes and
open proposals.

## Propose character changes

`character.propose` · recommended. Propose a change to the character state
within the bounds you set. Nothing changes until you approve it on Settings →
Character. Chat only (locked).

## Read mail rules

`rules` · never. List mail classification rules. Needs a connected mail
account.

## Manage mail rules

`rules.write` · required, Unattended Off. Create, update and delete rules.
Needs a connected mail account.

## Task delegation tools

`delegation` · recommended. `delegate_task`, `get_delegated_task` and
`list_delegated_tasks` hand a read-only sub-question to a background child
task. In a trigger run, `wait_for_delegated_tasks` pauses the run until its
children finish. See [Delegation](runtime-triggers.md#delegation).

## Skill library tools

`skills` · required, Unattended Off. Create, update, search and import skills
in the shared skill library. Administrators only, because skills steer every
user's Janus.

## Janus documentation

`docs` · never. `list_docs`, `read_doc` and `search_docs` read this
documentation, so Janus can answer "how do I…" or "why was my tool call
BLOCKED" from the docs. Always available. Dated review reports are not
offered.

## Page actions

`ui_actions` · never. Navigate, set a filter, open, highlight or scroll to an
item, or prefill a form on the page you have open. Only items on your screen;
forms are never submitted. Chat only (locked), in the web UI. Timeout:
`chat_ui_action_timeout_seconds` (10 s).

## Page introspection

`ui_introspection` · never. `get_ui_snapshot` and `get_element_styles` read
an outline of your open page and one element's styles. Never sees form
values, passwords or the chat panel. Chat only (locked), in the web UI.

## UI blocks

`ui_blocks` · never. `render_ui` adds a card, table, chart, checklist, facts
list, buttons or image to a reply. Display only: a button just sends a chat
message. Chat only (locked), in the web UI.

## Image generation

`images` · recommended. `generate_image` sends a prompt to the image provider
an administrator chose and shows the picture in chat. Each image is a paid
request. Images are stored per user (200 MB by default) and deleted with
their conversation. Chat only (locked) unless Unattended is set to **Allow
everything**. Needs an image provider set up by an administrator.

## Self-evolution tools

`self_evolution` · required. File improvement issues, start implementation
jobs, merge gated pull requests and releases, and schedule production
self-checks. Administrators only; needs GitHub and Linear and the
self-evolution loop switched on (Settings → Tools & Connections). Its
Unattended switch follows that setting.

## Shell

`shell` · required, Unattended Off. `run_shell_command` runs one command on
the Janus host: no shell syntax by default, a timeout, an output cap, and a
minimal environment. Administrators only; off until the operator sets
`shell_tool_enabled = true`.

## Coding agents

`coding_agents` · required, Unattended Off. `ask_coding_agent` asks a local
coding-agent CLI (Claude Code or Codex) one question, read-only unless the
operator allows edits. Administrators only; off until the operator sets
`coding_agents_enabled = true` and a CLI is installed.

## Remote MCP tools

`mcp.<server>.<tool>`. Tools from the MCP servers you added on Settings →
Tools & Connections. A tool the server marks read-only (`readOnlyHint`) is
`never`; every other one is `required` and asks before each call. Output and
descriptions are untrusted content. Chat only (locked) unless Unattended is
set to **Allow everything**. See [Integrations](integrations.md).

## Remote agents

`a2a.<id>` · required. `ask_agent_<id>` sends one prompt to an Agent2Agent
(A2A) agent you added and switched on. The agent sees only that prompt. Its
answer is untrusted content. Chat only (locked) unless Unattended is set to
**Allow everything**. See [Integrations](integrations.md).
