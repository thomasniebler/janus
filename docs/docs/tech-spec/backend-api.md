# Backend and API Architecture

The backend is a FastAPI app built by `app_factory.create_fastapi_app()`. Routers in `src/janus/api/` are thin: they authenticate, validate with pydantic schemas (`src/janus/schemas/`), call one service, and map the result. Domain logic lives in `src/janus/services/` and the runtime modules.

## App assembly

`app_factory.py`:

- builds the app with `/apidocs` and `/openapi.json` only when `JANUS_PUBLIC_API_DOCS_ENABLED` is on;
- adds CORS (from `request_origin.browser_origins`: `ALLOWED_ORIGINS` plus the `base_url` origin) and `TrustedProxyHeadersMiddleware` (forwarded headers, host and origin checks, security headers; see [auth and security](auth-security.md#proxy-host-origin-and-cookie-policy));
- includes every router with the CSRF dependency (`security.enforce_csrf_for_cookie_auth`), which applies to cookie sessions on mutating methods;
- mounts the built frontend's `/assets` (`frontend/dist`, or the wheel's `janus/_data/frontend`) and serves `index.html` for every other non-API path (`serve_spa`);
- registers the three WebSockets.

## Auth guards

| Dependency | Use |
| --- | --- |
| `security.get_current_user` | default for every route; `401` without a valid session |
| `security.admin_required` | admin-only routes (user management, error groups, instance settings writes) |
| `security.get_user_or_none` | routes that may be anonymous (setup status, login helpers) |
| `security.get_websocket_user_or_none` | WebSockets; authenticates on its own short-lived session that is closed before the socket opens |
| `api/inbox.get_inbox_user` | the action inbox: a web session, or a scoped device token |

The token comes from the `access_token` cookie or an `Authorization: Bearer` header. Semgrep rule `janus-route-without-auth-dependency` flags a route with no auth dependency; the few public routes are listed exceptions.

Instance-wide settings (the shared `settings` row: LLM, web search, GitHub, Linear, mail sync limit) are readable by every user and writable only by admins.

## Ownership rule

Every user-owned read or write goes through a service method named `*_for_user` that filters on the owner in the same query. It returns `None`/`False` for a row that is missing or foreign, and the router maps both to the same `404`. So a foreign id never discloses that it exists.

The same rule applies to everything that reads on a user's behalf:

- Mail is owned through its account. Every mail read starts from `EmailService.query_emails_for_user`; `send_email` sends only from the owner's own active account.
- Rule learning and rule evaluation use only the mail owner's rules.
- Reminders, planner context and briefing context take the owner's id.
- Agent tools read with the run owner (`ToolContext.user`), never a model-supplied user.

Semgrep rule `janus-unscoped-owned-row-lookup` flags `db.get(models.X, id)` on owned models in routers, toolboxes and WebSocket handlers.

## Routers

| Prefix | Module | Purpose |
| --- | --- | --- |
| `/api/auth` | `auth.py` | login, logout, setup, CSRF token, `me`, UI preference, password change, users (admin), session revocation, Google sign-in and linking |
| `/api/character` | `character.py` | character profile, preview, character state, envelope, history, rollback, state proposals |
| `/api/conversations` | `conversations.py` | chat history, messages with tool calls and UI blocks, delegations of a conversation |
| `/api/dashboard` | `dashboard.py` | dashboard data (emails, WhatsApp, events, task tree, topics), widget preferences |
| `/api/desktop` | `desktop.py` | `summary` JSON for desktop widgets |
| `/api/emails` | `emails.py` | mail accounts, rules, notification preferences, mail settings, email list and relabel |
| `/api/errors` | `errors.py` | error groups (admin) |
| `/api/events` | `events.py` | event log, owner-scoped (admins see all), paginated, filterable |
| `/api/inbox` | `inbox.py` | action inbox and device tokens |
| `/api/github`, `/api/linear` | `github.py`, `linear.py` | instance-wide integration settings |
| `/api/images` | `images.py` | generated images, owner-scoped, `no-store` |
| `/api/memory` | `memory.py` | memory facts (with their review state), compaction, `PUT /{id}/pin` (pin into or hide from the memory index) |
| `/api/topics` | `topics.py` | tracked topics and their evidence |
| `/api/knowledge` | `knowledge.py` | uploads, sources, extraction settings, entities, merge suggestions, search, graph |
| `/api/wiki` | `wiki.py` | wiki pages, revisions, proposals, provenance |
| `/api/planner` | `planner.py`, `planner_sources.py`, `planner_items.py`, `planner_suggestions.py`, `llm_models.py` | planner accounts and sync, imported events and tasks, local tasks and events, suggestions and bulk dismissal, LLM settings, model listing and connection test |
| `/api/connections` | `connections.py` | Google and Microsoft OAuth connections, mailbox creation |
| `/api/runtime/triggers` | `runtime_triggers.py` | queue an ad-hoc trigger, run history, worker status |
| `/api/runtime/definitions` | `trigger_definitions.py` | trigger definitions, event catalog, run now, runs per definition, proposals (approve, reject) |
| `/api/runtime/tasks` | `runtime_tasks.py` | Agent Tasks: trigger, automation and delegated runs with step traces |
| `/api/tools` | `tools.py` | tool registry, tool policies, policy modes, injection classifier, remote MCP servers and OAuth |
| `/api/tools/a2a-agents` | `a2a.py` | remote A2A agents |
| `/api`, `/api/skills/suggestions` | `skills.py`, `skill_suggestions.py` | skill library, catalog discovery and import, skill suggestions |
| `/api/stats` | `stats.py` | per-user stats: mail, triggers, memory, messaging, approvals, LLM usage |
| `/api/status` | `status.py` | `health`, `workers`, `summary`, `version` |
| `/api/web-search` | `web_search.py` | web search backend settings |
| `/api/self-evolution` | `self_evolution.py` | self-evolution settings, caps and audit |
| `/api/whatsapp` | `whatsapp.py` | accounts, pairing sessions, conversations, messages |
| `/api/telegram` | `telegram.py` | link, test and unlink the user's bot |
| `/api/webhooks` | `webhooks.py` | webhook endpoints, plus the public `POST /api/webhooks/in/{key}` |
| `/api/docs` | `docs.py` | in-app documentation (login required) |
| `/dev/storybook` | `dev.py` | the built Storybook, admin only |

The router list in `app_factory.py` is the source of truth; `src/janus/api/CODEMAP.md` maps each file.

## WebSockets

All three authenticate with `get_websocket_user_or_none` (cookie or bearer JWT), check `Origin` against `request_origin.browser_origins`, and close anonymous connections with code 1008. None holds a database session while idle.

### `/ws/chat`

One connection per browser tab (`websocket/chat.py`, inbound reader `websocket/chat_inbox.py`). The server sends text frames; control frames start with a marker:

| Frame | Meaning |
| --- | --- |
| `__CONVERSATION_ID__:<id>` | the conversation this socket is bound to |
| `__TOOL_SELECTION__:<json>` | the step's authorized tools, denials and toolset choice |
| `__START__` / `__END__` | a reply begins / is committed |
| plain text | reply text, streamed |
| `__TOOL_EVENT__:<json>` | one phase of a tool call (`start`, then `end`, `error` or `blocked`) |
| `__PLAN_EVENT__:<json>` | a turn step (one model round that called tools) changed status |
| `__TOOL_APPROVAL__:<json>` | an approval card for a `confirmation="required"` call |
| `__UI_ACTION__:<json>` | a page action for the client to perform |
| `__UI_INTROSPECT__:<json>` | a request for the page outline or element styles |
| `__UI_BLOCK__:<json>` | a structured block for the reply |
| `__ERROR__:<message>` | the turn failed; the socket closes |

The structured frames go only to clients that open `/ws/chat?tool_events=1`. Others (the iOS prototype) get only text, `__START__`, `__END__` and errors, and still receive the deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers. Inbound frames are a plain-text message, a JSON message with `page_context`, a `tool_approval` answer, a `ui_action_result` or a `ui_introspection_result`. The runtime side of every frame is in [Background workers and runtime](background-workers-runtime.md#chat).

### `/ws/briefing`

Sends today's completed Morning briefing run (today in the definition's timezone). When there is none, or the client passes `?refresh=1`, it runs the built-in inline first. `?timezone=` lets the server move a still-unedited briefing off UTC. The text arrives as one message.

### `/ws/changes`

Tells a tab which data domains changed so it can refetch (`websocket/changes.py`, `domain_changes.py`).

- **Frames (server to client only):** `{"type":"ready"}` on subscribe, `{"type":"changed","domains":[...]}` after a commit (bursts within 100 ms coalesced), `{"type":"ping"}` every 25 s. The session is re-validated before each ping; a logged-out or revoked session is closed with 1008.
- **Payload:** domain names only, never ids or content. A user hears only about rows they own; the global skill library (`skills`) is broadcast.
- **Emission:** `domain_changes.py` hooks SQLAlchemy session events (`after_flush` for ORM instances, `do_orm_execute` for bulk updates and deletes), resolves each tracked row's owner (directly or through a parent row), and publishes on commit. A rollback drops the pending changes. Routers and services publish nothing themselves, so REST writes, chat tools, trigger runs and workers all emit the same events.
- **Domains:** `domain_changes.DOMAINS`, mirrored by `DOMAIN_QUERY_KEYS` in `frontend/src/lib/domainChanges.ts`; `tests/test_domain_changes.py` fails when they differ.
- **Limits:** the bus is in-process, so only sockets of the same process are reached. Raw SQL outside an ORM session is not observed. Domain changes never start a trigger run.

## Action inbox

`services/inbox_service.py` aggregates what waits for the user: unread Janus-started conversations, pending planner suggestions and proposed trigger definitions. `GET /api/inbox` returns at most 50 items (20 per source), newest first, with an `ETag` for cheap polling. Titles and summaries are untrusted text, cut and stripped of control and bidi characters. `POST /api/inbox/{kind}/{id}/{action}` runs `mark_read` (conversations) or `dismiss` (planner suggestions); anything else is `404`. Trigger proposals are listed but can only be approved in the web app. Device tokens are covered in [auth and security](auth-security.md#device-tokens).

## Status and events

- `GET /api/status/health`: public liveness with version; `500` when a mail monitor is stale. Used by deploy checks.
- `GET /api/status/summary`: service groups (mail, planner, WhatsApp, LLM, knowledge search) with `operational`, `degraded`, `outage` or `unknown`; the overall state is the worst top-level state. Built by `status_service.get_status_overview` from worker state, sync timestamps and the stored LLM probe.
- `GET /api/status/version?since=X.Y.Z`: running version, changelog path, and up to five release-note entries newer than `since` (`services/changelog_service.py`).
- `GET /api/events`: newest-first pages (`limit` ≤ 200) filtered by type, source and time range. Admins read every row; other users read rows with their own `user_id` (`services/event_service.visible_events_condition`).

## In-app documentation API

`services/docs_service.py` serves both `/api/docs` and the agent's `docs` toolbox.

- `GET /api/docs/nav` parses `docs/zensical.toml` so the app shows the same navigation as the docs site.
- `GET /api/docs/{path}` returns one Markdown file from `docs/docs`. The path must resolve to a regular file inside `docs/docs`; `..`, absolute paths and outward symlinks are "not found".
- Two root files are served through aliases: `tech-spec.md` (the root `TECH_SPEC.md`) and `changelog.md` (`CHANGELOG.md`).
- The router requires a login. The agent toolbox additionally skips `docs/docs/reviews/`.

## OpenAPI and generated types

The schema is exported offline by `scripts/export_openapi.py`: it builds the app object in-process, with no server, startup hooks, workers, database or network, and does not depend on `/openapi.json` being served. The committed schema is `frontend/src/generated/openapi.json`; `npm run generate:types` writes `frontend/src/generated/api.d.ts` from it.

- `task openapi:generate` refreshes both after an API change.
- `task openapi:check` (CI job `openapi-check`) fails when either file drifts from the backend.

Response shapes must therefore be stable pydantic models. Critical responses are also validated at runtime in the frontend (see [frontend](frontend.md#api-client-and-validation)).
