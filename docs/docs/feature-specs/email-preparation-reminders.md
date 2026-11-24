# Email preparation reminders

**Status:** Shipped
**Linear:** none (shipped before Linear tracking); later changes: JAN-113 (topic linkage into briefing/reminders), JAN-232 (reminders persisted by the Mail processing trigger), JAN-296 (owner-scoped reminder memory and planner outlook)
**Depends on:** IMAP ingestion (`imap_service.py`), email processor and Mail processing built-in trigger, planner sync, LLM provider configuration, prompt-injection guardrails


## Problem

Janus currently performs an unbounded historical IMAP sync on first startup, classifies mail without turning it into durable preparation memory, and generates morning briefings from only today's planner data. That makes it expensive to onboard a large mailbox and easy to miss medium-term preparation needs that appear first in email.

## Goals

1. Limit initial historical mail ingestion to the most recent 100 messages per account.
2. Let Janus read recent mail and extract actionable preparation reminders plus concrete event reminders.
3. Persist reminders as active memory so they survive restarts and repeat briefings until they are resolved or obsolete.
4. Surface active reminders as Janus-managed calendar-style entries so planner views and summaries can treat them like scheduled items.
5. Expand morning briefing context to include a 4-6 week preparation horizon and point out upcoming items that need prep.
6. Keep the implementation local-first and safe when LLM connectivity is unavailable.

## Non-goals

1. Writing reminder events back to third-party calendar providers in v1.
2. Full-body email ingestion or retroactive processing of an entire mailbox archive.
3. Automatic reminder dismissal based on external provider mutations that Janus does not yet sync bidirectionally.

## User experience

### Initial mail sync

- For each active IMAP account, Janus ingests at most the most recent 100 messages during the first bounded historical sync.
- After that initial bounded sync, normal incremental sync continues from the persisted highest UID.

### Reminder generation

- When Janus processes a recent email, it may derive:
  - an **event reminder** for something that should happen at a known time, and/or
  - a **preparation reminder** for work that should happen before a known event or deadline.
- If the same reminder already exists, Janus updates the active reminder memory instead of duplicating it. It never updates another user's reminder.
- Reminders belong to the owner of the account their source email arrived on. Extraction context, briefings, and planner context only include the owner's own reminders and the next 6 weeks of events from the owner's own planner accounts (JAN-296).
- Active reminders are mirrored into synthetic calendar entries owned by Janus so they show up in planner summaries.

### Morning briefing

- The morning briefing still summarizes today's schedule and tasks.
- It additionally considers the next 6 weeks of planner events and active reminder memory.
- If Janus sees an upcoming event that likely needs preparation, it explicitly calls that out in the briefing.

## Data model

Add a new `ReminderMemory` table with these fields:

- `id`
- `source_email_id` (nullable FK to `emails.id`)
- `synthetic_calendar_event_id` (nullable FK to `calendar_events.id`)
- `kind` (`event` or `preparation`)
- `status` (`active`, `completed`, `dismissed`, `expired`)
- `title`
- `notes`
- `due_at`
- `prepare_for_at` (nullable)
- `dedupe_key`
- `confidence`
- `last_seen_at`
- `created_at`
- `updated_at`

Extend `CalendarEvent` with Janus-owned metadata:

- `source_type` (`planner` or `janus_reminder`)
- `description` (nullable)

Rules:

1. Provider-synced events remain `source_type = planner`.
2. Janus-created reminder entries use `source_type = janus_reminder`.
3. A reminder stays active until explicitly completed/dismissed or until `due_at` is sufficiently in the past.

## Backend design

### Bounded mail history

- Add `INITIAL_HISTORY_LIMIT = 100` to the IMAP service.
- When `last_uid == 0`, fetch only the newest 100 messages from the server.
- When `last_uid > 0`, keep the current UID-based incremental fetch behavior.

### Reminder extraction pipeline

- Add a dedicated reminder-analysis step after `EmailProcessor.process_email()` persists an email.
- The reminder analyzer receives:
  - the current email subject/sender/snippet,
  - active reminder memory,
  - the next 6 weeks of planner events,
  - the prompt-injection guardrail already used for email prompts.
- The analyzer returns structured JSON describing zero or more reminders:
  - title
  - kind
  - notes
  - due_at
  - prepare_for_at
  - confidence
  - dedupe_key material

Fallback behavior:

- If the LLM is unavailable, Janus logs the failure and keeps normal email ingestion intact.
- Reminder extraction failure must not block email persistence.

### Reminder memory service

Add a new service responsible for:

1. building the 6-week preparation context,
2. extracting reminder candidates from emails,
3. upserting active reminders by `dedupe_key`,
4. mirroring each active reminder into a synthetic `CalendarEvent`,
5. expiring stale reminders,
6. returning active reminder summaries for briefings and chat.

### Planner summary changes

- `PlannerService.get_todays_summary()` should become a broader planner summary that includes:
  - today's calendar,
  - active tasks,
  - active reminder entries due soon,
  - a preparation outlook covering the next 6 weeks.
- `PlannerService.get_context_data()` should include:
  - `calendar_today`
  - `calendar_upcoming`
  - `tasks`
  - `active_reminders`
  - `preparation_outlook`

## Briefing design

Update briefing prompts so Janus:

1. summarizes today's schedule,
2. highlights active preparation reminders,
3. scans the next 6 weeks for significant upcoming events,
4. points out items that probably need preparation now.

This applies to:

- CLI `janus brief`
- `/ws/briefing`
- `/ws/chat` context construction

## Active memory behavior

- Existing reminders are preserved in `active` state across runs.
- Repeated supporting emails refresh `last_seen_at` and notes instead of creating duplicates.
- Reminder memory is considered active if:
  - status is `active`, and
  - `due_at` is not sufficiently stale.
- Default expiration rule:
  - event reminders expire 24 hours after `due_at`
  - preparation reminders expire 72 hours after `prepare_for_at` or `due_at`

## APIs and surfaces

No new frontend surface is required for v1. Existing planner/dashboard consumers may see synthetic Janus reminder entries through calendar-event queries once the schema is extended.

## Testing

Add regression coverage for:

1. initial IMAP sync only ingesting the most recent 100 messages,
2. reminder extraction/upsert preserving a single active reminder across repeated emails,
3. synthetic calendar entry mirroring for active reminders,
4. briefing/planner context including active reminders and 6-week outlook,
5. migration behavior for existing local databases.

## Risks and mitigations

### LLM over-generation

- Mitigation: require structured JSON output, confidence scoring, dedupe keys, and non-blocking failure handling.

### Reminder spam

- Mitigation: dedupe by normalized title/time/source and only keep active reminders within the configured horizon.

### Planner/provider confusion

- Mitigation: mark Janus-owned entries with `source_type = janus_reminder` and a clear title prefix such as `[Prep]`.

## Review checklist

1. The initial sync cap is explicitly bounded at 100 messages.
2. Reminder memory is persistent, deduplicated, and not tied to process memory.
3. Morning briefing uses both active reminders and a 6-week planner horizon.
4. The v1 scope is honest about being Janus-managed local reminder entries, not provider write-back.
5. Failure of reminder extraction cannot block mail ingestion or startup.
