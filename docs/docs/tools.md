# Janus's tools

Janus acts through tools. Each tool below is one entry on **Settings → Tools &
Connections → Built-in Janus tools**, and each heading here is the page the
tool's `docs_ref` points to (for example `tools.md#send-mail`). When a tool
answers `BLOCKED`, the message names that page so Janus (or you) can look up
why.

## Following along in chat

When a request needs more than one round of tools, Janus first posts a short
plan (what it will look at, and in what order), then one short line after each
round: whether the result was what it expected, the most interesting finding,
and what it will do next. The full answer comes at the end. A direct answer or
a single lookup skips the plan. These updates are part of the reply, so they
stay in the conversation, next to the live step list. Trigger runs and
delegated tasks don't write them: their output is a result, not a conversation.

## How permissions work

- **Chat** offers every built-in tool below except *Mail processing* (that one
  is only for the Mail processing trigger) and *Start conversations* (that one
  is only for trigger runs; in chat Janus is already talking to you), plus the
  *Remote MCP tools* of your enabled MCP servers. A tool
  is offered only when its own condition is met:
    - *Read mail*, *Relabel mail*, *Send mail*, *Read mail rules*, and *Manage
      mail rules* need a connected mail account, and *Read WhatsApp* and *Send
      WhatsApp* an enabled WhatsApp account;
    - *Calendar tools* need a connected calendar, *Jira task tools* a connected
      task tracker, and *Dashboard tools* either of the two;
    - *GitHub tools*, *Linear tools*, and *Web search* need the instance-wide
      GitHub token, Linear key, or search provider set up by an administrator;
    - the five *Microsoft* tools (Outlook mail, calendar, Teams chats, and the
      two send tools) need the instance-wide Microsoft 365 sign-in
      (`janus microsoft login`, see [Configuration](configuration.md#microsoft-365-outlook-and-teams))
      and are for administrators only;
    - the *Azure resources* tool needs `JANUS_AZURE_ENABLED=true` (see
      [Configuration](configuration.md#azure-resources)) and is for administrators only;
    - *Image generation* needs an image provider and its API key, set up by
      an administrator;
    - *Skill library tools* are for administrators only;
    - *Self-evolution tools* are for an administrator who switched the
      self-evolution loop on, with GitHub and Linear set up;
    - *Shell* is for administrators only, and only when the operator turned
      it on with `shell_tool_enabled` (off by default);
    - *Coding agents* is for administrators only, and only when the operator
      turned it on with `coding_agents_enabled` (off by default) and at least
      one coding-agent CLI is installed.

    Everything else is always offered. The **Unattended** switch does not
    apply in chat.

    **Janus asks before the tools with the biggest effects.** The tools
    marked "Unattended default: Off" below (*Send mail*, *Send Microsoft Outlook mail*, *Send Microsoft Teams
    messages*, *Send WhatsApp*, *Change local planner*, *GitHub tools*, *Linear tools*, *Web page reader*, *Propose wiki
    edits*, *Manage mail rules*, *Skill library tools*, *Self-evolution tools*,
    *Shell*) ask for your OK in chat, unless you chose *Always allow* for one
    (see below).
    When Janus calls one, the reply pauses and an approval card appears in the
    conversation, in the sidebar chat, on the Chat page, and in every UI
    shell. It shows the tool, why it needs your approval, and the arguments
    Janus wants to use (passwords and tokens shown as `[redacted]`):
    - **Approve** runs the tool with exactly those arguments, once. The next
      call, even to the same tool, asks again.
    - **Always allow** runs this call too, and switches the tool's **In chat**
      setting to *Always allow*: from your next message on, Janus uses that
      tool in chat without asking. Change it back on Settings → Tools &
      Connections.
    - **Deny** does not run it. Janus is told `BLOCKED: ... was not run
      because the user declined` and carries on with its reply.
    - If you do not answer within 5 minutes (an administrator can change
      this with `chat_tool_approval_timeout_seconds`), or you close the tab
      or lose the connection, the call is not run either. Reconnecting never
      runs or re-asks a call that was waiting.

    After you decide, the card turns into the tool's chip below, marked
    *approved by you* or *denied*, and the decision is saved with the
    conversation. A chat app that cannot show the card (the iOS app, for
    now) gets `BLOCKED` for these tools instead: they never run unasked.

    **You choose how each of these tools behaves in chat.** On Settings →
    Tools & Connections each of these tools has an **In chat** switch next to
    **Unattended**:
    - **Ask every time** (the default) offers the tool and shows the
      approval card for every call, as above.
    - **Always allow** offers the tool and runs it without a card. The
      call still shows as a chip under the reply.
    - **Off in chat** takes the tool out of chat completely: Janus does not
      see it, cannot call it, and cannot ask for it. Use this for a tool you
      never want suggested in chat, such as *Send mail*.

    Above the list, **In chat: Allow all without asking** switches every
    approval card off at once; tools set to *Off in chat* stay off. Settings
    asks you to confirm before turning it on. There is no "allow for the
    rest of this conversation". The **In chat** settings are yours alone
    (other users' settings never apply to you) and do not change what
    triggers, automations, or background tasks may do; that is the
    **Unattended** switch. Tools that run without asking have no **In chat**
    switch.

    **Page actions change only what you see.** With *Page actions* Janus can
    open another page, set a filter, open, highlight, or scroll to something
    you can see, or fill in the "Add manual topic" form for you. They run
    without asking because they change nothing in your data: a filled-in form
    is never submitted, and anything that does change data still goes through
    the tools above. Janus can only act on items listed on your screen, and
    your browser checks every request again against the page you have open.
    Each action shows as a chip (for example *open_item · Invoice overdue*)
    with **Undo** where that is cheap. They are chat-only and stop for the
    rest of a reply once something Janus read looked like a prompt injection.

    **Page introspection only looks.** With *Page introspection* Janus can
    read an outline of the page you have open (regions, headings, buttons,
    links, form-field labels, short visible text) and the layout and colour
    styles of one element, to answer "why is this button greyed out?" or
    "why does this look wrong?". It runs without asking because it changes
    nothing, and it never sees what you typed into a form, password fields,
    the chat panel, or anything a page marks private. It is chat-only, and
    the outline is treated as untrusted data. See
    [Page introspection](#page-introspection).

    **UI blocks only show something.** With *UI blocks* Janus can put a
    card, table, chart, checklist, or a row of buttons into its reply. They
    change nothing; a button only sends a chat message asking Janus to run a
    tool, and that tool then follows the rules above (an approval card for the
    biggest effects). See [UI blocks](#ui-blocks).

    **Image generation sends your prompt to a cloud provider.** When you ask
    for a picture, Janus writes a prompt and sends it to the image provider
    your administrator chose (OpenAI, Gemini, or OpenRouter). It runs without
    an approval card, but it costs money on that account, so Janus only calls
    it when you ask for an image. See [Image generation](#image-generation).

    Each reply lists the tools Janus called as chips under the message. A
    chip shows whether the call is still running, finished, failed, or was
    **blocked** (refused, for example because the integration it needs is not
    connected), and how long it took. Click it to expand the arguments Janus
    passed and the start of the result (up to about 4 KB, marked
    *truncated* when cut), with a **Copy** button. Passwords, tokens, API
    keys, and similar fields in the arguments are shown as `[redacted]`.
    Results are shown as plain text, exactly as the tool returned them, since
    they can contain mail or web content. The calls are saved with the
    conversation.

    **This turn** shows Janus's steps while it works on a reply. Each time
    Janus decides to call tools is one step, listed with its tool calls and
    its state: *generated* (decided, not started), *running*, *waiting for
    approval* (an approval card above is open for one of its calls),
    *completed*, or *failed* (a call failed or was blocked or denied). On the
    Chat page it sits beside the conversation on wide screens; in the sidebar
    chat, and on narrower screens, it is a collapsed **This turn** bar above
    the chat that you can open. Steps are not saved: after a reload the
    conversation still shows its tool chips, but not the steps.
- **Unattended runs** -- triggers (including the built-in Morning briefing,
  Afternoon digest, and Mail processing) and background tasks Janus delegates
  -- follow each tool's **Unattended** switch, *Allowed* or *Off*. Tools marked
  "Unattended default: Off" below have the biggest effects; everything else is
  *Allowed* until you change it.

  **Unattended: Allow everything**, above the list on Settings → Tools &
  Connections, overrides every switch at once: every tool runs in triggers,
  automations, and background tasks, *Off* ones included, with nobody asked.
  It also opens *Image generation* and your *Remote MCP tools* to those runs.
  It does not open *Page actions*, *Page introspection*, *UI blocks*,
  *Propose automations*, or *Propose character changes* (they need you in
  the chat), the *Self-evolution tools* (they keep their own switch), or
  write tools for delegated background tasks (those still only read).
  Settings asks you to confirm before turning it on, and every run still
  records which tools it used. With the per-tool setting (the default),
  what an *Off* tool does depends on the run:
    - **A saved trigger** (one listed on Settings → Triggers, built-in or your
      own) gets exactly the tools selected on it, plus *Core tools*. An *Off*
      tool among them is still offered, but calling it answers `BLOCKED: ...
      was not run because the user has switched it off for unattended runs`,
      and the run continues and lists it under **Refused tool calls**. On a
      trigger you wrote yourself, selecting a tool allows it for that trigger
      even when it is *Off*; the built-in triggers do not get this.
    - **A one-time trigger or follow-up with no tool list**, and **a delegated
      background task with no tool list**, get only the tools that are
      *Allowed*. *Off* tools are not offered at all, so there is nothing to
      call and no `BLOCKED` answer.
    - **A one-time trigger or delegated task that names its tools** fails as a
      whole, before anything runs, when one of the named tools is *Off*. Its
      error names the tool with `confirmation_required`.
- `BLOCKED ... the integration it needs is not connected` means the account
  or service behind the tool is not set up yet. Settings → Tools &
  Connections shows such a tool as **Not connected yet**. A one-time trigger
  or delegated task that named that tool fails instead of answering
  `BLOCKED`.
- `BLOCKED ... looked like a prompt injection` means something Janus read in
  the same turn (mail, web results, tickets) contained instructions aimed at
  Janus, so it stopped before doing anything consequential. Ask again in a new
  message if you really want the action.
- **Optional model-based check.** Janus spots such instructions with
  built-in rules, which catch the usual wordings in English and German. Under
  Settings → Tools & Connections, **Model-based prompt-injection check**
  (off by default) adds a second look: outside text the rules find clean
  but that looks borderline (it talks to an "AI" or "assistant" about
  instructions, or is mostly in another script) is also shown to your
  utility model. When the model thinks it is an injection, Janus treats it
  like a rule hit: the safety notice names it with a `model_` label, and the
  same tools stop. The model can only add a warning, never remove one. Each
  text is checked once and remembered (only a fingerprint, never the text).
  If the model fails, is slow, or the card's monthly token budget is spent,
  Janus keeps the rules' result and carries on. Your own chat messages are
  never checked. A slow check still counts against the budget: Janus sets
  its estimated tokens aside before asking and keeps them when it stops
  waiting. With a cloud utility model (hosted Ollama included), borderline
  outside text is sent to that provider, so the card asks before it switches
  on.
- Background tasks Janus delegates only ever get read-only tools, and only
  ones the task that started them had.

See [Runtime triggers](runtime-triggers.md) and
[Configuration](configuration.md#janus-character-triggers-and-tool-permissions)
for the full picture.

## How Janus picks tools

Janus does not hand the model every tool it may use. Before each chat message,
each ad-hoc follow-up it scheduled, and each delegated task, a quick call to the
*utility model* (Settings -> AI & System) picks the toolboxes the request needs,
and only those are offered. This keeps each request smaller and faster.

- **It can only narrow.** The pick is limited to what your settings already
  allow: the tools above, your *Unattended* and *In chat* switches, a saved
  automation's own tool list, and what a delegated task was granted. A tool the
  model names that you have not allowed is dropped, and tools that ask before
  each call still ask.
- **It fails safe.** If the utility model is not set up, too slow, or answers
  with something unusable, Janus offers every tool it is allowed to use, as
  before.
- **You can see it.** Above the chat, *Tools picked for this message* lists the
  chosen tools and why. An automation's run result has a *Tool selection* line.
- **Asking for more.** If a tool was left out, ask for it ("use the calendar").
  Janus can add it with `request_tools`; it is offered from your next message on
  and stays for the rest of the conversation.
- Saved automations (including the morning briefing, digest and mail
  processing) always get the tools you picked for them; nothing is narrowed.
- An administrator can turn this off with `tool_selection_enabled = false`
  (see [Configuration](configuration.md)).

## Core tools

`get_current_datetime`. Always available. Unattended default: Allowed.

## System introspection tools

`list_recent_events` reads the Janus event log. In chat an admin sees the whole
log; everywhere else Janus reads only your own entries. Unattended default:
Allowed.

## Trigger history

`list_my_trigger_history` reads the outcomes of your own trigger runs, so a run
can check what earlier runs found. Unattended default: Allowed.

## Read mail

`search_emails`, `get_email_detail`, `get_email_details_batch` search and read
the mail Janus stored from your own accounts. Needs a connected mail account.
Mail text is treated as untrusted. Unattended default: Allowed.

## Relabel mail

`relabel_email`, `relabel_emails_batch` change a message's Janus label
(IMPORTANT, DIGEST, IGNORE, SPAM). Relabelling teaches your own rules. Needs a
connected mail account. Unattended default: Allowed.

## Send mail

`send_email` sends mail from one of your own active accounts. Unattended
default: **Off**.

## Send WhatsApp

`send_whatsapp_message` sends a plain-text WhatsApp message as you, from one
of your enabled WhatsApp accounts. It sends either into a chat Janus already
stored (a person or a group, by the `id` from `list_whatsapp_conversations`)
or to a phone number in international format (for example `+4915112345678`),
which starts a new direct chat. When you have several enabled accounts and
Janus sends to a phone number, it has to name the account.

- **Asks first.** In chat every message shows an approval card with the
  recipient and the exact text, unless you chose *Always allow*.
- **Needs a live connection.** The message goes out over the account's
  running WhatsApp connection. When that connection is down, Janus gets an
  error and nothing is sent.
- **Stored.** A sent message appears in the conversation as an outgoing
  message.
- **Text only.** No pictures, files, reactions, or WhatsApp Status posts.
  At most 4,096 characters per message.

Unattended default: **Off**.

## Notify me on WhatsApp

`notify_me_on_whatsapp` lets the built-in Secretary interrupt you with one
short WhatsApp message on **your own number**, for something time-critical that
you would otherwise miss. It cannot message anyone else: the recipient is the
number you set under **Settings → Notifications → Secretary**, never something
Janus chooses.

- **Off until you switch it on.** Janus does not even see the tool until you
  turn on *WhatsApp alerts* and enter your number (international format, such
  as `+4915112345678`), and you have a connected WhatsApp account.
- **Only for runs nobody watches.** Not offered in chat.
- **Rarely.** At most one message per run and three a day, 600 characters,
  plain text. No links or images. Nothing is sent after Janus read something
  that looked like an injected instruction.
- **When it cannot send.** If WhatsApp is not connected, Janus gets an error
  and opens a conversation in Janus instead; nothing is lost.
- **Stored.** The message appears in your own chat as an outgoing message.

Unattended default: Allowed (the switch above is the real gate).

## Mail processing

`set_email_priority`, `create_reminder`, `track_email_topic`,
`suggest_planner_item`: what the built-in Mail processing trigger uses to file
a new message. Not offered in chat. Unattended default: Allowed.

## Read WhatsApp

Janus can read the WhatsApp conversations it stored from your own connected
WhatsApp accounts, so it can answer questions like "what did Anna write me on
WhatsApp yesterday?". Four tools:

- `list_whatsapp_accounts`: your accounts, their connection status, and when
  they last synced.
- `list_whatsapp_conversations`: recent chats, most recent first. It can
  filter by a search on the chat name or participants, by unread chats only,
  by chats with a message since a date, or by account.
- `read_whatsapp_messages`: one chat's messages, newest first, optionally
  between two dates.
- `search_whatsapp_messages`: messages containing a text, optionally in one
  chat or since a date.

What to know:

- **Only what Janus stored.** The tools read what the WhatsApp connection
  synced into Janus. They do not reach WhatsApp themselves, and they cannot
  send, react, or mark anything read. Sending is a separate tool,
  [Send WhatsApp](#send-whatsapp).
- **Only your own, enabled accounts.** Nobody else's chats are ever returned.
  An account you disabled in Settings stays listed (marked disabled), but its
  chats and messages are not readable until you enable it again.
- **Limits.** Each call returns at most 100 items, and any text (a message, a
  chat's participants or last snippet, an attachment description, a sync
  error) longer than 4,000 characters is cut. A search treats `%` and `_` as
  ordinary characters.
- **Untrusted content.** Message text, names, and chat titles come from other
  people. Janus treats them as information, never as instructions, the same
  way as mail.
- **When it is offered.** In chat only, once you have at least one enabled
  WhatsApp account. The Morning briefing and digest do not read WhatsApp, just
  as they do not read mail.

Unattended default: Allowed.

## Calendar tools

`list_calendar_events`, `get_event_detail` read the events of your own connected
calendars and the reminder entries Janus created from your mail; other users'
events are never visible. Unattended default: Allowed.

## Microsoft Outlook mail

`microsoft_list_mail`, `microsoft_get_mail` list, search, and read messages in
your Microsoft 365 (Outlook) mailbox through Microsoft Graph, as you. This is
separate from *Read mail*, which reads the IMAP mail Janus stored. Needs the
Microsoft sign-in and the `Mail.Read` permission. Administrators only. Mail
text is treated as untrusted. Unattended default: Allowed.

## Microsoft Outlook calendar

`microsoft_list_calendar_events` lists your Outlook calendar events (recurring
events expanded) in UTC. Needs the Microsoft sign-in and the `Calendars.Read`
permission. Administrators only. Unattended default: Allowed.

## Microsoft Teams chats

`microsoft_list_teams_chats`, `microsoft_read_teams_chat` list your Teams chats
with their members and read the newest messages of one chat. Needs the
Microsoft sign-in and the `Chat.Read` (or `Chat.ReadWrite`) permission.
Administrators only. Chat text is treated as untrusted. Unattended default:
Allowed.

## Send Microsoft Outlook mail

`microsoft_send_mail` sends a plain-text mail from your Outlook mailbox. Needs
the `Mail.Send` permission. Administrators only. Chat pauses for your approval
on every call. Unattended default: **Off**.

## Send Microsoft Teams messages

`microsoft_send_teams_message` posts a plain-text message into one of your
Teams chats as you, so the recipients see it as written by you. Needs the
`Chat.ReadWrite` permission. Administrators only. Chat pauses for your approval
on every call. Unattended default: **Off**.

## Azure resources

`azure_list_subscriptions`, `azure_list_resource_groups`, `azure_list_resources`
list what the machine's Azure identity can see in Azure Resource Manager
(read-only; no create, change, or delete). The identity comes from
`DefaultAzureCredential`: a service principal in the environment, a managed
identity, or the `az login` session of the machine Janus runs on. It is off
unless `JANUS_AZURE_ENABLED=true`, and administrators only. Resource names and
tags are treated as untrusted content.

## Jira task tools

`list_tasks`, `get_task_detail` read tasks from your own Jira integration; other
users' tasks are never visible. Unattended default: Allowed.

## Read local planner

`list_local_tasks`, `get_local_task` read Janus's own planner. Unattended
default: Allowed.

## Change local planner

`create_local_task`, `update_local_task`, `complete_local_task`,
`move_local_task`, `reorder_local_task`. Unattended default: **Off**.

## Dashboard tools

`get_daily_briefing`, `list_dashboard_widgets`, `update_dashboard_widget`,
`set_dashboard_widget_prompt`, `clear_dashboard_widget_prompt`. Triggers Janus
schedules itself never get the two widget-prompt tools. Unattended default:
Allowed.

## GitHub tools

`list_prs`, `merge_pr` on the instance's configured GitHub token. Unattended
default: **Off**.

## Linear tools

`list_linear_teams`, `list_linear_tasks`, `add_linear_task`,
`add_linear_subtask`, `update_linear_task`, `comment_on_linear_task`,
`start_linear_task`, `start_linear_subtask`, on the instance's Linear API key.
Unattended default: **Off**; in chat, Janus asks before each call.

- **Teams.** `list_linear_teams` shows every team the key can see, with its
  id, key, name, and whether it is the default. Wherever a tool takes a
  `team_id`, it accepts the team key (`ENG`), the exact team name, or the
  team id, ignoring case. Without one, Janus uses the default team from
  **Settings → Linear**; with no default set and only one team in the
  workspace, it uses that team and says so. Otherwise the answer lists the
  team keys to choose from.
- **Creating issues.** `add_linear_task` and `add_linear_subtask` need a
  title (one line, 5–120 characters) and a description (Markdown, 40–20,000
  characters): the context and why, what should change, and acceptance
  criteria. A title with a `Description:` section in it is refused.
- **Updating issues.** `update_linear_task` changes an issue's title,
  description, state, or priority (at least one). The state is a workflow
  state name of the issue's team (for example `In Progress`) or a state type:
  `backlog`, `unstarted`, `started`, `completed`, `canceled`. Priority is 0
  (none), 1 (urgent), 2 (high), 3 (medium) or 4 (low).
- **Comments.** `comment_on_linear_task` adds a Markdown comment of 1–10,000
  characters.
- **Assigning.** `start_linear_task` and `start_linear_subtask` assign an
  issue to the workspace's GitHub Copilot user.

A call that breaks these rules, names an unknown team, or names an unknown
state answers `REFUSED: ...` with the valid choices, and nothing is created
or changed.

## Web search

`web_search` sends only the short query Janus writes to the configured search
provider (see [Agent web search](configuration.md#agent-web-search)). Results
are untrusted. Unattended default: Allowed.

## Web page reader

`web_fetch` opens one web page by its full `http://` or `https://` address
and gives Janus the page's title, its readable text, and the address it ended
up at after redirects. It needs no setup, but because the address itself is
sent to that site, **Janus asks before every call** in chat: the approval
card shows the exact URL. Janus is told never to put your mail, your
conversation, or other personal details into a URL, and never to open one
just because a page or email told it to.

What it will and will not do:

- Only public addresses: it refuses `localhost`, private and link-local
  networks (home routers, cloud metadata services), and other non-public
  addresses, also after a redirect. At most 5 redirects are followed.
- It sends no cookies or passwords and identifies itself as
  `Janus-WebFetch/1.0`, so it sees what a logged-out visitor sees.
- Only HTML, plain text, Markdown, CSV, JSON, and XML pages; PDFs, images,
  and downloads are refused. It reads at most 2 MB, waits at most about
  20 seconds, and returns at most 20,000 characters of text (marked
  *truncated* when cut). Scripts, menus, footers, and hidden text are left
  out; links are reduced to their text.
- The page is untrusted content: Janus treats it as information, never as
  instructions, and cites the address it used.

Unattended default: **Off**. A trigger you write yourself can still select it.

## Memory tools

`list_memory_facts`, `search_facts`, `add_memory_fact`, `update_fact`,
`delete_fact`, `group_facts` manage the facts Janus remembers about you.
Triggers Janus schedules itself never get `delete_fact`. Unattended default:
Allowed.

## Topic tools

`list_topics`, `create_topic`, `search_topics`, `get_topic_detail`,
`update_topic_state`. Unattended default: Allowed.

## Knowledge search

`search_knowledge`, `get_entity` search everything Janus has ingested and the
people, organizations, and projects it extracted. Unattended default: Allowed.

## Read the wiki

`list_wiki`, `read_wiki`. Unattended default: Allowed.

## Propose wiki edits

`propose_wiki_edit` suggests a new text for a wiki page; nothing changes until
you accept it. Unattended default: **Off**.

## Trigger tools

`create_scheduled_trigger`, `list_my_triggers`, `get_trigger`,
`cancel_trigger`. Trigger runs may schedule a few one-time follow-ups within
fixed limits (see [Runtime triggers](runtime-triggers.md)). Unattended
default: Allowed.

## Start conversations

`start_conversation` lets a trigger run (including a follow-up Janus
scheduled, or a saved trigger you selected it on) start a new chat with you
that opens with a message from Janus, for example "Your flight was moved;
should I update the calendar?". The chat appears in your conversation list
marked **Started by Janus** and **New** until you open it, the Chat entry of
the navigation shows how many such chats are new, and a local (non-server)
install also shows a desktop notification. Reply in it like in any other
chat.

Limits: one new chat per run and five per day, only for you (it can never
write into an existing chat or anyone else's), no links to other sites, URLs,
or images in the message (links inside Janus such as `/settings` are fine),
and nothing after Janus read something that looked like a prompt injection in
the same run. Not offered in chat or to background tasks. Unattended
default: Allowed.

## Propose automations

Janus drafts a saved trigger for you to review on Settings → Triggers; a draft
never runs until you approve it. Chat only: no unattended run can use it, so
its switch is locked.

## Character state

`get_character_state` lets Janus explain its current adaptive character state:
tone, verbosity, proactivity, and current focus, the values you allow for each,
its last few changes, and its open proposals. Read-only. Unattended default:
Allowed.

## Propose character changes

`propose_character_state` lets Janus suggest a different tone, verbosity,
proactivity, or current focus, for example after you ask for shorter answers.
Nothing changes until you approve the proposal on Settings → Character, and
only values inside the bounds you set are accepted. At most one proposal per
chat turn. Janus never changes its constitution. Chat only: no unattended run
can use it, so its switch is locked.

## Read mail rules

`list_mail_rules`, `list_pending_rule_suggestions`. Needs a connected mail
account. Unattended default: Allowed.

## Manage mail rules

`create_mail_rule`, `update_mail_rule`, `delete_mail_rule`,
`approve_rule_suggestion`, `dismiss_rule_suggestion`. Unattended default:
**Off**.

## Task delegation tools

`delegate_task`, `get_delegated_task`, `list_delegated_tasks` hand a bounded,
read-only sub-question to a background task. Unattended default: Allowed.

In a trigger run Janus also gets `wait_for_delegated_tasks`: the run pauses
after its answer (status `waiting`) until the tasks it delegated have
finished, then continues automatically with their results. A run can wait
only once, only for tasks it delegated itself, and the built-in briefing,
digest, and mail processing runs never wait. In chat, Janus checks on
delegated tasks with `get_delegated_task` instead.

A background task that was cut off (for example because Janus restarted) is
started again once, automatically; if that is cut off too, it is reported as
failed.

The tasks Janus delegated from a chat are listed in that chat: a collapsed
**Delegated tasks (n)** line above the conversation, with an **n active** badge
while any is queued or running. Open it to see, for each task, its status
(queued, running, completed, or failed), the question it was given, the tools
it could use (requested tools it did not get, because they are not read-only
or were not available to the chat, are listed as *withheld*), and its answer
or error. The list updates by itself while a task is running and when a reply
finishes; **Show more** loads older tasks. A task cut off by a restart shows as queued again while
it is started a second time, and as failed ("interrupted") if that is cut off
too.
Only you see the tasks of your own conversations.

## Skill library tools

`create_skill`, `list_skills`, `update_skill`, `search_remote_skills`,
`import_remote_skill`. Administrators only: skills steer every user's Janus.
Unattended default: **Off**.

## Janus documentation

`list_docs`, `read_doc`, `search_docs` let Janus read this documentation, so it
can answer "how do I...", "what does this setting do", "why was my tool call
BLOCKED", and "what can you do" from the docs instead of guessing. Read-only.
The dated review and security assessment reports under `reviews/` are not
offered to Janus. Unattended default: Allowed.

## Page actions

`ui_navigate`, `ui_set_filter`, `ui_open_item`, `ui_highlight`,
`ui_scroll_to`, `ui_prefill_form` let Janus act on the page you have open in
the web UI: "open the mail from Anna", "filter to important", "go to triggers
and show the failed runs", "highlight the topic about the kitchen". They do
nothing on the server; your browser performs them and reports back, and
Janus is told when the page could not do something (or did not answer within
10 seconds, `chat_ui_action_timeout_seconds`), so it never claims a change
that did not happen.

- **Where:** the Dashboard (scroll to a widget; open or highlight a listed
  email, topic, or calendar event), Emails (priority filter, open,
  highlight), Memory → Topics (status filter and search, open, highlight,
  fill in "Add manual topic"), and Settings → Triggers (open the tab, filter
  the recent runs by status or kind, show finished follow-ups, expand or
  highlight a trigger). Janus can open any page of the app, and the Memory and
  Settings tabs.
- **Only what you see:** Janus may only open or highlight items that are
  listed on your screen, only set the filters and values a page offers, and
  only fill in the fields of the "Add manual topic" form. It never submits a
  form.
- **Undo:** each action appears as a chip under the reply. Opening a page,
  changing a filter, opening an item, and filling in the form have an
  **Undo** button (go back, restore the previous value, close the item, clear
  what was filled in) while the conversation is open in that tab.
- **Chat only**, in the web UI: no trigger or background task can use them, so
  the switch is locked. A chat app that cannot perform them (the iOS app, for
  now) gets an error instead. After something Janus read in the same turn
  looked like a prompt injection, every page action answers `BLOCKED`, even
  opening another page.

## Page introspection

`get_ui_snapshot` and `get_element_styles` let Janus look at the page you have
open in the web UI when you ask about what you see: "why is the save button
greyed out?", "I don't see the error message", "why does this look wrong?".
Janus first uses the short page description every message carries (the page,
its filters, and the listed items); it calls these tools only when it needs
more.

- **What Janus can see:** `get_ui_snapshot` returns an outline of the page:
  its regions (header, navigation, main area, side panels, dialogs), headings,
  buttons, links, form fields with their labels and whether they are
  disabled, required, or marked invalid, image descriptions, alerts, and short
  pieces of visible text, plus the window size, scroll position, light or
  dark mode, and which UI look (classic, terminal, console) you use. Each
  element gets a short reference such as `e12`; `get_element_styles(e12)`
  then returns that element's position and size and a fixed set of layout and
  colour styles (display, visibility, size, spacing, colours, fonts, ...).
  References are only valid until the next outline.
- **What Janus never sees:** what you typed or picked in a form field
  (values are never read), password fields (left out entirely), editable text
  areas, the chat panel itself, and anything a page marks private. Link
  targets are sent without their query string.
- **Bounded and treated as untrusted:** an outline holds at most 200 elements
  and 24 KB, checked again by the server. Page text can come from mail and
  other outside sources, so Janus treats the outline as data, never
  instructions; if it looks like a prompt injection, the reply carries a
  safety notice and page actions and other consequential tools answer
  `BLOCKED` for the rest of that reply.
- **Nothing changes:** both tools only read; your page is not altered. Janus
  waits up to 10 seconds for the page to answer
  (`chat_ui_action_timeout_seconds`, shared with page actions) and says so
  when it did not.
- **Chat only**, in the web UI: no trigger or background task can use them, so
  the switch is locked. A chat app that cannot answer (the iOS app, the CLI)
  gets an "unavailable" error instead.

## UI blocks

`render_ui` lets Janus answer with a small piece of structured UI instead of
only text: a summary card, a table, a bar, line, or pie chart, a checklist, a
list of key/value facts, a row of buttons, or an image. Ask for it ("show my
open tasks as a table", "chart this week's mail by day"), or Janus uses it on
its own when it fits. The blocks appear under the reply in the sidebar chat
and on the Chat page, in every UI shell, and are saved with the conversation,
so they look the same after a reload.

- **Display only:** a block changes nothing. Everything in it is shown as
  plain text (no formatting, HTML, or scripts), links go only to pages of
  Janus or to `https://` sites (shown with their address, opened in a new
  tab), and images only come from Janus itself, never from another site.
- **Buttons ask, they do not act:** clicking a button such as *Snooze* sends
  a normal chat message, for example `Run "Snooze": call snooze_topic with
  {"topic_id": 4}`, which you can see in the conversation. Janus then runs
  the tool in its reply like any other request, so tools that need your OK
  still show their approval card. Each button works once.
- **Size limits:** a table has at most 50 rows and 8 columns, a chart 50
  points and 5 series, a checklist 50 items, a row of buttons 6 buttons, and a
  reply at most 8 blocks. Janus is told when a block does not fit and tries
  again or answers in text.
- **When a block cannot be shown** (for example after a downgrade, or if it
  was damaged), the chat shows its raw content with "Could not render this
  block" instead; the rest of the conversation is unaffected.
- **Chat only**, in the web UI: no trigger or background task can use it, so
  the switch is locked. A chat app that cannot show blocks (the iOS app, for
  now) gets a plain-text answer instead. After something Janus read in the
  same turn looked like a prompt injection, blocks with buttons or links to
  other sites answer `BLOCKED`; other blocks still show.

## Image generation

`generate_image` lets Janus draw a picture when you ask for one ("draw a
lighthouse at dusk as a watercolor", "make a flat icon of a calendar"). The
picture appears in the reply in the sidebar chat and on the Chat page, in
every UI shell, with a **Download** link, and stays with the conversation, so
it is still there after a reload.

- **Off until an administrator turns it on:** pick a provider and model on
  **Settings → AI & System → Image generation** (see
  [Configuration](configuration.md#image-generation)). Until then Janus is not
  offered the tool and tells you it cannot draw. Ollama and Anthropic models
  cannot make images.
- **The prompt leaves your machine:** the description Janus writes (and any
  style you asked for) is sent to OpenAI, Google Gemini, or OpenRouter with
  the API key stored for that provider, and that provider's terms apply. Do
  not ask for pictures of things you would not send there.
- **It costs money:** each image is a paid request on that account. The tool
  runs without an approval card, but it shows as a chip under the reply with
  the prompt Janus used, and every image is recorded in the event log
  (provider, model, and prompt length, never the prompt itself).
- **Where images are kept:** as files in Janus's data directory, one folder per
  user, never inside the database. Only you can open your images, at
  `/api/images/<id>` while you are signed in; another user gets "not found".
  **Deleting a conversation deletes its images.** Each user can keep up to
  200 MB of images (an administrator can change this); when you reach the
  limit, Janus refuses new images until you delete conversations with images.
- **Chat only**, in the web UI, unless you chose **Unattended: Allow
  everything** (see [How permissions work](#how-permissions-work)): then a
  trigger or automation can generate images too. Such an image is stored
  without a conversation, the run's result names its `/api/images/<id>`
  link, and it counts toward your 200 MB until you delete it with
  `DELETE /api/images/<id>`. Otherwise the switch is locked. A chat app that cannot show images (the
  iOS app, for now) gets an error before anything is sent or paid for. After
  something Janus read in the same turn looked like a prompt injection (for
  example a mail asking Janus to draw something), image generation answers
  `BLOCKED` and nothing is sent; ask again in a new message.

## Self-evolution tools

`self_evolution_status`, `file_improvement_issue`, `start_implementation_job`,
`merge_feature_pr`, `merge_release_pr`, `schedule_production_self_check`: the
opt-in loop in which Janus files issues about its own gaps, starts Claude Code
implementation jobs, merges the resulting pull requests and the release, and
checks production afterwards -- **without asking anyone first**.

- **Off until an administrator turns it on** on **Settings → Tools &
  Connections → Self-evolution** (see
  [Configuration](configuration.md#self-evolution-loop)). Its **Unattended**
  switch follows that setting and cannot be changed on the tools list; while
  it is off, every call answers that self-evolution is switched off.
- **What it may merge:** only pull requests opened by a Claude Code session it
  started (the description must name that session), minor or patch Dependabot
  bumps (merged one at a time, at the owner's request), revert pull requests it
  opened, and the release-please pull request after its feature.
- **Gates in code, not approvals:** a pull request is merged only when every
  CI check on it passed, GitHub reports it mergeable, it targets the default
  branch, and it touches nothing in the scope fence (authentication, the tool
  policy, the prompt-injection guard, CI and deploy files, migrations, and the
  loop itself). The release PR is merged only after the loop merged the
  feature and the release contains it. Per day at most 5 issues, 3 jobs, 3
  feature merges, 3 release merges, 3 self-checks, and 3 revert PRs.
- **If the production check fails** after a release, Janus alerts you, files
  a Linear issue, and opens a revert pull request (merged later through the
  same gates).
- Every attempt, refusal, and result is listed on the settings card and in the
  event log. A refused call answers `REFUSED: ...` and changes nothing. In
  chat each call asks first, like the other tools with the biggest effects.

See the [feature spec](feature-specs/autonomous-self-evolution-loop.md) for
every gate.

## Shell

`run_shell_command` runs one command on the machine Janus runs on and gives
Janus its exit code and output. It is meant for a **local, personal
install**: Janus can look things up or change things on your own computer
when you ask it to.

- **Off until the operator turns it on** with `shell_tool_enabled = true` in
  `settings.toml` (or `JANUS_SHELL_TOOL_ENABLED=true`); see
  [Configuration](configuration.md#shell-tool). Leave it off on a shared or
  hosted server. **Administrators only.**
- **Janus asks before every command** in chat: the approval card shows the
  exact command. Commands longer than 1,000 characters, or with control or
  invisible characters (other than tab and newline), are refused, so the
  card can never be cut or hide part of what runs. Read it
  before you approve: a harmless-looking command can still delete files or
  send data off the machine.
- **No shell by default.** The command is split like a shell would split it
  and run directly, so pipes (`|`), redirection (`>`), `&&`, `*` globs, and
  `$VARIABLES` are passed as plain text. The operator can allow them with
  `shell_tool_allow_shell_syntax = true` (commands then run through
  `/bin/sh -c`).
- **Fenced process.** Commands run in `shell_tool_working_dir` (default: your
  home directory), with no input (no interactive prompts, no `sudo`
  password), a time limit (`shell_tool_timeout_seconds`, default 30 s), and
  an output cap (`shell_tool_output_max_bytes`, default 16,000 bytes of
  stdout and stderr together). When the time or output limit is hit, or the
  command finishes, everything it started in its process group is stopped.
  A program that detaches itself completely (a daemon) keeps running, but
  Janus does not wait for it.
- **No secrets.** A command sees only `PATH`, `HOME`, `USER`, `LOGNAME`,
  `SHELL`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TERM`, `TZ`, and `TMPDIR` from
  Janus's environment, never Janus's own settings or API keys. It still runs
  as the same operating-system user as Janus, so it can read any file that
  user can (including the Janus database and key file).
- The output is untrusted content: Janus treats it as information, never as
  instructions. After something Janus read in the same step looked like a
  prompt injection, *Shell* answers `BLOCKED`; ask again in a new message.
- Delegated background tasks never get it.

Unattended default: **Off**. You can switch it on for unattended runs, and a
trigger you write yourself that selects *Shell* runs its commands **without
asking**, so only do that for commands you trust.

## Coding agents

`ask_coding_agent` lets Janus ask a coding agent installed on the machine
Janus runs on for help: Claude Code (`claude`) or the OpenAI Codex CLI
(`codex`). Janus stays one assistant; the
coding agent is a tool it calls, like an MCP server. It is meant for a
**local, personal install** where you already use one of those CLIs and are
logged in to it.

- **Off until the operator turns it on** with `coding_agents_enabled = true`;
  see [Configuration](configuration.md#coding-agents-tool). Only agents whose
  program is installed are offered. **Administrators only.**
- **Janus asks before every question** in chat: the approval card shows the
  agent and the exact prompt. The other agent does not see your conversation,
  only that prompt, and the prompt goes to that agent's model provider under
  your own account. Prompts longer than 1,000 characters, with invisible
  characters, or starting with `-` (which the agent would read as an option)
  are refused.
- **Read-only by default.** The agents can read and explain the files in
  `coding_agents_working_dir`, but not change them. With
  `coding_agents_allow_edits = true` they may edit files there.
- **Fenced process**, as for *Shell*: no input, a time limit
  (`coding_agents_timeout_seconds`, default 300 s), an output cap
  (`coding_agents_output_max_bytes`, default 50,000 bytes), and everything the
  agent started is stopped when it finishes. The agent sees only the basic
  environment (`PATH`, `HOME`, `LANG`, ...) plus the names listed in
  `coding_agents_env_passthrough`, for example an API key the CLI needs;
  never Janus's own settings.
- The answer is another agent's untrusted output: Janus checks it and never
  follows instructions in it. After a suspected prompt injection in the same
  step, *Coding agents* answers `BLOCKED`.
- Delegated background tasks never get it.

Unattended default: **Off**. If you switch it on, unattended runs can ask
coding agents without asking you first.

Want another coding agent supported? Open a
[feature request](https://github.com/thomasniebler/janus/issues/new).

See the [feature spec](feature-specs/external-agents.md) for every gate and
the planned remote (A2A) agents.

## Remote MCP tools

Tools from MCP servers you added on Settings → Tools & Connections. In chat,
Janus is offered every tool you left enabled on a server that is enabled and
whose last **Discover** succeeded. A change there (a server or tool switched
off, a new discovery) applies from your next message.

- **Names and arguments.** Each tool appears as `mcp_<server number>_<tool>` with
  the server's own description, and its arguments come from the input schema
  the server published, so Janus fills them in instead of guessing. A schema
  Janus cannot map (for example one using references or mixed types) falls
  back to a single free-form `arguments` object. Like JSON Schema, extra
  arguments are accepted unless the schema forbids them.
- **Asking first.** A tool the server marks read-only (`readOnlyHint`) runs
  without asking. Every other call waits for your approval, and the approval
  card shows the exact arguments, which are the ones sent to the server.
  Arguments are checked after you approve, so a call that lacks a required
  argument still shows a card and the error appears only after approval
  (checking before the card is planned, JAN-351). The card's reason names the
  server and tool; the server's own description appears only as quoted text.
  You can set such a tool to **Off in chat**
  through the tool-policy API, or switch it off on its server.
  The read-only mark is the server's own claim: enable only servers you
  trust that far, and switch off any tool you do not want Janus to use.
- **Untrusted descriptions.** A server writes its tools' names, descriptions,
  and argument descriptions itself, and that text reaches the model. Janus
  shortens it and scans it for prompt injections when you run **Discover**.
  A tool whose text looks like one is switched off and marked in its server's
  tool list. Switch it back on only if you trust the server: that accepts
  the current text. If the server later changes the text, the tool is
  switched off again.
- **Untrusted output.** What a remote tool returns is treated like a web
  page. It is marked as outside content and scanned for prompt injections.
  After something looked like one, every remote tool, including read-only
  ones, is refused for the rest of that reply, because each call sends
  arguments Janus chose to another server.
- **Chat only**, unless you chose **Unattended: Allow everything**: then
  triggers and automations get your remote tools too, without asking.
  Otherwise their **Unattended** switch is locked, and no trigger definition
  can list them. Delegated background tasks never get them.
- In the server's tool list, each tool shows whether it runs without asking
  or asks before each call, and whether it was switched off for its
  description.
- Removing a server, or a tool disappearing on the next **Discover**, also
  removes that tool's **In chat** setting.

A server signs Janus in one of two ways:

- **OAuth**: enter the server's authorization endpoint, token endpoint, and
  client ID, then click **Authorize** when discovery asks for it. You sign in
  on the provider's page.
- **Bearer token**: for a server that gives you a fixed access token (common
  for self-hosted or local MCP servers), paste it into **Bearer token** when
  you add the server, or open the server's details later to save, replace, or
  remove it (the token controls are hidden while the server's OAuth endpoints
  and client ID are filled in).
  Janus stores it encrypted, sends it as `Authorization: Bearer
  ...` on every request to that server, and never shows it again. A server
  uses one or the other, not both. Changing the server's address removes the
  saved token unless you enter a new one at the same time. If the server
  rejects the token (during **Discover** or when Janus calls one of its
  tools), the server shows **Token rejected**: save a new token and run
  **Discover** again.
