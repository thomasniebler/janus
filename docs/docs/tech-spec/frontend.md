# Frontend Architecture

The web app is a React 19 single-page app in `frontend/`, built with Vite into `frontend/dist/` and served by the backend from the same origin. Stack: TypeScript, react-router 7, TanStack Query 5, axios, zod, Tailwind CSS 4, lucide-react icons, react-markdown.

## Structure

| Path | Contents |
| --- | --- |
| `src/App.tsx`, `src/main.tsx` | providers, routing, shell host |
| `src/pages/` | route-level pages, each a lazy chunk |
| `src/components/ui/` | primitives (Button, Input, Modal, ConfirmDialog, Card, Badge, Combobox) |
| `src/components/features/` | domain components that fetch their own data |
| `src/components/layout/`, `layout-v2/`, `layout-v3/` | the three app shells |
| `src/contexts/` | UI version, chat session, page context, page actions |
| `src/hooks/` | page context, domain-change sync, confirm dialog, media query |
| `src/lib/` | API modules, zod schemas, frame parsers, helpers |
| `src/api.ts`, `src/*Api.ts` | typed request functions |
| `src/generated/` | `openapi.json` and the generated `api.d.ts` |
| `e2e/` | Playwright specs and fakes |

Pages compose feature components; there are no monolithic pages. `src/components/CODEMAP.md` lists the components.

## Routing

| Route | Page |
| --- | --- |
| `/` | Dashboard |
| `/chat` | full-page chat |
| `/briefing` | Morning briefing |
| `/tasks` | Agent Tasks: trigger, automation and delegated runs with step traces |
| `/memory` | Memory & Knowledge tabs: Agent Memory, Topics, Documents, Knowledge, Wiki, Mail Prioritization Rules |
| `/planner` | planner accounts, calendars, local tasks and suggestions |
| `/emails` | mail list and detail |
| `/settings?tab=…` | settings tabs (below) |
| `/docs/*` | in-app documentation |
| `/setup`, `/login` | first-run setup and login, outside the shell |

`/events`, `/status` and `/errors` redirect to their Settings tabs. Unknown paths go to `/`.

Settings pages, grouped in the navigation (`pages/Settings.tsx`; ids and sub-tabs in `pages/settingsTabs.ts`):

| Group | Page (`?tab=`) | Sub-tabs (`&sub=`) |
|---|---|---|
| Setup | AI & Models (`system`) | `models` (LLM, models per surface), `capabilities` (semantic search, web search, image generation) |
| Setup | Connections (`connections`) | `mail` (mailboxes, mail ingestion), `calendar` (Google, Microsoft), `messaging` (WhatsApp, Telegram), `developer` (GitHub, Linear) |
| Setup | Tools & Safety (`tools`) | tool permissions, remote MCP, remote agents, injection classifier, self-evolution |
| Assistant | Character (`character`), Skills (`skills`) | |
| Automation | Triggers (`runtime`), Daily Briefing (`briefing`, with the secretary) | |
| Account | Security (`security`, with device tokens), Appearance (`appearance`) | |
| Monitoring | System Status, Stats, System Events, Error Groups (admins) | |

A page without sub-tabs has no `sub` parameter; an unknown `sub` falls back to the page's first sub-tab. The first-run default is AI & Models. `LEGACY_TABS` keeps the earlier top-level ids working (`accounts`, `mail-ingestion`, `whatsapp`, `github`, `linear`, `planner`): they resolve to their Connections sub-tab and the URL is rewritten. The OAuth callbacks land on `?tab=connections` (Google and Microsoft, which opens Calendar & tasks) and `?tab=tools` (MCP). The backend's `SETTINGS_TABS` allowlist for `ui_navigate` (`services/ui_actions.py`, mirrored in `contexts/uiActions.ts`) lists the page ids and the earlier top-level ids; `ui_navigate` takes a single `tab`, so an earlier id is how Janus reaches a Connections sub-tab.

## Interchangeable app shells

Three shells share every page and component; only the chrome differs.

| Version | Name | Look |
| --- | --- | --- |
| `v1` | Classic | glass header and left sidebar |
| `v2` | Terminal | CRT look, command bar, chat drawer |
| `v3` | Rusty Console | rust-red console, typewriter type, chat drawer |

- **Registry.** `src/contexts/uiVersion.ts` declares `UI_VERSIONS`, the default and each shell's `<html>` class. `src/components/appShells.ts` maps each version to a dynamic import; `AppShellHost` renders the loaded one and keeps the previous shell on screen while a new one downloads.
- **Preference.** `?ui=v1|v2|v3` wins for one page load; otherwise `localStorage['ui_version']` seeds the value and `GET /api/auth/me` (the user's `ui_version`) is the source of truth. Settings → Appearance changes it.
- **No flash.** `public/ui-shell-bootstrap.js`, a blocking same-origin script (the CSP forbids inline scripts), sets the shell class before React mounts; `src/uiShellBootstrap.test.ts` keeps it in sync with the registry.
- **Theming.** Every shell is dark (`dark` on `<html>`); `ui-v2` or `ui-v3` adds the identity. Shared primitives are reskinned with the Tailwind variants `uiv2:` and `uiv3:`, never forked.
- **Code splitting.** Each shell and each page is a lazy chunk. `src/lib/staleChunkReload.ts` reloads once (at most every 30 s) when a chunk from an older deploy is gone.

## Provider tree

`App.tsx` nests `QueryClientProvider` → `BrowserRouter` → `UIVersionProvider` → `ErrorBoundary` → routes. For a signed-in user it adds `PageContextProvider` → `UIActionProvider` → `ChatSessionProvider`, which holds `DomainChangeSync` (runs `useDomainChangeSync`), `WhatsNewFlash` (release notes after an update) and the `AppShellHost`.

## Chat session

`ChatSessionProvider` (`src/contexts/ChatSessionContext.tsx`, read with `useChatSession()`) owns the tab's only `/ws/chat` socket and all chat state: active conversation, messages (last 50), draft, responding flag, running tool calls, tool selection, pending approvals, live turn steps, connection error. Switching pages or shells never opens a second socket or loses the transcript.

- **Protocol.** It opens `/ws/chat?tool_events=1` and parses the control frames (`src/contexts/chatFrames.ts`, `src/lib/toolCallEvents.ts`, `src/lib/planSteps.ts`, `src/lib/uiBlocks.ts`). Page-action and introspection requests go to `useChatSideFrames` (`src/hooks/useUIActions.ts`). Other text is appended to the streaming reply. Frame formats are in [Backend and API](backend-api.md#wschat).
- **Actions.** `sendMessage` (attaches the page snapshot), `sendText`, `newConversation`, `selectConversation`, `deleteConversation`, `reconnect`. The socket reopens only when the conversation changes or on **Reconnect**; there is no auto-reconnect.
- **Restore.** The active conversation id is kept in `sessionStorage` per user; on mount its messages load before the socket opens. A change of signed-in user drops the previous session.
- **Tool calls.** `ToolCallInspector` shows one chip per call; expanding it shows the redacted arguments and result preview as plain text in a `<pre>`, never Markdown or HTML. Stored calls from `GET /api/conversations/{id}` render the same.
- **Approval cards.** A `__TOOL_APPROVAL__` frame turns the call's chip into `ToolApprovalCard` (tool, reason, redacted arguments, timeout) with **Approve**, **Always allow** and **Deny**. The answer goes back as `{"type":"tool_approval",...}`; the next tool event collapses the card.
- **Turn timeline.** `TurnTimeline` groups the reply's calls under the live turn steps; a step with an open approval shows as waiting. Steps are live only.
- **Page context.** Pages publish what is on screen with `usePageContext()`/`usePageContextSection()` into a ref-backed registry (`PageContextProvider`, never rendered from). `fitPageContext` (`src/lib/pageContext.ts`) keeps only ids and short labels and fits the snapshot into 2 KB. Pages that publish nothing still send `{page, route}`.
- **Page actions.** Pages register their filters, item types, sections and forms with `usePageActions()`/`usePageContext(snapshot, actions)`. `UIActionProvider` executes a request only if it matches a registered target and an id in the current snapshot, and answers `ui_action_result`. Highlight and scroll use only `data-ui-item` and `data-ui-section` attributes. Executed actions show as chips with undo.
- **Introspection.** `src/lib/uiOutline.ts` serializes the visible page into a bounded outline (never form values, passwords, editable content, the chat panel or `data-janus-private` subtrees); `src/lib/uiIntrospection.ts` answers requests.
- **UI blocks.** `UIBlockList` renders blocks validated by the zod catalog in `src/lib/uiBlocks.ts`: text nodes only, safe links, images only from `/api/images/<id>`, charts as inline SVG. An unparsable block is shown as JSON. An action button only sends a chat message.
- **Views.** `BriefingChat` is presentational (`compactSidebar` or `fullPage`). At `lg` and wider the chat is the shell's sidebar or drawer; below, it renders inline. `/chat` (`ChatPage`) shows no second chat view and docks the turn timeline at `xl`.

## Server state and live sync

- **TanStack Query is the client truth.** Feature components fetch and mutate their own data; `useState` holds only UI state. There is no global store.
- **Live sync.** `useDomainChangeSync` opens `/ws/changes` (`src/lib/domainChanges.ts`). `DOMAIN_QUERY_KEYS` maps each backend domain to query-key prefixes; `changed` frames are collected for 150 ms and invalidated in one pass; every `ready` frame invalidates all mapped domains; reconnects back off from 1 s to 30 s. Adding a domain means adding it on both sides; `tests/test_domain_changes.py` checks they match.
- **Timestamps.** The backend sends UTC, sometimes without an offset. Every server timestamp goes through `parseServerDate` and the `formatServer*` helpers in `src/lib/serverTime.ts`; never `new Date(value)` on a server string.

## API client and validation

- **One axios instance.** `src/lib/httpClient.ts` (re-exported by `api.ts`) uses same-origin cookies (`withCredentials`) and adds `X-CSRF-Token` to unsafe requests, fetching and caching the token from `/api/auth/csrf`.
- **Typed wrappers.** `src/api.ts` and the `lib/*Api.ts` modules wrap each endpoint. Types come from `src/generated/api.d.ts`, generated from the committed OpenAPI schema (`task openapi:generate`; `task openapi:check` in CI).
- **Runtime validation.** Critical responses are parsed with zod schemas (`src/lib/schemas.ts`, `runtimeSchemas.ts`, `knowledgeSchemas.ts`, `opsSchemas.ts`) through `parseResponse` (`src/lib/apiValidation.ts`). A drifted response fails at the call as an `ApiValidationError` naming method, path and field paths, never values.
- **Errors.** `src/lib/requestErrors.ts` turns backend errors into messages; `components/ui/ErrorBoundary.tsx` keeps a crash from blanking more than its subtree.

## In-app documentation reader

`pages/Documentation.tsx` renders `docs/docs` inside the app at `/docs/*`.

- Navigation comes from `GET /api/docs/nav` (the Zensical nav); pages from `GET /api/docs/{path}`.
- `pages/documentationRoutes.ts` maps routes to source paths (`/docs/tech-spec` is the root `TECH_SPEC.md` through its alias) and resolves relative `.md` links, including `../../../TECH_SPEC.md`, to app routes.
- Markdown is rendered with react-markdown and `remark-gfm`; Zensical admonitions (`!!! note`) and content tabs (`=== "Tab"`) are converted to structure, never shown raw.
- A missing page shows an alert with a link back to the start page. The docs API needs a login.

## Tests

- **Vitest** (jsdom), next to the sources (`*.test.ts(x)`), with V8 coverage floors in `vitest.config.ts`.
- **Storybook** stories for shells and features; the built Storybook is served to admins at `/dev/storybook`.
- **Playwright** (`frontend/e2e/`) against a real backend on a fresh database with fakes for the LLM (`llm-stub.ts`), upstream services (`fake-upstream.ts`, `fakeIntegrations.ts`) and Neonize. Specs never depend on each other's state; CI runs them in four shards.
- **Lint.** ESLint with complexity and size limits (baselined in `eslint.baseline.json`), `tsc -b` in the build, knip for dead code.
