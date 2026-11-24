# Todo Chat design

## Recommendation

Build `todo_chat` as a focused companion UI for Janus. Janus remains the sole source of truth for tasks, conversations, authentication, and LLM execution. The companion must not keep a second task database or synchronize two independently editable copies of a task.

The first implementation can be a small React/Vite client in this repository that talks to a Janus instance over its existing authenticated HTTP and WebSocket interfaces. Janus remains a background process and its existing persistent SQLite database remains the durable store.

The user experience is a two-pane task workspace:

- a tree of open and completed tasks, grouped by parent;
- a chat pane backed by Janus, with task mutations performed through explicit tools.

The UI should make every LLM mutation visible as a normal task change, with a short confirmation in the conversation and an undo affordance where practical.

## Janus integration boundary

Janus already provides most of the foundation:

- persistent database and Alembic migrations in `../../alembic/`;
- Janus-owned writable tasks through `../../src/janus/services/local_planner_service.py` and `/api/planner/local/tasks`;
- imported read-only planner context through `/api/planner/tasks`;
- authenticated sessions and CSRF handling in `../../src/janus/security.py`;
- persisted conversations through `/api/conversations`;
- streaming agent chat through `/ws/chat`;
- tool assembly in `../../src/janus/agent_tools.py`.

The Janus checkout currently has local, uncommitted changes. Any implementation must preserve those changes and work in a dedicated branch or otherwise keep the task changes isolated.

There are two useful task namespaces in Janus. Local `PlannerTask` rows are the right home for personal todos. Linear issues, exposed through Janus's `linear_access` tools, represent engineering work and delegation. The todo app should not turn every personal reminder into a Linear issue; a later, explicit “promote to Linear” action can bridge the two when requested.

## Data model

Extend the existing Janus-owned `PlannerTask` instead of introducing a `Todo` table:

```text
PlannerTask
  id                integer primary key
  user_id           foreign key users.id
  parent_task_id    nullable foreign key planner_tasks.id
  title             required string
  description       nullable text
  status            Todo | In Progress | Done
  urgency           low | normal | high | critical
  due_at            nullable datetime (UTC)
  scheduled_for     nullable datetime (UTC)
  completed_at      nullable datetime (UTC)
  sort_order        nullable integer for sibling ordering
  source            manual | email_agent | digest_agent | imported
  created_at        datetime
  updated_at        datetime
```

`priority` already exists in Janus planner records, but it is a loose string used by existing planner suggestions and email-derived context. Keep it backward-compatible and add a typed `urgency` field for this app instead of silently changing the meaning of `priority`.

Rules:

1. `parent_task_id` must refer to a local task owned by the same user.
2. A task cannot be its own ancestor; the service must reject cycles.
3. Completing a parent does not implicitly complete children. The UI may show “completed with open subtasks” and offer an explicit cascade action.
4. Deleting tasks should be deferred from the MVP. If needed later, use an archive/soft-delete state or require an explicit child policy.
5. Urgency is user-editable; overdue state is derived from `due_at` and current time, never stored as a second mutable flag.

Suggested ordering for the default view:

1. open tasks before completed tasks;
2. overdue before due soon;
3. higher urgency before lower urgency;
4. explicit sibling `sort_order` / manual order;
5. due date and last update as stable tie-breakers.

## API additions to Janus

Keep the existing endpoints working for the dashboard and older clients. Extend their response shape with `urgency`, `parent_task_id`, and child summary fields, and add focused endpoints only where tree operations need stronger semantics:

```text
GET   /api/planner/local/tasks?include_completed=&root_only=
POST  /api/planner/local/tasks
PATCH /api/planner/local/tasks/{task_id}
POST  /api/planner/local/tasks/{task_id}/complete
POST  /api/planner/local/tasks/{task_id}/move
GET   /api/planner/local/tasks/tree
```

The normal create/update payloads should accept `parent_task_id`, `urgency`, and `sort_order`. The move endpoint should validate ownership and cycles in one transaction. A tree endpoint is preferable to requiring the client to reconstruct a large hierarchy from an arbitrary flat list, while the flat list remains useful for search and incremental refresh.

Domain behavior belongs in `LocalPlannerService`; routers in `../../src/janus/api/planner.py` should remain thin. Add Pydantic request/response types in `../../src/janus/schemas/planner.py`, an Alembic migration, service tests, and API tests.

## LLM chat contract

Reuse Janus's existing conversation and WebSocket lifecycle. The todo client should send:

```json
{
  "text": "Break the launch plan into three subtasks and make the first one critical",
  "page_context": "todo tree"
}
```

Add a dedicated Janus agent capability such as `planner_task_write_access`. Read and write tools should be separate so a read-only agent cannot mutate tasks accidentally. The write-enabled todo agent needs tools with narrow, typed arguments:

- `list_local_tasks` with status, urgency, due-window, and parent filters;
- `get_local_task`;
- `create_local_task`;
- `update_local_task`;
- `complete_local_task`;
- `move_local_task` / `reparent_local_task`;
- `reorder_local_task`.

Tool implementations should call `LocalPlannerService` and use the same user-scoped authorization as HTTP routes. They must not expose raw SQLAlchemy objects or accept arbitrary database IDs without ownership checks. Mutations should run in short-lived sessions, matching the existing `run_isolated_tool_mutation` pattern.

The assistant should be instructed to:

- ask one concise clarification when a requested due date or parent is genuinely ambiguous;
- never claim a task was changed until the tool result confirms it;
- preserve existing title/description/urgency unless the user asks to change them;
- summarize all mutations, including created IDs and parent relationships;
- treat imported Jira/Google/other planner records as read-only unless a future provider-specific write path is explicitly added.

## Persistence and background behavior

There is no need for a new background process in the MVP. Janus already runs as the durable local service and stores its SQLite database in its configured XDG data directory. The todo client should tolerate Janus restarts and reconnect its WebSocket with the active conversation ID.

Later, add a Janus worker only for derived behavior such as:

- reminders for due/overdue tasks;
- periodic rollups or stale-task suggestions;
- reconciliation with external writable providers.

Those jobs should enqueue durable events or notifications and must not compete with the task database as another source of truth.

## Client structure

The Janus module is organized as:

```text
modules/todo-chat/
  frontend/
    src/
      api/              typed Janus HTTP client
      chat/             WebSocket lifecycle and streamed messages
      tasks/            tree query, mutations, ordering, optimistic UI
      components/       TaskTree, TaskEditor, ChatPanel, UrgencyBadge
  README.md
  package.json
```

Use TanStack Query or an equivalent server-state layer. The client should optimistically update simple status/urgency edits, but invalidate or refetch after LLM mutations because one response may create, move, and complete several tasks.

For deployment, the cleanest option is to serve the built companion UI from the same Janus origin once the feature stabilizes. During development, run Vite separately and configure the Janus allowed origin; use Janus's cookie session and CSRF behavior rather than inventing another login flow.

## Standalone desktop shell for Hyprland

For the standalone program, use a Tauri 2 application with the existing React/TypeScript UI and a small Rust integration layer:

```text
React UI
  | invoke/events
Tauri Rust commands
  | HTTP + WebSocket, cookie jar, CSRF
Janus
```

The Rust layer should own the Janus session. On first launch it posts to Janus's existing login endpoint, keeps the JWT cookie and CSRF token in memory, and stores only the session material in the desktop keyring. Never put the Janus cookie, password, or token in `localStorage`. This avoids relying on a `tauri://` webview origin to share browser cookies with Janus and keeps the Janus API off the public internet.

Start with a normal undecorated Tauri window because it is substantially simpler to debug and package than a custom Wayland layer-shell window:

- launch one resident process at login;
- keep the window hidden until toggled;
- use a global Hyprland keybind such as `Super+T` to invoke `todo-chat --toggle`;
- use Tauri's single-instance handling so the toggle command reaches the existing process;
- make the window floating, pinned if desired, right-aligned, and approximately 400–460 px wide;
- animate its horizontal position/opacity in the client, with the Hyprland window rule supplying the final edge placement;
- close with `Escape` or the same global keybind, while leaving the process alive.

This gives the desired “slide-in sidebar” without requiring the app to reserve screen space. Hyprland window rules match the app class and can control floating, pinning, size, and position; global binds can launch or dispatch commands. The current Hyprland documentation uses Lua configuration in recent releases, while older installations may still use the familiar `bind = ...` syntax. Use the syntax already present in the local Hyprland config.

If a normal floating window proves visually or behaviorally insufficient, replace only the window shell with GTK4 Layer Shell. Keep the React/Tauri UI and Janus adapter unchanged if feasible, or move the UI into a WebKitGTK view hosted by a small GTK4 process. `gtk4-layer-shell` is designed for edge-anchored Wayland panels and supports GTK4 applications and GObject-introspection languages, including Python. Hyprland can apply layer rules by namespace for effects such as blur and animation. This should be a second step because it introduces compositor-specific packaging and focus behavior.

The desktop-specific state should be small and local:

- Janus base URL;
- selected Janus agent ID;
- selected conversation ID;
- sidebar width and preferred monitor;
- whether the panel is pinned across workspaces.

Task data and chat history remain in Janus. A local cache may hold only transient UI state and must be safe to delete.

## Suggested process lifecycle

```text
Hyprland login
  -> user service starts todo-chat
  -> app creates hidden single-instance window
  -> Super+T sends toggle request
  -> window shows, focuses, and reconnects to Janus
  -> task edits use REST; chat uses WebSocket
  -> hide leaves the process and session alive
```

Run it as a user service rather than repeatedly starting a second server. If Janus is unavailable, the panel should remain usable enough to show a reconnect state and retry with backoff; it should never silently create an offline fork of the task database.

## MVP scope

1. Janus schema migration for `parent_task_id`, `urgency`, and sibling order.
2. Tree read endpoint plus create/update/complete/reparent operations.
3. Dedicated task-capable Janus agent with read/write tools.
4. Tauri desktop shell with a hidden resident window and Hyprland toggle.
5. Todo client with task tree, editor, urgency filtering, and streamed chat.
6. Conversation list/resume using existing Janus conversation APIs.
7. Tests for user isolation, cycle prevention, parent/child behavior, auth/session storage, and tool confirmation.

Explicitly postpone external-provider writes, recurring tasks, dependencies, reminders, drag-and-drop polish, and destructive deletion until the local model feels right.

## Acceptance criteria

- Restarting Janus does not lose tasks or chat history.
- A task created in the todo UI appears in Janus's existing planner dashboard.
- A local task created or edited in Janus appears in the todo UI after refresh.
- Tasks can be nested to arbitrary depth without cycles.
- Urgency and due dates affect ordering and filtering predictably.
- An LLM can create, update, complete, and reparent tasks only through authorized tools.
- Imported planner tasks are clearly marked read-only.
- A failed WebSocket or LLM request never causes a local task mutation to be reported as successful.

## Delivery sequence

1. Add and test the Janus model/API contract without the new client.
2. Add task tools and a dedicated agent capability; test them directly against the service.
3. Scaffold the client against mocked Janus responses, then connect it to a real local Janus instance.
4. Add conversation resume, reconnect handling, and mutation invalidation.
5. Integrate the built UI into Janus only after the standalone development workflow is stable.
