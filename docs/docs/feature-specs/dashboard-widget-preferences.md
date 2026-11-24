# Dashboard widget preferences

**Status:** Shipped
**Linear:** JAN-95 (configurable widgets and collapse); sub-issues JAN-96 (persisted preferences), JAN-97 (config-driven dashboard), JAN-98/JAN-99/JAN-100 (briefing prompt override), JAN-103 (dashboard chat tools)
**Depends on:** Data model & migrations tech spec (migrations `2d4e6f8a9b0c`, `4b7f1c2d3e4a`), Morning briefing built-in trigger, structured topic tracking (Topic Focus widget), WhatsApp connector (WhatsApp widget)


## Feature definition

This feature adds per-user, per-widget configuration to the Janus dashboard.

It covers:

1. persisting each user's show/hide preference for every dashboard widget,
2. persisting each user's collapse/expand preference for every dashboard widget,
3. a widget configuration menu on the dashboard for managing those preferences,
4. per-widget custom prompt overrides that steer the Janus briefing wording,
5. AI chat tools that expose the same preferences to Janus agents,
6. dedicated widget data endpoints for important emails, WhatsApp messages, schedule, tasks, and topics,
7. structured widget-scoped error payloads so the frontend can isolate failures to one card.

## User value

- The user can hide widgets they do not need without losing the underlying data.
- The user can collapse noisy widgets to reclaim vertical space while keeping them reachable.
- The user can add personal briefing instructions to the Briefing widget without touching global configuration.
- Preferences are stored server-side, so they follow the user across browsers and devices.

## In scope

- A `dashboard_widget_preferences` database table (Alembic migration `2d4e6f8a9b0c`).
- A follow-up migration (`4b7f1c2d3e4a`) that adds the `prompt_override` column to that table.
- `DashboardService` methods: `list_widget_preferences`, `update_widget_preferences`, `get_widget_prompt_override`.
- `GET /api/dashboard/widgets` and `PATCH /api/dashboard/widgets` REST endpoints.
- `GET /api/dashboard/`, `GET /api/dashboard/important-emails`, `GET /api/dashboard/whatsapp-messages`, `GET /api/dashboard/events`, `GET /api/dashboard/tasks`, and `GET /api/dashboard/topics`.
- Frontend widget configuration menu (⋮ button on the dashboard header).
- Controlled `CollapsibleCard` component driven by server-persisted `is_collapsed` state.
- Briefing prompt override editor modal (accessible from the widget menu).
- AI chat tools: `list_dashboard_widgets`, `update_dashboard_widget`, `set_dashboard_widget_prompt`, `clear_dashboard_widget_prompt`.

## Out of scope

- Custom per-user widget ordering.
- Widget resizing beyond the existing two-column grid.
- Creating or removing widgets from outside the fixed shipped set.

## Widgets

Six widgets ship in the default set; all are visible and expanded by default.

| Widget key | Display name | Prompt override supported |
|---|---|---|
| `briefing` | Briefing | Yes |
| `topic_focus` | Topic Focus | No |
| `important_emails` | Important Emails | No |
| `whatsapp_messages` | WhatsApp | No |
| `schedule` | Schedule | No |
| `tasks` | Active Tasks | No |

## Data model

`dashboard_widget_preferences` stores one row per `(user_id, widget_key)` pair. Missing rows are treated as the shipped defaults (visible, not collapsed, no prompt override) and are never written until the user first changes a preference.

| Column | Type | Description |
|---|---|---|
| `user_id` | integer (FK → `users.id`) | Owning user |
| `widget_key` | enum | One of the six widget keys listed above |
| `is_visible` | boolean | `true` by default — hides the widget from the grid when `false` |
| `is_collapsed` | boolean | `false` by default — collapses the card body when `true` |
| `prompt_override` | text, nullable | Extra briefing instructions appended after the system prompt; `NULL` means use the default |
| `created_at` / `updated_at` | datetime | Audit timestamps |

A unique constraint on `(user_id, widget_key)` prevents duplicate rows.

## API endpoints

### `GET /api/dashboard/widgets`

Returns the effective widget preferences for the authenticated user. Widgets without a persisted row are included with their default values.

**Response** — `200 OK`:

```json
[
  { "widget_key": "briefing", "is_visible": true, "is_collapsed": false, "prompt_override": null },
  { "widget_key": "topic_focus", "is_visible": true, "is_collapsed": false, "prompt_override": null },
  { "widget_key": "important_emails", "is_visible": true, "is_collapsed": false, "prompt_override": null },
  { "widget_key": "whatsapp_messages", "is_visible": true, "is_collapsed": false, "prompt_override": null },
  { "widget_key": "schedule", "is_visible": false, "is_collapsed": false, "prompt_override": null },
  { "widget_key": "tasks", "is_visible": true, "is_collapsed": true, "prompt_override": null }
]
```

### `PATCH /api/dashboard/widgets`

Applies partial updates to one or more widgets and returns the full effective set. Only fields present in the request body are written.

**Request body**:

```json
{
  "widgets": [
    { "widget_key": "schedule", "is_visible": true },
    { "widget_key": "tasks", "is_collapsed": false }
  ]
}
```

**Response** — `200 OK`: same shape as the GET response.

### Widget data endpoints

The dashboard exposes dedicated data endpoints for the four non-briefing widgets so the frontend can refresh or retry one card without reloading the entire dashboard.

| Endpoint | Purpose | Success response |
|---|---|---|
| `GET /api/dashboard/important-emails` | Recent important email payloads for the Important Emails widget | `200 OK` with `EmailRecord[]` |
| `GET /api/dashboard/events` | Today's planner-backed calendar events for the authenticated user's planner accounts | `200 OK` with `CalendarEvent[]` |
| `GET /api/dashboard/tasks` | Active planner tasks: the user's local parent/subtask tree (subtasks nested under `children`, a done parent kept only as the grouping row of its open subtasks), followed by read-only imported tasks as flat roots (JAN-317) | `200 OK` with `PlannerTaskTreeItem[]` |
| `GET /api/dashboard/topics` | Active tracked-topic summaries for the Topic Focus widget | `200 OK` with `TopicSummary[]` |

The aggregate `GET /api/dashboard/` response continues to return the legacy dashboard payload (`important_emails`, `all_emails`, `events`, `tasks`, `topics`, `now`) for the authenticated user, but it now resolves widget failures during the primary build path instead of replaying healthy widget loaders after an exception.

### Widget-scoped failure payloads

Any widget loader failure — both on the dedicated widget endpoints above and while building `GET /api/dashboard/` — returns a structured `500 Internal Server Error` payload that names the failed widget.

**Response** — `500 Internal Server Error`:

```json
{
  "detail": {
    "code": "dashboard_widget_load_failed",
    "widget": "tasks",
    "message": "Failed to load the tasks widget.",
    "hint": "Check planner task records and planner account connectivity.",
    "cause": "planner backend is unavailable"
  }
}
```

Field meanings:

- `code` — stable machine-readable error code for widget load failures.
- `widget` — the widget key whose loader failed (`important_emails`, `schedule`, `tasks`, or `topic_focus`).
- `message` — short user-facing summary suitable for inline UI display.
- `hint` — operator/developer guidance about the likely failing subsystem.
- `cause` — the underlying exception message captured by the API.

Healthy widgets are not replayed to discover the failing card; once one widget loader fails, the aggregate request returns the first widget-scoped failure immediately so the original error context is preserved.

## Frontend behavior

The dashboard header exposes a ⋮ (MoreVertical) button that opens the widget configuration panel. The panel lists all six widgets with a checkbox for toggling visibility; the Briefing widget additionally shows an "Edit instructions" button that opens the prompt override modal.

Each non-briefing widget loads through its own frontend query and backend endpoint. If one widget request fails, that card shows an inline error message while the rest of the dashboard keeps rendering and refreshing normally.

Changes are applied via optimistic mutation: the UI updates immediately while the PATCH request is in flight. If the request fails, the previous preferences are restored.

Widgets with `is_visible = false` are removed from the grid entirely. If all widgets are hidden, the grid is replaced with a placeholder card that prompts the user to re-enable widgets from the menu.

Each widget card wraps a `CollapsibleCard` component driven by `is_collapsed`. Clicking the card header toggles collapse state and persists the change to the server.

### Topic Focus widget

The Topic Focus widget is a read-only dashboard view over the shared tracked-topic summary already used by planner and briefing flows.

- It surfaces the most relevant active topics, with pinned topics sorted first.
- Each topic row shows status, confidence, summary, evidence count, and last-seen timestamp.
- Clicking a topic deep-links to **Memory & Knowledge -> Topics** with that topic selected.
- The widget action routes to the broader **Memory & Knowledge -> Topics** management surface.

## Briefing prompt override

When a `prompt_override` is stored for the `briefing` widget, the LLM service appends it to the system prompt before generating the briefing. The default briefing structure and planner summary are always included — the override adds wording or emphasis instructions on top.

Blank overrides (empty string, whitespace-only) are normalized to `NULL` so the default prompt is restored cleanly.

The override is editable in the widget menu → "Edit instructions" modal. Setting the field to blank and saving resets it to the default prompt. The widget menu shows "Custom instructions" or "Default prompt" depending on whether an override is stored.

## AI chat tools

The `dashboard` toolbox exposes five tools to Janus in chat:

| Tool | Description |
|---|---|
| `get_daily_briefing` | Returns today's planner summary and generated briefing, respecting the stored prompt override. Accepts `force_refresh` to bypass the cache. |
| `list_dashboard_widgets` | Lists the current user's widget preferences. |
| `update_dashboard_widget` | Updates `is_visible` or `is_collapsed` for one widget. |
| `set_dashboard_widget_prompt` | Stores a custom prompt override for a widget that supports overrides (currently `briefing` only). |
| `clear_dashboard_widget_prompt` | Clears any stored prompt override for a supported widget. |

`set_dashboard_widget_prompt` and `clear_dashboard_widget_prompt` reject widget keys that do not support prompt overrides.

## Implementation notes

- Preferences are isolated per user: querying or mutating always filters by `user_id`.
- `DashboardService` is a thin service in `src/janus/services/dashboard_service.py` that extends `BaseService`.
- The shipped default set is defined as a module-level tuple so defaults never depend on database state.
