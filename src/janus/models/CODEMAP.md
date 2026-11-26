# ORM Models Code Map

Scope: `src/janus/models/`, the SQLAlchemy ORM schema grouped by domain.

## Responsibilities

- declare every core table on `Base` from `../database.py`
- re-export every name from `__init__.py`, including the `../models_*.py` feature modules, so `from janus import models` works and Alembic sees the full metadata

## File map

| File | Responsibility |
| --- | --- |
| `__init__.py` | Imports and re-exports all domain modules, feature modules, and helpers |
| `enums.py` | Enums shared across domains |
| `users.py` | Users, notification and dashboard-widget preferences |
| `agents.py` | `CharacterProfile` and `AgentSkill` (skill library) |
| `email.py` | Email accounts, emails, rules, clarification requests |
| `planner.py` | Planner accounts, tasks, calendar events, suggestions, links |
| `whatsapp.py` | WhatsApp accounts, setup sessions, conversations, messages |
| `reminders.py` | Reminder memory |
| `topics.py` | Tracked topics, evidence, suggestions, preferences |
| `conversations.py` | Chat conversations, messages, stored tool calls and UI blocks |
| `events.py` | Status event log and error groups |
| `triggers.py` | `RuntimeTrigger`, `TriggerDefinition`, tool policies and policy modes |
| `delegation.py` | Delegations and plan runtime state |
| `memory.py` | Memory facts and compaction runs |
| `auth.py` | Revoked sessions, OAuth state/connections, device tokens |
| `settings.py` | Per-user system and LLM settings |
| `mcp.py` | MCP servers, discovered tools, credentials |
| `webhooks.py` | Inbound webhook endpoints |
| `llm_usage.py` | Daily LLM usage rows |
| `skill_suggestions.py` | Dismissed skill suggestions |
| `telegram.py` | Telegram bots |
| `a2a.py` | Remote A2A agents |

## Rules

- Domain modules import only `../database.py`, `../time_utils.py`, SQLAlchemy, and `enums.py`; relationships use string names, so domain modules never import each other.
- A new model goes into its domain module (or a new module re-exported by `__init__.py`).

## Read-next guidance

- Schema changes and migrations -> the domain module, then `../../../alembic/` and [`data-model-migrations.md`](../../../docs/docs/tech-spec/data-model-migrations.md)
- Knowledge, images, character state, safety, self-evolution, toolset tables -> `../models_*.py`
