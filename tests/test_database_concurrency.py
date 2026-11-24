"""SQLite lock tolerance for the threaded runtime (JAN-382)."""

import threading

import pytest
from sqlalchemy import text

from janus import database


@pytest.fixture
def on_disk_engine(tmp_path):
    """An engine for a real on-disk SQLite file built through the app's own factory."""
    engine = database.create_app_engine(f"sqlite:///{tmp_path / 'concurrency.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE hits (id INTEGER PRIMARY KEY, worker INTEGER, n INTEGER)"))
    try:
        yield engine
    finally:
        engine.dispose()


def test_on_disk_sqlite_uses_wal_and_busy_timeout(on_disk_engine):
    """Every pooled connection is switched to WAL with the long busy timeout."""
    with on_disk_engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == database.SQLITE_BUSY_TIMEOUT_SECONDS * 1000
        assert conn.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_in_memory_sqlite_is_left_alone():
    """In-memory databases cannot use WAL, so the pragmas are not attached."""
    engine = database.create_app_engine("sqlite://")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "memory"
    finally:
        engine.dispose()


def test_concurrent_writers_do_not_hit_database_is_locked(on_disk_engine):
    """Threads that write while others read (worker, chat, IMAP shape) all commit."""
    writers, writes_each = 6, 25
    errors: list[BaseException] = []
    stop_reading = threading.Event()

    def write(worker: int) -> None:
        try:
            for n in range(writes_each):
                with on_disk_engine.begin() as conn:
                    conn.execute(text("INSERT INTO hits (worker, n) VALUES (:w, :n)"), {"w": worker, "n": n})
        except BaseException as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(exc)

    def read() -> None:
        try:
            while not stop_reading.is_set():
                with on_disk_engine.connect() as conn:
                    conn.execute(text("SELECT COUNT(*) FROM hits")).scalar()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    reader = threading.Thread(target=read)
    threads = [threading.Thread(target=write, args=(i,)) for i in range(writers)]
    reader.start()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    stop_reading.set()
    reader.join(timeout=10)

    assert errors == []
    with on_disk_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM hits")).scalar() == writers * writes_each
