# Alembic Code Map

Scope: migration wiring and schema history under `alembic/`.

## Responsibilities

- configure the Alembic migration environment
- track the schema history for Janus database changes

## File and submodule map

| Path | Responsibility |
| --- | --- |
| `env.py` | Alembic environment configuration and metadata loading; excludes the runtime-managed `knowledge_fts_*` FTS5 tables from autogenerate |
| `script.py.mako` | Migration file template |
| `versions/` | Ordered migration history for Janus schema changes (latest: `d1a7c3e5f9b2`, the `device_tokens` table for the action inbox; before it `c9e3a5b7d1f2`, the proactive secretary's `notification_preferences.secretary_*` settings and `whatsapp_messages.origin_trigger_id`; before it `b7d2e4a6c8f1`, `email_accounts.sync_cursor` and `emails.graph_message_id` for Outlook mail over Graph; before it `a1c4e7f9b3d5`, the `oauth_connections` table, OAuth mailbox columns and the removal of the DB-held Google client; before it `c3f8a1d6e9b2`, the data-only planner baseline that lets the next sync rewrite stored times as UTC, JAN-394) |

## Read-next guidance

- New schema change or migration debugging -> `versions/`
- Alembic environment or metadata wiring -> `env.py`
