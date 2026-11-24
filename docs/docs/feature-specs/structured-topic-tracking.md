# Structured topic tracking

**Status:** Shipped
**Linear:** JAN-173
**Depends on:** Background workers runtime tech spec (email ingestion pipeline), data model & migrations tech spec, LLM provider configuration

## What it adds

Structured topic tracking lets Janus turn repeated email threads into durable
topics instead of treating every message in isolation.

Each tracked topic represents an ongoing thread such as:

- a hiring loop,
- an incident response,
- a travel plan,
- a project milestone,
- a recurring customer escalation.

Janus extracts these topics while processing incoming mail, stores them in the
database, and makes them available to the dashboard, planner context, and
topic-enabled agent tools.

## User value

- Repeated workstreams stay visible even when the original emails scroll away.
- Janus can carry durable context from inbox activity into briefings and chat.
- Users can pin, resolve, snooze, and review topics in one place.
- Topic memory is persistent and survives restarts.

## Where it appears

### Dashboard and memory UI

- The main dashboard now includes a **Topic Focus** widget that surfaces the
  most relevant active topics inside the daily dashboard flow.
- Important-email surfaces now render topic badges so users can jump directly
  from an email back into the tracked thread it belongs to.
- The **Memory & Knowledge** page now includes a **Topics** tab.
- The Topics view lists tracked topics with their status, confidence, summary,
  evidence count, pin state, and a selected-topic drill-down with recent
  supporting evidence.
- Users can update topic state from the UI without editing raw data.

### Planner and briefing context

- Active topics are included in planner context assembly.
- Briefing and chat flows can reuse those active topic summaries as additional
  context when reasoning about current work.
- Briefings should foreground durable topic threads rather than only repeating
  per-message summaries.
- Topic-aware digests should group linked digest mail under those structured
  threads, with a separate catch-all bucket for ungrouped updates.

### Agent tools

The Janus agent always has topic-specific tools (ungated since JAN-201):

- `list_topics`
- `search_topics`
- `get_topic_detail`
- `update_topic_state`

## Data model

This feature adds four main persistence tables:

| Table | Purpose |
| --- | --- |
| `tracked_topics` | The canonical topic record for one user and one normalized topic slug |
| `topic_evidence` | Supporting evidence rows, usually tied to processed emails |
| `topic_suggestions` | Reviewable suggestions such as pin/dismiss actions |
| `topic_tracking_preferences` | Per-user extraction and tracking preferences |

It also adds topic-related settings defaults. (It originally added a
`topic_access` flag on agent definitions too; that flag stopped gating topic
tools in JAN-201 and was dropped with the `agent_definitions` table in
JAN-227.)

## Extraction flow

1. Janus persists an incoming email as usual.
2. After reminder capture, Janus asks the topic service to analyze the email.
3. Topic extraction is skipped for `SPAM` and `IGNORE` mail.
4. The LLM returns zero or more structured topic candidates.
5. Janus deduplicates by normalized slug per user, updates the tracked topic,
   and records email evidence.
6. A newly discovered auto topic is stored as a `candidate`, not as `active`
   (see [Corroboration before a topic goes active](#corroboration-before-a-topic-goes-active)).
7. Extraction failures are logged but do not block email ingestion.

## Topic lifecycle

Topics currently support these main lifecycle states:

- `candidate`
- `active`
- `ignored`
- `resolved`
- `snoozed`

Users can also pin a topic or set a snooze-until timestamp to reduce noise while
keeping the topic available for later review.

## Corroboration before a topic goes active

A single email is weak evidence that something is an ongoing thread. If every
first mention became an active topic, briefings, digests, chat context, and the
Topic Focus widget would fill up with one-off mail.

Auto-discovered topics therefore start in the `candidate` state and must be
corroborated by further mail before they graduate:

1. The first email that proposes a new slug creates a `candidate` topic with
   origin `auto` and attaches that email as evidence.
2. Every later email whose extracted dedupe hint normalizes to the same slug
   refreshes the topic (title, summary, confidence, last-seen) and attaches its
   own evidence row.
3. Once the topic has evidence from at least `min_confirmation_mentions`
   distinct emails, it is promoted to the candidate's status hint, or to
   `active` when the model gave no usable hint.
4. Promotion is one-way in this pipeline: an already promoted auto topic keeps
   the existing refresh behavior, and later mail never demotes it back to
   `candidate`.

Because promotion depends on repeated mail landing on the *same* slug, both the
extraction context and the prompt are built around slug stability: the context
passed to the model lists the user's active topics *and* the topics still being
watched, each with the dedupe hint (the stored slug) the model should reuse, and
the prompt tells it to keep hints stable and to leave genuinely one-off or
transactional mail alone rather than proposing a topic that can never be
corroborated.

Manual curation short-circuits the wait:

- Topics created through the API, the UI, or the `create_topic` agent tool are
  manual (`origin = manual`), user-confirmed, and `active` immediately.
- Editing a `candidate` topic's title, summary, marker keywords, or pin state
  counts as explicit confirmation and promotes it to `active` right away, the
  same way confirming an existing auto topic through `create_topic` does.
- Setting a status explicitly (including snoozing, resolving, or ignoring a
  candidate) is always honored as given.

Where candidate topics do and do not appear:

| Surface | Candidate topics |
| --- | --- |
| Briefing / chat context (`build_context_topic_summary`) | Excluded |
| Dashboard Topic Focus widget (`build_active_topic_summary`) | Excluded |
| Topic-aware digest grouping | Excluded |
| Agent `search_topics` default, `list_topics` with `status=active` | Excluded |
| Topic badges on email surfaces | Shown, labelled `watching` |
| Topics tab in **Memory & Knowledge** (`GET /api/topics` without a filter) | Shown, with a `Watching` status filter |

Badges and the topics list deliberately keep candidates visible: users should be
able to see what Janus is watching, and confirm or ignore it, before it starts
consuming briefing space.

## API surface

Authenticated topic management is exposed through these endpoints:

- `GET /api/topics`
- `GET /api/topics/{topic_id}`
- `PATCH /api/topics/{topic_id}`
- `GET /api/topics/suggestions`
- `POST /api/topics/suggestions/{topic_id}/approve`
- `POST /api/topics/suggestions/{topic_id}/dismiss`

## Configuration and defaults

Topic tracking ships with configurable defaults in Janus settings, including:

- whether topic tracking is available at all,
- whether it is enabled by default,
- which mail priorities are in extraction scope,
- minimum confidence threshold,
- how many distinct emails must mention a topic before it is promoted out of
  `candidate` (`topic_tracking_default_min_confirmation_mentions`, default 2,
  accepted range 1-10; `1` restores the previous "first mention wins" behavior),
- active/context topic limits,
- whether review is required before certain actions are applied.

These defaults are applied centrally and can be refined per user through topic
tracking preferences, which carry the matching `min_confirmation_mentions`
column.

## Current implementation notes

- Topic extraction uses the same untrusted-email guardrail pattern as other
  email-derived LLM flows.
- The Topic Focus widget is intentionally read-only in v1, but each topic row
  now deep-links into **Memory & Knowledge -> Topics** with that specific topic
  selected for drill-down.
- Briefing context now prefers active topics that have not been surfaced
  recently, and digest generation reuses linked topic badges to keep durable
  workstreams visible across non-urgent mail.
- Background ingestion currently resolves the topic owner to the first user in
  the database when no explicit user context is available. This matches the
  current single-user-oriented background-processing model and should be revisited
  if Janus grows into full multi-user mail ownership.

## Testing coverage

Regression coverage for this feature includes:

- topic extraction failure staying non-blocking for email processing,
- repeated related emails refreshing one tracked topic instead of duplicating it,
- a single email creating a `candidate` topic rather than an active one,
- repeated mentions promoting a candidate once the confirmation threshold is met,
- candidate topics staying out of briefing, chat, dashboard, and digest context,
- manual creation and manual confirmation still yielding active topics at once,
- the confirmation-threshold default flowing from settings into per-user
  preferences, and the migration adding both columns,
- authenticated topic API list/detail/update/suggestion behavior,
- topic tool gating and update behavior,
- dashboard rendering of the Topic Focus widget with tracked-topic payloads,
- topic badge drill-down from email/dashboard surfaces into the Topics view,
- frontend rendering of the Topics tab in **Memory & Knowledge**.
