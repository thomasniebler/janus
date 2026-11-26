# Frontend Source Code Map

React app source. Behavior: `docs/docs/tech-spec/frontend.md`.

| Area | Files |
| --- | --- |
| Entry | `main.tsx`, `index.css`; `App.tsx` (routes, auth/setup gating, QueryClient, shell host) |
| API | `api.ts` (most endpoints, response-validated), `*Api.ts` (per-feature clients) |
| Types | `types.ts`, `connectionTypes.ts`, `statsTypes.ts` |
| `pages/` | Route-level pages |
| `components/` | [`components/CODEMAP.md`](components/CODEMAP.md) |
| `contexts/`, `hooks/` | Providers (UI shell, chat session, page context/actions) and shared hooks |
| `lib/` | HTTP client, zod response schemas (`lib/*Schemas.ts`, `lib/apiValidation.ts`), chat frame parsing, helpers |
| `generated/` | OpenAPI types; never edit (`task openapi:generate`) |
| `test/`, `assets/` | Test setup and mocks; imported static assets |

Tests and stories sit next to their sources.

## Where to look

- `ApiValidationError`: `lib/schemas.ts` and siblings, then `lib/apiValidation.ts`
- Live cache invalidation: `lib/domainChanges.ts`, `hooks/useDomainChangeSync.ts`
