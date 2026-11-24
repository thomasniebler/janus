# Frontend Code Map

Scope: the React/Vite dashboard project in `frontend/`.

## Responsibilities

- build and lint the dashboard application
- own the React source tree, typed API client, and shared UI primitives
- emit the production bundle served by the backend

## File and submodule map

| Path | Responsibility | Read next |
| --- | --- | --- |
| `src/` | Application source tree, pages, components, contexts, and typed API helpers | [`src/CODEMAP.md`](src/CODEMAP.md) |
| `index.html` | Vite HTML entry; a blocking `<script src="/ui-shell-bootstrap.js">` in `<head>` applies the cached UI shell class before React loads (anti-flash). No inline scripts: the backend's CSP (`script-src 'self'`) blocks them (JAN-225) | Read directly |
| `package.json` | Frontend package scripts and dependencies; `generate:types` writes `src/generated/api.d.ts` from the committed `src/generated/openapi.json` (no running backend), and `generate:types:check` fails when it is stale (JAN-139) | Read directly |
| `vite.config.ts` | Vite dev/build configuration, including the shared `icons` code-splitting group for the lazy-loaded shells and pages (JAN-179) | Read directly |
| `eslint.config.js`, `eslint.baseline.json` | ESLint rules, including the complexity and size limits for production code (JAN-259, JAN-264), and the measured per-file baseline of hotspots that may shrink but not grow | Read directly |
| `vitest.config.ts` | Vitest test runner configuration (jsdom, setup file) and V8 coverage (scope, reports to `coverage/`, floors), merged with `vite.config.ts` | Read directly |
| `playwright.config.ts` | Playwright e2e configuration: spins up a real backend on a throwaway SQLite DB, provisions an admin user, and reuses a logged-in browser storage state across specs | Read directly |
| `e2e/` | End-to-end (Playwright) specs, plus `env.ts` (shared runtime config), `global-setup.ts` (admin bootstrap + login), and `fixtures.ts` (the `test` every spec uses, which fails on an API response that broke its client-side schema, JAN-142) | Read directly |
| `knip.json` | Unused-code (dead export/file) scan configuration | Read directly |
| `tailwind.config.js` | Tailwind theme/config used by the dashboard | Read directly |
| `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json` | TypeScript project references and compiler options | Read directly |
| `.storybook/` | Storybook configuration for component exploration (`static/images/` is a sample PNG served at `/api/images/storybook-sample` for the ImageBlock stories, JAN-253); built via `npm run build-storybook` and served at the admin-only `/dev/storybook` route (see `src/janus/api/dev.py`) | Read directly |
| `public/` | Static assets served as-is (favicon, wallpaper image), plus `ui-shell-bootstrap.js`, the anti-flash shell bootstrap `index.html` loads; its shell table is kept in sync with `src/contexts/uiVersion.ts`, which `src/uiShellBootstrap.test.ts` checks (JAN-225) | Read directly |
| `dist/` | Built frontend output consumed by the backend; generated artifact | Read directly only when diagnosing build output |
| `storybook-static/` | Built Storybook static site consumed by the backend's `/dev/storybook` route; generated artifact | Read directly only when diagnosing that route |

## Read-next guidance

- Route/page behavior -> `src/CODEMAP.md`
- Frontend build or tooling issue -> `package.json`, `vite.config.ts`, `eslint.config.js`
- Test runner or dead-code-scan issue -> `vitest.config.ts`, `knip.json`
- Styling/theme behavior -> `tailwind.config.js` and relevant files in `src/`
- End-to-end test behavior or the throwaway e2e backend -> `playwright.config.ts`, then `e2e/CODEMAP.md`
- Storybook stories or the `/dev/storybook` deployment route -> `.storybook/`, then `src/janus/api/dev.py`
