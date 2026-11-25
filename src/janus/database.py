"""Database configuration and session management."""

import importlib.util
import threading
from pathlib import Path
from urllib.parse import unquote

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

from alembic import command
from janus import config, resources, runtime

ALEMBIC_INI_PATH = resources.alembic_ini_path()
#: URL scheme prefix -> (driver module, the extra that installs it).
_OPTIONAL_DRIVERS = {"postgres": ("psycopg2", "postgres")}


def driver_problems(database_url: str) -> list[str]:
    """Explain a database URL whose driver is an optional extra that is not installed."""
    return [
        f"database_url points to {prefix}, but its driver ({module}) is not installed. "
        f"Install the {extra} extra: uv sync --extra {extra} (or janus[{extra}])."
        for prefix, (module, extra) in _OPTIONAL_DRIVERS.items()
        if database_url.startswith(prefix) and importlib.util.find_spec(module) is None
    ]


_INITIAL_REVISION = "318b4a706a6e"
_REVISION_ALIASES = {
    "e4f6a7b8c9d0": "b1c2d3e4f5a6",
}
_APP_TABLES = {
    "agent_memory_compaction_runs",
    "agent_memory_facts",
    "agent_skills",
    "calendar_events",
    "character_profiles",
    "conversation_messages",
    "conversations",
    "email_accounts",
    "email_clarification_requests",
    "emails",
    "events",
    "oauth_states",
    "mcp_servers",
    "mcp_discovered_tools",
    "mcp_credentials",
    "notification_preferences",
    "planner_accounts",
    "planner_events",
    "planner_links",
    "planner_suggestions",
    "planner_tasks",
    "reminder_memory",
    "revoked_sessions",
    "rules",
    "runtime_triggers",
    "settings",
    "tasks",
    "users",
}


#: How long a SQLite connection waits for another writer before raising
#: ``database is locked`` (JAN-382). The stdlib default is 5 s, which the trigger
#: worker, chat, IMAP monitors and the vector index can exceed together.
SQLITE_BUSY_TIMEOUT_SECONDS = 30
#: A literal, not formatted SQL; ``tests/test_database_concurrency.py`` keeps it in step
#: with ``SQLITE_BUSY_TIMEOUT_SECONDS``.
_BUSY_TIMEOUT_PRAGMA = "PRAGMA busy_timeout=30000"


def _engine_kwargs(url: str) -> dict:
    """Returns engine kwargs appropriate for the given database URL."""
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_SECONDS}}
    return {}


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """Applies per-connection pragmas that keep concurrent threads from locking each other out.

    WAL lets readers proceed while one writer commits, ``busy_timeout`` makes a blocked
    writer wait instead of failing at once, and ``synchronous=NORMAL`` is the setting
    SQLite documents as safe with WAL (a power loss can drop the last commit, never
    corrupt the file).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(_BUSY_TIMEOUT_PRAGMA)
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_app_engine(url: str):
    """Creates an engine for ``url``, with lock-tolerant pragmas for on-disk SQLite databases."""
    created = create_engine(url, **_engine_kwargs(url))
    if _persistent_sqlite_database_path(url) is not None:
        event.listen(created, "connect", _configure_sqlite_connection)
    return created


engine = None
_session_factory = sessionmaker(autocommit=False, autoflush=False)
_schema_ready_lock = threading.RLock()
_schema_ready_urls: set[str] = set()

Base = declarative_base()


def get_engine(database_url: str | None = None):
    """Returns the configured SQLAlchemy engine, creating it lazily when needed."""
    global engine

    target_url = database_url or config.settings.database_url
    if engine is None or str(engine.url) != target_url:
        runtime.prepare_runtime_paths()
        engine = create_app_engine(target_url)
        _session_factory.configure(bind=engine)
    return engine


def SessionLocal():
    """Returns a new database session from the lazily configured session factory."""
    target_url = config.settings.database_url
    _ensure_schema_ready(target_url)
    get_engine(target_url)
    return _session_factory()


def _persistent_sqlite_database_path(target_url: str) -> Path | None:
    """Returns the on-disk path for persistent SQLite URLs that are safe to auto-upgrade."""
    url = make_url(target_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    if url.query.get("mode") == "memory" or url.query.get("cache") == "shared":
        return None

    database_name = unquote(url.database)
    if database_name.startswith("file:"):
        database_name = database_name.removeprefix("file:")
        if not database_name or database_name == ":memory:":
            return None

    path = Path(database_name).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _should_auto_ensure_schema(target_url: str) -> bool:
    """Returns whether lazy schema bootstrap is safe for the configured database URL."""
    return _persistent_sqlite_database_path(target_url) is not None


def _prepare_sqlite_database_directory(target_url: str) -> None:
    """Ensures the parent directory exists for eligible on-disk SQLite databases."""
    database_path = _persistent_sqlite_database_path(target_url)
    if database_path is None:
        return
    database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)


def _mark_schema_ready(target_url: str) -> None:
    """Caches that the schema has been brought to the current revision for this URL."""
    with _schema_ready_lock:
        _schema_ready_urls.add(target_url)


def _ensure_schema_ready(target_url: str) -> None:
    """Lazily upgrades persistent databases when sessions are opened before startup hooks run."""
    if not _should_auto_ensure_schema(target_url) or target_url in _schema_ready_urls:
        return

    with _schema_ready_lock:
        if target_url in _schema_ready_urls:
            return
        ensure_schema_up_to_date(target_url)


def _repair_orphaned_revisions(alembic_config: Config, target_url: str) -> None:
    """Rewrites known stale Alembic revision IDs to their surviving equivalents."""
    script = ScriptDirectory.from_config(alembic_config)
    known_revisions = {revision.revision for revision in script.walk_revisions()}

    migration_engine = create_app_engine(target_url)
    try:
        table_names = set(inspect(migration_engine).get_table_names())
        if "alembic_version" not in table_names:
            return

        with migration_engine.begin() as conn:
            current_revisions = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            orphaned_revisions = sorted({revision for revision in current_revisions if revision not in known_revisions})
            if not orphaned_revisions:
                return

            unmapped_revisions = [revision for revision in orphaned_revisions if revision not in _REVISION_ALIASES]
            if unmapped_revisions:
                raise RuntimeError(
                    "Database is stamped with unknown Alembic revision(s): " + ", ".join(unmapped_revisions)
                )

            for revision in orphaned_revisions:
                conn.execute(
                    text("UPDATE alembic_version SET version_num = :replacement WHERE version_num = :revision"),
                    {
                        "replacement": _REVISION_ALIASES[revision],
                        "revision": revision,
                    },
                )
    finally:
        migration_engine.dispose()


def ensure_schema_up_to_date(database_url: str | None = None) -> None:
    """Applies Alembic migrations, stamping legacy pre-Alembic databases when needed."""
    alembic_config = Config(str(ALEMBIC_INI_PATH))
    target_url = database_url or config.settings.database_url
    alembic_config.set_main_option("sqlalchemy.url", target_url)
    _prepare_sqlite_database_directory(target_url)
    if database_url is None:
        get_engine()

    migration_engine = create_app_engine(target_url)
    try:
        table_names = set(inspect(migration_engine).get_table_names())
        if "alembic_version" not in table_names and table_names & _APP_TABLES:
            command.stamp(alembic_config, _INITIAL_REVISION)
    finally:
        migration_engine.dispose()
    _repair_orphaned_revisions(alembic_config, target_url)
    command.upgrade(alembic_config, "head")
    _mark_schema_ready(target_url)


def get_db():
    """Provides a database session for use in FastAPI dependencies.

    Yields:
        Session: A database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
