# Action inbox

**Status:** Shipped (phase 1; everything under "Out of scope" and the sources marked *later* are planned, not built)
**Linear:** not yet filed (create the issue and link it here before implementation starts)
**Depends on:** [Proactive secretary](proactive-secretary.md), Janus-started conversations ([JAN-319](agent-started-conversations.md)), live UI refresh over `/ws/changes` (JAN-104), the existing suggestion/approval endpoints listed below, the iOS scaffold ([`mobile-ios.md`](../tech-spec/mobile-ios.md))

## Problem

The Secretary can now decide on its own that something deserves the owner's
attention, but it can only deliver it as a conversation (Dashboard badge, a
`notify-send` popup on a local install) or one WhatsApp line. The owner wants
the interrupt where they already are: a small desktop popup (an `eww` widget or
a notification) with the finding and a few **action buttons**, and later the
phone. Today:

- things waiting for the owner are spread over seven endpoints and screens
  (below), so a popup has no single list to render;
- the only desktop endpoint is `GET /api/desktop/summary` (two counters), and
  a script has no way to authenticate (login is cookie plus CSRF for browsers,
  JWT for the iOS chat socket);
- the iOS client lists a Janus-started conversation but shows no marker and
  gets no push.

## What is waiting for the owner today

| Item | Where it comes from | Existing actions |
| --- | --- | --- |
| Janus-started conversation, unread | `start_conversation` (JAN-319) | open (`GET /api/conversations/{id}` clears unread) |
| Planner suggestion | Mail processing, `suggest_planner_item` | `POST /api/planner/suggestions/{id}/approve` and `/dismiss` |
| Topic suggestion | topic tracking | `POST /api/topics/suggestions/{id}/approve` and `/dismiss` |
| Email clarification | Mail processing | `POST /api/emails/clarifications/{id}/approve` and `/dismiss` |
| Trigger definition proposal | `triggers.propose` (JAN-235) | `POST /api/runtime/definitions/{id}/approve` and `/reject` |
| Character state proposal | `character.propose` (JAN-301) | `POST /api/character/state/proposals/{id}/approve` and `/reject` |
| Wiki revision proposal | wiki synthesis | `POST /api/wiki/revisions/{id}/accept` and `/dismiss` |

## What phase 1 built

Decisions made when implementing, which settle the open questions below:

- **Five sources**: unread Janus-started conversations, planner suggestions,
  topic suggestions, email clarifications, and proposed trigger definitions.
  Character state proposals and wiki revisions are *later*.
- **Only awaiting-action items**, not Secretary run results that did not become a
  conversation.
- **A device may only `mark_read` and `dismiss`.** The first design also allowed
  approving planner and topic suggestions and clarifications from a popup; that was
  withdrawn, because approving without seeing what it creates is too eager for a
  surface that shows 300 characters. Approving is "Review" in the web app for every
  kind.
- **Open items only**: `dismiss` is refused for a suggestion or clarification that is no longer pending.
- **Account recovery**: a password change or revoked sessions revoke every device token, and deleting a user deletes theirs.
- **Rate limits and metadata**: ten active tokens per owner, `last_used_at` written
  at most once a minute, no per-token rate limit.
- **Where**: tokens are managed on Settings → Daily Briefing → Desktop and device
  access; the popup example and the API contract are in the user guide
  ([Action inbox](../inbox.md)).

## Behavior

### One list: `GET /api/inbox`

An owner-scoped, read-only list of everything above that is still open, newest
first, each item with a stable `kind`, `id`, `title`, a short plain-text
`summary`, `created_at`, a `risk` (`low` or `needs_web`), the `actions` the
caller may take, and an `open_url` (an app path, never an external URL). The
list reuses the existing services and filters; it adds no new source of truth.
The response carries an `ETag`, so a popup can poll every 30 to 60 seconds with
`If-None-Match` and pay almost nothing. `/ws/changes` stays the live channel for
the web app (it already carries domain hints); the inbox needs no new socket.

### Actions: `POST /api/inbox/{kind}/{id}/{action}`

A thin dispatcher over the existing approve/dismiss/open endpoints, so the
rules each of them enforces stay in one place. The set of actions per kind is
fixed in code; an unknown kind or action is a 404, a foreign id looks like a
missing one.

### Which actions a device token may take

The key safety decision. A popup is a convenient surface, but some approvals
are not low risk:

- Approving a **trigger definition proposal** writes the approval fingerprint
  that lets an unattended run use the tools it names (JAN-235). Approving a
  **character state proposal** changes how Janus behaves. Both stay
  **web-session only**: the popup's button is "Review", which deep-links to the
  dashboard (`risk = needs_web`).
- Everything else is `low`, and a device token may only open it, mark it read, or
  dismiss it. Approving a planner or topic suggestion or a clarification is also
  a "Review" in the web app (see "What phase 1 built").

### Device tokens for scripts

A script cannot use a browser session. A **device token** is created by the
owner in Settings, shown once, stored only as a hash, scoped to the inbox
endpoints (it is refused everywhere else), revocable, and shows its last use.
It is sent as `Authorization: Bearer ...`. It cannot create other tokens,
change settings, or reach chat, mail, or the tool endpoints. One token per
device, so a lost laptop is one revocation.

### Desktop popups

Janus ships a small, documented **example** (a shell script and an `eww`
widget definition under `docs/`, not part of the backend) that polls
`/api/inbox` with a device token, shows each new item, and wires the buttons to
the action endpoint. `notify-send` with actions covers the plain-notification
case. The existing server-side `notify_conversation` popup stays for a local
install; the script is for a desktop that does not run the server. Nothing in
the backend knows about `eww`.

### Phone

- **WhatsApp** already works for time-critical items (Secretary).
- **iOS:** the app lists the inbox (the same `/api/inbox`, using the existing
  JWT) and marks Janus-started conversations as new. **Push** needs an Apple
  developer account, an APNs key on the server, and device-token registration;
  these are external prerequisites, and the app is not installed yet, so push is
  a separate follow-up and not part of the first implementation.

## Design decisions

1. **An aggregate view, not a new table.** Each item stays owned by its
   existing model, so approving in the dashboard and in a popup cannot disagree.
2. **Reuse approve/dismiss, do not reimplement.** The dispatcher calls the
   existing services; their ownership checks, fingerprint rules, and limits are
   not copied.
3. **Polling with an ETag before push.** It works from any shell, needs no
   server-to-client channel, and is cheap. Push is added where the platform
   requires it (APNs).
4. **Risky approvals stay on the web.** The popup can start a review, never
   grant a tool.
5. **Scoped, revocable, hashed tokens** instead of reusing the user's password
   or a full JWT in a script.

## Still open

- Whether the inbox should also list recent Secretary findings that did not become
  a conversation (the run result).
- A per-token rate limit, if polling ever becomes a problem.

## Out of scope

- APNs push and any iOS UI change in the first implementation (planned
  separately, needs the prerequisites above).
- Writing into an existing conversation from a popup (a quick reply). It is a
  natural next step once the inbox exists, but needs its own safety review: it
  would let a token start a chat turn.
- Windows or macOS notification helpers.

See [Proactive secretary](proactive-secretary.md) for the producer side and
[Background workers and runtime](../tech-spec/background-workers-runtime.md) for
the trigger runtime. The tech-spec child docs for the endpoints, the token model
and the schema change are updated in the implementation PR, spec-first.
