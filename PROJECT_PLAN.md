# Project Janus: Personal AI Assistant - Project Plan

> **Status note:** This document is the original day-one product roadmap and is
> kept as a historical/product-level reference. It is **not** the current
> technical source of truth — that is [`TECH_SPEC.md`](TECH_SPEC.md) and its
> child specs under [`docs/docs/tech-spec/`](docs/docs/tech-spec/), which are
> updated alongside every significant change. The checkboxes and tech-stack
> notes below have been refreshed to reflect what is actually implemented
> today (2026-09-26); see the caveats inline where the shipped feature is
> narrower than, or different from, the original one-line description.

## 1. Executive Summary
**Janus** is a local, passive AI assistant designed to filter noise and surface signal. It monitors communication channels (Email, and now WhatsApp) and task management systems (Jira and Google Calendar/Tasks today; ADO and O365 remain unimplemented placeholders — see Phase 3) to provide:
1.  **Real-time Notifications** for strictly "Important" items.
2.  **Daily Briefings** to interactively plan the day.
3.  **Afternoon Digests** for non-critical updates.

The system runs locally (SQLite, optional Ollama), with optional cloud integrations (Google Calendar/Tasks OAuth, Jira, GitHub, Linear, OpenAI/Gemini) layered on top of a hybrid intelligence model (rule-based + LLM) that learns from user feedback. It has since grown well past this original scope into a plan-driven single-agent runtime with durable triggers, delegated background tasks, structured topic tracking, durable memory, an MCP tool registry, a character/constitution system, and three interchangeable dashboard shells plus an iOS client scaffold — none of which were part of this original plan. See [`TECH_SPEC.md`](TECH_SPEC.md) for that architecture.

## 2. Core Architecture
- **Runtime:** FastAPI application process on Python 3.12+, with background work run in daemon threads (not a separate Go binary and not async workers — see [`docs/docs/tech-spec/background-workers-runtime.md`](docs/docs/tech-spec/background-workers-runtime.md)).
- **Database:** SQLite by default, via SQLAlchemy ORM with Alembic migrations; PostgreSQL is also supported as an optional backend (`DATABASE_URL`, `psycopg2-binary`) for multi-connection/remote setups — see [`docs/docs/database.md`](docs/docs/database.md).
- **Intelligence:**
    -   **Level 1:** Regex/Keyword Rules (fast, deterministic; `re2`-compiled to bound catastrophic backtracking).
    -   **Level 2:** LLM Analysis (contextual, semantic; Ollama, OpenAI, or Gemini).
- **Interfaces:**
    -   **CLI:** For configuration, account/rule/planner management, and interactive briefings (`janus brief`, `janus digest`).
    -   **System Tray:** A minimal `pystray`-based icon with "Open Dashboard" and "Quit" only — no pause control or digest menu (see Phase 4 caveat below).
    -   **Web Dashboard:** React/Vite dashboard for reviewing digests, managing rules/accounts, and much more (settings, memory, topics, runtime triggers, MCP tools, character).
    -   **iOS client:** A SwiftUI scaffold (`ios/`) covering chat and dashboard reads — not part of the original plan.
    -   *(Not built)* **Chat Bot:** No Telegram/Discord alert-forwarding integration exists. A WhatsApp connector (Neonize-based) was built instead, for ingestion rather than outbound alert forwarding.

## 3. Phased Implementation Roadmap

### Phase 1: The Foundation (Core & Email) — Done
*Goal: Establish the system and start monitoring emails.*
- [x] **Project Setup:** Repository, environment, and database established.
- [x] **IMAP Connector:** `imap_service.py` maintains persistent per-account connections with IDLE support.
- [x] **Basic Rule Engine:** `processor.py` evaluates regex `Rule` patterns to classify mail.
- [x] **CLI Interface:** `janus` CLI (Typer-based) covers login, accounts, rules, planner accounts, users, and config, well beyond a basic start/logs command.
- [x] **Notification System (v1):** Desktop notifications via `notify-send` (Linux-focused), gated off in server mode.

### Phase 2: The Intelligence (The "Brain") — Done
*Goal: Reduce noise by using AI to classify emails and generate digests.*
- [x] **LLM Integration:** `llm_service.py` supports Ollama, OpenAI, and Gemini behind a single active-provider setting.
- [x] **Hybrid Filter:** Rules run first; the LLM is only consulted as a constrained JSON classifier fallback.
- [x] **The "Digest" Module:** `DIGEST`-classified mail is stored and summarized in the afternoon digest.
- [x] **Feedback Loop:** Manual re-classification feeds a persistent clarification/review queue of candidate rules, requiring explicit user approval before a rule is created.

### Phase 3: The Planner (Tasks & Calendar) — Partially done
*Goal: Provide context for the day.*
- [ ] **Calendar Integration:**
    -   [x] Google Calendar API (`connectors/google.py`).
    -   [ ] Microsoft Graph API (for O365) — **not implemented.** `models.Provider.O365` exists as an enum value, but `PlannerService.get_connector()` has no branch for it and falls through to `None` (`# Placeholder for O365 and ADO`); accounts configured with this provider sync nothing.
- [ ] **Task Integration:**
    -   [x] Jira API (`connectors/jira.py`, auth & query).
    -   [ ] Azure DevOps API — **not implemented**, same placeholder fallthrough as O365 above.
- [x] **Interactive Briefing:** `janus brief`/`janus digest` and the `/ws/briefing` and `/ws/chat` WebSocket flows present schedule, tasks, and mail context.

### Phase 4: The Interface (UX Upgrade) — Mostly done, narrower than planned
*Goal: Make Janus accessible and visual.*
- [~] **System Tray App:** `desktop/tray.py` provides a status icon with "Open Dashboard" and "Quit" menu items. There is no "Pause" button and no "Show Digest" menu item — those were never built.
- [x] **Web Dashboard:** Built out far beyond "view the afternoon digest" — full account/rule/planner management, settings, memory, topics, runtime triggers, MCP tool registry, and a character editor, across three switchable UI shells (classic, terminal, rusty console).
- [ ] **Chat Bot Integration:** No Telegram/Discord alert forwarding exists. (A WhatsApp connector was added for a different purpose — see Section 2.)

## 4. Technical Stack (Actual)
- **Language:** Python 3.12+ (the original "3.10+" target was superseded; `pyproject.toml` requires `>=3.12`).
- **Database:** SQLite by default; PostgreSQL is also supported as an optional backend (see [`docs/docs/database.md`](docs/docs/database.md)).
- **Core Libraries (selected, non-exhaustive):**
    -   `imaplib` / `email`: For IMAP monitoring.
    -   `typer`: For the CLI.
    -   `pystray`: For the minimal System Tray icon.
    -   `fastapi`: Backend API and WebSocket server.
    -   `sqlalchemy` + `alembic`: ORM and migrations.
    -   `langchain-core` / `langchain-classic` / `langchain-ollama` / `langchain-openai` / `langchain-google-genai`: AI provider abstraction.
    -   `atlassian-python-api`, `google-api-python-client`: Jira and Google Calendar/Tasks connectors.
- See [`docs/docs/tech-spec/architecture-overview.md`](docs/docs/tech-spec/architecture-overview.md) for the current, maintained technology-stack description.

## 5. Next Steps
Phase 1 project setup (the original "current focus") has long been complete. Current-priority work is tracked in Linear (team `Janus`), not in this document — see [`docs/docs/tech-spec/delivery-artifacts.md`](docs/docs/tech-spec/delivery-artifacts.md) for how in-flight and planned work is tracked (`docs/docs/feature-specs/` and `docs/docs/features/planned/`, each linked to its Linear issue). The clearest remaining gaps from this original plan are:
1.  Azure DevOps and O365 planner connectors (Phase 3) — enum values and settings UI exist, but no connector implementation.
2.  A richer system tray (pause control, digest shortcut) if that UX is still wanted, or removing the stale "Pause"/"Show Digest" aspirations from this plan if not.
3.  An outbound alert-forwarding chat bot (Telegram/Discord), if still desired — currently there is no equivalent surface.
