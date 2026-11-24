# Todo Chat companion sidebar

**Status:** Shipped
**Linear:** none (shipped before Linear tracking); related: JAN-170 (canceled agent preselection follow-up)
**Depends on:** Janus local-planner tasks and chat (JAN-201 ungated access); `modules/todo-chat/` desktop module

Todo Chat is an optional Tauri + React desktop sidebar for a Janus server. It
gives you a focused task list and a chat surface, but Janus remains the source
of truth for tasks, conversations, authentication, and agent execution.

For full module build, packaging, and Hyprland service commands, use the
repository guide at `modules/todo-chat/README.md`. This page covers the Janus
behavior that operators and users need to understand.

## What you need in Janus

- A running Janus server, either local or remote
- A Janus user account you can log in with from the companion

Login, task-list browsing, and direct task edits do not require an agent.
Janus chat can always read and change your local tasks; no per-agent planner
permission is needed (JAN-201).

Janus no longer has agent definitions (JAN-227): chat in the companion
always speaks as your Janus character, so there is no agent picker.

Todo Chat reads its server URL from the shared Janus desktop config at
`~/.config/janus/config.toml`:

```toml
base_url = "https://janus.example.com"
```

`base_url` is global; an old `[todo-chat] agent_id` entry is ignored. When a valid URL is configured here, Todo Chat uses
it in preference to any older URL saved in the system keyring.

Chat can read, create, edit, complete, reparent, and reorder local tasks
without any per-agent permission flag. The sidebar's direct task list and task controls use
the authenticated Janus user's local-task API.

## What the companion can see and change

The companion only works with Janus-owned local planner tasks. Imported planner
records from synced sources (currently Google Calendar/Tasks and Jira — Azure
DevOps and Office 365 are not yet implemented, see
[Phase 3: The Planner](../roadmap/planner.md)) remain read-only and are not
exposed as editable companion tasks.

Local tasks support:

- parent and child relationships through `parent_task_id`
- urgency values of `low`, `normal`, `high`, and `critical`
- explicit sibling ordering through `sort_order`
- direct complete and move operations without a second task database

Janus rejects invalid hierarchy changes such as self-parenting or moving a task
below one of its descendants. Completing a parent task does not automatically
complete its children.

When you filter the companion task list by urgency, the companion preserves the
ancestor chain so matching subtasks still appear in context instead of becoming
orphaned rows.

## Session and desktop behavior

The desktop shell stores authenticated session material in the system keyring.
Passwords are used only during login and are not written to browser storage.
The Tauri shell owns the Janus cookie jar, CSRF token, and chat WebSocket
connection.

On startup, the companion revalidates any restored Janus session before showing
it as connected. If the saved session was revoked or expired, the companion
returns to the login state and clears stale authenticated context such as the
selected conversation.

A resident single-instance process handles `todo-chat --toggle`, so the window
can stay hidden until summoned from Hyprland or another launcher.

On Hyprland, the supported desktop mode is a fixed right-edge companion panel:
440 px wide, full monitor height, and pinned to the right border. The
repository installer task wires `Super+T` to that toggle flow and installs the
matching window rules.

## Companion task and chat behavior

The sidebar uses Janus's nested local-task tree endpoint, so subtasks remain
grouped under their parents. If the currently selected task disappears after a
refresh, completion, or move, the companion automatically selects the next
visible task or clears the selection when nothing remains.

Chat runs as your Janus character and can continue an existing Janus
conversation or start a new one. Task mutations performed from chat use the
same scoped local-task permissions described above.

If Janus itself cannot reach the configured LLM backend, the
companion now shows the backend error message in the chat pane instead of
failing silently or closing the session.
