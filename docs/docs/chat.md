# Chat

Chat is where you talk to Janus. Janus answers in the voice of your character
profile, streams its reply as it writes, and uses tools along the way.

## Where you chat

There are two views of the same chat:

- **The Chat page** (`/chat`): the full-width view, with your conversation
  list on the left and the transcript on the right.
- **The chat panel** on every other page: a sidebar (Classic shell) or a
  drawer (Terminal and Rusty Console shells) on wide screens, and an inline
  panel below the page content on narrow ones.

Both views show one chat session per browser tab. Moving between pages,
switching shells, or opening `/chat` keeps the open conversation and your
unsent draft. The Chat page shows no panel, so the conversation never appears
twice.

Janus sees which page you have open (the page, its filters, and the items
listed on it) with every message, so "open the mail from Anna" or "what does
this error mean?" work without extra context.

If the connection drops, a notice with a **Reconnect** button appears above the
transcript.

## Conversations

Every chat is saved as a conversation.

- **New Chat** starts an empty conversation.
- Click a conversation in the list (or **Recent chats** in the panel) to
  reopen it and continue where you left off.
- The trash icon next to a conversation deletes it after you confirm.
  Deleting a conversation also deletes the images in it.
- Janus names a conversation after your first message (a short title from the
  utility model, or the start of your message if that fails).

Each browser tab remembers its own open conversation, so two tabs can hold two
different chats.

### Conversations Janus starts

A trigger run can open a new conversation with you when it finds something you
should read or decide, for example the Secretary noticing that a flight moved.
Such a conversation:

- shows **Started by Janus** under its title, and **New** until you open it;
- adds a count to the **Chat** entry of the navigation, in every shell;
- carries a note above the transcript that a trigger run started it;
- shows a desktop notification on a local install (see
  [Desktop](desktop.md#desktop-notifications)).

Reply in it like in any other chat. Janus starts at most five such
conversations a day. Unread ones are also listed in the
[action inbox](inbox.md#the-action-inbox).

## The briefing

The morning briefing is the built-in **Morning briefing** trigger, which runs
daily at 06:30. You read it in two places:

- the **Briefing** widget on the Dashboard;
- the **Morning Briefing** page (`/briefing`).

Both show today's finished run. If none exists yet, opening the page runs the
trigger once. The refresh button (**Refresh briefing**) runs it again now.

Edit the briefing's objective, tools, and schedule in **Settings → Triggers**.
**Settings → Daily Briefing** adds extra instructions and an optional email
window on top. See [Runtime triggers](runtime-triggers.md) for how built-in
triggers work.

## Following a reply

Above the transcript, Janus shows the status of the current turn:

- **Tools picked for this message**: when Janus narrowed its toolset for your
  message, which tools it kept and why.
- **Tools unavailable this turn**: tools withheld for this message, with the
  reason.
- **This turn**: the turn timeline. Each round in which Janus asked for tools
  is a step (running, waiting for approval, completed, or failed), with its
  tool calls as chips.

On the Chat page at full desktop width, the timeline docks as a column next to
the transcript (**Plan steps**). Narrower, and in the panel, it sits collapsed
above the conversation. The timeline is live only: a reloaded conversation
shows no steps.

Under each reply, the tool calls Janus made are listed with their arguments
and results. While a tool runs, a **Running tool** line names it.

For multi-step requests Janus also writes a short plan first and one line after
each round. See [Tools](tools.md).

## Approval cards

Some tools change things outside Janus, such as sending mail. When Janus wants
to use one in chat, the reply pauses and shows an approval card: **Janus wants
to use …**, with the tool, its risk level, and the exact arguments.

| Button | Effect |
| --- | --- |
| **Approve** | Runs this one call, with exactly these arguments. |
| **Always allow** | Runs this call and stops asking for this tool in chat. |
| **Deny** | Refuses the call; Janus continues without it. |

A card you don't answer counts as denied after 5 minutes.

Whether a tool asks, is off, or always runs in chat is its **In chat** switch
(**Ask / Off / Always allow**) in **Settings → Tools & Connections**. The
**Allow all** mode skips the card for every tool not switched Off. See
[How permissions work](tools.md#how-permissions-work) for the full rules,
including what unattended runs may do.

## Page actions, UI blocks, and images

These tools work only in the web chat, never in a trigger run (images are the
one exception; see [Tools](tools.md#how-permissions-work)).

- **Page actions**: Janus can act on the page you have open: navigate, set a
  filter, open or highlight a listed item, scroll, or prefill a form. Your
  browser performs the action, and each one appears as a chip under the reply,
  most with an **Undo** button. Janus never submits a form.
- **Page introspection**: when you ask about what you see ("why is this button
  greyed out?"), Janus can read an outline of the page and an element's
  styles. It never reads what you typed into form fields.
- **UI blocks**: Janus can answer with a card, table, chart, checklist, list
  of facts, row of buttons, or image. Blocks are saved with the conversation.
  A button sends a normal chat message, so any tool it leads to still asks for
  approval.
- **Images**: Janus can draw a picture when you ask, once an administrator has
  set an image provider. The picture appears in the reply with a **Download**
  link and is deleted with its conversation.

Details and limits are in [Tools](tools.md).

## Delegated tasks

Janus can hand a read-only sub-question to a background task and keep chatting.
The tasks of the open conversation appear as a collapsed **Delegated tasks (n)**
line above the transcript. Open it to see each task's status (queued, running,
completed, or failed), its question, the tools it could use, and its answer.

Everything Janus ran without you watching is on **Agent Tasks** (`/tasks`):
trigger runs, your automations, and delegated tasks, each with its plan steps
and tool trace.

## Submit an idea to Linear

When Linear is connected (**Settings → Linear**), the chat header shows
**Submit Idea**, which files a Linear issue from a title and description you
enter.

## UI shells

Janus has three app shells. They show the same pages and data; only the chrome
around them differs.

| Shell | Look | Chat on wide screens |
| --- | --- | --- |
| **Classic (UIv1)** | Blurred wallpaper, glass panels, left sidebar navigation. | Right-hand sidebar, always shown. |
| **Terminal (UIv2)** | Near-black CRT look, lime accents, monospaced chrome, command bar on top. | Right-hand drawer; the **Chat** button in the top bar shows or hides it. |
| **Rusty Console (UIv3)** | Black-red weathered terminal, rust-ruled panels, typewriter type. | Right-hand drawer, as in Terminal. |

### Switch shells

Open **Settings → Appearance** and pick a shell. The switch is instant and is
saved to your account, so it follows you to other browsers and devices.

### If a shell does not load

Add `?ui=v1`, `?ui=v2`, or `?ui=v3` to any Janus URL to force that shell for
one page load, for example `http://localhost:8080/settings?ui=v1`. Your saved
choice still applies once you drop the parameter. To change it for good, open
**Settings → Appearance** while the override is active and pick a shell.
