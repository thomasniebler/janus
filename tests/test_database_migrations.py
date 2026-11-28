"""Tests for migration bootstrap behavior on legacy databases."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from asgi_client import SyncASGIClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from janus import database, llm_service, models


def _current_migration_head() -> str:
    """Return the single current Alembic head used by upgrade assertions."""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture
def restore_database_module_state():
    """Restores database module globals that migration tests intentionally override."""
    original_engine = database.engine
    original_urls = set(database._schema_ready_urls)
    original_session_bind = database._session_factory.kw.get("bind")
    try:
        yield
    finally:
        database.engine = original_engine
        database._session_factory.configure(bind=original_session_bind)
        database._schema_ready_urls.clear()
        database._schema_ready_urls.update(original_urls)


def test_ensure_schema_up_to_date_does_not_disable_other_loggers(tmp_path):
    """Running a migration must not silently disable unrelated `janus.*` loggers for the process.

    `logging.config.fileConfig()` (called by alembic/env.py to apply alembic.ini's [loggers]
    section) defaults to `disable_existing_loggers=True`, which disables every already-created
    logger not explicitly listed in the ini file — permanently, for the life of the process,
    since nothing re-enables them. A pre-existing logger for an unrelated module must survive
    a real schema upgrade.
    """
    unrelated_logger = logging.getLogger("janus.worker_manager")
    unrelated_logger.disabled = False

    db_path = tmp_path / "logger-check.db"
    database.ensure_schema_up_to_date(f"sqlite:///{db_path}")

    assert unrelated_logger.disabled is False


def test_ensure_schema_up_to_date_upgrades_legacy_settings_schema(tmp_path, monkeypatch):
    """Legacy SQLite databases created before Alembic should upgrade before settings queries run."""
    db_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "318b4a706a6e")

        with engine.begin() as conn:
            conn.execute(text("DROP TABLE alembic_version"))

        database.ensure_schema_up_to_date(database_url)

        columns = {column["name"] for column in inspect(engine).get_columns("settings")}
        table_names = set(inspect(engine).get_table_names())
        memory_fact_columns = {column["name"] for column in inspect(engine).get_columns("agent_memory_facts")}
        user_columns = {column["name"] for column in inspect(engine).get_columns("users")}
        revoked_session_columns = {column["name"] for column in inspect(engine).get_columns("revoked_sessions")}
        notification_preference_columns = {
            column["name"] for column in inspect(engine).get_columns("notification_preferences")
        }
        dashboard_widget_preference_columns = {
            column["name"] for column in inspect(engine).get_columns("dashboard_widget_preferences")
        }
        topic_columns = {column["name"] for column in inspect(engine).get_columns("tracked_topics")}
        topic_preference_columns = {
            column["name"] for column in inspect(engine).get_columns("topic_tracking_preferences")
        }
        planner_task_columns = {column["name"] for column in inspect(engine).get_columns("planner_tasks")}
        planner_event_columns = {column["name"] for column in inspect(engine).get_columns("planner_events")}
        planner_suggestion_columns = {column["name"] for column in inspect(engine).get_columns("planner_suggestions")}
        planner_link_columns = {column["name"] for column in inspect(engine).get_columns("planner_links")}
        assert {
            "initial_sync_email_limit",
            "ollama_model",
            "ollama_base_url",
            "ollama_api_key",
            "openai_model",
            "openai_base_url",
            "openrouter_model",
            "openrouter_base_url",
            "openrouter_api_key",
            "gemini_model",
            "gemini_api_key",
            "anthropic_model",
            "anthropic_api_key",
            "anthropic_base_url",
            "topic_tracking_available",
            "topic_tracking_default_enabled",
            "topic_tracking_default_scope",
            "topic_tracking_default_min_confidence",
            "topic_tracking_default_min_confirmation_mentions",
            "topic_tracking_max_active_topics",
            "topic_tracking_max_context_topics",
            "topic_tracking_review_required",
        } <= columns
        assert "mail_ingestion_agent_definition_id" not in columns
        assert not {"agent_definitions", "agent_definition_skills", "agent_handoff_runs"} & table_names
        assert {"user_id", "summary", "status", "compacted_into_id"} <= memory_fact_columns
        assert "source_agent_definition_id" not in memory_fact_columns
        assert "session_generation" in user_columns
        assert {"session_id", "user_id", "reason", "revoked_at", "expires_at"} <= revoked_session_columns
        assert {
            "user_id",
            "notify_important",
            "notify_digest",
            "daily_digest_enabled",
        } <= notification_preference_columns
        assert {
            "user_id",
            "widget_key",
            "is_visible",
            "is_collapsed",
            "prompt_override",
            "briefing_email_window",
        } <= dashboard_widget_preference_columns
        assert "briefing_agent_definition_id" not in dashboard_widget_preference_columns
        assert {"user_id", "slug", "title", "status", "origin", "confidence", "marker_keywords"} <= topic_columns
        assert {
            "user_id",
            "enabled",
            "extraction_scope",
            "minimum_confidence",
            "min_confirmation_mentions",
            "max_active_topics",
        } <= topic_preference_columns
        assert {
            "user_id",
            "title",
            "status",
            "source",
            "due_at",
            "scheduled_for",
            "parent_task_id",
            "urgency",
            "sort_order",
        } <= planner_task_columns
        assert {"user_id", "title", "start_time", "end_time", "all_day", "source"} <= planner_event_columns
        assert {
            "user_id",
            "suggestion_type",
            "status",
            "source",
            "dedupe_key",
            "source_email_id",
            "target_task_id",
            "target_event_id",
        } <= planner_suggestion_columns
        assert {"user_id", "planner_task_id", "planner_event_id", "source_type", "source_id"} <= planner_link_columns

        testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(database, "SessionLocal", testing_session_local)

        captured: dict[str, object] = {}

        class FakeOllama:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def invoke(self, prompt):
                return prompt

        monkeypatch.setattr(llm_service, "ChatOllama", FakeOllama)

        service = llm_service.LLMService()
        assert captured["model"] == "llama3:latest"
        assert service.test_connection() == (True, "Successfully connected to LLM provider.")
    finally:
        engine.dispose()


def test_fa9753_upgrade_is_idempotent_when_version_table_is_stale(tmp_path):
    """Re-running fa9753 after a partial version rollback should succeed."""
    db_path = tmp_path / "rerun.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "fa9753de9eed")

        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'f366d10bc74d'"))

        command.upgrade(alembic_config, "fa9753de9eed")

        inspector = inspect(engine)
        planner_columns = {column["name"] for column in inspector.get_columns("planner_accounts")}
        rule_columns = {column["name"] for column in inspector.get_columns("rules")}
        email_columns = {column["name"] for column in inspector.get_columns("emails")}
        email_uniques = {
            constraint["name"] for constraint in inspector.get_unique_constraints("emails") if constraint.get("name")
        }

        assert {"last_sync_at", "last_sync_error"} <= planner_columns
        assert "order" in rule_columns
        assert "imap_uid" in email_columns
        assert "uq_email_account_uid" in email_uniques
    finally:
        engine.dispose()


def test_fa9753_upgrade_skips_missing_tables(tmp_path):
    """Stamped databases without legacy tables should not fail in fa9753."""
    db_path = tmp_path / "stamped-empty.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.stamp(alembic_config, "f366d10bc74d")
        command.upgrade(alembic_config, "fa9753de9eed")

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        assert version == "fa9753de9eed"
    finally:
        engine.dispose()


def test_openrouter_migration_downgrade_remaps_provider_before_enum_shrink(tmp_path):
    """Downgrading the OpenRouter migration should remap unsupported provider values first."""
    db_path = tmp_path / "openrouter-downgrade.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "e7a9c2d1f4b8")

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO settings (llm_provider, llm_model) VALUES ('OLLAMA', 'llama3:latest')"))
            conn.execute(text("UPDATE settings SET llm_provider = 'OPENROUTER'"))

        command.downgrade(alembic_config, "37b30d7c85e1")

        with engine.connect() as conn:
            provider = conn.execute(text("SELECT llm_provider FROM settings")).scalar_one()

        assert provider == "OLLAMA"
    finally:
        engine.dispose()


def test_anthropic_migration_upgrades_existing_settings_and_downgrades_cleanly(tmp_path):
    """JAN-214: an existing settings row keeps its provider, gains the Claude default, and round-trips."""
    db_path = tmp_path / "anthropic-provider.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "c4e7a9d2f1b3")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO settings (llm_provider, llm_model, gemini_model) "
                    "VALUES ('GEMINI', 'gemini-2.5-pro', 'gemini-2.5-pro')"
                )
            )

        command.upgrade(alembic_config, "44b79f99846d")

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_provider, gemini_model, anthropic_model, anthropic_api_key, anthropic_base_url "
                    "FROM settings"
                )
            ).one()
            assert tuple(row) == ("GEMINI", "gemini-2.5-pro", "claude-opus-5", None, None)
            conn.execute(text("UPDATE settings SET llm_provider = 'ANTHROPIC', anthropic_api_key = 'encrypted'"))

        command.downgrade(alembic_config, "c4e7a9d2f1b3")

        with engine.connect() as conn:
            provider = conn.execute(text("SELECT llm_provider FROM settings")).scalar_one()
        columns = {column["name"] for column in inspect(engine).get_columns("settings")}
        assert provider == "OLLAMA"
        assert not {"anthropic_model", "anthropic_api_key", "anthropic_base_url"} & columns
    finally:
        engine.dispose()


def test_azure_v1_migration_moves_only_the_old_default_to_preview(tmp_path):
    """JAN-356: the stored old default 2024-10-21 becomes preview (v1 route); a chosen dated version stays."""
    db_path = tmp_path / "azure-v1.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def version() -> str:
        with engine.connect() as conn:
            return conn.execute(text("SELECT azure_openai_api_version FROM settings")).scalar_one()

    try:
        command.upgrade(alembic_config, "e6b2d8f4a1c3")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO settings (azure_openai_api_version) VALUES ('2024-10-21')"))
        command.upgrade(alembic_config, "f3a7c1e9d2b4")
        assert version() == "preview"
        command.downgrade(alembic_config, "e6b2d8f4a1c3")
        assert version() == "2024-10-21"

        with engine.begin() as conn:
            conn.execute(text("UPDATE settings SET azure_openai_api_version = '2025-04-01-preview'"))
        command.upgrade(alembic_config, "f3a7c1e9d2b4")
        assert version() == "2025-04-01-preview"

        # Every v1 value goes back to the old default on downgrade: the older code would send it
        # verbatim to its deployments route, which rejects it (JAN-357).
        for v1_value in ("latest", "v1"):
            with engine.begin() as conn:
                conn.execute(text("UPDATE settings SET azure_openai_api_version = :v"), {"v": v1_value})
            command.downgrade(alembic_config, "e6b2d8f4a1c3")
            assert version() == "2024-10-21"
            command.upgrade(alembic_config, "f3a7c1e9d2b4")
    finally:
        engine.dispose()


def test_github_models_and_azure_migration_upgrades_existing_settings_and_downgrades_cleanly(tmp_path):
    """JAN-340: an existing settings row keeps its provider, gains the new defaults, and round-trips."""
    db_path = tmp_path / "github-azure-providers.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    new_columns = {
        "github_models_model",
        "github_models_api_key",
        "github_models_base_url",
        "azure_openai_endpoint",
        "azure_openai_deployment",
        "azure_openai_api_version",
        "azure_openai_api_key",
        "azure_openai_auth_mode",
    }

    try:
        command.upgrade(alembic_config, "c3e9a1f7d2b8")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO settings (llm_provider, anthropic_model) VALUES ('ANTHROPIC', 'claude-x')"))

        command.upgrade(alembic_config, "d8a2f4c6b1e9")

        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_provider, anthropic_model, github_models_model, github_models_api_key, "
                    "azure_openai_endpoint, azure_openai_api_version, azure_openai_auth_mode FROM settings"
                )
            ).one()
            assert tuple(row) == ("ANTHROPIC", "claude-x", "openai/gpt-4.1-mini", None, None, "2024-10-21", "entra")
            conn.execute(
                text(
                    "UPDATE settings SET llm_provider = 'GITHUB_MODELS', github_models_api_key = 'x', "
                    "chat_llm_provider = 'azure_openai', chat_llm_model = 'Kimi-K2.6', "
                    "utility_llm_provider = 'openai', utility_llm_model = 'gpt-4o-mini'"
                )
            )
            user_id = conn.execute(
                text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('u', 'h', 1) RETURNING id")
            ).scalar_one()
            for name, provider in (("gh", "github_models"), ("kept", "anthropic")):
                conn.execute(
                    text(
                        "INSERT INTO trigger_definitions (user_id, name, objective, tool_ids, kind, origin, "
                        "created_at, updated_at, llm_provider, llm_model) VALUES (:user_id, :name, 'x', '[]', "
                        "'manual', 'user', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :provider, 'm')"
                    ),
                    {"user_id": user_id, "name": name, "provider": provider},
                )
        # The widened column round-trips the longest new member through the ORM enum.
        with sessionmaker(bind=engine)() as session:
            # Only this column: the database is at an older revision than the model's newest columns.
            assert session.query(models.Settings.llm_provider).scalar() == models.LLMProvider.GITHUB_MODELS

        command.downgrade(alembic_config, "c3e9a1f7d2b8")

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT llm_provider, chat_llm_provider, chat_llm_model, utility_llm_provider, utility_llm_model "
                    "FROM settings"
                )
            ).one()
            definitions = dict(conn.execute(text("SELECT name, llm_provider FROM trigger_definitions")).all())
        columns = {column["name"] for column in inspect(engine).get_columns("settings")}
        # A choice naming a new provider falls back to the default model; other choices stay.
        assert tuple(row) == ("OLLAMA", None, None, "openai", "gpt-4o-mini")
        assert definitions == {"gh": None, "kept": "anthropic"}
        assert not new_columns & columns
    finally:
        engine.dispose()


def test_ensure_schema_up_to_date_repairs_rebased_merge_revision(tmp_path):
    """Databases stamped with a removed merge revision should remap, then upgrade to the latest head."""
    db_path = tmp_path / "rebased-merge.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "b1c2d3e4f5a6")

        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'e4f6a7b8c9d0'"))

        database.ensure_schema_up_to_date(database_url)

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        settings_columns = {column["name"] for column in inspector.get_columns("settings")}
        email_columns = {column["name"] for column in inspector.get_columns("emails")}
        agent_skill_columns = {column["name"] for column in inspector.get_columns("agent_skills")}

        revoked_session_columns = {column["name"] for column in inspector.get_columns("revoked_sessions")}
        revoked_session_pk = inspector.get_pk_constraint("revoked_sessions")
        dashboard_widget_preference_columns = {
            column["name"] for column in inspector.get_columns("dashboard_widget_preferences")
        }
        tracked_topic_columns = {column["name"] for column in inspector.get_columns("tracked_topics")}
        topic_evidence_columns = {column["name"] for column in inspector.get_columns("topic_evidence")}
        topic_preference_columns = {column["name"] for column in inspector.get_columns("topic_tracking_preferences")}
        topic_suggestion_columns = {column["name"] for column in inspector.get_columns("topic_suggestions")}

        planner_task_columns = {column["name"] for column in inspector.get_columns("planner_tasks")}
        planner_event_columns = {column["name"] for column in inspector.get_columns("planner_events")}
        planner_suggestion_columns = {column["name"] for column in inspector.get_columns("planner_suggestions")}
        planner_link_columns = {column["name"] for column in inspector.get_columns("planner_links")}

        assert version == _current_migration_head()
        assert not {"agent_definitions", "agent_definition_skills", "agent_handoff_runs"} & table_names
        assert {
            "github_token",
            "github_default_repo",
            "linear_api_key",
            "linear_default_team_id",
            "ollama_api_key",
            "openrouter_model",
            "openrouter_base_url",
            "openrouter_api_key",
            "topic_tracking_available",
            "topic_tracking_default_enabled",
            "topic_tracking_default_scope",
            "topic_tracking_default_min_confidence",
            "topic_tracking_default_min_confirmation_mentions",
            "topic_tracking_max_active_topics",
            "topic_tracking_max_context_topics",
            "topic_tracking_review_required",
        } <= settings_columns
        assert "classification_confidence" in email_columns
        assert {"id", "name", "description", "license", "content", "created_at", "updated_at"} <= agent_skill_columns
        assert "mail_ingestion_agent_definition_id" not in settings_columns
        assert "briefing_cache_entries" not in table_names
        assert {"session_id", "user_id", "reason"} <= revoked_session_columns
        assert revoked_session_pk["constrained_columns"] == ["id"]
        assert {
            "id",
            "user_id",
            "widget_key",
            "is_visible",
            "is_collapsed",
            "prompt_override",
            "briefing_email_window",
        } <= dashboard_widget_preference_columns
        assert {
            "user_id",
            "slug",
            "title",
            "status",
            "origin",
            "confidence",
            "marker_keywords",
        } <= tracked_topic_columns
        assert {"topic_id", "source_type", "source_id", "evidence_role"} <= topic_evidence_columns
        assert {
            "user_id",
            "enabled",
            "extraction_scope",
            "minimum_confidence",
            "min_confirmation_mentions",
        } <= topic_preference_columns
        assert {"user_id", "topic_id", "suggestion_type", "status", "confidence"} <= topic_suggestion_columns
        assert {
            "user_id",
            "title",
            "status",
            "source",
            "parent_task_id",
            "urgency",
            "sort_order",
        } <= planner_task_columns
        assert {"user_id", "title", "start_time", "end_time", "source"} <= planner_event_columns
        assert {"user_id", "suggestion_type", "status", "source", "target_task_id"} <= planner_suggestion_columns
        assert {"user_id", "planner_task_id", "planner_event_id", "source_type"} <= planner_link_columns
    finally:
        engine.dispose()


def test_session_local_lazily_upgrades_persistent_legacy_databases(
    tmp_path, monkeypatch, restore_database_module_state
):
    """Opening a session should upgrade legacy file-backed databases even if startup hooks did not run."""
    db_path = tmp_path / "lazy-session-upgrade.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "3324b96c4dd2")

        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "3324b96c4dd2"

        database.engine = None
        database._schema_ready_urls.clear()
        monkeypatch.setattr(database.config.settings, "database_url", database_url)

        session = database.SessionLocal()
        try:
            session.execute(text("SELECT 1"))
        finally:
            session.close()

        inspector = inspect(engine)
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        dashboard_widget_preference_columns = {
            column["name"] for column in inspector.get_columns("dashboard_widget_preferences")
        }
        tracked_topic_columns = {column["name"] for column in inspector.get_columns("tracked_topics")}

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == _current_migration_head()

        assert "session_generation" in user_columns
        assert {"user_id", "widget_key", "prompt_override"} <= dashboard_widget_preference_columns
        assert {"user_id", "slug", "title"} <= tracked_topic_columns
    finally:
        engine.dispose()


def test_dashboard_endpoints_upgrade_legacy_database_when_startup_was_skipped(
    tmp_path, monkeypatch, restore_database_module_state
):
    """Dashboard routes should not 500 when the process opens a legacy database before startup hooks run."""
    import janus.main as main_module
    from janus import security
    from janus.main import api

    db_path = tmp_path / "legacy-dashboard.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    original_startup_initialized = main_module._startup_initialized
    original_get_db_override = api.dependency_overrides.pop(database.get_db, None)

    try:
        command.upgrade(alembic_config, "3324b96c4dd2")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)"))

        database.engine = None
        database._schema_ready_urls.clear()
        main_module._startup_initialized = True

        monkeypatch.setattr(database.config.settings, "database_url", database_url)
        monkeypatch.setattr(database.config.settings, "allowed_hosts", ["testserver"])
        monkeypatch.setattr(database.config.settings, "allowed_origins", ["http://testserver"])
        api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1, username="admin")

        with SyncASGIClient(api, raise_server_exceptions=False) as client:
            response = client.get("/api/dashboard/", headers={"origin": "http://testserver"})
            assert response.status_code == 200

            widgets_response = client.get("/api/dashboard/widgets", headers={"origin": "http://testserver"})
            assert widgets_response.status_code == 200
            assert widgets_response.json()

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == _current_migration_head()
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)
        if original_get_db_override is not None:
            api.dependency_overrides[database.get_db] = original_get_db_override
        main_module._startup_initialized = original_startup_initialized
        engine.dispose()


def test_session_local_creates_missing_parent_directory_before_lazy_upgrade(
    tmp_path, monkeypatch, restore_database_module_state
):
    """Lazy session bootstrap should create missing parent directories for on-disk SQLite databases."""
    db_path = tmp_path / "nested" / "runtime" / "lazy-session-upgrade.db"
    database_url = f"sqlite:///{db_path}"

    database.engine = None
    database._schema_ready_urls.clear()
    monkeypatch.setattr(database.config.settings, "database_url", database_url)

    session = database.SessionLocal()
    try:
        session.execute(text("SELECT 1"))
    finally:
        session.close()

    assert db_path.parent.is_dir()
    assert db_path.exists()

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            assert version == _current_migration_head()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("database_url", "description"),
    [
        ("sqlite://", "empty in-memory SQLite URL"),
        ("sqlite:///:memory:", "explicit in-memory SQLite URL"),
        ("sqlite:///file::memory:?cache=shared", "shared in-memory SQLite URI"),
        ("sqlite:///file:janus-shared?mode=memory&cache=shared&uri=true", "named shared in-memory SQLite URI"),
        ("postgresql://janus:secret@localhost/janus", "non-SQLite database URL"),
    ],
)
def test_lazy_schema_bootstrap_skips_non_persistent_or_non_sqlite_databases(
    database_url, description, monkeypatch, restore_database_module_state
):
    """Request-path schema upgrades should only run for persistent file-backed SQLite databases."""
    called_with: list[str] = []
    monkeypatch.setattr(database, "ensure_schema_up_to_date", lambda target_url=None: called_with.append(target_url))

    database._ensure_schema_ready(database_url)

    assert called_with == [], f"unexpected lazy schema bootstrap for {description}"


def test_lazy_schema_bootstrap_skips_lock_when_schema_is_cached(monkeypatch, restore_database_module_state):
    """Cached schema URLs should bypass the lock on subsequent request-path checks."""
    database_url = "sqlite:////tmp/already-ready.db"
    database._schema_ready_urls.add(database_url)

    class UnexpectedLock:
        def __enter__(self):
            raise AssertionError("schema lock should not be acquired for cached URLs")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(database, "_schema_ready_lock", UnexpectedLock())

    database._ensure_schema_ready(database_url)


def test_dashboard_widget_key_upgrade_allows_topic_focus_on_existing_databases(tmp_path):
    """Upgraded dashboard widget tables should accept the Topic Focus enum value on SQLite."""
    db_path = tmp_path / "topic-focus-widget-upgrade.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "4b7f1c2d3e4a")

        legacy_constraints = inspect(engine).get_check_constraints("dashboard_widget_preferences")
        assert all("TOPIC_FOCUS" not in (constraint.get("sqltext") or "") for constraint in legacy_constraints)

        command.upgrade(alembic_config, "head")

        upgraded_constraints = inspect(engine).get_check_constraints("dashboard_widget_preferences")
        assert any("TOPIC_FOCUS" in (constraint.get("sqltext") or "") for constraint in upgraded_constraints)

        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, is_admin, session_generation)
                    VALUES (:username, :hashed_password, :is_admin, :session_generation)
                    """
                ),
                {
                    "username": "upgraded-widget-user",
                    "hashed_password": "hashed",
                    "is_admin": 1,
                    "session_generation": 0,
                },
            ).lastrowid

            conn.execute(
                text(
                    """
                    INSERT INTO dashboard_widget_preferences (
                        user_id, widget_key, is_visible, is_collapsed, prompt_override
                    )
                    VALUES (:user_id, :widget_key, :is_visible, :is_collapsed, :prompt_override)
                    """
                ),
                {
                    "user_id": user_id,
                    "widget_key": "TOPIC_FOCUS",
                    "is_visible": 1,
                    "is_collapsed": 0,
                    "prompt_override": None,
                },
            )
            persisted = conn.execute(
                text(
                    """
                    SELECT widget_key
                    FROM dashboard_widget_preferences
                    WHERE user_id = :user_id
                    """
                ),
                {"user_id": user_id},
            ).scalar_one()

        assert persisted == "TOPIC_FOCUS"
    finally:
        engine.dispose()


def test_google_tasks_provider_upgrade_allows_existing_databases_to_store_google_tasks(tmp_path):
    """Upgraded planner tables should accept the Google Tasks provider enum on SQLite."""
    db_path = tmp_path / "google-tasks-provider-upgrade.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "2bbf0d6f8b44")

        legacy_constraints = inspect(engine).get_check_constraints("planner_accounts")
        assert all("GOOGLE_TASKS" not in (constraint.get("sqltext") or "") for constraint in legacy_constraints)

        command.upgrade(alembic_config, "head")

        upgraded_constraints = inspect(engine).get_check_constraints("planner_accounts")
        assert any("GOOGLE_TASKS" in (constraint.get("sqltext") or "") for constraint in upgraded_constraints)

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)"))
            user_id = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar_one()
            planner_account_id = conn.execute(
                text(
                    """
                    INSERT INTO planner_accounts (user_id, name, provider, token, is_active)
                    VALUES (:user_id, :name, :provider, :token, :is_active)
                    """
                ),
                {
                    "user_id": user_id,
                    "name": "Google Tasks",
                    "provider": "GOOGLE_TASKS",
                    "token": "encrypted-token",
                    "is_active": 1,
                },
            ).lastrowid
            persisted = conn.execute(
                text("SELECT provider FROM planner_accounts WHERE id = :planner_account_id"),
                {"planner_account_id": planner_account_id},
            ).scalar_one()

        assert persisted == "GOOGLE_TASKS"
    finally:
        engine.dispose()


def test_topic_marker_keywords_migration_adds_column_and_downgrades_cleanly(tmp_path):
    """Topic marker keywords should be added at head and removable by downgrading to the prior revision."""
    db_path = tmp_path / "topic-marker-keywords.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "9d3c1b2a4f6e")
        pre_upgrade_columns = {column["name"] for column in inspect(engine).get_columns("tracked_topics")}

        command.upgrade(alembic_config, "7e3a1c5d9b2f")
        upgraded_columns = {column["name"] for column in inspect(engine).get_columns("tracked_topics")}

        command.downgrade(alembic_config, "9d3c1b2a4f6e")

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        downgraded_columns = {column["name"] for column in inspect(engine).get_columns("tracked_topics")}

        assert "marker_keywords" not in pre_upgrade_columns
        assert "marker_keywords" in upgraded_columns
        assert version == "9d3c1b2a4f6e"
        assert "marker_keywords" not in downgraded_columns
    finally:
        engine.dispose()


def test_8c4f1a2b3d4e_downgrade_drops_ollama_api_key_on_sqlite(tmp_path):
    """Downgrading the Ollama API-key migration should work on SQLite."""
    db_path = tmp_path / "ollama-api-key-downgrade.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "8c4f1a2b3d4e")
        upgraded_columns = {column["name"] for column in inspect(engine).get_columns("settings")}

        command.downgrade(alembic_config, "623957070a5f")

        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

        downgraded_columns = {column["name"] for column in inspect(engine).get_columns("settings")}

        assert "ollama_api_key" in upgraded_columns
        assert version == "623957070a5f"
        assert "ollama_api_key" not in downgraded_columns
    finally:
        engine.dispose()


def test_clarification_queue_migration_sets_database_timestamp_defaults(tmp_path):
    """Clarification requests created via raw SQL should receive database-managed timestamps."""
    db_path = tmp_path / "clarification-defaults.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "head")

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)"))
            user_id = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar_one()

            conn.execute(
                text(
                    "INSERT INTO email_accounts (user_id, name, email, password) "
                    "VALUES (:user_id, 'Inbox', 'inbox@example.com', 'hash')"
                ),
                {"user_id": user_id},
            )
            account_id = conn.execute(
                text("SELECT id FROM email_accounts WHERE email = 'inbox@example.com'")
            ).scalar_one()

            conn.execute(
                text(
                    "INSERT INTO emails ("
                    "account_id, message_id, subject, sender, snippet, priority, is_read, notified"
                    ") "
                    "VALUES ("
                    ":account_id, 'message-1', 'Subject', 'sender@example.com', 'Snippet', 'DIGEST', 0, 0"
                    ")"
                ),
                {"account_id": account_id},
            )
            email_id = conn.execute(text("SELECT id FROM emails WHERE message_id = 'message-1'")).scalar_one()

            conn.execute(
                text(
                    "INSERT INTO email_clarification_requests ("
                    "user_id, email_id, previous_priority, requested_priority, "
                    "suggested_rule_name, suggested_rule_pattern, suggested_rule_field, suggested_rule_priority, "
                    "suggestion_confidence, suggestion_reason"
                    ") VALUES ("
                    ":user_id, :email_id, 'DIGEST', 'IMPORTANT', "
                    "'Messages from sender@example.com', '^sender@example\\\\.com$', 'sender', 'IMPORTANT', "
                    "'HIGH', 'Created by migration test'"
                    ")"
                ),
                {"user_id": user_id, "email_id": email_id},
            )

            clarification = conn.execute(text("SELECT created_at, updated_at FROM email_clarification_requests")).one()

        clarification_columns = {
            column["name"]: column for column in inspect(engine).get_columns("email_clarification_requests")
        }
        assert clarification.created_at is not None
        assert clarification.updated_at is not None
        assert clarification_columns["created_at"]["default"] is not None
        assert clarification_columns["updated_at"]["default"] is not None
    finally:
        engine.dispose()


def test_agent_skills_table_survives_as_the_flat_skill_library_at_head(tmp_path):
    """At head the skill library stays (unique names) while the agent-definition join table is gone."""
    db_path = tmp_path / "agent-skills.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(engine)
        skill_columns = {column["name"] for column in inspector.get_columns("agent_skills")}
        unique_constraints = inspector.get_unique_constraints("agent_skills")

        assert {"id", "name", "description", "content", "created_at", "updated_at"} <= skill_columns
        assert any(constraint.get("column_names") == ["name"] for constraint in unique_constraints)
        assert "agent_definition_skills" not in inspector.get_table_names()
    finally:
        engine.dispose()


def test_remove_agent_definitions_migration_drops_legacy_schema_and_keeps_skills(tmp_path):
    """JAN-169 drops agent definitions, skill links, handoffs, and FK columns; skills and facts survive."""
    db_path = tmp_path / "remove-agent-definitions.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    legacy_tables = {"agent_definitions", "agent_definition_skills", "agent_handoff_runs"}
    legacy_columns = {
        "dashboard_widget_preferences": "briefing_agent_definition_id",
        "settings": "mail_ingestion_agent_definition_id",
        "agent_memory_facts": "source_agent_definition_id",
    }

    def column_names(table: str) -> set[str]:
        return {column["name"] for column in inspect(engine).get_columns(table)}

    try:
        command.upgrade(alembic_config, "44b79f99846d")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('owner', 'x', 1, 0)"
                )
            )
            user_id = conn.execute(text("SELECT id FROM users")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO agent_definitions (name, system_prompt, created_at, updated_at) "
                    "VALUES ('Legacy', 'Prompt', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            agent_id = conn.execute(text("SELECT id FROM agent_definitions")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO agent_skills (name, description, content, created_at, updated_at) "
                    "VALUES ('kept-skill', 'Kept', 'Body', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            skill_id = conn.execute(text("SELECT id FROM agent_skills")).scalar_one()
            conn.execute(
                text("INSERT INTO agent_definition_skills (agent_definition_id, agent_skill_id) VALUES (:a, :s)"),
                {"a": agent_id, "s": skill_id},
            )
            conn.execute(
                text(
                    "INSERT INTO agent_memory_facts "
                    "(user_id, summary, source, status, source_agent_definition_id, created_at, updated_at) "
                    "VALUES (:u, 'Kept fact', 'USER', 'ACTIVE', :a, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"u": user_id, "a": agent_id},
            )

        command.upgrade(alembic_config, "d5e8f1a2b3c4")

        assert not legacy_tables & set(inspect(engine).get_table_names())
        for table, column in legacy_columns.items():
            assert column not in column_names(table)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM agent_skills")).scalars().all() == ["kept-skill"]
            assert conn.execute(text("SELECT summary FROM agent_memory_facts")).scalars().all() == ["Kept fact"]

        command.downgrade(alembic_config, "44b79f99846d")

        assert legacy_tables <= set(inspect(engine).get_table_names())
        for table, column in legacy_columns.items():
            assert column in column_names(table)
    finally:
        engine.dispose()


def test_error_group_migration_creates_table_with_database_defaults(tmp_path):
    """Error groups created via raw SQL should receive database-managed defaults."""
    db_path = tmp_path / "error-groups.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "head")

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO error_groups (fingerprint, source, message_template) "
                    "VALUES ('fingerprint-1', 'PlannerSync', 'Job failed <NUM>')"
                )
            )
            group = conn.execute(
                text(
                    "SELECT first_seen, last_seen, occurrence_count, created_at "
                    "FROM error_groups WHERE fingerprint = 'fingerprint-1'"
                )
            ).one()

        error_group_columns = {column["name"]: column for column in inspect(engine).get_columns("error_groups")}
        assert group.first_seen is not None
        assert group.last_seen is not None
        assert group.created_at is not None
        assert group.occurrence_count == 1
        assert error_group_columns["first_seen"]["default"] is not None
        assert error_group_columns["last_seen"]["default"] is not None
        assert error_group_columns["created_at"]["default"] is not None
        assert error_group_columns["occurrence_count"]["default"] is not None
    finally:
        engine.dispose()


def test_whatsapp_foundation_migration_sets_database_timestamp_defaults(tmp_path):
    """WhatsApp foundation tables created via raw SQL should receive database-managed timestamps."""
    db_path = tmp_path / "whatsapp-foundation-defaults.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "f1b2c3d4e5f6")

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)"))
            user_id = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar_one()

            account_id = conn.execute(
                text(
                    """
                    INSERT INTO whatsapp_accounts (
                        user_id, display_name, provider, connection_status
                    ) VALUES (
                        :user_id, 'Personal WhatsApp', 'whatsapp_neonize', 'disconnected'
                    )
                    """
                ),
                {"user_id": user_id},
            ).lastrowid

            conversation_id = conn.execute(
                text(
                    """
                    INSERT INTO whatsapp_conversations (
                        user_id, whatsapp_account_id, provider_chat_id, conversation_type, title
                    ) VALUES (
                        :user_id, :account_id, 'chat-1', 'direct', 'Alice'
                    )
                    """
                ),
                {"user_id": user_id, "account_id": account_id},
            ).lastrowid

            conn.execute(
                text(
                    """
                    INSERT INTO whatsapp_messages (
                        user_id,
                        whatsapp_account_id,
                        whatsapp_conversation_id,
                        provider_message_id,
                        provider_timestamp,
                        direction,
                        snippet,
                        priority
                    ) VALUES (
                        :user_id,
                        :account_id,
                        :conversation_id,
                        'msg-1',
                        CURRENT_TIMESTAMP,
                        'incoming',
                        'Hello',
                        'DIGEST'
                    )
                    """
                ),
                {"user_id": user_id, "account_id": account_id, "conversation_id": conversation_id},
            )

            account_row = conn.execute(
                text("SELECT created_at, updated_at FROM whatsapp_accounts WHERE id = :account_id"),
                {"account_id": account_id},
            ).one()
            conversation_row = conn.execute(
                text("SELECT created_at, updated_at FROM whatsapp_conversations WHERE id = :conversation_id"),
                {"conversation_id": conversation_id},
            ).one()
            message_row = conn.execute(
                text("SELECT created_at, updated_at FROM whatsapp_messages WHERE provider_message_id = 'msg-1'")
            ).one()

        account_columns = {column["name"]: column for column in inspect(engine).get_columns("whatsapp_accounts")}
        conversation_columns = {
            column["name"]: column for column in inspect(engine).get_columns("whatsapp_conversations")
        }
        message_columns = {column["name"]: column for column in inspect(engine).get_columns("whatsapp_messages")}

        assert account_row.created_at is not None
        assert account_row.updated_at is not None
        assert conversation_row.created_at is not None
        assert conversation_row.updated_at is not None
        assert message_row.created_at is not None
        assert message_row.updated_at is not None
        assert account_columns["created_at"]["default"] is not None
        assert account_columns["updated_at"]["default"] is not None
        assert conversation_columns["created_at"]["default"] is not None
        assert conversation_columns["updated_at"]["default"] is not None
        assert message_columns["created_at"]["default"] is not None
        assert message_columns["updated_at"]["default"] is not None
    finally:
        engine.dispose()


def test_runtime_delegations_migration_is_additive_and_downgrades_cleanly(tmp_path):
    """JAN-200 adds runtime_delegations beside the (then) untouched legacy agent_handoff_runs table."""
    db_path = tmp_path / "runtime-delegations.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "a7c4e9b21d05")
        before = set(inspect(engine).get_table_names())

        command.upgrade(alembic_config, "b8d2f4a6c1e3")
        after = set(inspect(engine).get_table_names())
        columns = {column["name"] for column in inspect(engine).get_columns("runtime_delegations")}
        unique_indexes = {
            index["name"] for index in inspect(engine).get_indexes("runtime_delegations") if index["unique"]
        }

        command.downgrade(alembic_config, "a7c4e9b21d05")
        downgraded = set(inspect(engine).get_table_names())

        assert "runtime_delegations" not in before
        assert "runtime_delegations" in after
        assert "agent_handoff_runs" in after, "JAN-200 must not touch the legacy handoff table"
        assert {"parent_task_id", "child_task_id", "claim_token", "allowed_tool_ids", "depth"} <= columns
        assert {"ix_runtime_delegations_delegation_id", "ix_runtime_delegations_child_task_id"} <= unique_indexes
        assert "runtime_delegations" not in downgraded
    finally:
        engine.dispose()


def test_qualify_legacy_google_calendar_ids_migration_prefixes_primary_events(tmp_path):
    """A pre-multi-calendar row's unqualified external_id gets the "primary:" prefix on upgrade.

    Regression test for a Codex review finding on JAN's multi-calendar sync support: without
    this migration, an existing "<event_id>" row would never match the post-upgrade
    "primary:<event_id>" external_id PlannerService.sync_all() looks up, so the next sync would
    insert a duplicate row instead of updating the existing one.
    """
    db_path = tmp_path / "legacy-calendar-ids.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "68a37cc28773")

        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (username, hashed_password, is_admin, session_generation)
                    VALUES (:username, :hashed_password, :is_admin, :session_generation)
                    """
                ),
                {
                    "username": "legacy-calendar-user",
                    "hashed_password": "hashed",
                    "is_admin": 1,
                    "session_generation": 0,
                },
            ).lastrowid
            account_id = conn.execute(
                text(
                    "INSERT INTO planner_accounts (name, provider, token, is_active, user_id) "
                    "VALUES (:name, :provider, :token, :is_active, :user_id)"
                ),
                {
                    "name": "Google Calendar",
                    "provider": "GOOGLE_CALENDAR",
                    "token": "encrypted",
                    "is_active": 1,
                    "user_id": user_id,
                },
            ).lastrowid
            # A Jira-imported event with the same unqualified shape must be left untouched.
            other_account_id = conn.execute(
                text(
                    "INSERT INTO planner_accounts (name, provider, token, is_active, user_id) "
                    "VALUES (:name, :provider, :token, :is_active, :user_id)"
                ),
                {
                    "name": "Jira",
                    "provider": "JIRA",
                    "token": "encrypted",
                    "is_active": 1,
                    "user_id": user_id,
                },
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO calendar_events (planner_account_id, external_id, summary, source_type) "
                    "VALUES (:account_id, :external_id, :summary, 'PLANNER')"
                ),
                {"account_id": account_id, "external_id": "legacy-event-1", "summary": "Standup"},
            )
            conn.execute(
                text(
                    "INSERT INTO calendar_events (planner_account_id, external_id, summary, source_type) "
                    "VALUES (:account_id, :external_id, :summary, 'PLANNER')"
                ),
                {"account_id": other_account_id, "external_id": "JIRA-123", "summary": "Sprint review"},
            )

        command.upgrade(alembic_config, "head")

        with engine.connect() as conn:
            google_external_id = conn.execute(
                text("SELECT external_id FROM calendar_events WHERE planner_account_id = :id"),
                {"id": account_id},
            ).scalar_one()
            jira_external_id = conn.execute(
                text("SELECT external_id FROM calendar_events WHERE planner_account_id = :id"),
                {"id": other_account_id},
            ).scalar_one()
        assert google_external_id == "primary:legacy-event-1"
        assert jira_external_id == "JIRA-123"

        command.downgrade(alembic_config, "68a37cc28773")

        with engine.connect() as conn:
            google_external_id = conn.execute(
                text("SELECT external_id FROM calendar_events WHERE planner_account_id = :id"),
                {"id": account_id},
            ).scalar_one()
        assert google_external_id == "legacy-event-1"
    finally:
        engine.dispose()


def test_split_trigger_history_migration_rewrites_saved_events_selections(tmp_path):
    """JAN-195: saved ``events`` selections and policy overrides move to ``trigger-history``."""
    db_path = tmp_path / "split-trigger-history.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def definition_tools(conn, definition_id):
        raw = conn.execute(
            text("SELECT tool_ids FROM trigger_definitions WHERE id = :id"), {"id": definition_id}
        ).scalar_one()
        return json.loads(raw)

    def insert_definition(conn, user_id, name, tool_ids):
        return conn.execute(
            text(
                "INSERT INTO trigger_definitions "
                "(user_id, name, objective, tool_ids, kind, timezone, enabled, origin, created_at, updated_at) "
                "VALUES (:user_id, :name, 'Check.', :tool_ids, 'manual', 'UTC', 1, 'user', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"user_id": user_id, "name": name, "tool_ids": json.dumps(tool_ids)},
        ).lastrowid

    try:
        command.upgrade(alembic_config, "a9afd7fc84dd")

        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('split-user', 'hashed', 1, 0)"
                )
            ).lastrowid
            with_events = insert_definition(conn, user_id, "Self check", ["core", "events", "calendar"])
            without_events = insert_definition(conn, user_id, "Calendar only", ["core", "calendar"])
            conn.execute(
                text(
                    "INSERT INTO tool_policies (user_id, tool_id, block_without_confirmation, updated_at) "
                    "VALUES (:user_id, 'events', 1, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )

        command.upgrade(alembic_config, "c4e1a7b9d2f3")

        with engine.connect() as conn:
            assert definition_tools(conn, with_events) == ["core", "trigger-history", "calendar"]
            assert definition_tools(conn, without_events) == ["core", "calendar"]
            policies = dict(
                conn.execute(
                    text("SELECT tool_id, block_without_confirmation FROM tool_policies WHERE user_id = :id"),
                    {"id": user_id},
                ).all()
            )
        assert policies == {"events": 1, "trigger-history": 1}

        command.downgrade(alembic_config, "a9afd7fc84dd")

        with engine.connect() as conn:
            assert definition_tools(conn, with_events) == ["core", "events", "calendar"]
            remaining = (
                conn.execute(text("SELECT tool_id FROM tool_policies WHERE user_id = :id"), {"id": user_id})
                .scalars()
                .all()
            )
        assert remaining == ["events"]
    finally:
        engine.dispose()


def test_secretary_planner_suggest_migration_adds_the_tool_to_existing_secretaries(tmp_path):
    """Existing built-in Secretaries get planner.suggest once; others are untouched; downgrade removes it."""
    db_path = tmp_path / "secretary-planner-suggest.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def tools(conn, definition_id):
        raw = conn.execute(text("SELECT tool_ids FROM trigger_definitions WHERE id = :id"), {"id": definition_id})
        return json.loads(raw.scalar_one())

    def insert(conn, user_id, name, builtin_key, tool_ids):
        return conn.execute(
            text(
                "INSERT INTO trigger_definitions (user_id, builtin_key, name, objective, tool_ids, kind, timezone, "
                "enabled, origin, created_at, updated_at) VALUES (:user_id, :builtin_key, :name, 'Think.', "
                ":tool_ids, 'manual', 'UTC', 1, 'system', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"user_id": user_id, "builtin_key": builtin_key, "name": name, "tool_ids": json.dumps(tool_ids)},
        ).lastrowid

    try:
        command.upgrade(alembic_config, "d8a0c2e4f6b8")
        with engine.begin() as conn:
            users = [
                conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                        "VALUES (:name, 'hashed', 0, 0)"
                    ),
                    {"name": name},
                ).lastrowid
                for name in ("first", "second")
            ]
            secretary = insert(conn, users[0], "Secretary", "builtin.secretary", ["core", "calendar"])
            already = insert(conn, users[1], "Secretary", "builtin.secretary", ["core", "planner.suggest"])
            briefing = insert(conn, users[0], "Morning briefing", "builtin.briefing", ["core"])

        command.upgrade(alembic_config, "e1b3d5f7a9c2")
        with engine.connect() as conn:
            assert tools(conn, secretary) == ["core", "calendar", "planner.suggest"]
            assert tools(conn, already) == ["core", "planner.suggest"]
            assert tools(conn, briefing) == ["core"]

        command.downgrade(alembic_config, "d8a0c2e4f6b8")
        with engine.connect() as conn:
            assert tools(conn, secretary) == ["core", "calendar"]
            assert tools(conn, already) == ["core"]
    finally:
        engine.dispose()


def test_unattended_allowed_migration_inverts_block_without_confirmation(tmp_path):
    """JAN-255: a blocked tool becomes "not allowed unattended", an unblocked one "allowed", and back."""
    db_path = tmp_path / "unattended-allowed.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "d130c10835c8")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('policy-user', 'hashed', 1, 0)"
                )
            ).lastrowid
            for tool_id, block in (("memory", 1), ("mail.send", 0)):
                conn.execute(
                    text(
                        "INSERT INTO tool_policies (user_id, tool_id, block_without_confirmation, updated_at) "
                        "VALUES (:user_id, :tool_id, :block, CURRENT_TIMESTAMP)"
                    ),
                    {"user_id": user_id, "tool_id": tool_id, "block": block},
                )

        command.upgrade(alembic_config, "f2a7c3d9e1b4")
        with engine.connect() as conn:
            allowed = dict(conn.execute(text("SELECT tool_id, unattended_allowed FROM tool_policies")).all())
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(tool_policies)")).all()}
        assert allowed == {"memory": 0, "mail.send": 1}
        assert "block_without_confirmation" not in columns

        command.downgrade(alembic_config, "d130c10835c8")
        with engine.connect() as conn:
            blocked = dict(conn.execute(text("SELECT tool_id, block_without_confirmation FROM tool_policies")).all())
        assert blocked == {"memory": 1, "mail.send": 0}
    finally:
        engine.dispose()


def test_event_owner_and_trigger_lineage_migration_keeps_existing_rows(tmp_path):
    """JAN-272/274: existing events become system rows, existing triggers roots, and downgrade drops both."""
    db_path = tmp_path / "event-owner.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "f2a7c3d9e1b4")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO events (event_type, source, message) VALUES ('INFO', 'Old', 'legacy')"))
            conn.execute(
                text(
                    "INSERT INTO runtime_triggers (trigger_id, idempotency_key, task_id, kind, source, payload, "
                    "status, attempt_count, created_at, updated_at) VALUES ('t-1', 'k-1', 'task', 'manual', 'api', "
                    "'{}', 'queued', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        command.upgrade(alembic_config, "a3e5c7d9f1b2")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT message, user_id FROM events")).all() == [("legacy", None)]
            assert conn.execute(text("SELECT parent_trigger_id, chain_depth FROM runtime_triggers")).all() == [
                (None, 0)
            ]
        event_indexes = {index["name"] for index in inspect(engine).get_indexes("events")}
        event_fks = inspect(engine).get_foreign_keys("events")
        assert "ix_events_user_id" in event_indexes
        assert [(fk["referred_table"], fk["constrained_columns"]) for fk in event_fks] == [("users", ["user_id"])]

        command.downgrade(alembic_config, "f2a7c3d9e1b4")
        event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
        trigger_columns = {column["name"] for column in inspect(engine).get_columns("runtime_triggers")}
        assert "user_id" not in event_columns
        assert not {"parent_trigger_id", "chain_depth"} & trigger_columns
        with engine.connect() as conn:
            assert conn.execute(text("SELECT message FROM events")).scalars().all() == ["legacy"]
    finally:
        engine.dispose()


def test_trigger_proposal_migration_keeps_existing_definitions_and_downgrades_cleanly(tmp_path):
    """JAN-235: the proposal columns are additive; existing rows keep proposal_status NULL."""
    db_path = tmp_path / "trigger-proposals.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    proposal_columns = {
        "proposal_status",
        "proposal_rationale",
        "proposed_at",
        "proposal_source",
        "supersedes_id",
        "approved_at",
        "approved_by_user_id",
        "approval_fingerprint",
        "rejected_at",
        "rejection_reason",
    }

    try:
        command.upgrade(alembic_config, "a3e5c7d9f1b2")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('proposal-user', 'hashed', 1, 0)"
                )
            ).lastrowid
            once = '{"type": "once", "at": "2026-12-01T09:00:00+00:00"}'
            for origin, schedule in (("user", None), ("agent", once)):
                conn.execute(
                    text(
                        "INSERT INTO trigger_definitions (user_id, name, objective, tool_ids, kind, schedule, "
                        "timezone, enabled, origin, created_at, updated_at) VALUES (:user_id, :name, 'x', '[]', "
                        "'schedule', :schedule, 'UTC', 1, :origin, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"user_id": user_id, "name": f"def-{origin}", "schedule": schedule, "origin": origin},
                )

        command.upgrade(alembic_config, "b7d2e9f4a1c3")
        inspector = inspect(engine)
        columns = {column["name"] for column in inspector.get_columns("trigger_definitions")}
        indexes = {index["name"] for index in inspector.get_indexes("trigger_definitions")}
        assert proposal_columns <= columns
        assert "ix_trigger_definitions_user_proposal_status" in indexes
        with engine.connect() as conn:
            statuses = conn.execute(text("SELECT origin, proposal_status FROM trigger_definitions")).all()
        assert sorted(statuses) == [("agent", None), ("user", None)]

        command.downgrade(alembic_config, "a3e5c7d9f1b2")
        columns = {column["name"] for column in inspect(engine).get_columns("trigger_definitions")}
        assert not proposal_columns & columns
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM trigger_definitions")).scalar() == 2
    finally:
        engine.dispose()


def test_conversation_tool_calls_migration_keeps_existing_messages_and_downgrades_cleanly(tmp_path):
    """JAN-249: the tool-call table is additive; existing messages survive and get no rows."""
    db_path = tmp_path / "conversation-tool-calls.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "b7d2e9f4a1c3")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('tool-call-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conversation_id = conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'old chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            message_id = conn.execute(
                text(
                    "INSERT INTO conversation_messages (conversation_id, role, content, created_at) "
                    "VALUES (:conversation_id, 'ASSISTANT', 'hello', CURRENT_TIMESTAMP)"
                ),
                {"conversation_id": conversation_id},
            ).lastrowid

        command.upgrade(alembic_config, "c4e8a1f6b2d9")
        inspector = inspect(engine)
        assert "conversation_tool_calls" in inspector.get_table_names()
        foreign_keys = inspector.get_foreign_keys("conversation_tool_calls")
        assert [(fk["referred_table"], fk["options"].get("ondelete")) for fk in foreign_keys] == [
            ("conversation_messages", "CASCADE")
        ]
        with engine.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_tool_calls")).scalar() == 0
            # Database-side defaults cover rows written without the ORM.
            conn.execute(
                text(
                    "INSERT INTO conversation_tool_calls (message_id, position, call_id, tool_name, phase) "
                    "VALUES (:message_id, 0, 'run-1', 'search_emails', 'end')"
                ),
                {"message_id": message_id},
            )
            row = conn.execute(
                text("SELECT args_truncated, result_truncated, created_at FROM conversation_tool_calls")
            ).one()
        assert row[0] == 0 and row[1] == 0 and row[2] is not None

        command.downgrade(alembic_config, "b7d2e9f4a1c3")
        assert "conversation_tool_calls" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 1
    finally:
        engine.dispose()


def test_tool_call_approval_column_is_additive_and_nullable(tmp_path):
    """JAN-254: existing tool-call rows keep a NULL approval; downgrade drops only the column."""
    db_path = tmp_path / "tool-call-approval.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "c4e8a1f6b2d9")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('approval-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conversation_id = conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'old chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            message_id = conn.execute(
                text(
                    "INSERT INTO conversation_messages (conversation_id, role, content, created_at) "
                    "VALUES (:conversation_id, 'ASSISTANT', 'hello', CURRENT_TIMESTAMP)"
                ),
                {"conversation_id": conversation_id},
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO conversation_tool_calls (message_id, position, call_id, tool_name, phase) "
                    "VALUES (:message_id, 0, 'run-1', 'send_email', 'end')"
                ),
                {"message_id": message_id},
            )

        command.upgrade(alembic_config, "d9f3b2a7c5e1")
        columns = {column["name"]: column for column in inspect(engine).get_columns("conversation_tool_calls")}
        assert columns["approval"]["nullable"] is True
        with engine.begin() as conn:
            assert conn.execute(text("SELECT approval FROM conversation_tool_calls")).scalar() is None
            conn.execute(text("UPDATE conversation_tool_calls SET approval = 'denied'"))

        command.downgrade(alembic_config, "c4e8a1f6b2d9")
        assert "approval" not in {column["name"] for column in inspect(engine).get_columns("conversation_tool_calls")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_tool_calls")).scalar() == 1
    finally:
        engine.dispose()


def test_conversation_ui_blocks_migration_keeps_existing_messages_and_downgrades_cleanly(tmp_path):
    """JAN-252: the UI-block table is additive; existing messages survive and get no rows."""
    db_path = tmp_path / "conversation-ui-blocks.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "d9f3b2a7c5e1")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('ui-block-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conversation_id = conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'old chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            message_id = conn.execute(
                text(
                    "INSERT INTO conversation_messages (conversation_id, role, content, created_at) "
                    "VALUES (:conversation_id, 'ASSISTANT', 'hello', CURRENT_TIMESTAMP)"
                ),
                {"conversation_id": conversation_id},
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO conversation_tool_calls (message_id, position, call_id, tool_name, phase) "
                    "VALUES (:message_id, 0, 'run-1', 'search_emails', 'end')"
                ),
                {"message_id": message_id},
            )

        command.upgrade(alembic_config, "e3a7c1b9d4f2")
        inspector = inspect(engine)
        assert "conversation_ui_blocks" in inspector.get_table_names()
        foreign_keys = inspector.get_foreign_keys("conversation_ui_blocks")
        assert [(fk["referred_table"], fk["options"].get("ondelete")) for fk in foreign_keys] == [
            ("conversation_messages", "CASCADE")
        ]
        indexes = {index["name"] for index in inspector.get_indexes("conversation_ui_blocks")}
        assert "ix_conversation_ui_blocks_message_id" in indexes
        with engine.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_tool_calls")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_ui_blocks")).scalar() == 0
            # The database-side created_at default covers rows written without the ORM.
            conn.execute(
                text(
                    "INSERT INTO conversation_ui_blocks "
                    "(message_id, position, block_id, block_type, schema_version, payload) "
                    "VALUES (:message_id, 0, 'blk_1', 'card', 1, :payload)"
                ),
                {"message_id": message_id, "payload": '{"id": "blk_1", "version": 1, "type": "card", "title": "x"}'},
            )
            assert conn.execute(text("SELECT created_at FROM conversation_ui_blocks")).scalar() is not None

        command.downgrade(alembic_config, "d9f3b2a7c5e1")
        assert "conversation_ui_blocks" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 1
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_tool_calls")).scalar() == 1
    finally:
        engine.dispose()


def test_generated_images_migration_is_additive_and_downgrades_cleanly(tmp_path):
    """JAN-253: the image table and settings columns are additive; existing settings keep image generation off."""
    db_path = tmp_path / "generated-images.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "e3a7c1b9d4f2")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('image-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conversation_id = conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'old chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            conn.execute(text("INSERT INTO settings (initial_sync_email_limit, openai_model) VALUES (100, 'gpt-4o')"))

        command.upgrade(alembic_config, "f4b8d2c6a1e3")
        inspector = inspect(engine)
        assert "generated_images" in inspector.get_table_names()
        foreign_keys = {
            (fk["constrained_columns"][0], fk["referred_table"], fk["options"].get("ondelete"))
            for fk in inspector.get_foreign_keys("generated_images")
        }
        assert foreign_keys == {
            ("user_id", "users", "CASCADE"),
            ("conversation_id", "conversations", "CASCADE"),
            ("message_id", "conversation_messages", "SET NULL"),
        }
        indexes = {index["name"] for index in inspector.get_indexes("generated_images")}
        assert {"ix_generated_images_user_id", "ix_generated_images_conversation_id"} <= indexes
        with engine.begin() as conn:
            row = conn.execute(text("SELECT image_provider, image_model, openai_model FROM settings")).one()
            assert tuple(row) == (None, None, "gpt-4o")
            conn.execute(
                text(
                    "INSERT INTO generated_images (id, user_id, conversation_id, prompt, provider, model, path, "
                    "mime_type, size_bytes) VALUES ('img_1', :user_id, :conversation_id, 'a fox', 'openai', "
                    "'gpt-image-1', '1/img_1.png', 'image/png', 10)"
                ),
                {"user_id": user_id, "conversation_id": conversation_id},
            )
            assert conn.execute(text("SELECT created_at FROM generated_images")).scalar() is not None

        command.downgrade(alembic_config, "e3a7c1b9d4f2")
        inspector = inspect(engine)
        assert "generated_images" not in inspector.get_table_names()
        assert {"image_provider", "image_model"}.isdisjoint(
            column["name"] for column in inspector.get_columns("settings")
        )
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM conversations")).scalar() == 1
            assert conn.execute(text("SELECT openai_model FROM settings")).scalar() == "gpt-4o"
    finally:
        engine.dispose()


def test_planner_account_names_become_unique_per_owner(tmp_path):
    """JAN-318: two users can each own a "Google Calendar" account; one user still cannot hold two."""
    db_path = tmp_path / "planner-account-names.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def insert_account(conn, user_id, name):
        conn.execute(
            text(
                "INSERT INTO planner_accounts (user_id, name, provider, token, is_active) "
                "VALUES (:user_id, :name, 'GOOGLE_CALENDAR', 'tok', 1)"
            ),
            {"user_id": user_id, "name": name},
        )

    try:
        command.upgrade(alembic_config, "b7d1e4a9c2f6")
        with engine.begin() as conn:
            user_ids = [
                conn.execute(
                    text(
                        "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                        "VALUES (:name, 'hashed', 0, 0)"
                    ),
                    {"name": name},
                ).lastrowid
                for name in ("alice", "bob")
            ]
            insert_account(conn, user_ids[0], "Google Calendar")
        # The previous head refuses the second user's "Google Calendar" outright.
        with pytest.raises(IntegrityError), engine.begin() as conn:
            insert_account(conn, user_ids[1], "Google Calendar")

        command.upgrade(alembic_config, "c5d2e8f1a4b7")
        inspector = inspect(engine)
        indexes = {index["name"]: index for index in inspector.get_indexes("planner_accounts")}
        assert not indexes["ix_planner_accounts_name"]["unique"]
        constraints = {
            constraint["name"]: constraint["column_names"]
            for constraint in inspector.get_unique_constraints("planner_accounts")
        }
        assert constraints["uq_planner_accounts_user_name"] == ["user_id", "name"]
        with engine.begin() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM planner_accounts")).scalar() == 1
            insert_account(conn, user_ids[1], "Google Calendar")
        with pytest.raises(IntegrityError), engine.begin() as conn:
            insert_account(conn, user_ids[1], "Google Calendar")

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM planner_accounts WHERE user_id = :user_id"), {"user_id": user_ids[1]})
        command.downgrade(alembic_config, "b7d1e4a9c2f6")
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("planner_accounts")}
        assert indexes["ix_planner_accounts_name"]["unique"]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM planner_accounts")).scalars().all() == ["Google Calendar"]
    finally:
        engine.dispose()


def test_chat_mode_migration_keeps_existing_overrides_and_downgrades_cleanly(tmp_path):
    """JAN-306: existing unattended overrides survive, chat_mode starts NULL, and a chat-only row can exist."""
    db_path = tmp_path / "chat-mode.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "c5d2e8f1a4b7")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('chat-mode-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            for tool_id, allowed in (("memory", 0), ("mail.send", 1)):
                conn.execute(
                    text(
                        "INSERT INTO tool_policies (user_id, tool_id, unattended_allowed, updated_at) "
                        "VALUES (:user_id, :tool_id, :allowed, CURRENT_TIMESTAMP)"
                    ),
                    {"user_id": user_id, "tool_id": tool_id, "allowed": allowed},
                )

        command.upgrade(alembic_config, "c8e2f5a1d7b3")
        with engine.begin() as conn:
            rows = {
                tool_id: (allowed, chat_mode)
                for tool_id, allowed, chat_mode in conn.execute(
                    text("SELECT tool_id, unattended_allowed, chat_mode FROM tool_policies")
                ).all()
            }
            assert rows == {"memory": (0, None), "mail.send": (1, None)}
            # A row may now override only the In chat switch.
            conn.execute(
                text(
                    "INSERT INTO tool_policies (user_id, tool_id, unattended_allowed, chat_mode, updated_at) "
                    "VALUES (:user_id, 'github', NULL, 'off', CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )

        command.downgrade(alembic_config, "c5d2e8f1a4b7")
        with engine.connect() as conn:
            kept = dict(conn.execute(text("SELECT tool_id, unattended_allowed FROM tool_policies")).all())
            columns = {row[1]: row[3] for row in conn.execute(text("PRAGMA table_info(tool_policies)")).all()}
        assert kept == {"memory": 0, "mail.send": 1}
        assert "chat_mode" not in columns
        assert columns["unattended_allowed"] == 1  # NOT NULL again
    finally:
        engine.dispose()


def test_delegation_requeue_migration_keeps_existing_delegations_and_downgrades_cleanly(tmp_path):
    """JAN-207: existing delegation rows read as never requeued; the downgrade drops only the new columns."""
    db_path = tmp_path / "delegation-requeue.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "c3e5a7f9b1d2")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('delegation-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO runtime_delegations (delegation_id, user_id, parent_task_id, child_task_id, depth, "
                    "objective, requested_tool_ids, allowed_tool_ids, status, created_at, updated_at) "
                    "VALUES ('d-1', :user_id, 'chat-conversation-1', 'delegation-d-1', 1, 'Look it up', '[]', "
                    "'[\"core\"]', 'running', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )

        command.upgrade(alembic_config, "f2a7c4e9b1d3")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "f2a7c4e9b1d3"
            row = conn.execute(
                text("SELECT status, requeue_count, requeued_at FROM runtime_delegations WHERE delegation_id = 'd-1'")
            ).one()
        assert tuple(row) == ("running", 0, None)

        command.downgrade(alembic_config, "c3e5a7f9b1d2")
        with engine.connect() as conn:
            columns = {column["name"] for column in inspect(conn).get_columns("runtime_delegations")}
            kept = conn.execute(text("SELECT objective FROM runtime_delegations")).scalars().all()
        assert {"requeue_count", "requeued_at"}.isdisjoint(columns)
        assert kept == ["Look it up"]
    finally:
        engine.dispose()


def test_runtime_trigger_definition_name_migration_backfills_and_downgrades(tmp_path):
    """JAN-204: linked runs copy their definition's name; runs of a deleted definition get a placeholder."""
    db_path = tmp_path / "definition-name.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def add_run(conn, trigger_id: str, definition_id: int | None, payload: dict) -> None:
        conn.execute(
            text(
                "INSERT INTO runtime_triggers (trigger_id, idempotency_key, task_id, kind, source, payload, status,"
                " attempt_count, definition_id, chain_depth, created_at, updated_at) VALUES (:trigger_id, 'key',"
                " :trigger_id, 'manual', 'test', :payload, 'completed', 1, :definition_id, 0, '2026-10-01 09:00:00',"
                " '2026-10-01 09:00:00')"
            ),
            {"trigger_id": trigger_id, "definition_id": definition_id, "payload": json.dumps(payload)},
        )

    try:
        command.upgrade(alembic_config, "f2a7c4e9b1d3")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO trigger_definitions (id, user_id, name, objective, tool_ids, kind, timezone, enabled,"
                    " origin, created_at, updated_at) VALUES (7, 1, 'Weekly review', 'Review', '[]', 'manual', 'UTC',"
                    " 1, 'user', '2026-10-01 09:00:00', '2026-10-01 09:00:00')"
                )
            )
            add_run(conn, "linked", 7, {"objective": "Review", "definition_id": 7})
            add_run(conn, "orphaned", None, {"objective": "Review", "definition_id": 9})
            add_run(conn, "adhoc", None, {"objective": "Check the inbox"})

        command.upgrade(alembic_config, "d4f7b2c9e1a6")
        with engine.connect() as conn:
            names = dict(conn.execute(text("SELECT trigger_id, definition_name FROM runtime_triggers")).all())
        assert names == {"linked": "Weekly review", "orphaned": "Deleted trigger definition", "adhoc": None}

        command.downgrade(alembic_config, "f2a7c4e9b1d3")
        columns = {column["name"] for column in inspect(engine).get_columns("runtime_triggers")}
        assert "definition_name" not in columns
    finally:
        engine.dispose()


def test_conversation_origin_migration_keeps_existing_conversations_and_downgrades_cleanly(tmp_path):
    """JAN-319: existing conversations become user-started and read; the columns go again on downgrade."""
    db_path = tmp_path / "conversation-origin.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "d4f7b2c9e1a6")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('origin-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conversation_id = conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'old chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO conversation_messages (conversation_id, role, content, created_at) "
                    "VALUES (:conversation_id, 'USER', 'hello', CURRENT_TIMESTAMP)"
                ),
                {"conversation_id": conversation_id},
            )

        command.upgrade(alembic_config, "d7f9b1c3e5a7")
        with engine.begin() as conn:
            row = conn.execute(text("SELECT title, origin, origin_trigger_id, unread FROM conversations")).one()
            assert tuple(row) == ("old chat", "user", None, 0)
            # Database-side defaults cover rows written without the ORM.
            conn.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (:user_id, 'raw', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )
            raw = conn.execute(text("SELECT origin, unread FROM conversations WHERE title = 'raw'")).one()
            assert tuple(raw) == ("user", 0)
        indexes = {index["name"] for index in inspect(engine).get_indexes("conversations")}
        assert "ix_conversations_origin_trigger_id" in indexes

        command.downgrade(alembic_config, "d4f7b2c9e1a6")
        with engine.connect() as conn:
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(conversations)")).all()}
            assert conn.execute(text("SELECT COUNT(*) FROM conversation_messages")).scalar() == 1
        assert {"origin", "origin_trigger_id", "unread"}.isdisjoint(columns)
    finally:
        engine.dispose()


def test_runtime_tool_selections_migration_is_additive_and_downgrades_cleanly(tmp_path):
    """JAN-300: the new table appears at the head with its unique step key; the downgrade drops only it."""
    db_path = tmp_path / "tool-selections.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "b8d4f2a6c1e9")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('selection-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            insert = text(
                "INSERT INTO runtime_tool_selections (user_id, task_id, step_id, task_mode, source, reason, "
                "tool_ids, proposed_tool_ids, trimmed_tool_ids, widened_tool_ids, created_at, updated_at) "
                "VALUES (:user_id, 't', 't::step-1', 'trigger', 'selected', 'why', '[\"core\"]', '[]', '[]', '[]', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
            conn.execute(insert, {"user_id": user_id})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert, {"user_id": user_id})

        command.downgrade(alembic_config, "d7f9b1c3e5a7")
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
        assert "runtime_tool_selections" not in tables
        assert "conversations" in tables
    finally:
        engine.dispose()


def test_adaptive_character_state_migration_is_additive_and_downgrades_cleanly(tmp_path):
    """JAN-301: existing profiles keep the built-in envelope; the downgrade drops only the new schema."""
    db_path = tmp_path / "character-state.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    new_tables = {"character_states", "character_state_proposals", "character_state_changes"}

    try:
        command.upgrade(alembic_config, "b8d4f2a6c1e9")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('character-user', 'hashed', 0, 0)"
                )
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO character_profiles (user_id, constitution_id, version, name, identity, core_values, "
                    "non_negotiables, traits, exploration_rate, created_at, updated_at) VALUES (:user_id, "
                    "'user-1-character', 3, 'Pilot', 'Plain.', '[\"Be useful\"]', '[\"No guessing\"]', '[]', 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )

        command.upgrade(alembic_config, "b4d7e2a9c6f1")
        inspector = inspect(engine)
        assert new_tables <= set(inspector.get_table_names())
        user_fks = {
            table: {
                (fk["constrained_columns"][0], fk["referred_table"], fk["options"].get("ondelete"))
                for fk in inspector.get_foreign_keys(table)
            }
            for table in new_tables
        }
        assert user_fks["character_states"] == {("user_id", "users", "CASCADE")}
        assert user_fks["character_state_proposals"] == {("user_id", "users", "CASCADE")}
        assert user_fks["character_state_changes"] == {
            ("user_id", "users", "CASCADE"),
            ("proposal_id", "character_state_proposals", "SET NULL"),
        }
        unique = {index["name"]: index["unique"] for index in inspector.get_indexes("character_states")}
        assert unique["ix_character_states_user_id"]
        with engine.begin() as conn:
            row = conn.execute(text("SELECT name, version, state_envelope FROM character_profiles")).one()
            assert tuple(row) == ("Pilot", 3, None)
            conn.execute(
                text(
                    "INSERT INTO character_states (user_id, tone, verbosity, proactivity, updated_at) "
                    "VALUES (:user_id, 'warm', 'brief', 'balanced', CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )
            assert conn.execute(text("SELECT revision FROM character_states")).scalar() == 0
            conn.execute(
                text(
                    "INSERT INTO character_state_proposals (user_id, changes, rationale, created_at) "
                    "VALUES (:user_id, '{\"tone\": \"warm\"}', 'Asked for warmth', CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            )
            assert conn.execute(text("SELECT status FROM character_state_proposals")).scalar() == "proposed"

        command.downgrade(alembic_config, "b8d4f2a6c1e9")
        inspector = inspect(engine)
        assert new_tables.isdisjoint(inspector.get_table_names())
        assert "state_envelope" not in {column["name"] for column in inspector.get_columns("character_profiles")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name, version FROM character_profiles")).one() == ("Pilot", 3)
    finally:
        engine.dispose()


def _drift_include_name(name, type_, parent_names) -> bool:
    """Mirrors ``alembic/env.py``: runtime-managed FTS5 tables are not part of the migrated schema."""
    return not (type_ == "table" and name and name.startswith("knowledge_fts_"))


def test_migrated_schema_matches_the_orm_models(tmp_path):
    """JAN-338: a fresh database upgraded to head has no autogenerate differences from the models.

    Fails whenever a model change ships without its migration (or the other way round), so a fresh
    install (migrations) and ``create_all`` (tests) can no longer disagree on columns, types,
    nullability, or indexes.
    """
    db_path = tmp_path / "drift.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "head")
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True, "include_name": _drift_include_name})
            diffs = compare_metadata(context, models.database.Base.metadata)
        assert diffs == [], f"Models and migrations drifted; add a migration or fix the model: {diffs!r}"
        # The literal ``alembic check`` (through ``alembic/env.py``) agrees.
        command.check(alembic_config)
    finally:
        engine.dispose()


def test_schema_drift_migration_widens_topic_status_and_keeps_rows(tmp_path):
    """JAN-338: ``tracked_topics.status`` fits ``CANDIDATE``; rows, owners, and constraints survive both ways."""
    db_path = tmp_path / "schema-drift.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    def status_column():
        return next(column for column in inspect(engine).get_columns("tracked_topics") if column["name"] == "status")

    def topics():
        with engine.connect() as conn:
            return dict(conn.execute(text("SELECT slug, status FROM tracked_topics")).all())

    try:
        command.upgrade(alembic_config, "b4d7e2a9c6f1")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('topic-owner', 'hashed', 1, 0)"
                )
            ).lastrowid
            # The owner columns and the fingerprint were already strict before this revision.
            for table in ("email_accounts", "planner_accounts", "rules"):
                with pytest.raises(IntegrityError), conn.begin_nested():
                    conn.execute(text(f"INSERT INTO {table} (name, user_id) VALUES ('orphan', NULL)"))
            conn.execute(
                text("INSERT INTO error_groups (fingerprint, source, message_template) VALUES ('fp', 's', 'm')")
            )
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(
                    text("INSERT INTO error_groups (fingerprint, source, message_template) VALUES ('fp', 's', 'x')")
                )
            for slug, status in (
                ("candidate", "CANDIDATE"),
                ("active", "ACTIVE"),
                ("lower", "snoozed"),
                ("mixed", "Resolved"),
                ("unknown", "archived"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO tracked_topics (user_id, slug, title, status) "
                        "VALUES (:user_id, :slug, :slug, :status)"
                    ),
                    {"user_id": user_id, "slug": slug, "status": status},
                )
            conn.execute(
                text("INSERT INTO tracked_topics (user_id, slug, title) VALUES (:user_id, 'defaulted', 'd')"),
                {"user_id": user_id},
            )
            topic_id = conn.execute(text("SELECT id FROM tracked_topics WHERE slug = 'candidate'")).scalar()
            conn.execute(
                text(
                    "INSERT INTO topic_suggestions (user_id, topic_id, suggestion_type, status, reason, confidence) "
                    "VALUES (:user_id, :topic_id, 'CREATE', 'PENDING', 'r', 'HIGH')"
                ),
                {"user_id": user_id, "topic_id": topic_id},
            )
        assert str(status_column()["type"]) == "VARCHAR(8)"

        command.upgrade(alembic_config, "c3e9a1f7d2b8")
        assert str(status_column()["type"]) == "VARCHAR(9)"
        assert status_column()["nullable"] is False
        upgraded = {
            "candidate": "CANDIDATE",
            "active": "ACTIVE",
            "lower": "SNOOZED",
            "mixed": "RESOLVED",
            "unknown": "archived",
            "defaulted": "ACTIVE",
        }
        assert topics() == upgraded
        inspector = inspect(engine)
        assert {index["name"] for index in inspector.get_indexes("tracked_topics")} >= {"ix_tracked_topics_status"}
        assert {constraint["name"] for constraint in inspector.get_unique_constraints("tracked_topics")} == {
            "uq_tracked_topics_user_slug"
        }
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO tracked_topics (user_id, slug, title) VALUES (:user_id, 'after', 'a')"),
                {"user_id": user_id},
            )
            assert conn.execute(text("SELECT status FROM tracked_topics WHERE slug = 'after'")).scalar() == "ACTIVE"
            assert conn.execute(text("SELECT topic_id FROM topic_suggestions")).scalar() == topic_id
            conn.execute(text("DELETE FROM tracked_topics WHERE slug = 'after'"))

        command.downgrade(alembic_config, "b4d7e2a9c6f1")
        assert str(status_column()["type"]) == "VARCHAR(8)"
        assert topics() == upgraded
    finally:
        engine.dispose()


def test_mcp_tool_annotations_migration_is_additive_and_downgrades_cleanly(tmp_path):
    """JAN-343: discovered tools gain a nullable ``annotations`` column; existing rows keep NULL; downgrade drops it."""
    db_path = tmp_path / "mcp-annotations.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "d8a2f4c6b1e9")
        with engine.begin() as conn:
            user_id = conn.execute(
                text(
                    "INSERT INTO users (username, hashed_password, is_admin, session_generation) "
                    "VALUES ('mcp-owner', 'hashed', 0, 0)"
                )
            ).lastrowid
            server_id = conn.execute(
                text(
                    "INSERT INTO mcp_servers (user_id, provider_name, endpoint, transport, enabled, discovery_status, "
                    "created_at, updated_at) VALUES (:user_id, 'Acme', 'http://127.0.0.1/mcp', 'STREAMABLE_HTTP', 1, "
                    "'READY', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"user_id": user_id},
            ).lastrowid
            conn.execute(
                text(
                    "INSERT INTO mcp_discovered_tools (server_id, upstream_name, namespaced_id, description, "
                    "input_schema, enabled, created_at, updated_at) VALUES (:server_id, 'search', 'mcp.1.search', '', "
                    "'{}', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"server_id": server_id},
            )

        command.upgrade(alembic_config, "e6b2d8f4a1c3")
        columns = {column["name"]: column for column in inspect(engine).get_columns("mcp_discovered_tools")}
        assert columns["annotations"]["nullable"] is True
        with engine.connect() as conn:
            assert conn.execute(text("SELECT annotations FROM mcp_discovered_tools")).scalar() is None
            assert conn.execute(text("SELECT COUNT(*) FROM mcp_discovered_tools")).scalar() == 1

        command.downgrade(alembic_config, "d8a2f4c6b1e9")
        assert "annotations" not in {column["name"] for column in inspect(engine).get_columns("mcp_discovered_tools")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM mcp_discovered_tools")).scalar() == 1
    finally:
        engine.dispose()


def test_planner_utc_migration_makes_the_next_sync_a_baseline(tmp_path):
    """JAN-394: clearing last_sync_at lets the next sync rewrite stored times as UTC without change events."""
    db_path = tmp_path / "planner-utc.db"
    database_url = f"sqlite:///{db_path}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "b7e1d4c9a2f6")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('u', 'x', 0)"))
            conn.execute(
                text(
                    "INSERT INTO planner_accounts (name, provider, user_id, is_active, last_sync_at) "
                    "VALUES ('Work', 'GOOGLE_CALENDAR', 1, 1, '2026-11-10 09:00:00')"
                )
            )

        command.upgrade(alembic_config, "c3f8a1d6e9b2")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT last_sync_at FROM planner_accounts")).scalar_one() is None

        command.downgrade(alembic_config, "b7e1d4c9a2f6")
    finally:
        engine.dispose()


def test_oauth_connections_migration_backfills_google_calendar_connections(tmp_path):
    """A Google calendar connected before connections existed keeps working: its token becomes a connection."""
    database_url = f"sqlite:///{tmp_path / 'oauth-connections.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "c3f8a1d6e9b2")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password, is_admin) VALUES ('admin', 'hash', 1)"))
            user_id = conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO planner_accounts (user_id, name, provider, token, is_active) "
                    "VALUES (:u, 'Google Calendar', 'GOOGLE_CALENDAR', 'encrypted-blob', 1)"
                ),
                {"u": user_id},
            )

        command.upgrade(alembic_config, "head")

        with engine.connect() as conn:
            row = conn.execute(text("SELECT user_id, provider, credentials FROM oauth_connections")).one()
        assert tuple(row) == (user_id, "google", "encrypted-blob")
    finally:
        engine.dispose()


def test_web_search_model_migration_adds_a_nullable_column_and_keeps_the_stored_backend(tmp_path):
    """The model column is additive: an existing Brave setup is untouched, and downgrading drops only the column."""
    database_url = f"sqlite:///{tmp_path / 'web-search-model.db'}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)

    try:
        command.upgrade(alembic_config, "f3c5e7a9b1d4")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO settings (web_search_provider, web_search_api_key) VALUES ('brave', 'enc')"))
        assert "web_search_model" not in {column["name"] for column in inspect(engine).get_columns("settings")}

        command.upgrade(alembic_config, "a1c4e7b9d2f6")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT web_search_provider, web_search_api_key, web_search_model FROM settings")
            ).one()
        assert tuple(row) == ("brave", "enc", None)

        command.downgrade(alembic_config, "f3c5e7a9b1d4")
        assert "web_search_model" not in {column["name"] for column in inspect(engine).get_columns("settings")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT web_search_provider FROM settings")).scalar_one() == "brave"
    finally:
        engine.dispose()
