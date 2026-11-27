# Janus Technical Specification

Janus is a local-first personal AI agent: one always-on agent per user, a plan-driven tool-calling runtime, durable triggers, and connectors for mail, planners and messaging, served by one FastAPI process with SQLite. This file is the index. Each child spec describes how one part works: its modules, data, flows and invariants. The user guide starts at [`docs/docs/index.md`](docs/docs/index.md); the conceptual overview is [`docs/docs/architecture.md`](docs/docs/architecture.md).

| Spec | Scope |
| --- | --- |
| [`architecture-overview.md`](docs/docs/tech-spec/architecture-overview.md) | Process model, threads and workers, module map, request and run flows, import boundaries, CI and quality gates. |
| [`backend-api.md`](docs/docs/tech-spec/backend-api.md) | Routers, auth guards, ownership rule, WebSockets, domain-change sync, in-app docs and status APIs, OpenAPI generation. |
| [`auth-security.md`](docs/docs/tech-spec/auth-security.md) | Sessions, JWT, CSRF, setup, device tokens, proxy/host/origin policy, tenant isolation, secrets, dependency audit and Semgrep. |
| [`background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md) | Plan-driven runtime, tool registry and toolsets, task modes, tool policy mechanics, triggers, the durable worker, definitions, events, built-ins, proposals, delegation, connector workers. |
| [`llm-agent-safety.md`](docs/docs/tech-spec/llm-agent-safety.md) | LLM providers, prompt assembly, prompt-injection defenses, unattended fences, locked tools, chain guard, delegation limits, approval fingerprint, shell and coding-agent guards, self-evolution gates. |
| [`data-model-migrations.md`](docs/docs/tech-spec/data-model-migrations.md) | Table groups, ownership columns, Alembic, startup migration, SQLite settings, PostgreSQL. |
| [`frontend.md`](docs/docs/tech-spec/frontend.md) | React app: shells, routing, chat session, state, API client, generated types, tests, in-app docs reader. |
| [`mobile-ios.md`](docs/docs/tech-spec/mobile-ios.md) | SwiftUI prototype: login, dashboard, chat transport, what is missing. |
| [`deployment-operations.md`](docs/docs/tech-spec/deployment-operations.md) | Packaging, install modes, Uberspace deploy, release workflow, self-update, release validation, ops metrics, capability checks. |
| [`delivery-artifacts.md`](docs/docs/tech-spec/delivery-artifacts.md) | Which docs ship with a change, and the Status / Linear / Depends-on convention. |

## Repository-wide rules

1. **Spec first.** A change to architecture, a service boundary, a schema, an integration or a safety fence updates the relevant child spec in the same change, before or with the code. Update this index when a summary or link changes.
2. **Docs are delivery artifacts.** User-facing pages and feature specs ship with the behavior they describe, not as a follow-up. See [`delivery-artifacts.md`](docs/docs/tech-spec/delivery-artifacts.md).
3. **CODEMAPs are navigation only.** Each `CODEMAP.md` says where code lives, one short row per file or area. How it works belongs in these specs. A change that adds, moves or removes a file updates the nearest map (`task codemap:check`).
4. **Safety fences have one home.** What an unattended run, a delegated child or a proposed automation may do is specified in [`llm-agent-safety.md`](docs/docs/tech-spec/llm-agent-safety.md) and enforced as described in [`background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md). Read both before changing tool policy, the registry or the runtime adapter.
5. **Draft pull requests save CI.** Open PRs as drafts; a draft runs only the cheap checks. Mark it ready once validated; a PR is merge-ready only after a full run is green on its final commit. See [CI and draft pull requests](docs/docs/tech-spec/architecture-overview.md#draft-pull-requests).
6. **Production host probes go through code.** Nobody runs ad-hoc commands on the production host. Add a read-only probe to `scripts/deployment_capabilities.py` and dispatch the capability-check workflow ([Deployment capability checks](docs/docs/tech-spec/deployment-operations.md#deployment-capability-checks)).
