# Frontend Code Map

Scope: the React/Vite dashboard project in `frontend/`.

## File and submodule map

| Path | Responsibility | Read next |
| --- | --- | --- |
| `src/` | Application source: pages, components, contexts, API client | [`src/CODEMAP.md`](src/CODEMAP.md) |
| `e2e/` | Playwright end-to-end specs and fixtures | [`e2e/CODEMAP.md`](e2e/CODEMAP.md) |
| `index.html` | Vite HTML entry; loads the shell bootstrap script from `public/` | Read directly |
| `public/` | Static assets served as-is, including `ui-shell-bootstrap.js` (kept in sync with `src/contexts/uiVersion.ts`) | Read directly |
| `package.json` | Scripts (incl. API type generation) and dependencies | Read directly |
| `vite.config.ts` | Vite dev/build configuration | Read directly |
| `vitest.config.ts` | Vitest and coverage configuration | Read directly |
| `playwright.config.ts` | Playwright configuration (throwaway backend for e2e) | Read directly |
| `eslint.config.js`, `eslint.baseline.json` | ESLint rules and the per-file size/complexity baseline | Read directly |
| `knip.json` | Dead-code scan configuration | Read directly |
| `tailwind.config.js` | Tailwind theme | Read directly |
| `tsconfig*.json` | TypeScript configuration | Read directly |
| `.storybook/` | Storybook configuration (served at the admin-only `/dev/storybook` route, `src/janus/api/dev.py`) | Read directly |

Generated output (`dist`, `storybook-static`) is not the source of truth.
