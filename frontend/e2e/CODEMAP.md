# E2E Code Map

Scope: Playwright end-to-end specs for the dashboard, run against a real, disposable backend on a throwaway SQLite database (see `../playwright.config.ts`). How the harness works: the "End-to-end tests" entry in `../../docs/docs/tech-spec/frontend.md`.

## Responsibilities

- exercise user-facing flows in a real browser against an isolated backend
- provide shared fixtures, a logged-in admin storage state, throwaway users, and fakes for every external service
- keep specs independent of each other and of their order

## Harness and shared helpers

| Path | Purpose |
| --- | --- |
| `env.ts` | Runtime config shared by the Playwright config and global setup: data dir, port, DB URL, admin credentials, backend env |
| `global-setup.ts` | Bootstraps the e2e admin once and saves the shared browser storage state |
| `fixtures.ts` | The `test`/`expect` every spec imports: API-validation guard, `asThrowawayUser` option, `account` and `adminRequest` fixtures |
| `helpers.ts` | `api` calls, throwaway-user creation and sign-in, `ConfirmDialog` answers, utility-model switches, chat-socket waits |
| `backendSeed.ts` | `runBackendPython` (seed the e2e DB as a named user) and `seedMail` |
| `freshBackend.ts` | Starts a second, user-less backend for the first-run setup flow |
| `llm-stub.ts` | Scripted OpenAI-compatible model server so `/ws/chat` and `/ws/briefing` run end to end |
| `fake-upstream.ts` | Local fake for external APIs the backend calls (Google, Jira, Telegram Bot API) |
| `fakeIntegrations.ts` | Loopback fake for LLM providers, a remote MCP server, and its OAuth provider |
| `fakeNeonize.ts` | Spec side of the fake WhatsApp client: send commands, read its lifecycle log, pair accounts |
| `fake-neonize/neonize/` | Python stand-in for the `neonize` package imported by the e2e backend; entry `fake-neonize/neonize/client.py` |

## Specs (`*.spec.ts`)

| Area | Specs |
| --- | --- |
| Auth, setup, session | `login.spec.ts`, `setup.spec.ts`, `account-session.spec.ts`, `device-access.spec.ts` |
| Dashboard, briefing | `dashboard.spec.ts`, `briefing.spec.ts`, `live-sync.spec.ts` |
| Mail | `emails.spec.ts`, `email-accounts.spec.ts`, `mail-rules.spec.ts`, `outlook-mailbox.spec.ts` |
| Memory, topics, wiki | `memory.spec.ts`, `agent-memory.spec.ts`, `topics.spec.ts`, `wiki-links.spec.ts`, `wiki-provenance.spec.ts` |
| Chat against the real backend (`llm-stub.ts`) | `chat.spec.ts`, `chat-tool-selection.spec.ts`, `chat-remote-mcp.spec.ts`, `remote-agents.spec.ts` |
| Chat rendering from replayed `/ws/chat` frames | `chat-*.spec.ts` not listed above (page context/actions, UI introspection, UI blocks, images, tool calls/approval, timeline, narration, safety notice, delegated tasks) |
| Janus-started chats | `janus-started-conversations.spec.ts` |
| Agent tasks, delegation | `agent-tasks.spec.ts`, `delegation-wait.spec.ts`, `delegated-task-status.spec.ts` |
| Triggers, automations, skills | `suggested-automations.spec.ts`, `webhooks.spec.ts`, `skill-suggestions.spec.ts`, `self-evolution.spec.ts` |
| Settings | `settings.spec.ts`, `settings-llm.spec.ts`, `settings-integrations.spec.ts`, `injection-classifier.spec.ts`, `tool-permission-modes.spec.ts`, `secretary-settings.spec.ts`, `character-state.spec.ts`, `version.spec.ts` |
| Planner (fake Google/Jira) | `planner.spec.ts` |
| Connectors | `whatsapp.spec.ts` (fake Neonize), `telegram.spec.ts` (fake Bot API) |
| Documentation page | `documentation.spec.ts` |

## Conventions that prevent wrong turns

- Per-user data: run as a throwaway user (`test.use({ asThrowawayUser: true })`, or `createThrowawayUser` in `beforeAll` plus `signInAs`). Never assert global counts or empty states of the shared admin.
- Instance-wide settings: change through `adminRequest` and reset in `afterEach`.
- Confirmations are in-app `ConfirmDialog`s: use `answerConfirm` from `helpers.ts`, not `page.on('dialog')`.
- A spec serving a malformed API answer on purpose sets `allowApiValidationErrors` (see `fixtures.ts`).

## Running side by side

Each checkout gets its own data dir and port; two runs from the same checkout at once are not supported. Overrides read by `env.ts`: `E2E_PORT`, `E2E_DATA_DIR` (wiped each run; a non-empty dir without the e2e marker file is refused), `TMPDIR` (parent of the default data dir).

## Read next

- Backend wiring or credentials -> `env.ts`, then `global-setup.ts`
- A failing spec -> the matching `*.spec.ts`, then the page under `../src/pages/`
- Seeding state no UI/API can create -> `backendSeed.ts`
- New UI functionality -> add a spec here in the same change (repo root `AGENTS.md`)
