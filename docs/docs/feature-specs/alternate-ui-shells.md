# Alternate UI shells (UIv2, UIv3)

**Status:** Shipped
**Linear:** JAN-174 (UIv1/UIv2),
JAN-177 (UIv3)
**Depends on:** Dashboard authentication (`/api/auth/me`); no other feature spec.

## Feature definition

Janus ships three interchangeable app shells and lets each user pick one from
**Settings → Appearance**:

- **Classic (UIv1)** — the original shell: a blurred wallpaper backdrop,
  translucent glass header/sidebar/footer, gradient wordmark, left sidebar
  navigation, and an ambient chat panel on every page except the full-width
  **Chat** page (`/chat`).
- **Terminal (UIv2)** — a high-contrast console alternative: a near-black CRT
  ground washed with a faint lime grid, a top bloom and scanlines; hard-edged
  panels with no rounded corners and a phosphor edge; monospaced uppercase
  chrome (JetBrains Mono) with indexed navigation slots, a blinking caret in the
  wordmark and a route/status readout strip; acid-lime accents with glow on
  active elements; and a toggleable right-hand chat drawer (absent on `/chat`).
- **Rusty Console (UIv3)** — a weathered hacker terminal: a warm almost-black-red
  ground (`#0d0605`) stained with corrosion blooms, dusted with film grain and
  burned in at the edges by a heavy vignette, with a slow roll band drifting
  down the tube instead of UIv2's fixed scanlines; oxidised rust-red rules
  (`#5a2618`, 1–2px) around every panel, card and navigation key, which are the
  shell's structure rather than a decorative accent; a dusty rust accent
  (`#c1451f`, bright `#e35c33`, amber ember `#d9873f`); and typewriter
  lettering — Courier Prime for body and Special Elite for the chrome. It shares
  UIv2's top-bar-plus-chat-drawer geometry but boxes each nav item in its own
  rust-ruled key instead of underlining an active tab.

Every shell renders exactly one chat view of the tab-wide chat session
(JAN-248): the right-hand panel/drawer at the `lg` breakpoint and above, inline
below the page content on narrower screens, and none on `/chat`, which is the
full-width view of the same session. See
[Frontend architecture → Chat Session](../tech-spec/frontend.md#chat-session).

All shells are dark. Only the chrome differs — every page and every feature
component is shared and rendered unchanged by all three shells, and long-form
content areas keep a calm, readable palette inside the bold chrome.

Reference screenshots of the shells live in `docs/screenshots/ui-shells/` in the
repository. They are kept outside `docs/docs/` on purpose: the in-app
documentation viewer serves Markdown as plain text and cannot serve binary
assets, so an inline image would render as a broken link there.

Shared feature components carry their per-shell treatment inline through the
`uiv2:` / `uiv3:` Tailwind variants rather than being forked per shell. The
runtime trigger widget (`frontend/src/components/features/triggers/`) is the
worked example: `npm run storybook` in `frontend/` opens a
*Features → Runtime triggers → TriggerWidget* exploration that renders every
candidate style in all three shells, and reference captures of it live in
`docs/screenshots/runtime-trigger-widget/`.

## User value

- Users who dislike the ambient glass aesthetic get denser, higher-contrast
  alternatives — a cold phosphor console or a warm corroded one — without
  losing any functionality.
- The preference is stored server-side, so it follows the user across browsers
  and devices.
- Switching is instant and reversible, with three independent escape hatches if
  a shell misbehaves.

## In scope

- `users.ui_version` column (`VARCHAR NOT NULL DEFAULT 'v1'`), Alembic migration
  `2be48bcaff15`. Adding UIv3 needed no migration: the column is a plain string
  with no check constraint, so only the Pydantic `Literal` widened.
- `ui_version` on the `GET /api/auth/me` payload.
- `PATCH /api/auth/me/ui-preference` on the existing auth router, accepting
  `{"ui_version": "v1" | "v2" | "v3"}` and returning the updated user.
- `UIVersionProvider` / `useUIVersion` above the router, choosing between
  `Layout` (v1), `LayoutV2` (v2) and `LayoutV3` (v3).
- `frontend/src/components/layout-v2/` — `LayoutV2` and `AppTopNav`.
- `frontend/src/components/layout-v3/` — `LayoutV3` and `AppTopNavV3`.
- A shared `useNavItems()` hook so every shell renders identical navigation data.
- Tailwind v4 `uiv2:` and `uiv3:` variants plus `--color-v2-*` / `--color-v3-*`
  design tokens and the `.ui-v2`- and `.ui-v3`-scoped stylesheets (backdrop
  layers, geometry, typography) in `frontend/src/index.css`, including the
  JetBrains Mono (400/700), Courier Prime (400/700) and Special Elite web-font
  imports used only by alternate-shell chrome.
- An **Appearance** tab in Settings listing every shell.
- An anti-flash bootstrap script, `frontend/public/ui-shell-bootstrap.js`, that
  `frontend/index.html` loads as a blocking same-origin script (the CSP blocks
  inline scripts, JAN-225).

## Out of scope

- A user-controlled light/dark toggle. Every shell is always dark; the choice
  is an identity, not a brightness setting.
- A parallel `components/ui-v2/` (or `-v3`) primitive set. The shared primitives
  are reskinned in place via CSS custom properties and the `uiv2:` / `uiv3:`
  variants.
- Per-shell differences in page content, routing, or data fetching.

## Architecture

### Shell swap

`App.tsx` wraps the router in `UIVersionProvider` and renders the shell through
`AppShellHost`, which resolves it from a lookup table of dynamic imports keyed on
the UI version (`frontend/src/components/appShells.ts`, JAN-179):

```ts
export const appShellRegistry = createAppShellRegistry({
  v1: withStaleChunkReload(() => import('./layout/Layout')),
  v2: withStaleChunkReload(() => import('./layout-v2/LayoutV2')),
  v3: withStaleChunkReload(() => import('./layout-v3/LayoutV3')),
});
```

Each shell is its own chunk, fetched only when rendered or prefetched: a session
downloads just its own shell unless the Appearance picker is opened, which
prefetches all three so a switch commits in one render. While a newly selected
shell loads, the previous one stays on screen.

Every shell accepts the same `{ children, currentUser }` props, so the swap is a
single component substitution and adding a shell is a new table entry rather
than a new branch. `Layout.tsx` and `AppSidebar.tsx` are UIv1's implementation
and are not shared with the alternate shells.

The set of shells is declared once, in `frontend/src/contexts/uiVersion.ts`:

```ts
export const UI_VERSIONS = ['v1', 'v2', 'v3'] as const;
export const DEFAULT_UI_VERSION: UIVersion = 'v1';
export const UI_VERSION_HTML_CLASSES: Record<UIVersion, string | null> = {
  v1: null,
  v2: 'ui-v2',
  v3: 'ui-v3',
};
```

That table drives the type, the localStorage and `?ui=` validation, and the
`<html>` class bookkeeping, so none of those carry a hardcoded two-shell
assumption. `frontend/src/types.ts` and `api.ts` re-use the same `UIVersion`
type rather than repeating the union.

### Reskinning shared components

The provider keeps `dark` on `<html>` at all times and adds at most one shell
identity class, clearing the others:

| Shell | `<html>` classes |
| ----- | ---------------- |
| UIv1  | `dark`           |
| UIv2  | `dark ui-v2`     |
| UIv3  | `dark ui-v3`     |

`dark` is always present, so the shared feature components always render their
dark palette. `ui-v2` layers the terminal identity on top; `ui-v3` layers the
rusty-console identity. Each identity layer supplies its own `--color-v*-*`
tokens, a rule flattening every non-circular `rounded-*` utility to square
corners, a retint of the shared `text-primary`/`bg-primary`/`border-primary`
brand accent, its own heading/form/label typeface, and its own focus ring,
selection colour and scrollbar.

The bold chrome is deliberately bounded: the backdrop wash, the command bar and
the panel edges carry the identity, while body copy stays on the shared dark
palette at full contrast.

Two shared primitives (`Card`, `CollapsibleCard`) previously hardcoded the glass
look in **inline styles**, which no class-based variant can override. They now
read CSS custom properties (`--janus-card-bg`, `--janus-card-border`,
`--janus-card-blur`, `--janus-card-footer-bg`) that are redefined under `.ui-v2`
and `.ui-v3`. Everything else uses the Tailwind v4 shell variants:

```css
@variant uiv2 (&:where(.ui-v2, .ui-v2 *));
@variant uiv3 (&:where(.ui-v3, .ui-v3 *));
```

The same shared primitives carry both treatments side by side — `Button`,
`Card`, `CollapsibleCard`, `PageSectionNav`, the Emails filter pills, the
`BriefingChat` bubbles and the Appearance cards. `Badge`, `Input` and `Modal`
need no per-shell classes: they use the shared brand utilities and form
elements, which each identity layer already retints globally.

UIv3's destructive `Button` variant is deliberately a cooler, brighter red
outline (`#a33a3a` / `#f0938c`) rather than the shell's rust fill, so "danger"
still reads as different from "primary" inside an already-red shell.

### Persistence and precedence

The effective shell is resolved in this order:

1. **`?ui=<version>` query parameter** — pins the shell for the current page
   load, bypassing both the cache and the backend. Unknown values are ignored.
2. **`localStorage['ui_version']`** — a write-through cache, read by the inline
   bootstrap script in `index.html` before React mounts so the page never
   flashes the wrong chrome. The script carries its own copy of the shell table
   and must be kept in sync with `uiVersion.ts`.
3. **`GET /api/auth/me` → `ui_version`** — the source of truth. When it
   disagrees with the cache, the cache and the `<html>` classes are silently
   updated to match.

Changing the setting applies optimistically (instant re-render + localStorage
write) and PATCHes the backend in the background; a failed PATCH rolls the shell
back and surfaces an error.

## Rollback

Three independent layers, in increasing order of desperation:

1. **Settings → Appearance** — pick *Classic (UIv1)*.
2. **`?ui=v1`** — append it to any URL to force the classic shell for that page
   load, without touching the stored preference.
3. **Crash recovery** — the top-level `ErrorBoundary` fallback offers
   *Reset to classic UI (v1)*, which clears the cached preference and reloads
   with `?ui=` set to `DEFAULT_UI_VERSION`. Because the boundary sits above the
   router, this works even when an alternate shell itself is what crashed, and
   it stays correct however many shells exist.

## Testing

- Backend (`tests/test_auth.py`): default value, UIv2 and UIv3 round-trip
  persistence, switch back to v1, rejection of unknown values, and
  authentication enforcement.
- Frontend: `UIVersionContext.test.tsx` (precedence, write-through cache,
  `<html>` classes including the v2↔v3 swap leaving no stale class, `?ui=`
  overrides for every shell), `LayoutV2.test.tsx`, `AppTopNav.test.tsx`,
  `LayoutV3.test.tsx`, `AppTopNavV3.test.tsx`, `ErrorBoundary.test.tsx` (kill
  switch), and Appearance-tab cases in `Settings.test.tsx`.
