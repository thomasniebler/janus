# Data Model and Migrations

Janus keeps all durable state in one SQL database through SQLAlchemy 2.0: SQLite by default, PostgreSQL optionally. Files outside the database are the Fernet key, generated images, the Microsoft token cache, logs and update bookkeeping.

## Where the models live

- `src/janus/models/`: one module per domain (`users`, `auth`, `email`, `planner`, `reminders`, `topics`, `memory`, `conversations`, `events`, `triggers`, `delegation`, `agents`, `settings`, `mcp`, `a2a`, `whatsapp`, `telegram`, `webhooks`, `skill_suggestions`, `llm_usage`, and the shared `enums`). `janus.models` re-exports them.
- `models_character.py`, `models_images.py`, `models_knowledge.py`, `models_safety.py`, `models_self_evolution.py`, `models_toolsets.py`: feature modules.
- Columns use `Column(...)`. Timestamps are stored as naive UTC.

## Table groups

| Group | Tables |
| --- | --- |
| Users and auth | `users`, `revoked_sessions`, `device_tokens`, `oauth_connections`, `oauth_states` |
| Settings | `settings` (one instance-wide row: LLM providers and keys, embeddings, images, web search, GitHub, Linear, mail sync limit, topic defaults), `notification_preferences`, `dashboard_widget_preferences` |
| Mail | `email_accounts`, `emails`, `rules`, `email_clarification_requests` |
| Planner | `planner_accounts`, `calendar_events`, `tasks` (imported, read-only); `planner_tasks`, `planner_events`, `planner_links`, `planner_suggestions` (Janus-owned) |
| Reminders and topics | `reminder_memory`, `tracked_topics`, `topic_evidence`, `topic_suggestions`, `topic_tracking_preferences` |
| Memory | `agent_memory_facts`, `agent_memory_compaction_runs` |
| Knowledge | `knowledge_sources`, `knowledge_chunks`, `knowledge_embeddings`, `knowledge_entities`, `knowledge_mentions`, `knowledge_relations`, `knowledge_relation_evidence`, `knowledge_merge_suggestions`, `knowledge_preferences`, `knowledge_llm_usage`, `wiki_pages`, `wiki_page_revisions` |
| Chat | `conversations`, `conversation_messages`, `conversation_tool_calls`, `conversation_ui_blocks`, `generated_images` |
| Character and skills | `character_profiles`, `character_states`, `character_state_proposals`, `character_state_changes`, `agent_skills`, `skill_suggestion_dismissals` |
| Runtime | `runtime_triggers`, `trigger_definitions`, `plan_runtime_states`, `runtime_delegations`, `runtime_tool_selections` |
| Tool policy and safety | `tool_policies`, `tool_policy_modes`, `injection_classifier_settings`, `injection_classifications` |
| Integrations | `mcp_servers`, `mcp_discovered_tools`, `mcp_credentials`, `a2a_agents`, `webhook_endpoints`, `telegram_bots`, `whatsapp_accounts`, `whatsapp_conversations`, `whatsapp_messages`, `whatsapp_setup_sessions` |
| Self-evolution | `self_evolution_settings`, `self_evolution_actions` |
| Observability | `events`, `error_groups`, `llm_usage_days` |

`email_clarification_requests` and `topic_suggestions` are kept so existing databases upgrade without data loss; nothing creates or reviews their rows (deleting a topic only unlinks its old suggestions).

Not ORM tables: the `knowledge_fts_*` FTS5 tables (created and backfilled at startup when SQLite has FTS5) and the sqlite-vec virtual table behind `services/vector_index.py`. Both are excluded from Alembic's autogenerate comparison.

## Key records

- **`runtime_triggers`.** One run: owner, `task_id`, `idempotency_key` (unique together), `status`, claim token and `claimed_at`, attempt count and next attempt time, `payload` (never returned by APIs), `definition_id` and a `definition_name` snapshot, `output`, `parent_trigger_id` and `chain_depth`.
- **`trigger_definitions`.** Reusable automation: objective, `tool_ids`, kind and schedule or event (with `event_filter`, `max_runs_per_hour`, rate-limit counters), timezone, `origin`, `builtin_key`, model choice, run limits, and the proposal and approval columns (`proposal_status`, `proposal_source`, `supersedes_id`, `approved_by_user_id`, `approved_at`, `approval_fingerprint`, rejection fields).
- **`plan_runtime_states`.** The serialized plan graph per owner and task, with a revision for optimistic writes. Step results and errors live here; history views decode them.
- **`runtime_delegations`.** Parent task and step, child task, objective, requested and allowed tools, status, claim token, `requeue_count`, model choice, result or error, conversation link.
- **`tool_policies`.** Optional override per user and toolbox: `unattended_allowed` (nullable = default) and `chat_mode` (`ask`, `off`, `always`). **`tool_policy_modes`:** one row per user with `unattended_mode` and `chat_mode` (`per_tool`, `allow_all`).
- **`events`.** Event log with nullable `user_id` (`NULL` = system row).

## Ownership

- Every user-owned table has a `user_id`, or is owned through a parent (an email through its account, topic evidence through its topic, a reminder through its source email, an imported event or task through its planner account).
- `rules.user_id`, `email_accounts.user_id` and `planner_accounts.user_id` are `NOT NULL`.
- SQLite does not enforce foreign keys here, and it reuses freed ids. Code therefore deletes or unlinks dependent rows explicitly: deleting a user deletes device tokens, webhook endpoints, A2A agents, the Telegram bot and character state, and turns events into system rows; deleting a definition unlinks its runs.
- `agent_skills` is the one instance-wide content table; only admins write it.

## Secrets

Sensitive columns (mail passwords, provider keys, OAuth tokens, bot tokens, webhook secrets, MCP and A2A credentials) are Fernet-encrypted with the key file at `JANUS_FERNET_KEY_PATH`. Without that file the encrypted values are unreadable, so it must be backed up and moved with the database. Embeddings are stored unencrypted.

## Startup migration

`janus start` (and `janus brief`/`digest`) call `database.ensure_schema_up_to_date()`:

1. create the SQLite directory when needed;
2. stamp the initial revision on a pre-Alembic database that already has app tables;
3. repair known orphaned revision ids (`_repair_orphaned_revisions`);
4. `alembic upgrade head`;
5. mark the schema ready for this process.

The wheel ships `alembic.ini` and `alembic/` under `janus/_data`, and `resources.py` finds them in a checkout or an install. A deploy or self-update runs the same upgrade (and a pre-release snapshot) before the new code serves.

## SQLite settings

`database.create_app_engine` sets, on every connection to an on-disk SQLite file: `journal_mode=WAL`, `busy_timeout=30000` (also the driver timeout) and `synchronous=NORMAL`. Many threads share one file (worker, chat, mail monitors, vector index), and WAL lets readers proceed while one writer commits. WAL adds `janus.db-wal` and `janus.db-shm`: back up with SQLite's online backup API or copy all three files while stopped, never `janus.db` alone.

Optional SQLite capabilities, probed once at startup (`startup.prepare_knowledge_search`): extension loading and sqlite-vec for vector search, FTS5 for ranked keyword search. Without them search degrades to keyword or substring matching; startup never fails on them.

## PostgreSQL

`DATABASE_URL` may point at PostgreSQL (the `postgres` extra). Vector search is unavailable there (keyword search uses `ILIKE`). `janus db copy` (`services/database_copy_service.py`) copies all data from one database to another while Janus is stopped; the Fernet key file moves with it.

## Migration rules

- **Models and migrations must match.** `tests/test_database_migrations.py` upgrades a fresh SQLite database to head and fails on any autogenerate difference or `alembic check` finding.
- **Both backends.** CI's `migration-tests` job runs the chain, a downgrade/upgrade round trip and an enum-parity check on PostgreSQL (`tests/test_postgres_migrations.py`). PostgreSQL rules:
    - widen a native enum with `ALTER TYPE ... ADD VALUE IF NOT EXISTS`; values cannot be dropped, so downgrades leave them;
    - reference an existing enum type as `postgresql.ENUM(..., create_type=False)`;
    - native enum types store member names (`RUNNING`), not values;
    - identifiers are at most 63 characters; `batch_alter_table(recreate="always")` is SQLite-only.
- **Single head.** Merge revisions resolve parallel heads.
- **Additive by default.** New columns are nullable or server-defaulted so existing rows keep their behavior without a backfill. Seeded data (built-in definitions) is created at runtime, not in migrations.
- **Say when it is lossy.** A migration that drops data or cannot be reversed exactly says so in its docstring.
- **Preserve ownership and history.** Never orphan memory facts, trigger runs or plan state; keep imported and Janus-owned planner records separate.
- **Regression tests.** Schema and startup bug fixes ship with a test against a realistic existing database.
- `uv run alembic revision --autogenerate -m "..."` creates a revision; review it by hand.
