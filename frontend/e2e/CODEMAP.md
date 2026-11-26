# E2E Code Map

Playwright specs run against a real, disposable backend (`../playwright.config.ts`). Harness, running, and ports: "End-to-end tests" in `../../docs/docs/tech-spec/frontend.md`.

| Area | Files |
| --- | --- |
| Harness | `env.ts` (shared config), `global-setup.ts` (admin storage state), `fixtures.ts` (the test object every spec imports), `helpers.ts`, `backendSeed.ts` (seed the DB as a user), `freshBackend.ts` (user-less backend for setup) |
| Fakes | `llm-stub.ts` (scripted model), `fake-upstream.ts` (Google, Jira, Telegram), `fakeIntegrations.ts` (LLM providers, remote MCP), `fakeNeonize.ts`, `fake-neonize/` (Python stand-in for the WhatsApp client) |
| Chat | `chat.spec.ts`, `chat-*.spec.ts`, `janus-started-conversations.spec.ts`, `remote-agents.spec.ts` |
| Auth, dashboard | `login.spec.ts`, `setup.spec.ts`, `account-session.spec.ts`, `device-access.spec.ts`, `dashboard.spec.ts`, `briefing.spec.ts`, `live-sync.spec.ts`, `version.spec.ts`, `documentation.spec.ts` |
| Mail | `emails.spec.ts`, `email-accounts.spec.ts`, `mail-rules.spec.ts`, `outlook-mailbox.spec.ts` |
| Memory, wiki | `memory.spec.ts`, `agent-memory.spec.ts`, `topics.spec.ts`, `wiki-*.spec.ts` |
| Tasks, automations | `agent-tasks.spec.ts`, `delegation-wait.spec.ts`, `delegated-task-status.spec.ts`, `suggested-automations.spec.ts`, `webhooks.spec.ts`, `skill-suggestions.spec.ts`, `self-evolution.spec.ts`, `planner.spec.ts` |
| Settings, connectors | `settings*.spec.ts`, `injection-classifier.spec.ts`, `tool-permission-modes.spec.ts`, `secretary-settings.spec.ts`, `character-state.spec.ts`, `whatsapp.spec.ts`, `telegram.spec.ts` |

- Per-user data: run as a throwaway user; change instance settings via the admin request fixture and reset them in afterEach; answer in-app confirms with answerConfirm from `helpers.ts`, not page.on('dialog').
