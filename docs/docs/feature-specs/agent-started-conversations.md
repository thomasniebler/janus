# Janus-started conversations

**Status:** Shipped
**Linear:** JAN-319
**Depends on:** Trigger runtime and Janus follow-ups (JAN-257), unattended chain guard (JAN-274), tool permissions / Unattended switch (JAN-255), prompt-injection guardrails (JAN-127), live UI refresh over `/ws/changes` (JAN-104), conversation history (JAN-137)

## Problem

Janus could only answer: every chat conversation was opened by the user.
When a trigger run or a follow-up Janus scheduled for itself found something
the user should read and probably reply to ("your flight moved; should I
update the calendar?"), the only place to put it was the run's result on
Settings → Triggers, which nobody watches.

## Behavior

- A trigger run (an ad-hoc trigger, a Janus follow-up, or a saved trigger
  that lists the tool) calls `start_conversation(title, message)`.
- Janus creates a **new** conversation for the run's owner whose first
  message is Janus's. It appears in the owner's conversation list (the Chat
  page and the sidebar chat) marked **Started by Janus**, and **New** until
  the owner opens it. The Chat navigation entry shows the number of new ones
  in every UI shell. Open tabs refresh at once over `/ws/changes`; a local
  (non-server) install also shows a desktop notification.
- The user answers in that conversation like in any other; chat rules apply
  from then on.

## Design decisions

The issue had no description. These are the decisions this design makes:

1. **A tool, not a run-result flag.** It is a new registry entry
   (`conversations.start`), so the model decides when something is worth a
   conversation, and the user controls it with the existing per-tool
   Unattended switch. No new settings.
2. **Unattended only.** Enabled only in the `trigger` and `automation` task
   modes. Chat already is a conversation; delegated children are read-only.
3. **`confirmation="recommended"`, default Allowed.** `required` would make it
   default-off and unusable by Janus follow-ups (they have no tool list to
   override the switch). It writes nothing outside the owner's own Janus, so
   it sits with `memory` and `triggers` rather than `mail.send`.
4. **Owner-scoped by construction.** The owner is the run's user, and the
   tool takes no conversation id: it can only create a new conversation for
   that owner.
5. **Rate limits in code:** one conversation per run and five per owner per
   rolling day (shared by every run of a follow-up chain); title up to 120
   characters, message up to 4000.
6. **No exfiltration channel:** no URLs, links to other sites, or Markdown
   images in the title or message (an image would load on open), checked on
   the text with entities and backslash escapes resolved and on every link
   destination a CommonMark parser finds (only app paths and anchors pass). Blocked
   after a suspected prompt injection in the same run.
7. **Unread state on the conversation**, cleared when the owner opens it via
   `GET /api/conversations/{id}`, instead of a separate notifications
   table.

## Out of scope

- Writing into an existing conversation, or Janus starting a conversation
  from chat or from a delegated task.
- Push notifications to the iOS app or e-mail; the iOS client lists the
  conversation but shows no marker yet.
- Rich content (UI blocks, images) in the opening message.

See [Background workers and runtime](../tech-spec/background-workers-runtime.md#janus-started-conversations-jan-319)
for the technical contract, and [Janus's tools](../tools.md#start-conversations)
and [Runtime triggers](../runtime-triggers.md) for the user-facing docs.
