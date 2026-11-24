"""Migration-chain tests against a real PostgreSQL server.

The regular suite runs on SQLite only, which cannot catch PostgreSQL-specific migration bugs
(native enum types, 63-character identifiers, in-place ALTERs). These tests run only when
``JANUS_TEST_POSTGRES_URL`` points at a disposable database; its ``public`` schema is dropped
and recreated for every test.
"""

import os
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from janus import database

_POSTGRES_URL = os.environ.get("JANUS_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(not _POSTGRES_URL, reason="JANUS_TEST_POSTGRES_URL is not set")


@pytest.fixture
def postgres_engine():
    """Yield an engine on an empty ``public`` schema of the disposable test database."""
    engine = sa.create_engine(_POSTGRES_URL)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    try:
        yield engine
    finally:
        engine.dispose()


def _alembic_config() -> Config:
    """Return an Alembic config bound to the PostgreSQL test database."""
    alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # configparser treats "%" as interpolation syntax, so escape it for URL-encoded passwords.
    alembic_config.set_main_option("sqlalchemy.url", _POSTGRES_URL.replace("%", "%%"))
    return alembic_config


def _postgres_enum_labels(engine: sa.Engine) -> dict[str, set[str]]:
    """Return every PostgreSQL enum type in the database with its labels."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid")
        ).all()
    labels: dict[str, set[str]] = {}
    for type_name, label in rows:
        labels.setdefault(type_name, set()).add(label)
    return labels


def test_upgrade_head_creates_every_model_table(postgres_engine):
    """The full migration chain applies on PostgreSQL and creates every ORM table."""
    command.upgrade(_alembic_config(), "head")

    table_names = set(sa.inspect(postgres_engine).get_table_names())
    missing = set(database.Base.metadata.tables) - table_names
    assert not missing


def test_upgrade_head_declares_every_model_enum_value(postgres_engine):
    """Every value the ORM can write to a native enum column exists in the PostgreSQL type.

    SQLite stores enums as plain text, so a migration that forgets to widen the PostgreSQL type
    only fails at runtime, on the first write of the new value.
    """
    command.upgrade(_alembic_config(), "head")

    labels = _postgres_enum_labels(postgres_engine)
    mismatches = {}
    for table in database.Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.Enum) and column.type.native_enum:
                missing = set(column.type.enums) - labels.get(column.type.name, set())
                if missing:
                    mismatches[f"{table.name}.{column.name}"] = sorted(missing)
    assert not mismatches


def test_downgrade_base_and_upgrade_again(postgres_engine):
    """Downgrading to base leaves no leftovers that break a fresh upgrade."""
    alembic_config = _alembic_config()
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    assert set(sa.inspect(postgres_engine).get_table_names()) <= {"alembic_version"}
    assert not _postgres_enum_labels(postgres_engine)

    command.upgrade(alembic_config, "head")


def test_copy_database_moves_sqlite_data_into_postgresql(postgres_engine, tmp_path):
    """`janus db copy` moves every row, keeps enum/boolean/datetime values, and resets sequences."""
    from janus import models
    from janus.services.database_copy_service import DatabaseCopyError, copy_database

    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    database.ensure_schema_up_to_date(source_url)
    source_engine = sa.create_engine(source_url)
    heartbeat = datetime(2026, 9, 1, 8, 30)
    with Session(source_engine) as session:
        user = models.User(username="alice", hashed_password="x", is_admin=True)
        session.add(user)
        session.flush()
        account = models.EmailAccount(
            user_id=user.id,
            name="work",
            email="alice@example.test",
            imap_server="imap.example.test",
            username="alice",
            password="encrypted",
            monitor_status=models.MonitorStatus.RUNNING,
            last_heartbeat=heartbeat,
        )
        session.add(account)
        session.flush()
        session.add(models.EmailRecord(account_id=account.id, subject="Spam", priority=models.Priority.SPAM))
        session.add(models.Settings(llm_provider=models.LLMProvider.ANTHROPIC))
        # Self-references pointing at rows inserted later must survive the copy order.
        child = models.PlannerTask(user_id=user.id, title="child")
        session.add(child)
        session.flush()
        parent = models.PlannerTask(user_id=user.id, title="parent")
        session.add(parent)
        session.flush()
        child.parent_task_id = parent.id
        session.commit()
    source_engine.dispose()

    result = copy_database(source_url, _POSTGRES_URL)

    assert result.rows_per_table["users"] == 1
    assert result.rows_per_table["planner_tasks"] == 2
    with Session(postgres_engine) as session:
        copied_account = session.query(models.EmailAccount).one()
        assert copied_account.monitor_status is models.MonitorStatus.RUNNING
        assert copied_account.last_heartbeat == heartbeat
        assert session.query(models.User).one().is_admin is True
        assert session.query(models.EmailRecord).one().priority is models.Priority.SPAM
        assert session.query(models.Settings).one().llm_provider is models.LLMProvider.ANTHROPIC
        copied_child = session.query(models.PlannerTask).filter_by(title="child").one()
        assert copied_child.parent.title == "parent"
        # Sequences continue after the copied ids.
        new_user = models.User(username="bob", hashed_password="x")
        session.add(new_user)
        session.commit()
        assert new_user.id == 2

    with pytest.raises(DatabaseCopyError, match="already contains data"):
        copy_database(source_url, _POSTGRES_URL)
