# Janus

A privacy-first, local-first personal AI agent. Janus runs on your own machine, keeps its data in a local database, and can use a local model through Ollama. It watches your mail, calendar, tasks and messages, briefs you every morning, answers in chat, and runs automations in the background, with every tool behind a switch you control.

## Key features

- **Chat** with tool calls you can see, approval cards before risky actions, and actions on the page you are viewing.
- **Mail triage**: regex rules and the model sort mail into Important, Digest, Ignore and Spam.
- **Triggers**: the morning briefing, afternoon digest, mail processing and your own scheduled or event-driven automations.
- **Tool permissions**: an Unattended switch per toolbox, and an In chat switch for the risky ones.
- **Knowledge**: memory facts, tracked topics, ingested documents, a knowledge graph and a wiki.
- **Planner**: Google Calendar and Tasks, Outlook calendars, Jira and a local planner.
- **Integrations**: Google, Microsoft 365, WhatsApp, Telegram, GitHub, Linear, remote MCP servers, remote agents and webhooks.
- **Models**: Ollama, OpenAI, OpenRouter, Gemini, Anthropic, GitHub Models and Azure OpenAI.

## Install

```bash
uv tool install janus-assistant                         # or: pipx install janus-assistant
janus config init                                       # then set secret_key in ~/.config/janus/settings.toml
janus user bootstrap-admin admin && janus start         # create the first admin, then open http://localhost:8080
```

WhatsApp, the tray icon, document parsing and PostgreSQL are optional extras. See [Optional features](docs/docs/quickstart.md#optional-features).

## Documentation

| Page | Covers |
|---|---|
| [Quickstart](docs/docs/quickstart.md) | Install, first admin, model, first mailbox |
| [Configuration](docs/docs/configuration.md) | Settings, paths, database |
| [Operations](docs/docs/operations.md) | Service, reverse proxy, updates, backups, monitoring, CLI |
| [Getting started](docs/docs/index.md) | Feature tour and the user guide |
| [Technical specification](TECH_SPEC.md) | How Janus is built |

The same docs are in the app under **Documentation**.

## Development

Backend tasks use `uv`, the frontend uses `npm` in `frontend/`, and [Task](https://taskfile.dev) wraps both.

```bash
git clone https://github.com/thomasniebler/janus.git && cd janus
uv sync                     # add --extra whatsapp --extra desktop --extra documents as needed
uv run prek install         # git hooks: ruff, dead-code scans, no direct commits to main

task run                    # build the web app and start Janus
task test                   # backend tests
task lint                   # ruff
task lint:dead              # vulture, deptry, knip
task format                 # ruff --fix + ruff format
task coverage               # backend tests with coverage
task docs                   # build the documentation
task openapi:generate       # refresh the OpenAPI schema and frontend API types

cd frontend && npm run dev  # frontend dev server on :5173
```

Pull requests open as drafts, which run only the cheap CI checks. The test suites run once a pull request is ready for review. See [CLAUDE.md](CLAUDE.md) for the full contributor workflow.

A SwiftUI iOS client prototype lives in [`ios/`](ios/).

## Acknowledgements

Some ideas, such as asking a local Claude Code or Codex CLI for help, a Telegram connector, signed inbound webhooks and a skill proposer, are inspired by [samalgotrader7-ops/janus](https://github.com/samalgotrader7-ops/janus), an unrelated MIT-licensed project with the same name. They were reimplemented for this codebase.

## License

Janus is released under the [MIT License](LICENSE).
