# Planner

The planner brings your calendars and task lists into Janus and adds a planner
of Janus's own. Janus reads all of it for the briefing, the Secretary, and chat,
and proposes new tasks and events for you to approve.

## Sources

| Source | What Janus reads | How you connect it |
| --- | --- | --- |
| **Google Calendar** | Events of the calendars you pick | **Settings → Connections** → connect Google |
| **Google Tasks** | Your tasks | Same Google connection |
| **Outlook Calendar** | Events of the calendars you pick, through Microsoft Graph | **Settings → Connections** → connect Microsoft |
| **Jira** | Open issues assigned to you, as tasks | `janus planner add` (below) |
| **Janus planner** | Your own local tasks and events | Built in; nothing to connect |

Synced sources are read-only: Janus never writes to Google, Outlook, or Jira.
Only the Janus planner can be edited, by you or by Janus.

The Google and Microsoft buttons work only after an administrator configured
the provider's OAuth client. See [Integrations](integrations.md).

### Jira

Add a Jira source from the command line:

```bash
janus login <username>
janus planner add "Work Jira" jira --api-url https://example.atlassian.net
```

The command prompts for the token. Enter `email:api_token` for Jira Cloud, or
a personal access token on its own. `janus planner list` shows your sources.

`PROVIDER` is one of `jira`, `google_calendar`, `google_tasks` or
`microsoft_calendar`; connect Google and Outlook in Settings rather than with
this command. Any other provider is refused before the token prompt.

## Sync

Janus syncs every active source every 30 minutes. Each sync fetches tasks and
the calendar events of today and tomorrow.

Changes found by a sync emit the `planner.task_created`,
`planner.task_changed`, `planner.calendar_event_created`, and
`planner.calendar_event_changed` events, which an event trigger can act on.
The first sync of a new source only records a baseline and emits nothing. See
[Runtime triggers](runtime-triggers.md).

## The Planner page

The **Planner** page (`/planner`) has three parts.

- **Calendar selection.** The **Google Calendar** and **Outlook Calendar**
  cards list every calendar of the connected account (owned, shared, and
  subscribed). Tick the ones Janus should sync and press **Save selection**.
  Each card shows the last sync time and the latest sync error. Unticking a
  calendar removes its imported events.
- **Sync now.** Syncs your sources right away instead of waiting for the next
  cycle, and reports how many sources it synced or which failed.
- **Events and Tasks.** Upcoming events and tasks from every source. The forms
  here add local events and tasks; the trash icon deletes a local item.
  Imported items are read-only.

## Dashboard

Two Dashboard widgets show the planner:

- **Schedule**: today's events, local and imported, one row per event.
- **Active Tasks**: your local tasks as a tree (a parent with open subtasks
  becomes a group heading), followed by imported tasks.

Use **Widget options** on the Dashboard to show or hide any widget.

## Local tasks

Janus-owned tasks support subtasks (any depth), an urgency (`low`, `normal`,
`high`, `critical`), and an explicit order among siblings. Completing a parent
does not complete its children. Janus refuses a move that would put a task
under itself or one of its own subtasks.

In chat, Janus can read and change local tasks with its planner tools. The
[Todo Chat companion](desktop.md#todo-chat-companion) is a desktop sidebar for
the same tasks.

## Planner suggestions

Janus does not add items to your planner on its own. It proposes them, and you
decide. Suggestions come from:

- **Mail processing**, for mail that implies a task, an event, or a change to
  one;
- **the afternoon digest**;
- **Janus runs** not tied to one email, such as the Secretary (labelled *Janus
  agent*).

A suggestion is a new task, a new event, or an update to an existing local task
or event. They wait in **Planner Suggestions** on the Dashboard. For each one
you can edit the title, times, due date, and description, then approve it
(Janus creates or updates the local item) or dismiss it.

### Bulk review

Above the list:

| Control | Effect |
| --- | --- |
| **Select all** | Ticks every listed suggestion. |
| **Dismiss selected (n)** | Dismisses the ticked suggestions. |
| **Dismiss duplicates (n)** | Keeps the newest of each duplicate group and dismisses the rest. |
| **Dismiss all** | Dismisses every pending suggestion, after you confirm. |

Dismissing never changes your planner. Ask Janus in chat to restore dismissed
suggestions if you change your mind.

### Duplicates

Mail often mentions the same thing more than once. A new suggestion therefore
updates a pending one instead of piling up when it targets the same local item,
or has the same title and type. Titles are compared ignoring case,
punctuation, and spacing. A suggestion you dismissed or approved for the same
day is not proposed again for 90 days.

Pending planner suggestions also appear in the
[action inbox](inbox.md#the-action-inbox).

## Preparation reminders from mail

When Janus processes a mail, it may find:

- an **event reminder**: something that happens at a known time;
- a **preparation reminder**: work to do before a known event or deadline.

Janus keeps these as reminder memory. A later mail about the same thing
refreshes the reminder instead of adding a second one. Each active reminder is
mirrored as a calendar entry titled `[Prep] …` or `[Event] …`.

The morning briefing and the Secretary see your active reminders and the next
six weeks of synced events, and point out what needs preparation now.

Reminders expire on their own: event reminders 24 hours after they are due,
preparation reminders 72 hours after their preparation time (or due time).
Spam and ignored mail never create reminders. Reminders belong to the owner
of the mailbox the mail arrived in.

## The Secretary

The Secretary is the built-in **Secretary** trigger. Every hour it looks across
your mail, calendar, tasks, topics, and memory, and works out what is coming,
stuck, or worth your attention. It can:

- create reminders, track topics, and propose planner items;
- start a conversation with you when you should decide or read something (see
  [Chat](chat.md#conversations-janus-starts));
- send you a WhatsApp alert for something time-critical, if you turned that on;
- stay silent when there is nothing to report.

It runs only during your waking hours. Within them, it skips an hour without
calling a model when nothing changed since its last real run (new mail, new
WhatsApp messages, changed reminders, or changed calendar and task data), but
it re-checks at least every six hours. It learns from which of its
conversations you opened or answered in the last 14 days.

### Secretary settings

Open **Settings → Daily Briefing → Secretary**.

| Setting | Default | Meaning |
| --- | --- | --- |
| **Active from** / **Active until** | 08:00 / 21:00 | The waking hours, in the trigger's timezone. |
| **WhatsApp alerts for time-critical items** | Off | Allow one short WhatsApp message per run, at most three a day. |
| **Your WhatsApp number** | empty | The only number alerts go to, in international form (`+4915112345678`). Needs a connected WhatsApp account. |

Press **Save Secretary Settings**. To change the Secretary's objective, tools,
schedule, or model, or to turn it off, edit the trigger in **Settings →
Triggers**.

!!! note "Cost and privacy"
    With a cloud model, each Secretary run sends excerpts of your planner,
    mail, and memory to the provider. Waking hours and the change check keep
    the number of runs down. Point the trigger at a local model, or disable
    it, if that matters to you.
