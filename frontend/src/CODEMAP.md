# Frontend Source Code Map

Scope: React application source under `frontend/src/`. Behavior detail: `docs/docs/tech-spec/frontend.md`.

## Responsibilities

- wire routing, auth gating, and error boundaries
- define typed, runtime-validated API access and shared frontend types
- compose pages from feature components and shared UI primitives

## File and submodule map

| Path | Responsibility | Read next |
| --- | --- | --- |
| `main.tsx`, `index.css` | React bootstrap and global styles | Read directly |
| `App.tsx` | Shell host, lazy route table, auth/setup gating, QueryClient, top-level error boundary | Read directly |
| `api.ts` | Typed API wrappers (most endpoints), response-validated; default export is the axios instance from `lib/httpClient.ts` | Read directly |
| `*Api.ts` | Per-feature API clients kept out of `api.ts` (connections, webhooks, A2A, skill suggestions, Telegram) | Read directly |
| `types.ts`, `connectionTypes.ts`, `statsTypes.ts` | Shared hand-written types (the latter two re-exported by `types.ts`) | Read directly |
| `api.validation.test.ts` | Valid/malformed response tests through the real `api.ts` wrappers | Read directly |
| `uiShellBootstrap.test.ts` | Guards the anti-flash shell bootstrap in `frontend/public/ui-shell-bootstrap.js` | Read directly |
| `pages/` | Route-level pages (Dashboard, Emails, Chat, Memory, Planner, Tasks, Settings and its Status/Events/Errors/Stats panels, Setup, Login, docs) | Read directly |
| `components/` | Feature components, UI primitives, app shells | [`components/CODEMAP.md`](components/CODEMAP.md) |
| `contexts/` | Cross-cutting providers: UI shell selection (`uiVersion.ts`), chat session and `/ws/chat` frames, page-context and page-action registries | Read directly |
| `hooks/` | Shared hooks: domain-change sync, media query, confirm dialog, deep links, page context and page actions | Read directly |
| `lib/` | Utilities and non-`api.ts` clients: HTTP client, zod response schemas (`*Schemas.ts`, `apiValidation.ts`), chat frame/plan/UI-block parsing, page context and introspection, server time parsing, stale-chunk reload, setup status, wiki links, graph layout | Read directly |
| `generated/` | Generated OpenAPI schema and TS types; never edit by hand (`task openapi:generate`) | Read directly |
| `test/` | Vitest setup, API mock, shared shell contracts, Storybook helpers | Read directly |
| `assets/` | Static assets imported by components | Read directly |

## Read-next guidance

- Route or auth-redirect behavior -> `App.tsx`
- Backend request contract or cache key -> `api.ts` or the matching `*Api.ts`
- `ApiValidationError` / `[api] Response validation failed` -> `lib/schemas.ts`, `lib/knowledgeSchemas.ts`, `lib/runtimeSchemas.ts`, `lib/opsSchemas.ts`, then `lib/apiValidation.ts`
- Live cache invalidation (`/ws/changes`) -> `lib/domainChanges.ts`, `hooks/useDomainChangeSync.ts`
- Server timestamps -> `lib/serverTime.ts`
- UI composition -> `pages/`, then [`components/CODEMAP.md`](components/CODEMAP.md)
