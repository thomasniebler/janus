# Frontend Architecture

## Structure

The frontend is organized into a component-based hierarchy for modularity and maintainability.

- `src/components/ui/`: Atomic UI primitives such as buttons, inputs, modals, and cards. `Modal` is a portalled `role="dialog"` (`aria-modal`, labelled by its title); `ConfirmDialog` wraps it as the in-app replacement for `window.confirm` (Cancel takes focus; a `warning` tone uses the danger confirm button). Feature components open it through `hooks/useConfirmDialog.tsx`; since JAN-292 no component calls `window.confirm` (deleting a chat, topic, memory fact, skill, or knowledge source, and the Knowledge extraction and Wiki synthesis cloud-model warnings all use it).
- `src/components/features/`: Domain-specific components such as briefing chat, rule management, and email items.
- `src/components/layout/`: The classic (UIv1) app shell — glass header, left sidebar, and footer.
- `src/components/layout-v2/`: The alternate (UIv2) "terminal" app shell — near-black HUD backdrop, monospaced command bar, and chat drawer.
- `src/components/layout-v3/`: The alternate (UIv3) "rusty console" app shell — warm black-red corroded backdrop, rust-ruled nav keys, typewriter chrome, and chat drawer.
- `src/contexts/`: Cross-cutting React context providers such as the UI shell selector and the shared chat session.
- `src/pages/`: Page-level containers that compose features.
- `src/hooks/`: Small reusable React hooks such as debounced values and media-query matching.

## Interchangeable App Shells

- **Shell-only swap:** `App.tsx` renders `Layout` (UIv1), `LayoutV2` (UIv2) or `LayoutV3` (UIv3) through `AppShellHost`, keyed on the UI-version context. The shells are declared in `src/components/appShells.ts` as a registry of dynamic imports, one per UI version. Pages and feature components are shared and unchanged across all three, so adding a shell is a new table entry, not a new branch.
- **Version and "what is new":** `WhatsNewFlash` (mounted once in `App.tsx`, so all three shells show it) asks `GET /api/status/version` with the version this browser last showed; after an update it flashes up to five highlights of the releases in between with a link to the changelog, and records the new version per user in `localStorage` once the flash was dismissed or timed out (a first visit only records it; hovering or focusing the card pauses the timeout). `VersionLabel` shows `Janus vX.Y.Z · Changelog` on the Settings page.
- **Code splitting (JAN-179):** each shell is its own chunk, fetched only when it is rendered or prefetched. A normal session downloads just the shell it renders; opening the Settings → Appearance picker prefetches all three, so a switch from there renders the new shell in the same commit as its `<html>` class. `appShells.ts` is a subscribable cache that loads each shell's chunk once (`loadAppShell`, `getLoadedAppShell`). `useAppShell` (`src/components/useAppShell.ts`) reads it through `useSyncExternalStore`, so a chunk that lands at any moment, including from a prefetch, re-renders the host. `AppShellHost` shows the full-screen loader only before the first shell has loaded, and keeps the previous shell on screen while a newly selected one is still downloading. `AppRouter` starts fetching the cached shell's chunk while `GET /api/auth/me` is in flight. Every route page (`Dashboard`, `ChatPage`, `Settings`, `Login`, `Setup`, …) is a `React.lazy` chunk; inside the shell a `Suspense` boundary shows a small in-content spinner for a page's first load, and React Router's transition-wrapped navigation keeps the current page visible while the next one loads. `vite.config.ts` groups every `lucide-react` icon into one shared `icons` chunk (`build.rolldownOptions.output.codeSplitting.groups`) so lazy pages do not each pull dozens of sub-1 kB icon chunks. No chunk is above Vite's default 500 kB warning, so `build.chunkSizeWarningLimit` is left at its default.
- **Stale chunks after a deploy:** the deploy replaces the asset tree (`rsync --delete`), so a tab left open across a deploy can ask for chunk URLs that no longer exist. `src/lib/staleChunkReload.ts` wraps every lazy import (shells and pages) and listens for Vite's `vite:preloadError` (installed in `main.tsx`): a chunk-load failure triggers one full reload into the new build instead of the root error boundary. A `sessionStorage` timestamp limits this to one automatic reload per 30 seconds; a second failure inside that window, or a browser without `sessionStorage`, surfaces as a normal error. Unsent in-memory state such as a chat draft does not survive the reload; keeping the previous build's assets during deploy would be the deploy-side alternative.
- **One shell registry:** `src/contexts/uiVersion.ts` declares `UI_VERSIONS`, `DEFAULT_UI_VERSION` and `UI_VERSION_HTML_CLASSES`; the type, the storage/query validation and the `<html>` class bookkeeping all derive from it. The anti-flash bootstrap `public/ui-shell-bootstrap.js`, which `index.html` loads as a blocking same-origin `<script src>` in `<head>`, mirrors the same table and must be kept in sync (`src/uiShellBootstrap.test.ts` checks it). It is not an inline script because the backend's Content-Security-Policy (`script-src 'self'`) blocks inline scripts; an inline copy never ran in production, so every reload painted without the shell class until React mounted (JAN-225).
- **Preference precedence:** a `?ui=v1` / `?ui=v2` / `?ui=v3` query parameter wins for the current page load, otherwise the `localStorage['ui_version']` write-through cache seeds the value and `GET /api/auth/me` reconciles it as the source of truth.
- **Theme coupling:** every shell is dark. The provider keeps `dark` on `<html>` at all times and adds exactly one shell identity class — `ui-v2` for the terminal identity (`--color-v2-*` tokens, hard-edged geometry, phosphor lime) or `ui-v3` for the rusty-console identity (`--color-v3-*` tokens, rust-red rules, typewriter type, corrosion/grain/vignette texture) — on top of the shared dark palette, clearing the others.
- **No primitive forks:** shared `components/ui/` primitives are reskinned in place through the Tailwind v4 `uiv2:` and `uiv3:` variants, plus CSS custom properties for the cases where the glass look was set through inline styles.
- See [Alternate UI shells (UIv2, UIv3)](../feature-specs/alternate-ui-shells.md) for the full design.

## Chat Session

- **One session per tab (JAN-248):** `ChatSessionProvider` (`src/contexts/ChatSessionContext.tsx`, read through `useChatSession()` from `src/contexts/chatSession.ts`) is mounted in `App.tsx` around the authenticated app shell, inside `QueryClientProvider` and the router. It owns the only `/ws/chat` socket in the tab plus every piece of chat session state: the active conversation id, the message list (capped at the last 50), the unsent draft, the "responding" flag, running tool calls, the latest `__TOOL_SELECTION__` decision, pending tool approvals, the current turn's live steps (JAN-316), and the connection error. Switching pages or UI shells, or remounting a chat view, never opens a second socket or loses the transcript or draft.
- **Protocol handling lives in the provider:** it opens `/ws/chat?tool_events=1`, parses `__CONVERSATION_ID__`, `__TOOL_SELECTION__`, `__START__`, `__END__`, `__TOOL_EVENT__`, `__TOOL_APPROVAL__`, `__PLAN_EVENT__` (JAN-316, below), `__UI_BLOCK__` (JAN-252, below) and `__ERROR__`, hands `__UI_ACTION__` page actions to the `UIActionProvider` (JAN-251, below), drops the deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers, appends every other frame to the streaming assistant message, and invalidates the `conversations` query. It does not map tool names to query keys any more: what a chat tool changed reaches every view through the domain-change channel below once the backend has committed it (JAN-104; this replaces `lib/chatToolInvalidations.ts`). A socket is (re)opened only when the active conversation changes to one the current socket is not bound to, or on the manual **Reconnect** action; there is no auto-reconnect.
- **Tool calls on each reply (JAN-249):** `__TOOL_EVENT__` frames (parsed by `src/lib/toolCallEvents.ts`) are merged by `call_id` into the streaming assistant message's `toolCalls`, and drive the transient "Running tool" status (`activeToolCalls`). Messages loaded from `GET /api/conversations/{id}` get the same `toolCalls` from their stored `tool_calls`, so a reloaded or reselected conversation shows the same chips. `BriefingChat` renders them with `ToolCallInspector` under the reply: one chip per call (status icon for running/done/failed/blocked, tool name, duration). Clicking a chip (a real button with `aria-expanded`, not a hover card, so it works on touch and for long output) expands an inline section with the pretty-printed, already-redacted arguments, the result preview, a **truncated** marker when the backend cut either, and a copy button. Both are rendered as text nodes in a monospace `<pre>`, never through Markdown or as HTML, because tool output is untrusted. Query invalidation after tool mutations is unchanged: it still goes through `/ws/changes`, not through tool events.
- **Tool approval cards (JAN-254):** a `__TOOL_APPROVAL__` frame (parsed by `src/lib/toolCallEvents.ts` as `ToolApprovalRequest`) puts an `approval` request on the matching call in the streaming message's `toolCalls` (creating the entry if its `start` event has not arrived). While a call has a request and no decision, `ToolCallInspector` renders it as an inline `ToolApprovalCard` instead of a chip: the tool's display name and id, the registry reason, the redacted arguments as plain text in a monospace `<pre>` (the same untrusted-text rule as the chip), how long the card stays open, and **Approve** / **Always allow** (JAN-359: approves and switches the tool to *Always allow* in chat) / **Deny** buttons. A click calls the provider's `answerToolApproval(callId, decision)`, which sends `{"type":"tool_approval","call_id":...,"decision":"approve"|"always"|"deny"}` on the socket and marks the request answered so the buttons disable at once; the next `__TOOL_EVENT__` for that call (its result, or `blocked` for deny/timeout) collapses the card into the normal chip, which then shows *approved by you* / *denied* / *timed out* from the event's `approval` field. Stored calls carry the same `approval`, so reloaded conversations show it. The card lives in the shared components, so the sidebar chat, `/chat`, and all three shells get it. When the socket closes, pending cards are dropped (the backend has already refused those calls); a reconnect never shows them again.
- **Turn timeline (JAN-316):** a `__PLAN_EVENT__` frame (parsed by `src/lib/planSteps.ts`, which also holds the step types `PlanStepRecord`/`PlanStepDisplayStatus`; the wire format is in the [runtime spec](background-workers-runtime.md), "Chat plan/step timeline") is merged by `step_id` into the streaming assistant message's live `planSteps` by `applyAssistantFrame` (`src/contexts/chatFrames.ts`); a finished step is never reopened by a late frame. Each `__TOOL_EVENT__` call carries its `step_id` (declared on `ToolCallRecord` from `planSteps.ts`, since `types.ts` is held at its size). `buildTimeline` groups the reply's `toolCalls` under its steps (a call without a `step_id`, such as an approval request whose `start` has not arrived, joins the newest unfinished step) and derives the display status: `pending` is shown as *generated*, and an unfinished step with a call awaiting approval (JAN-254) as *waiting for approval*. `TurnTimeline` (`components/features/TurnTimeline.tsx`) renders the latest reply's steps with status icons and compact chips (planned-but-not-started tools as dashed chips); tool details and the Approve / Deny card stay in `ToolCallInspector`. When the socket closes mid-turn, `closeInterruptedTurn` fails every unfinished step (and drops pending approvals). Steps are live only: replies loaded from `GET /api/conversations/{id}` have no `planSteps`, and the panel then says so. Placement: `ChatTurnStatus` (the turn's withheld-tools notice, the *Tool selection* note, and the timeline) sits above every `BriefingChat`, with the timeline collapsed behind a **This turn** toggle (its summary is still announced through a `role="status"` region); on `/chat` at `xl` and above, `ChatPage` docks an expanded copy as a second grid column and tells `BriefingChat` to leave its own out through `TurnTimelineDockContext` (`components/features/turnTimelineDock.ts`), so exactly one copy is mounted. The component uses the shared palette classes, so all three shells reskin it.
- **Session actions:** the provider exposes `sendMessage` (which attaches the current page snapshot, below), `sendText` (the same for a given text without touching the draft; used by UI-block action buttons, JAN-252), `newConversation`, `selectConversation`, `deleteConversation` and `reconnect`. The conversation list itself is the shared `['conversations']` TanStack Query cache.
- **Page context with every message (JAN-250):** `sendMessage` sends `{"type":"message","text":...,"page_context":{...}}` (the frame shapes and server-side handling are in the [runtime spec](background-workers-runtime.md), "Chat page context"). The snapshot describes the page the user is on, from whichever chat view they typed in (sidebar, inline, or `/chat`, in all three shells):
  - **Store:** `PageContextProvider` (`src/contexts/PageContextProvider.tsx`, read through `src/contexts/pageContext.ts`) sits in `App.tsx` directly around `ChatSessionProvider`. It is a plain ref-backed registry, not React state, so publishing a snapshot never re-renders anything; the provider reads it only at send time. No global state library.
  - **Hooks:** a page calls `usePageContext({ page, title, selection, filters, visible_items })` (`src/hooks/usePageContext.ts`); the hook adds the current `route` (path plus query string) and removes the snapshot when the page unmounts. A feature component that owns the data a page shows (the memory facts list, the topic list, the trigger definitions) calls `usePageContextSection({ selection, filters, visible_items })` instead; sections are merged into the page snapshot (filters merged, items appended, a section's selection wins over the page's).
  - **Route-only fallback (step 1):** when no page published a snapshot, or the published one belongs to another path (a page that has not unmounted yet), the message still carries `{ page, route }`, with the page name derived from the route by `routePageName` in `src/lib/pageContext.ts` (`Dashboard`, `Emails`, `Chat`, `Memory`, `Settings`, `Documentation`, ...).
  - **Only what is visible:** items are `{type, id, label}` with short labels (an email's subject and sender, a topic's title, a fact's summary); never bodies, snippets, message text, or detail fields. Pages publish the rows the user can see, the open item as `selection`, and the active tab/filter/search as `filters`.
  - **2 KB budget, client side:** `fitPageContext` keeps only the snapshot keys, cuts strings (page 60, title 120, route 200, type 32, id 64, label 80, filter keys 40 and values 80 characters, at most 12 filters and 40 items), then drops visible items from the end (counting them in `omitted_items`), then filters, selection, and title until the UTF-8 JSON is at most `PAGE_CONTEXT_MAX_BYTES` (2,048 bytes). The backend enforces the same budget again.
  - **Covered pages:** Dashboard (visible widgets, their important emails, topics, schedule and tasks), Emails (active filter, listed emails, the open email as `selection`), Memory (active tab; on Agent Memory the listed facts and search; on Topics the listed topics, status filter, search, and the open topic), Settings (active tab; on Triggers the listed trigger definitions). Other routes send the route-only context.
- **Page actions (JAN-251):** Janus can act on the page the user sees through the chat-only `ui_actions` tools (the server side, frame shapes, and allowlists are in the [runtime spec](background-workers-runtime.md), "Chat page actions"; the safety rules in the [safety spec](llm-agent-safety.md#page-actions-jan-251)). Only client-side, non-destructive actions exist: `navigate`, `set_filter`, `open_item`, `highlight`, `scroll_to`, `prefill_form` (fills, never submits). Data changes are never page actions; they stay server-side tools and reach the page through `/ws/changes`.
  - **Registry:** `UIActionProvider` (`src/contexts/UIActionProvider.tsx`, with the store, request parsing, and validation in `src/contexts/uiActions.ts`) sits in `App.tsx` inside `PageContextProvider`, around `ChatSessionProvider`, and inside the router (it navigates). Like the page-context store it is a plain registry, not React state; only the log of executed actions (for the transcript chips) is state.
  - **Pages register what they support, next to their snapshot:** `usePageContext(snapshot, actions)` / `usePageContextSection(section, actions)` (`src/hooks/usePageContext.ts`) and `usePageActions(actions)` (`src/hooks/useUIActions.ts`) register `{page, path?, filters, items, sections, forms}` for as long as the component is mounted. A filter names its allowed values (or free text) with a getter and setter; `items` maps an item type to an `open` handler (returning a close function, used for undo) and whether it can be highlighted; a form lists its field names with a getter and a `prefill` setter. Registrations of one page from several components (the Triggers tab's definitions and run history) are merged.
  - **Execution:** `ChatSessionProvider` hands each `__UI_ACTION__` frame to `useChatSideFrames` (`src/hooks/useUIActions.ts`; it also drops the deprecated `__TOOL_CALL__`/`__TOOL_RESULT__` markers), which has the `UIActionProvider` execute it and answers `{"type":"ui_action_result","action_id":...,"ok":bool,"error":...}` on the same socket. The request is checked again on the client: `navigate` only to the same route allowlist as the server; `set_filter` only to a filter the current page registered, with an allowed value; `open_item` and `highlight` only for an item type the current page registered and an id in the snapshot the page publishes *now* (`selection` or `visible_items`); `scroll_to` only to a registered section id; `prefill_form` only to a registered form and its known field names. Unknown actions, routes, ids, filters, sections, forms, or fields are rejected with an error message. A request that follows a navigation in the same reply waits up to 3 s for the new page to register and publish its items before it is rejected. Highlight and scroll use only the fixed `data-ui-item="<type>:<id>"` and `data-ui-section="<id>"` attributes (no selectors from the model); a highlight adds the `ui-action-highlight` ring for a few seconds.
  - **Transcript chips and undo:** `ToolCallInspector` shows a page-action call as `verb · target` (the item's label from the snapshot, the route, `key = value`, ...) instead of the tool name, and, while the action is live in this tab, an **Undo** button where undo is cheap: `navigate` goes back to the previous route (and tab), `set_filter` restores the previous value, `open_item` closes the item again, `prefill_form` restores the previous field values; `highlight` and `scroll_to` have none. Undo is client-side only, never a tool call, and is not available after a reload (stored calls show the chip without it).
  - **Covered pages:** Dashboard (`scroll_to` a widget; `open_item` for its listed emails, which opens them on Emails, topics, which opens them on Memory › Topics, and calendar events, which scrolls to and highlights the event; `highlight` for the same), Emails (`set_filter` priority, `open_item`, `highlight`), Memory › Topics (`set_filter` status and search, `open_item`, `highlight`, `prefill_form` for "Add manual topic"), Settings › Triggers (`navigate` to `/settings?tab=runtime` switches the tab even when Settings is already open; `set_filter` run status, run kind, and finished follow-ups; `open_item` expands a definition's runs; `highlight`; `scroll_to` the definitions or the run history). Other pages support `navigate` only.
- **UI introspection (JAN-282..285):** Janus can look at the page the user has open through the chat-only `ui_introspection` tools `get_ui_snapshot` and `get_element_styles` (the server side, frame shapes, and budgets are in the [runtime spec](background-workers-runtime.md), "Chat UI introspection"; the safety rules in the [safety spec](llm-agent-safety.md#ui-introspection-jan-282285)). The browser side is `src/lib/uiOutline.ts` (the DOM serializer) and `src/lib/uiIntrospection.ts` (request parsing, styles, and the session); nothing on the page changes.
  - **Execution:** `useChatSideFrames` (`src/hooks/useUIActions.ts`) also consumes `__UI_INTROSPECT__:` frames: it parses the request (`parseIntrospectionRequest`), has the tab's introspection session (`createIntrospectionSession`, one per `ChatSessionProvider`) answer it, and sends `{"type":"ui_introspection_result","request_id":...,"ok":bool,"result":{...},"error":...}` on the same socket.
  - **Snapshot (`serializeSnapshot`):** a depth-first walk of `document.body` that emits semantic nodes only: landmarks (`header`/`nav`/`main`/`aside`/`footer`, labelled `section`s, `form`, `dialog`, and the same explicit ARIA roles), `alert`/`status`, headings with their level, buttons, links (with their path; external links keep only their origin), form controls with their label (`aria-label`, `aria-labelledby`, `<label for>`, a wrapping `<label>`, or the placeholder), `input_type`, `disabled`, `required`, and `invalid` (`aria-invalid`), tabs, images with their `alt`, and short runs of visible text. Each node gets a `ref` (`e1`, `e2`, ...) valid for that snapshot only, a `depth` (enclosing landmark/form/dialog nodes), and `offscreen: true` when its box is outside the viewport. Plus the viewport: inner width and height, scroll position, `devicePixelRatio`, the colour scheme (the `dark` class on `<html>`), and the active shell (`ui-v2` / `ui-v3` class, else `v1`), and the route and document title.
  - **Never serialized:** the value of any form control (the serializer never reads `.value`, `checked`, or a `<textarea>`'s or `<select>`'s text), password inputs (and inputs any of whose whitespace-separated `autocomplete` tokens is `current-password`, `new-password`, `one-time-code`, or `cc-*`, so `billing cc-number` or `section-checkout one-time-code` count too), `contenteditable` content, the chat panel (`BriefingChat`'s root and the shells' chat asides carry `data-janus-chat`), any `[data-janus-private]` subtree (counted in `skipped_private`), `script`/`style`/`template`/`svg`, and hidden elements (`hidden`, `aria-hidden="true"`, `display: none`, `visibility: hidden`), also when they sit inside a button, link, heading, alert, or label whose name is taken from its text.
  - **Caps:** at most `MAX_SNAPSHOT_NODES` (200) nodes and 5,000 visited elements; names 120 and text runs 160 characters; then nodes are dropped from the end (counted in `omitted_nodes`) until the UTF-8 JSON is at most `MAX_SNAPSHOT_BYTES` (24 KB). The backend enforces the same budget again.
  - **Styles (`elementStyles`):** for a `ref` of the latest snapshot, the element's tag, rounded bounding box, and the computed values of the allowlisted `STYLE_PROPERTIES` only (the same list as the backend); a value with `url(` is left out. An unknown ref, or an element that left the page since the snapshot, answers an error.
- **UI blocks in replies (JAN-252):** a `__UI_BLOCK__:<json>` frame (the server side, catalog, and budgets are in the [runtime spec](background-workers-runtime.md), "Chat UI blocks"; the safety rules in the [safety spec](llm-agent-safety.md#chat-ui-blocks-jan-252)) appends the raw block to the streaming assistant message's `uiBlocks`; messages loaded from `GET /api/conversations/{id}` get the same `uiBlocks` from each stored row's `payload`, so a reloaded conversation renders the same blocks. `src/lib/uiBlocks.ts` holds the zod catalog (`version` 1) with every server budget mirrored, and `parseUIBlock`. `BriefingChat` renders a message's blocks under its text with `UIBlockList` (`components/features/ui-blocks/`), so the sidebar chat, `/chat`, and all three shells share them. `UIBlockView` parses each block at render time and picks the component (`CardBlock`, `TableBlock`, `ChartBlock`, `ChecklistBlock`, `KeyValueBlock`, `ActionsBlock`, `ImageBlock`); a block that does not parse (unknown type or version, a broken budget, a disallowed link) is shown as its pretty-printed JSON in a `<pre>` with a "Could not render this block" note. All block text is rendered as text nodes, never Markdown or HTML. Links go through `BlockLink`: same-origin paths as router links, `https://` URLs in a new tab with `rel="noopener noreferrer"` and their host shown; anything else is not a link. Charts are inline SVG (`ChartBlock`: bars, a polyline, or pie slices with a legend and an accessible data table fallback); the bundle has no chart library and this feature adds none. Images (`ImageBlock`) only load `/api/images/<id>`; otherwise, or when loading fails, the alt text is shown. A loaded image gets a **Download** link (`<a href="/api/images/<id>" download>`) to the same authenticated URL (JAN-253; the image the `generate_image` tool stores is served there with `Content-Disposition: inline; filename=...`). An `actions` button calls the session's `sendText(text)`, which sends an ordinary chat message (with page context) naming the button, the tool, and its arguments; nothing runs client-side, and a clicked button disables itself for the rest of the session.
- **Restore after reload/remount:** the active conversation id is written to `sessionStorage` under a per-user key (reads and writes wrapped in `try/catch`). On mount the provider loads that conversation's messages from `GET /api/conversations/{id}` before opening the socket, so the socket binds to the restored conversation; if the conversation is gone (deleted, or another user's), the stored id is dropped and a fresh chat starts. `sessionStorage` is per tab, which matches the one-session-per-tab model.
- **Per-user session:** the provider is keyed by the signed-in user id. If the account changes without the shell unmounting (another tab logged in as someone else), the previous account's socket is closed and its transcript, draft and conversation are dropped, so nothing leaks into the new account's session.
- **Views are presentational:** `BriefingChat` (`variant="compactSidebar"` or `"fullPage"`) renders the provider's state and keeps only view-local state (scroll position, the Linear issue modal). It must be rendered inside a `ChatSessionProvider`.
- **One chat view per shell:** each shell picks the chat placement with `useMediaQuery('(min-width: 1024px)')` (`src/hooks/useMediaQuery.ts`) instead of rendering a CSS-hidden desktop sidebar and a mobile inline copy at the same time: at `lg` and above the chat is the right-hand sidebar/drawer, below it the chat is rendered inline under the page content.
- **Tool selection note (JAN-300):** `__TOOL_SELECTION__` carries an optional `toolset` (`ToolsetSelection` in `src/contexts/chatSession.ts`: source, reason, offered `tool_ids` or `null` for all, `trimmed`, `widened`, `authorized_count`, `narrowed`). `components/features/ToolsetSelectionNote.tsx` renders it in `ChatTurnStatus` as a `role="note"` named *Tool selection* (deliberately not a second `status` region): the picked tools out of the authorized count, the reason, tools added with `request_tools`, and dropped unauthorized ids, capped at five lines. It renders nothing when the selection did not narrow (fallback, selection off). Covered by `ToolsetSelectionNote.test.tsx`, its stories, and `e2e/chat-tool-selection.spec.ts`.
- **Chat drawer sizing (JAN-220):** in the sidebar (UIv1) and drawer (UIv2/UIv3) the chat panel takes the height left below the recent-chats card (`flex-1` with a `10rem` floor), the recent-chats list is capped at `min(11rem, 15vh)`, and the *Tools unavailable this turn* notice at four lines, so at a 1280×720 viewport a long reply scrolls inside the **Conversation history** region and the composer stays on screen (the JAN-212 failure mode: the old `26rem` floor pushed both out of the fixed drawer). Should the drawer still be too short, the compact chat scrolls as a whole instead of clipping. `e2e/chat.spec.ts` checks this in all three shells.
- **`/chat` page:** `pages/ChatPage.tsx` is the full-width chat surface (conversation list plus transcript) and has a primary-nav entry in all three shells. On `/chat` the shells render no sidebar/inline chat (and hide the UIv2/UIv3 drawer toggle), so the session is never shown twice. The page is pinned to the viewport (JAN-273): its height is `100dvh` minus the active shell's chrome (per-shell offsets via the `uiv2:`/`uiv3:` variants, with the UIv2/UIv3 compact nav row below `md`), and the grids inside it use `minmax(0,1fr)` rows, so only the **Conversation history** region (`role="region"`, keyboard-focusable) scrolls, and it still auto-scrolls to the newest message. The page never grows past the viewport or paints over the footer; on very short viewports a `32rem` floor makes the whole page scroll instead. The `ChatPageLongTranscript` story in each shell's stories file is the visual check. Tool calls are inspected inline under each reply (JAN-249); at `xl` and above the turn timeline (JAN-316, above) fills the page's second column, below `xl` it is the collapsed bar above the chat.


## Live Domain-Change Sync (JAN-104)

- **TanStack Query stays the client truth:** no store mirrors backend state. The backend tells the tab *which domains* changed and the tab invalidates the queries that read them, so the next render refetches.
- **One subscription per tab:** `useDomainChangeSync` (`src/hooks/useDomainChangeSync.ts`) is mounted once in `App.tsx` for the signed-in user (next to `ChatSessionProvider`, above the shell) and opens `/ws/changes` through `startDomainChangeSync` in `src/lib/domainChanges.ts`. The socket authenticates with the same session cookie as `/ws/chat`.
- **Domain map:** `DOMAIN_QUERY_KEYS` in `src/lib/domainChanges.ts` maps each backend domain (`memory`, `topics`, `rules`, `email_clarifications`, `emails`, `planner`, `skills`, `dashboard_widgets`, `trigger_definitions`, `trigger_runs`, `knowledge`, `wiki`, `character`, `tools`, `whatsapp`) to query key *prefixes*. A backend test (`tests/test_domain_changes.py`) fails while this map and `domain_changes.DOMAINS` differ.
- **Debounce and resync:** `changed` frames are collected for 150 ms and invalidated in one pass. After a dropped connection the client reconnects with backoff (1 s doubling to 30 s). Every `ready` frame, the first one included, invalidates every mapped domain once: a commit made after a query loaded but before the subscription existed (or while the socket was down) produced no frame. Only active queries refetch, so this costs one round of the open view's requests per connect. The socket adds no polling; the dashboard widgets keep their existing 30 s refresh as a fallback.
- **Adding a domain:** add the model to `TRACKED_MODELS` (and the name to `DOMAINS`) in `src/janus/domain_changes.py`, then add the domain and its query key prefixes to `DOMAIN_QUERY_KEYS`. Map only data a write can change; preference/status queries that a mutation already sets with `setQueryData` (for example `['wiki-synthesis']`) stay out, so a refetch cannot overwrite a newer value; if such a query must be mapped (`['knowledge-extraction']`, whose token usage changes with background extraction), its mutation cancels in-flight fetches in `onMutate` first.

## Data Management

- **Local state:** React `useState` owns UI-only state such as modals and toggles. The chat session is the one piece of client state shared across pages; it lives in `ChatSessionProvider` (see Chat Session above). The page-context registry next to it (`PageContextProvider`) holds only the current page's snapshot for the next chat message and is never rendered from.
- **Global data state:** TanStack Query manages server state, caching, and background synchronization.
- **Self-sufficient components:** Feature components are responsible for their own data fetching and mutations so they remain portable across pages.
- **API access:** Axios calls are centralized in `frontend/src/api.ts` and use `withCredentials: true`.
- **Server timestamps (JAN-394):** the backend stores every timestamp as UTC and may serialize it without an offset (`2026-11-10T09:00:00`), which `new Date()` would read as local time. Every server-provided timestamp is parsed through `parseServerDate` in `src/lib/serverTime.ts` (an offset-less date-time is UTC; `Z`/`±HH:MM`/`±HHMM` keep their offset; a date-only `YYYY-MM-DD` stays a calendar date at local midnight so the shown day never moves) and rendered in the viewer's locale and time zone with `formatServerDateTime` / `formatServerDate` / `formatServerTime`. A `datetime-local` input is prefilled with `toDateTimeLocalInputValue` (local wall time) and sent back with `new Date(input).toISOString()` (UTC), so the round trip does not shift. Never call `new Date(value)` / `Date.parse(value)` directly on a server string.

## Dashboard and Management UI

- **Rules engine:** The UI supports creating and deleting regex-based prioritization rules.
- **Account integration:** Users connect Google and Microsoft under Settings → Connections (`ConnectionsConfig`; the OAuth client itself is set in `settings.toml`), choose calendars and sync on the Planner page (`/planner`, `pages/PlannerPage.tsx`, `features/planner/`), and add Gmail, Outlook or custom IMAP mailboxes from the Email page (`AccountManager`, also in Settings → Email Accounts).
- **Local planner surface:** The dashboard must show writable local tasks, writable local single-instance events, and pending planner suggestions alongside imported read-only planner context.
- **Widget preferences:** each widget's visibility and collapsed state are per-user preferences (`GET/PATCH /api/dashboard/widgets`), applied optimistically and rolled back when a save fails. Until the saved preferences load, the dashboard shows a loading message instead of the widgets and the **Widget options** menu (`components/features/DashboardWidgetMenu.tsx`) keeps its toggles disabled, so a hidden widget never flashes in and a toggle never acts on the defaults (JAN-225). If loading fails, the defaults are used and a notice says so.
- **Active Tasks hierarchy (JAN-317):** The Active Tasks widget renders the nested tree from `GET /api/dashboard/tasks` (the same `list_local_task_tree` the Todo Chat companion uses) through `components/features/DashboardTaskTree.tsx`. A parent with an open task anywhere in its visible subtree is a category heading (title and the recursive open count, no status control; a done subtask kept as the ancestor of an open one counts as structure, not as open) with its subtasks indented beneath it at any depth; leaf tasks keep their status select; imported tasks follow as read-only flat rows. The chat page context flattens the tree (`lib/taskTree.ts`), so subtasks stay visible to Janus.
- **Deleting local planner items (JAN-221):** Local tasks (leaves and category headings) in Active Tasks and local events in the Schedule widget (`components/features/DashboardScheduleList.tsx`) carry a Delete button; imported rows never do. `hooks/usePlannerItemDelete.tsx` asks through the `ConfirmDialog` ("Delete task?"/"Delete event?"), calls `DELETE /api/planner/local/{tasks,events}/{id}`, refetches the schedule, tasks, and suggestions, and shows the backend's error if the delete fails. A deleted category's subtasks stay and move up a level.
- **AI configuration:** Users can switch dynamically between Ollama, OpenAI, OpenRouter, Gemini, Anthropic, GitHub Models, and Azure OpenAI (JAN-340; their fields live in `GitHubModelsAzureFields.tsx`, their draft/payload helpers and the per-provider secret-status line in `lib/llmProviderDrafts.ts`), and the settings surface must show provider-specific setup requirements and editable connection parameters. Each provider's model field is a searchable `Combobox` populated from the provider's model list (using unsaved draft credentials, debounced), with a plain-input fallback when the list cannot be loaded. Azure OpenAI's deployment picker passes the draft auth mode (`authMode` -> `auth_mode`) so Entra ID lists the resource's deployments without a key (JAN-349), and its API key field appears only in `api_key` auth mode.
- **Briefing settings surface:** The Settings → Daily Briefing tab holds the dashboard briefing widget's additive instructions and optional bounded email window; it points to the **Morning briefing** trigger on Settings → Triggers, where the briefing's main prompt, tools, and schedule are edited. The dashboard briefing panel shows today's briefing run, and its refresh button forces a new run (`/ws/briefing?refresh=1`); the text arrives as one message rather than a token stream.
- **OAuth guidance:** the Connections tab shows the backend-authoritative redirect URIs and the `settings.toml` keys while a provider is not configured, and uses those URIs for Google and Microsoft authorization/callback flows. After the callback, Settings maps its `error` code to a specific message through `src/lib/googleOAuthErrors.ts` (JAN-318; codes listed in [Backend and API](backend-api.md#planner-api-surface)), falling back to `OAuth2 error: <code>`; the code (Google or Microsoft) opens the Connections tab and is dropped from the URL in the same update that writes `tab=connections` (`?tab=planner` still opens it), so a reload does not repeat the error (covered by `e2e/settings.spec.ts`).
- **Mail ingestion settings:** The dashboard exposes a dedicated Mail Ingestion settings surface for initial sync limits. How new mail is judged is the **Mail processing** trigger definition on Settings → Triggers.
- **No agent definitions:** The former "Agent Definitions (Legacy)" Settings tab and `AgentDefinitionManager` are gone (JAN-227); chat shows the user's Character name and no agent picker or handoff list. The Skills tab manages one flat library with no per-agent assignment.
- **Delegated tasks in chat (JAN-208):** `BriefingChat` (every shell's sidebar and `/chat`) renders `DelegatedTasksPanel` above the transcript for the active conversation. It reads `GET /api/conversations/{id}/delegations` through `lib/delegatedTasks.ts` (`delegatedTasksApi`, types from the generated schema) and stays hidden until the conversation has a delegated task; then a collapsed "Delegated tasks (n)" disclosure (with an "n active" badge) lists each task's status, objective, granted tools (and requested tools that were withheld), and result or error, all as plain text. It polls every 5 s while a task is queued or running, re-reads when a chat turn ends, and "Show more" raises the page size by ten up to the endpoint's limit of 100; switching conversations never shows the previous one's rows. A failed load shows a one-line notice.
- **Documents (JAN-242):** the Memory & Knowledge page has a **Documents** tab (`?tab=documents`, `KnowledgeSourcesManager`) that uploads a document (PDF, Word, Excel, PowerPoint, text, CSV, HTML, mail; up to 10 MB) and lists every ingested source with its type, status, chunk count, size, and any skip or failure reason, filterable by type; a failed listing shows an error instead of an empty list. Uploads and attachments can be deleted there; sources rebuilt from mail, chats, events, and tasks cannot.
- **Knowledge (JAN-243):** the Memory & Knowledge page has a **Knowledge** tab (`?tab=knowledge`, `KnowledgeGraphManager`): the Knowledge extraction switch (an in-app `ConfirmDialog` warning names the model before a cloud model is turned on, plus a standing notice while it is a cloud model), the monthly token budget with this month's usage meter, a search card (`KnowledgeSearchPanel`: cited hits with source, date, and why each matched), an entity detail card (`KnowledgeEntityDetail`: relations both ways, clickable to walk the graph, and the sources mentioning it) opened from a hit or the entity list, the merge review queue ("Possible duplicates", Merge / Keep apart), and the entity list with type filter and search. Each part shows its own load error instead of an empty state.
- **Wiki (JAN-244):** the Memory & Knowledge page has a **Wiki** tab (`?tab=wiki`, `WikiManager`, shared by all three shells): the Wiki synthesis switch (cloud warning as for extraction), a searchable page list with proposal and not-written badges, and the open page (`WikiPageView`): rendered Markdown whose `[n]` markers link to the numbered sources, an editor, pending proposals shown next to the current text (Accept / Dismiss), and revision history with Restore. `?tab=wiki&topic=<id>` opens a topic's page; each topic on the Topics tab links there.
- **Knowledge stack views (JAN-334):** `WikiPageView` has **Page** / **Sources** tabs (`role="tablist"`, arrow keys switch). `WikiProvenance` (the Sources tab) reads `GET /api/wiki/pages/{id}/provenance` (query key `['wiki-provenance', id]`) and lists each `[n]` with its raw passage or memory fact, the entities in the cited passages (opening an entity's own page through `WikiManager`'s selection), and the subject's memory facts. A click on a `[n]` marker in the page text is intercepted (capture phase on the `#wiki-cite-n` link) and opens the Sources tab with that citation expanded and highlighted; proposal previews keep the plain anchor. `WikiManager` has a **Pages / Graph** switch; `KnowledgeGraphView` reads `GET /api/knowledge/graph` (`['knowledge-graph']`) and draws an SVG with `lib/graphLayout.ts` (deterministic Fruchterman–Reingold, no new dependency), colors from the Stats charts' `VIZ_STYLE` `--viz-*` slots with a legend (topic, person, organization, other), dashed relation edges, nodes as keyboard-focusable buttons, a selected node's links and **Open wiki page**, and a **Show table** view. Types and calls live in `lib/knowledgeStack.ts`, because `api.ts` and `types.ts` are held at their size. Gray/`dark:` palette classes, `text-primary`/`border-primary`, `Card`, `Badge`, and `Button` reskin it in all three shells.
- **Wiki links (JAN-331):** Janus-authored Markdown (chat replies in `BriefingChat`, the briefing in `BriefingWidget`, wiki pages in `WikiPageView`) renders through `components/features/JanusMarkdown.tsx`. Before rendering, `lib/wikiLinks.ts` (`linkWikiPages`) rewrites `[[Title]]` / `[[Title|label]]` into Markdown links to `/memory?tab=wiki&wiki=<url-encoded title>`, skipping inline code spans, fenced code, embeds (`![[…]]`), escaped `\[[`, explicit links (`[[x]](url)`), purely numeric targets, and targets over 200 characters; the label is backslash-escaped so it is always plain text (no HTML, emphasis, nested links, or images). `WikiPageView` turns `[n]` into `[[n]](#wiki-cite-n)` first; the wiki-link pass leaves those alone. `JanusMarkdown`'s `a` renders app paths (`/…`, not `//…`) as router `Link`s, so following a link does not reload the page (and the chat session survives); other links render as before. In `WikiManager`, `useWikiSelection` (`components/features/wikiLinkTarget.ts`) looks up `?wiki=` with the page search and opens the exact title (case-insensitive) or slug match; with no match the tab shows "No wiki page called … yet" with the name in the search box. A page picked by hand and typed search text are tied to the router location key, so the next followed link always wins.
- **Setup flow:** When first-run setup is required and public setup is enabled, the frontend redirects to `/setup` so the user can create the initial admin with the printed token.

### Tools and Connections

The authenticated Settings page exposes the `Tools` tab through the
`MCPRegistryManager` feature component. It renders stable built-in metadata,
owner-scoped HTTP MCP server lifecycle and health state, discovery actions,
OAuth authorization redirects, and per-server/per-tool enablement controls.
The panel states that enablement is eligibility for future runtime selection,
not approval of consequential actions. It does not render credentials or
provider response bodies and provides explicit unavailable/error states for
missing migrations and safe upstream failures. Below it, `A2AAgentManager`
(JAN-404, API client in `a2aApi.ts`) lists the user's remote A2A agents: add
(name, URL, write-only bearer token), switch on (only with a fetched card),
refresh the card, remove. An agent's advertised skills are shown as its own,
unverified description.

The built-in tools are listed by `ToolPermissions` (JAN-255): one row per
registry entry with an **Unattended** select (*Allowed* / *Off*) and, for a
`confirmation="required"` tool, an **In chat** select (*Ask every time* /
*Always allow* / *Off in chat*, JAN-306, JAN-359; other tools show "In chat:
Allowed" without a select), both backed by `PUT /api/tools/policies/{tool_id}`, and a
"changed" marker with **Reset** (sends `null` for both switches) when
either differs from its default. Switching a tool
whose `operation` is not `read` to *Allowed* asks first in an in-app
`ConfirmDialog` ("Allow unattended use?", "…with nobody watching…"; JAN-275);
the select stays *Off* until the user picks **Allow**, and Cancel or Escape
saves nothing. A locked tool (one with a `locked_reason`: today only the
chat-only `triggers.propose`, JAN-235, `ui_actions`, JAN-251, `ui_blocks`, JAN-252, `images`, JAN-253, `ui_introspection`, JAN-283, and `character.propose`, JAN-301, since
JAN-272/274 unlocked `events` and `triggers`, plus `self_evolution` while its own switch is off, JAN-320) shows a lock icon and its reason instead of a select; a row whose integration
is missing says "Not connected yet"; a toolbox with withheld tool names says
"Triggers Janus schedules itself never get: …". The panel explains that the
switch covers triggers, saved automations (including the built-ins), and
background tasks: an off tool is still offered but refused and recorded,
picking a tool on a trigger the user created allows it there, and in chat a
tool marked *Ask every time* asks per call, one marked *Always allow* runs
without asking, and one *Off in chat* is not offered at all. Above the list,
`PolicyModes` (JAN-359) has two selects backed by `GET`/`PUT
/api/tools/policy-modes`: **Unattended** (*Per tool* / *Allow everything*)
and **In chat** (*Per tool* / *Allow all without asking*). Choosing either
*allow* value first asks in a `ConfirmDialog`; a row whose own switch is
overridden by a mode says so ("Allowed unattended by “Allow everything”",
"Runs in chat without asking (“Allow all”)") from the row's
`effective_unattended_allowed` / `effective_chat_asks`.

The trigger definition editor hides locked tools and marks tools that are off
for unattended runs: "off for unattended runs; allowed here if picked" on a
user-authored definition, "off for unattended runs" on a built-in or
Janus-authored one.

### Tool integration cards

Above the built-in tool list, `ToolIntegrationSettings` renders the
admin-configured integration cards, each in its own error boundary:
`WebSearchConfig` (JAN-211) and `SelfEvolutionConfig` (JAN-320). The
self-evolution card (`GET`/`PATCH /api/self-evolution/settings`, typed and
called through `src/lib/selfEvolution.ts`) has the opt-in switch, the Claude
Code Routine trigger id (`trig_...`), a write-only routine API token, a hint
that the routine's prompt must act on the fire payload, the GitHub repository
and the production base URL, then the last 24 hours' usage against each daily cap, the
scope fence, and the newest recorded actions. Every field is read-only for
non-admins, and the badge reads **On**/**Off**. While the settings load the
card shows a spinner; if loading fails it shows the error and a **Retry**
button instead of spinning forever. Stories: `SelfEvolutionConfig.stories.tsx`,
`ToolIntegrationSettings.stories.tsx`; e2e: `e2e/self-evolution.spec.ts`.

### Triggers panel

The authenticated Settings page exposes a `Triggers` tab (internal id
`runtime`) backed by the `RuntimeTriggerManager` feature component. It shows,
top to bottom: the real worker status banner (`GET /api/runtime/triggers/worker`:
running, enabled but not running, or disabled by configuration), a collapsible
help card explaining manual / recurring-schedule / event activation and the
unattended tool policy (linking to the runtime-triggers user doc),
**Suggested automations** (`SuggestedAutomations`, JAN-235, see below), the
`TriggerDefinitionManager`, and the **Recent runs** card
(`TriggerRunHistory`, JAN-256): status / kind / definition (or "Ad-hoc runs",
`definition_id=none`) / from–until filters over `GET /api/runtime/triggers/`,
20 runs per page with Newer / Older and an "x–y of N" count, polling only the
first page (offset pagination shifts rows). Each run is titled with its
`definition_name`; clicking it sets `highlightedId` on
`TriggerDefinitionManager`, which scrolls to and rings that definition.

`TriggerDefinitionManager` (`features/triggers/`) lists the user's trigger
definitions (built-ins first, marked with a "Built-in" badge) with an enable
toggle, **Run now**, **Edit**, **Runs** (that definition's run history via
`GET /api/runtime/definitions/{id}/runs`), and **Delete** for non-built-ins,
which asks through the in-app `ConfirmDialog` first (JAN-224).
The editor collects name, objective, activation kind (manual / schedule /
event), for schedules a daily time plus timezone and optional weekdays or an
interval in minutes (5–10080), for events a subsystem and one of its events
from `GET /api/runtime/definitions/event-catalog` (`TriggerEventPicker.tsx`,
dependent dropdowns offering only catalogued values, with the selected event's
description and payload fields, and a warning for a stored uncatalogued
value; JAN-210) and, below them, the event's optional payload filter and
run limit (`TriggerEventFilterEditor.tsx` with its state hook in
`triggerEventFilters.ts`, JAN-363: "Only when" conditions on the event's
payload fields with *is one of* / *is not one of* / *contains*, each value
held as its own removable chip and added one at a time with Enter or "Add"
(never split on commas, so stored values round-trip unchanged), WhatsApp
chat and contact suggestions from the stored conversations via a
`<datalist>` on the value input, and "Max runs per hour" prefilled from the event's
`default_max_runs_per_hour` for a new trigger), the tools the trigger may use
(built-in capabilities from the tool registry), an optional model
(`ModelChoiceFields`, JAN-233), and enabled. Cards with a model override show
`Model: <provider>/<model>` and a cloud marker for non-Ollama providers. Event
cards summarize the filter and limit (`TriggerEventOptionsSummary`) and, when
the limit dropped events, how many and when the last one was; suggested
automations show the same summary read-only.
Janus's one-time follow-ups (`origin="agent"`, `schedule.type="once"`,
JAN-257) render with "By Janus" / "One-time follow-up" badges, "Once at …",
and their objective; they offer only **Cancel follow-up** (`enabled=false`)
and **Delete** — no toggle, Run now, or Edit. Follow-ups that are cancelled or
past their time are collapsed behind "Show N finished Janus follow-ups" (judged
at the last definitions fetch), unless one is the highlighted definition. A built-in's
activation kind cannot be changed, and built-ins cannot be deleted. The old
ad-hoc "queue a trigger" form (task id, kind, idempotency/schedule/event
identity fields) was removed; ad-hoc runs are still accepted by the API and
by the agent's own trigger tools and appear in **Recent runs**.

`SuggestedAutomations` (`features/triggers/`, JAN-235) renders only when Janus
has drafted something (`GET /api/runtime/definitions/proposals`). Each open
proposal is a card with the name, a "Suggested by Janus" badge, the activation
and first occurrence in the proposal's timezone, the objective verbatim, the
rationale under "Janus says" (plain text, never markup), a "Drafted after
reading … content" chip from `untrusted_context`, and every tool; a tool in
`blocked_tool_ids` is highlighted with its own **Allow** checkbox (unticked by
default; unticked ones are left out of the approved `tool_ids`). A replacement
(`supersedes`) shows a side-by-side objective / tools / schedule diff. Actions:
**Approve** with **Enable now**, **Edit then approve** (the exported
`DefinitionEditor` with a "Save and approve" button, posting the draft to the
approve endpoint), and **Reject** with an optional reason. A **History**
disclosure lists the last five decided proposals. Approved proposals appear in
`TriggerDefinitionManager` with a "Suggested by Janus, approved" badge and
edit like user-authored ones. `useOpenProposalCount`
(`triggerProposalCount.ts`, polling `GET .../proposals/count` every 30 s)
feeds a `NavBadge` on the primary **Settings** entry of all three shells (via
`useNavItems`) and on the **Triggers** section entry of `PageSectionNav`. Every proposal query shares the `['trigger-proposals']` prefix, which the
`/ws/changes` map (`lib/domainChanges.ts`) invalidates on `trigger_definitions`
changes (proposals are definition rows) and on `tools` changes (blocked tools
follow the unattended switch), so the list, History, and badge refresh live.

The run list displays only the authenticated user's trigger records, one
self-contained `TriggerWidget` (`frontend/src/components/features/triggers/`)
per run rather than a flat row. Each widget shows a coloured status rail, a
kind badge and a status badge, the run's definition name (or "Ad-hoc run" /
"Janus follow-up"), the task ID as secondary metadata, created/claimed/completed
timestamps, run duration, attempt count, the next retry time when delayed, a
**Result** block once the run has actually executed (which tools were
authorized, which were actually invoked, any refused or failed tool calls, and
Janus's summary), and a latest error when available — the error persists even
after a retry has cleared the live failure, so the widget still shows why the
earlier attempt failed. Stored request payload and private claim-token data
are never rendered or returned in this view. `exhausted` is terminal and tells
the user that automatic retries have stopped. `waiting` (JAN-207) is a run
parked until the tasks it delegated finish: a violet, non-spinning badge, and
the card style adds a "resumes automatically" note; it is one of the Recent
runs status filter options.

`TriggerWidget` exposes three explored styles (`row`, `card`, `timeline`)
behind one `widgetStyle` prop, all driven by a shared
`triggerPresentation.ts` module so status colours and badges stay identical
across shells; `RuntimeTriggerManager` wires in `card` as the recommendation.
`uiv2:`/`uiv3:` Tailwind variants re-skin the widget for the Terminal and
Rusty Console shells without forking the component, and `TriggerWidget.stories.tsx`
plus `docs/screenshots/runtime-trigger-widget/` in the repository document the
style exploration (see the Alternate UI shells feature spec).

The run list polls the authenticated runtime-trigger GET endpoint every five
seconds (the worker status every fifteen). While the backend endpoint or
required migration is unavailable, the panel shows: “Runtime trigger API is
unavailable. Apply the backend migration and release before using this
panel.” This message is a rollout signal, not an indication that a trigger
was accepted.

A queued record is not a guarantee of immediate execution because it may be
waiting for its scheduled occurrence, the worker poll interval, or a retry
backoff window.

### Agent Tasks page (JAN-204)

`/tasks` (`pages/TasksPage.tsx`, a lazy route chunk) is the durable view of
what the plan-driven runtime did outside chat, linked as **Agent Tasks** in the
shared primary navigation (`useNavItems`), so it appears in all three shells.
It is deliberately limited to persisted state no other surface shows: trigger
runs, automation (definition) runs, and delegated child tasks with their plan
steps and tool trace (see [Runtime Task API](backend-api.md#runtime-task-api-jan-204)).
Live chat turns stay on the Chat page (turn timeline, JAN-316); definitions
stay on Settings → Triggers; a conversation's delegated tasks in chat are
JAN-208. The page links to those views instead of repeating them.

- **Layout:** `components/features/tasks/TaskList.tsx` (source and status
  filters, 20 tasks per page with Newer / Older and an "x–y of N" count; only
  the first page polls, every 5 s and only while a listed task is queued or
  running) beside `TaskDetail.tsx` (status, facts, objective, recorded errors,
  `TaskStepList.tsx` for the steps and their trace, including the
  brain-selected *Tool selection* line of JAN-300, delegated children). `TaskStatusBadge.tsx`
  reuses the trigger widgets' `STATUS_PRESENTATION`. Types and the API client
  live in `lib/runtimeTasks.ts` (hand-written to mirror
  `schemas/runtime_tasks.py`).
- **Selection in the URL:** `?kind=trigger|delegated&task=<id>` opens a task,
  so rows, the parent/child links, and Settings → Triggers → Recent runs
  ("Plan steps and tool trace", rendered through `TriggerWidgetList`'s
  `renderActions`) are plain links and a reload keeps the task open. A detail
  that answers 404 (unknown, or another user's) reads "This task does not exist
  or is not yours." and is not retried.
- **Trace rendering:** authorized and invoked tools are chips; refused calls,
  failed calls, unavailable tools, and suspected prompt injection are flagged
  lines. A step's answer is untrusted model text, collapsed under **Answer**
  and rendered as plain text only.
- **Links out:** a definition run links to Settings → Triggers; a delegated
  task links to the run that spawned it and, when it came from chat, to
  `/chat?conversation=<id>`. `hooks/useConversationDeepLink.ts` (used by
  `ChatPage`) opens that conversation once the tab's restore has finished and
  drops the parameter.
- **Page context:** the page publishes the listed tasks' titles and the open
  task's id (JAN-250); it registers no page actions.

### Character constitution editor

## Stats tab (JAN-258)

Settings → **Stats** (`?tab=stats`, `pages/StatsPage.tsx`, in the monitoring
group next to System Status) reads `GET /api/stats?days=7|30|90&timezone=<browser IANA zone>`
(default 30, refreshed every minute). It shows headline tiles (new mail,
trigger runs with success rate, active memory facts, messages), then one card
per area: mail per day stacked by priority; trigger runs per day stacked by
outcome plus a per-definition table (runs, success rate, failed, average
claim-to-completion time, last run; ad-hoc runs grouped as one row); memory
fact / topic / wiki / knowledge-source counts with new facts per day; an
**Approvals** card (`features/stats/ApprovalStatsCard.tsx`, JAN-371) with one tile
each for suggested automations, tool approvals in chat, and character changes
(approval rate, outcome counts, median time to decide where recorded, and a
note that a rate near 100% can also mean rubber-stamping); an **LLM usage**
card (`features/stats/LlmUsageCard.tsx`, JAN-408) with call and token tiles
(including calls without token counts), tokens per day, and tables by surface
and by model; and WhatsApp received/sent plus chat you/Janus messages per day. Charts are
`features/stats/DailyBarChart.tsx`: dependency-free SVG stacked columns with a
per-day hover tooltip, a legend for multi-series charts, and a **Show table**
view. Categorical series use the validated four-slot palette and run outcomes
the reserved status palette, as `--viz-*` CSS variables switched under `.dark`.

The Settings page exposes a `Character` tab backed by the
`CharacterProfileManager` feature component. The editor lets the authenticated
user update the character name and identity, enter core values and
non-negotiable boundaries one per line, and save the constitution. Identity,
values, and boundaries are stable constitution rules; they do not grant tools,
permissions, or authority.

Two free-text fields follow (JAN-230): **Additional instructions**, appended
to the rendered constitution for chat and every trigger run, and **Memory
instructions**, which replace the built-in rules Janus uses after each chat
turn to decide whether to store a memory fact (empty = defaults). A **Prompt
preview** button calls `POST /api/character/preview` with the unsaved draft and
shows the exact system prompt and the effective memory rules.

The editor also exposes a bounded exploration control from `0%` to `100%`.
This controls how often Janus may explore configured adaptive tendencies, but
cannot change identity, authority, safety rules, or user controls. If no
tendencies are configured, the setting is retained for future trait controls.

Reads and saves use the authenticated, versioned `GET/PUT /api/character`
contract. A missing persisted profile is rendered from the API's safe default;
the first successful save creates version `1`, and later saves advance the
constitution version. The API remains the source of truth for validation and
ownership, so rollout of the editor should preserve the safe default path and
avoid treating a successful save as evidence that adaptive behavior is active.

The rendered constitution is the system prompt for chat, every trigger run,
and every delegated task, together with the adaptive state below.

**Adaptive state (JAN-301).** Below the constitution editor the tab renders
`features/characterState/CharacterStateManager.tsx` (API client and types in
`lib/characterState.ts`, query keys `character-state`,
`character-state-history`, `character-state-proposals`, all invalidated by
the `character` domain of `/ws/changes`). It has four cards:
**Proposed by Janus** (open proposals with their changes and rationale,
Approve/Reject), **Current adaptive state** (tone, verbosity and proactivity
selects limited to the allowed values, a single-line focus input capped at
`focus_max_chars`, Save state, Reset to defaults), **History** (each audit row
with who changed what and the state before, and Restore on every earlier
revision; pages of 50 through `useInfiniteQuery`, with **Load older changes**
while the API returns a `next_before_id`), and **Bounds and defaults** (allowed values per dimension, the
default, whether a focus is allowed). A refused change (409) shows the
server's reason in an alert. Proposal text and the focus are untrusted and
always rendered as React text, never as HTML. Stories: one per
component (`CharacterStateManager`, `CharacterStateProposals`,
`CharacterStateEditor`, `CharacterStateHistory`, `CharacterStateBounds`); e2e: `e2e/character-state.spec.ts`.

## In-App Documentation UI

- **Frontend renderer:** The `Documentation` page uses `react-markdown` with `remark-gfm` to render fetched Markdown with GitHub-Flavored Markdown support.
- **Navigation parity:** The documentation sidebar reads `/api/docs/nav` so it mirrors `docs/zensical.toml`.
- **Technical spec surfacing:** The sidebar exposes the repository-root `TECH_SPEC.md` as the technical-spec landing page and exposes child specs under `tech-spec/`.

## Component Stories and End-to-End Testing

- **Storybook coverage:** Every component under `frontend/src/components/` (`ui/`, `features/`, `layout*/`) ships its own `*.stories.tsx` covering its meaningfully different states (loading, empty, error, populated, variants), not just a single default render. See `frontend/CODEMAP.md` and `AGENTS.md`'s testing conventions.
- **Storybook deployment:** `npm run build-storybook` produces `frontend/storybook-static/`, which the backend serves at the admin-only `/dev/storybook` route (`src/janus/api/dev.py`) so the live component catalog is reachable on a deployed instance without a separate hosting setup.
- **End-to-end tests:** `frontend/e2e/` holds Playwright specs run via `npm run test:e2e` (or `task test:e2e:frontend`). `playwright.config.ts` boots a real backend against a throwaway SQLite database (isolated XDG dirs, a bootstrapped admin user, a shared logged-in browser storage state) so specs exercise real UI flows rather than mocked ones. The data dir and default port come from a hash of the checkout path, so separate checkouts can run the suite at the same time; `E2E_PORT`, `E2E_DATA_DIR`, and `TMPDIR` override them (JAN-291, see `frontend/e2e/CODEMAP.md`). The e2e backend imports a scripted fake of the `neonize` package (`frontend/e2e/fake-neonize/`, put first on its `PYTHONPATH` by `e2e/env.ts`) instead of the real WhatsApp client library, so WhatsApp specs drive pairing and message events through files and never open a WhatsApp connection (JAN-223). On CI the suite runs in 4 shards, each with its own backend and database, and a follow-up job merges their blob reports into one HTML report (JAN-345); `task test:e2e:frontend -- --shard=N/M` reproduces one shard locally. Specs that need a model answer point the backend's OpenAI provider at `e2e/llm-stub.ts`, a scripted OpenAI-compatible server in the test process, so `/ws/chat` and `/ws/briefing` run end to end with fixed model output and no outside call (JAN-220); specs that only check how the UI renders given frames replay `/ws/chat` with `page.routeWebSocket` instead. External services the backend itself calls are never reached: `e2e/fake-upstream.ts` serves a local fake Google and Jira (JAN-221) on a port fixed per checkout (`E2E_FAKE_UPSTREAM_PORT`), the backend reaches the fake Google through `JANUS_GOOGLE_API_BASE_URL`, and the browser's consent redirect to `accounts.google.com` is answered by `page.route`. Azure OpenAI endpoints must be `https://`; `e2e/env.ts` sets the test seam `JANUS_AZURE_OPENAI_ALLOW_LOOPBACK_HTTP` so the backend accepts the plain-http loopback fake in `fakeIntegrations.ts` (JAN-349). Specs never depend on what other specs left on the shared e2e admin (JAN-336): a spec whose data is per user (memory facts, topics, widgets, mail, triggers, conversations, briefing runs) runs as its own throwaway non-admin user through `test.use({ asThrowawayUser: true })` from `e2e/fixtures.ts`, which signs `page` in before any hook runs and names the user in the `account` fixture, so its empty states and counts are its own. Instance-wide settings (the backend and utility model, semantic search, image generation, integrations) are changed through the worker-scoped `adminRequest` fixture and reset in `afterEach`. One `e2e/helpers.ts` implementation creates and signs in users (`createThrowawayUser`, `signInAs`, and `signInAsNewUser` for a second user in its own browser context), and one `e2e/backendSeed.ts` `runBackendPython` runs a Python snippet against the e2e database as a named user (asynchronous, adding its start-up time to the test's timeout); its `seedMail` seeds a throwaway user's mailbox. Every new piece of UI functionality ships with a spec here in the same change.

## Validation and Resilience

- **Generated API types (JAN-139):** The frontend keeps the backend's OpenAPI schema and the types generated from it under `frontend/src/generated/`: `openapi.json` and `api.d.ts` (`openapi-typescript`). Both are committed and never edited by hand.
  - Generation is offline and independent of production docs exposure. `task openapi:export` (`scripts/export_openapi.py`) builds the schema from the FastAPI app object in-process, without Uvicorn, startup hooks (database upgrade, setup token), workers, a database, or the network, so it works with `public_api_docs_enabled = false`.
  - `task openapi:types` (`npm run generate:types`) turns the committed schema into `api.d.ts`; `task openapi:generate` runs both.
  - `task openapi:check` (CI job `openapi-check`) fails when either file drifts from the backend, so a route or schema change ships with its regenerated client types.
- **Same-origin production networking:** Production frontend builds should default API and WebSocket traffic to the current browser origin, while allowing explicit absolute overrides for local development or split deployments.
- **Runtime validation:** Critical API responses, at minimum login/session state, email list, and dashboard summary, must be validated with Zod before the UI consumes them. Validation lives only in the API client: `src/api.ts` and the `src/lib/*` API modules (`runtimeTasks.ts`, `delegatedTasks.ts`, `characterState.ts`, `knowledgeStack.ts`) wrap a request with `parseResponse(schema)` from `src/lib/apiValidation.ts`; components never parse responses themselves. The schemas live in `src/lib/schemas.ts` (auth, dashboard, email, planner, status), `src/lib/knowledgeSchemas.ts`, `src/lib/runtimeSchemas.ts` and `src/lib/opsSchemas.ts`; new schemas go into those `lib/` modules, not the size-held `api.ts`/`types.ts`. The shared axios instance (cookies plus the CSRF header) lives in `src/lib/httpClient.ts` and is re-exported as `api.ts`'s default export.
  - **Coverage.** JAN-142 covered, besides the dashboard, planner settings/suggestions, status and health: the signed-in user and the shell preference (`/api/auth/me`, `/api/auth/me/ui-preference`), the password-change acknowledgement, email accounts and their connection test, rules, clarifications and their approve/dismiss answers, a reclassified email, planner accounts, created/updated local tasks and events, the LLM connection test, and the LLM model listing. JAN-335 extended it to every remaining group whose answer the UI renders or treats as a mutation's success state: topics (list, detail with evidence, suggestions), memory facts and compaction runs, conversations (list with the Janus-started `origin`/`unread` of JAN-319, and detail with stored tool calls), knowledge sources, extraction, entities, merge suggestions, search and entity detail, wiki pages, revisions and synthesis, wiki provenance and the knowledge graph; runtime trigger runs, the worker status, trigger definitions, the event catalog, proposals and their count; the tool registry, per-tool policies, MCP servers, discovery, tool toggles and the OAuth start; Agent Tasks pages and details (JAN-204, including each step's JAN-300 `tool_selection`), a conversation's delegated tasks, the character profile and preview, and the adaptive character state, history and proposals (JAN-301); WhatsApp accounts, setup sessions, completion, connector restart and test, conversations and messages (also the dashboard's); Google calendars and the OAuth URL (which must be an `http(s)` URL, since the browser is sent to it); the system event log and its sources, error groups and compaction, and the stats summary. The JAN-300 tool selection shown in chat arrives over `/ws/chat`, not REST, and is parsed by `chatFrames.ts`.
  - **Deliberately unvalidated.** `authApi.setup` (one-shot and already committed when it answers, so rejecting a drifted answer would report a finished setup as failed), `authApi.logout` and `authApi.unlinkGoogle` (their answers are never read and their failures are ignored), `DELETE`s and other answers the UI ignores, and plain-text docs pages. Skills, GitHub/Linear/web-search settings, self-evolution and the injection classifier are not validated yet.
  - **Failure behavior:** a response that fails its schema rejects with an `ApiValidationError` (method, route path, and each issue's field path, code, and expected type or values), which TanStack Query surfaces like any failed request, and logs one `console.error` line starting `[api] Response validation failed: <METHOD> <path>`. Neither carries the response body, a received value, or the query string, so mail content, credentials, and search terms never reach the console or an error message. The Playwright suite fails any test whose page logs that line (`frontend/e2e/fixtures.ts`), so every e2e flow checks the schemas against the real backend.
  - **When a new API function needs a schema:** add one when the UI renders from the answer or treats it as a mutation's success state (lists, detail views, saved objects, `{status, message}` results); a request whose body the UI ignores (most `DELETE`s) and plain-text answers (docs pages) stay generated-type-only. Every schema is a `z.looseObject` (JAN-335 moved the older `z.object` ones too) so a field the backend adds passes through instead of being stripped (`z.object` silently drops unlisted fields), list every field the UI reads, and the wrapper's return type is annotated with the hand-authored type from `types.ts` (`Parsed<T>` from `lib/apiValidation.ts`), so `tsc` proves the schema output fits it. Each new schema gets a valid and a malformed case in `src/api.validation.test.ts`.
  - **Degrading instead of failing:** a field whose unknown value has a safe default may fall back instead of failing the whole answer, e.g. an unknown `ui_version` from a newer backend becomes the default shell rather than signing the user out.
- **Models per surface (JAN-233):** Settings → AI & System renders `SurfaceModelConfig` below `LLMConfig`: a Chat and a Utility `ModelChoiceFields` (provider select with "Default model", then `LLMModelSelect` for the chosen provider), saved through `PATCH /api/planner/settings`. Every non-Ollama choice shows a cloud hint naming what data goes to that provider, and every explicit choice states that an unusable model fails instead of falling back.
- **Semantic search (JAN-241):** Settings → AI & System renders `EmbeddingConfig` below `SurfaceModelConfig`: an embedding provider select (Off, Ollama, OpenAI, Google Gemini), a model input whose placeholder is the backend's suggested model, and a 0–1 minimum-similarity input under an **Advanced** disclosure (collapsed at the `0.5` default, opened automatically for a stored custom value; JAN-245), saved through `PATCH /api/planner/settings`. It shows a cloud hint for non-Ollama providers and a notice when `vector_search_available` is false. `plannerSettingsSchema` (Zod) lists and type-checks the embedding and per-surface model fields; since JAN-335 it is a `z.looseObject`, so an unlisted field passes through unchecked instead of being stripped.
- **Image generation (JAN-253):** below it, `ImageGenerationConfig` (rendered with `SurfaceModelConfig` and `EmbeddingConfig` by `AdditionalModelSettings`) picks the cloud image provider (Off, OpenAI, Google Gemini, OpenRouter) and a model whose placeholder is the backend's default (`image_default_models`), saved as `image_provider`/`image_model` through `PATCH /api/planner/settings`. It always shows the cloud hint (the prompt is sent to the provider), the per-user `image_storage_limit_mb`, and a notice when the provider's `*_api_key_configured` flag is false. The fields are typed in `src/lib/imageGeneration.ts` (`PlannerSettings` extends them) and listed in `plannerSettingsSchema`.
- **Settings validation:** AI settings responses must include enough typed provider-specific data for the frontend to render the correct form fields and configured state per backend.
- **Error boundaries:** The app must provide a top-level error boundary and feature-level boundaries around higher-risk widgets such as briefing chat, rule management, and email lists.
- **Skeleton and empty states:** Dashboard summaries and email lists must render explicit loading placeholders and purpose-specific empty states rather than generic spinners alone.
- **Planner provenance:** Task and schedule UI must distinguish Janus-owned writable items from imported read-only items with visible provenance metadata.
- **Resilient chat UI:** The chat surface must expose connection failures clearly, provide a reconnect action, and cap retained chat history to keep long sessions bounded.
