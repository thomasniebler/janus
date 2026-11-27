# Mail and the action inbox

Janus reads your mailboxes, sorts each new mail into a priority, and pulls
tasks, reminders, and topics out of it. What needs your decision collects in
the action inbox.

## Email accounts

Manage mailboxes in **Settings → Email Accounts** (the same list also sits at
the top of the **Emails** page).

| Button | Mailbox | How Janus reads it |
| --- | --- | --- |
| **Custom IMAP** | Any IMAP server, with a username and a password or app password | IMAP IDLE |
| **Connect Google** / **Add Google mailbox** | Gmail, through your connected Google account (no password) | IMAP IDLE, signed in with Google |
| **Connect Microsoft** / **Add Microsoft mailbox** | Outlook, through your connected Microsoft account | Microsoft Graph, polled every 60 seconds |

The Google and Microsoft buttons first sign you in (**Connect …**) and then add
the mailbox in one click (**Add … mailbox**). They are disabled until an
administrator configured the provider. If your connected account has not granted
mail access yet, the button reads **Connect …** again, and signing in once more
grants it. See [Integrations](integrations.md).

Each account card has buttons to edit, test the connection, and delete. IMAP
passwords and tokens are stored encrypted. Janus starts reading a mailbox as
soon as it is added or switched on, and stops when it is switched off or
deleted; changing its address, server or login reconnects it.

A few things differ for Outlook mailboxes: Janus never marks their mail read on
the server, and a message moved into the inbox later is not picked up.

### First sync

A new mailbox does not import its whole history. The first sync looks at mail
from the last 30 days and takes at most the newest **Initial Sync Email Limit**
messages (default 100). After that, Janus only fetches new mail.

An administrator sets that limit in **Settings → Mail Ingestion**. Other users
see it read-only.

## How new mail is sorted

Every new mail ends up with one of four priorities:

| Priority | Meaning |
| --- | --- |
| **Important** | Needs your attention. On a local install it also shows a desktop notification. |
| **Digest** | Worth knowing; collected for the afternoon digest. |
| **Ignore** | Not worth your attention; kept, but not surfaced. |
| **Spam** | Kept apart from the inbox list. |

Sorting happens in this order:

1. **Your rules.** Janus checks your active mail prioritization rules, in
   order. The first match sets the priority.
2. **The Mail processing trigger.** If no rule filed the mail as Spam or
   Ignore, and your built-in **Mail processing** trigger (or another trigger
   on `mail.received`) will take it, Janus hands the mail over. Until the run
   decides, the mail shows as Digest with the note "Waiting for Janus mail
   processing".
3. **Direct classification.** Otherwise (the trigger is off, or its filter or
   rate limit turned the mail away), Janus classifies the mail itself with the
   LLM and records reminders, topics, and planner suggestions from it. If that
   fails too, the mail falls back to Digest.

The **Mail processing** run sets the priority with its reasoning, and can add
reminders, track topics, and propose planner items. You edit its objective,
tools, payload filter, and rate limit like any other trigger in **Settings →
Triggers**. See [Runtime triggers](runtime-triggers.md).

On the **Emails** page, filter by **all**, **important**, **digest**, or
**spam**, and open a mail to see why Janus sorted it the way it did.

## Mail prioritization rules

Rules live in **Memory → Mail Prioritization Rules** (`/memory?tab=rules`).

Each rule has:

| Field | Values |
| --- | --- |
| **Rule Name** | A label for you. |
| **Field** | **Sender**, **Subject**, or **Snippet** (the start of the body). |
| **Pattern (Regex or Keyword)** | A regular expression or a plain keyword, matched case-insensitively anywhere in the field. |
| **Priority** | **Important**, **Digest**, **Ignore**, or **Spam**. |

Rules run top to bottom; use the arrows to reorder them. Patterns run on RE2, a
linear-time regex engine, so no rule can stall mail processing. Janus rejects a
pattern RE2 cannot compile, such as one with backreferences or lookarounds.
Rules apply only to mail in your own mailboxes.

### Rules from corrections

When you change a mail's priority on the **Emails** page (with the
**Important**, **Digest**, **Ignore**, and **Spam** buttons), Janus saves the
correction as an active rule for similar mail, or updates the matching rule
it already has.

**Clarify misunderstandings**, above the rule list, holds rule suggestions
waiting for you: the mail, the old and new priority, and the suggested rule.
**Approve rule** saves it; **Dismiss** drops it.

You can also ask Janus in chat to draft or change a rule. Changing rules from a
trigger run needs the `rules.write` toolbox, which is Off for unattended runs
by default (see [Tools](tools.md#how-permissions-work)).

## The action inbox

The action inbox is one list of what is waiting for you, for a desktop popup or
a script to show without opening the dashboard.

| Item | Where it comes from | Action from a device | Where to approve |
| --- | --- | --- | --- |
| Conversation Janus started, unread | A trigger run, such as the Secretary | `mark_read` | Open it in Chat |
| Planner suggestion | Mail processing, digest, Secretary | `dismiss` | Dashboard → Planner Suggestions |
| Topic suggestion | Topic tracking | `dismiss` | Memory → Topics |
| Rule suggestion | Clarify misunderstandings | `dismiss` | Memory → Mail Prioritization Rules |
| Automation Janus proposed | `triggers.propose` in chat | none | Settings → Triggers |

A device can only mark a conversation read or dismiss a pending suggestion.
**Approving stays in the web app**: an approval can let an unattended run use a
tool, and a token on a laptop should not be able to grant that. Items marked
`needs_web` (automation proposals) can only be opened.

In the web app, the same items show up where they live: the **New** badge on
Chat, **Planner Suggestions** on the Dashboard, and the proposal count on
**Settings → Triggers**.

### Device tokens

A script authenticates with a device token instead of your password.

1. Open **Settings → Daily Briefing → Desktop and device access**.
2. Give the device a name (for example "Laptop popup") and press
   **Create token**.
3. Copy the token. It starts with `jdt_` and is shown only once.

You can have up to ten active tokens. Press **Revoke** next to a token to stop
it at once. Changing your password or revoking your sessions revokes every
device token too.

A device token opens only the inbox endpoints; everything else answers `401`.
Store it in a file only you can read (`chmod 600`).

### The API

```text
GET  /api/inbox                          -> {"items": [...]}   newest first, at most 50
POST /api/inbox/{kind}/{id}/{action}     -> 204
Authorization: Bearer jdt_...
```

Each item has:

| Field | Content |
| --- | --- |
| `kind` | `conversation`, `planner_suggestion`, `topic_suggestion`, `email_clarification`, or `trigger_proposal` |
| `id` | The item's id within its kind |
| `title`, `summary` | Short plain text (at most 120 and 300 characters) |
| `created_at` | When the item appeared |
| `risk` | `low`, or `needs_web` when no device action exists |
| `actions` | The actions a device may take: `mark_read` or `dismiss` |
| `open_url` | A path inside Janus; append it to your Janus address |

`title` and `summary` come from mail, calendars, and models. Show them as plain
text, never as markup or links.

`GET /api/inbox` returns an `ETag`. Send it back as `If-None-Match`, and an
unchanged inbox answers `304` with no body, so polling once a minute is cheap.
An unknown item, another user's item, or an action that kind does not offer
answers `404`.

For a ready-made desktop popup, see
[Desktop](desktop.md#action-inbox-popups).
