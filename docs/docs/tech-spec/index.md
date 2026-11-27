# Technical Spec Index

The technical spec explains how Janus works: modules, data, flows and invariants. The canonical index and the repository-wide rules are in the root [`TECH_SPEC.md`](../../../TECH_SPEC.md). For a one-page picture first, read [Architecture](../architecture.md).

| Spec | Read it for |
| --- | --- |
| [Architecture Overview](architecture-overview.md) | Process model, workers, module map, flows, CI. |
| [Backend and API](backend-api.md) | Routers, WebSockets, OpenAPI. |
| [Authentication and Security](auth-security.md) | Sessions, CSRF, tenant isolation, scans. |
| [Background Workers and Runtime](background-workers-runtime.md) | Runtime, tools, triggers, delegation. |
| [LLM and Agent Safety](llm-agent-safety.md) | Providers, prompt injection, unattended fences. |
| [Data Model and Migrations](data-model-migrations.md) | Tables, Alembic, SQLite and PostgreSQL. |
| [Frontend](frontend.md) | React app, shells, chat session, tests. |
| [Mobile iOS](mobile-ios.md) | The SwiftUI prototype. |
| [Deployment and Operations](deployment-operations.md) | Packaging, deploy, release, update. |
| [Delivery Artifacts](delivery-artifacts.md) | Which docs ship with a change. |
