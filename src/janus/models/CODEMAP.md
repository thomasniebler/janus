# ORM Models Code Map

SQLAlchemy schema by domain; `__init__.py` re-exports every module plus the `../models_*.py` feature tables so Alembic sees full metadata.

Domain modules: `users.py`, `email.py`, `planner.py`, `whatsapp.py`, `telegram.py`, `reminders.py`, `topics.py`, `conversations.py`, `events.py`, `triggers.py`, `delegation.py`, `memory.py`, `settings.py`, `mcp.py`, `webhooks.py`, `llm_usage.py`, `skill_suggestions.py`, `a2a.py`; non-obvious: `agents.py` (character profile, skill library), `auth.py` (sessions, OAuth, device tokens), `enums.py` (shared enums).

Rule: domain modules import only `../database.py`, `../time_utils.py`, and `enums.py`, never each other.
Migrations -> `../../../alembic/`.
