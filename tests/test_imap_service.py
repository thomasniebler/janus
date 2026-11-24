"""Targeted tests for IMAP batching and worker restart supervision."""

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

import janus.imap_service as imap_service
from janus import database, models
from janus.imap_service import _DEFAULT_INITIAL_LOOKBACK_DAYS, IMAPMonitor
from janus.time_utils import format_imap_date
from janus.worker_manager import WorkerManager, WorkerState


def _fixed_now() -> datetime:
    return datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


def _freeze_imap_service_now(monkeypatch, fixed_now: datetime) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(imap_service, "datetime", FrozenDateTime)


def _expected_since_date(fixed_now: datetime | None = None) -> str:
    """Return the IMAP SINCE date string that fetch_since_uid generates right now."""
    now = fixed_now or datetime.now(UTC)
    cutoff = now - timedelta(days=_DEFAULT_INITIAL_LOOKBACK_DAYS)
    return format_imap_date(cutoff)


def _message_bytes(uid: int, date_header: str | None = None) -> bytes:
    date_line = f"Date: {date_header}\r\n" if date_header is not None else ""
    return (
        f"From: sender{uid}@example.com\r\n"
        f"Subject: Subject {uid}\r\n"
        f"Message-ID: <msg-{uid}@example.com>\r\n"
        f"{date_line}"
        "\r\n"
        f"Body {uid}"
    ).encode()


def test_format_imap_date_uses_english_month_abbreviation():
    """IMAP date formatting should always use English month abbreviations."""
    assert format_imap_date(datetime(2024, 8, 5, tzinfo=UTC)) == "05-Aug-2024"


def test_fetch_since_uid_limits_initial_history_to_recent_100(monkeypatch):
    """Initial sync should only ingest the newest 100 messages within the lookback window."""
    fixed_now = _fixed_now()
    _freeze_imap_service_now(monkeypatch, fixed_now)

    class FakeClient:
        def __init__(self):
            self.fetch_call_sizes = []

        def search(self, criteria):
            assert criteria == ["SINCE", _expected_since_date(fixed_now)]
            return list(range(1, 451))

        def fetch(self, message_ids, parts):
            self.fetch_call_sizes.append(len(message_ids))
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                uid: {
                    b"BODY[]": _message_bytes(uid),
                    b"INTERNALDATE": None,
                }
                for uid in message_ids
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()
    monitor._get_initial_history_limit = lambda: 100

    results = monitor.fetch_since_uid(0)

    assert len(results) == 100
    assert results[0]["uid"] == 351
    assert results[-1]["uid"] == 450
    assert monitor.client.fetch_call_sizes == [100]


def test_fetch_since_uid_uses_configured_initial_history_limit(db, monkeypatch, testing_session_local):
    """Initial sync should respect the persisted settings limit."""
    fixed_now = _fixed_now()
    _freeze_imap_service_now(monkeypatch, fixed_now)

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["SINCE", _expected_since_date(fixed_now)]
            return list(range(1, 31))

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                uid: {
                    b"BODY[]": _message_bytes(uid),
                    b"INTERNALDATE": None,
                }
                for uid in message_ids
            }

    db.add(models.Settings(initial_sync_email_limit=12))
    db.commit()
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_since_uid(0)

    assert [item["uid"] for item in results] == list(range(19, 31))


def test_fetch_since_uid_batches_large_incremental_fetches():
    """Incremental sync should remain unbounded and batch large UID ranges."""

    class FakeClient:
        def __init__(self):
            self.fetch_call_sizes = []

        def search(self, criteria):
            assert criteria == ["UID", "451:*"]
            return list(range(451, 901))

        def fetch(self, message_ids, parts):
            self.fetch_call_sizes.append(len(message_ids))
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                uid: {
                    b"BODY[]": _message_bytes(uid),
                    b"INTERNALDATE": None,
                }
                for uid in message_ids
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_since_uid(450)

    assert len(results) == 450
    assert results[0]["uid"] == 451
    assert results[-1]["uid"] == 900
    assert monitor.client.fetch_call_sizes == [200, 200, 50]


def test_fetch_since_uid_prefers_refreshed_account_last_uid():
    """A freshly loaded account state from connect() should override a stale caller argument."""

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["UID", "901:*"]
            return list(range(901, 906))

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                uid: {
                    b"BODY[]": _message_bytes(uid),
                    b"INTERNALDATE": None,
                }
                for uid in message_ids
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted", last_uid=0)
    monitor = IMAPMonitor(account)

    def fake_connect():
        monitor.client = FakeClient()
        monitor.account.last_uid = 900

    monitor.connect = fake_connect

    results = monitor.fetch_since_uid(0)

    assert [item["uid"] for item in results] == [901, 902, 903, 904, 905]


def test_fetch_since_uid_prefers_message_date_header_over_internaldate(monkeypatch):
    """Ingestion should preserve the original message Date header when valid."""
    fixed_now = _fixed_now()
    _freeze_imap_service_now(monkeypatch, fixed_now)

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["SINCE", _expected_since_date(fixed_now)]
            return [1]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                1: {
                    b"BODY[]": _message_bytes(1, date_header="Fri, 05 Apr 2024 10:30:00 -0400"),
                    b"INTERNALDATE": datetime(2024, 4, 6, 8, 0, tzinfo=UTC),
                }
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()
    monitor._get_initial_history_limit = lambda: 100

    results = monitor.fetch_since_uid(0)

    assert len(results) == 1
    assert results[0]["received_at"] == datetime(2024, 4, 5, 14, 30, tzinfo=UTC)


def test_fetch_since_uid_orders_results_by_received_at_then_uid(monkeypatch):
    """Historical sync processing order should follow preserved message timestamps."""
    fixed_now = _fixed_now()
    _freeze_imap_service_now(monkeypatch, fixed_now)

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["SINCE", _expected_since_date(fixed_now)]
            return [10, 11, 12]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            payloads = {
                10: _message_bytes(10, date_header="Mon, 08 Apr 2024 11:00:00 +0000"),
                11: _message_bytes(11, date_header="Mon, 08 Apr 2024 10:00:00 +0000"),
                12: _message_bytes(12, date_header="Mon, 08 Apr 2024 09:00:00 +0000"),
            }
            return {
                uid: {
                    b"BODY[]": payloads[uid],
                    b"INTERNALDATE": datetime(2024, 4, 8, 12, 0, tzinfo=UTC),
                }
                for uid in message_ids
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()
    monitor._get_initial_history_limit = lambda: 100

    results = monitor.fetch_since_uid(0)

    assert [item["uid"] for item in results] == [12, 11, 10]


def test_fetch_since_uid_falls_back_to_internaldate_when_message_date_invalid(monkeypatch):
    """Malformed Date headers should fall back to INTERNALDATE metadata."""
    fixed_now = _fixed_now()
    _freeze_imap_service_now(monkeypatch, fixed_now)

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["SINCE", _expected_since_date(fixed_now)]
            return [2]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                2: {
                    b"BODY[]": _message_bytes(2, date_header="not-a-date"),
                    b"INTERNALDATE": datetime(2024, 4, 6, 8, 0, tzinfo=UTC),
                }
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()
    monitor._get_initial_history_limit = lambda: 100

    results = monitor.fetch_since_uid(0)

    assert len(results) == 1
    assert results[0]["received_at"] == datetime(2024, 4, 6, 8, 0, tzinfo=UTC)


def test_fetch_latest_emails_orders_by_received_at_descending():
    """Latest-email fetch should rank results by preserved message timestamp."""

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["ALL"]
            return [1, 2, 3]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            payloads = {
                1: _message_bytes(1, date_header="Mon, 08 Apr 2024 11:00:00 +0000"),
                2: _message_bytes(2, date_header="Mon, 08 Apr 2024 10:00:00 +0000"),
                3: _message_bytes(3, date_header="Mon, 08 Apr 2024 09:00:00 +0000"),
            }
            return {
                uid: {
                    b"BODY[]": payloads[uid],
                    b"INTERNALDATE": datetime(2024, 4, 8, 12, 0, tzinfo=UTC),
                }
                for uid in message_ids
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_latest_emails(limit=3)

    assert [item["uid"] for item in results] == [1, 2, 3]


def test_fetch_new_emails_includes_received_at_from_message_date():
    """New-message polling should persist the source message timestamp."""

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["UNSEEN"]
            return [3]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                3: {
                    b"BODY[]": _message_bytes(3, date_header="Sat, 06 Apr 2024 09:15:00 +0200"),
                    b"INTERNALDATE": datetime(2024, 4, 6, 8, 0, tzinfo=UTC),
                }
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_new_emails()

    assert len(results) == 1
    assert results[0]["received_at"] == datetime(2024, 4, 6, 7, 15, tzinfo=UTC)


def test_fetch_latest_emails_includes_received_at_from_message_date():
    """Import-style latest fetch should preserve the source Date header timestamp."""

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["ALL"]
            return [4]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                4: {
                    b"BODY[]": _message_bytes(4, date_header="Sun, 07 Apr 2024 11:45:00 -0500"),
                    b"INTERNALDATE": datetime(2024, 4, 7, 18, 0, tzinfo=UTC),
                }
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_latest_emails(limit=1)

    assert len(results) == 1
    assert results[0]["received_at"] == datetime(2024, 4, 7, 16, 45, tzinfo=UTC)


def test_fetch_latest_emails_falls_back_to_internaldate_when_message_date_invalid():
    """Latest-import fetch should fall back to INTERNALDATE when Date is malformed."""

    class FakeClient:
        def search(self, criteria):
            assert criteria == ["ALL"]
            return [5]

        def fetch(self, message_ids, parts):
            assert parts == [b"BODY.PEEK[]", b"INTERNALDATE"]
            return {
                5: {
                    b"BODY[]": _message_bytes(5, date_header="definitely-not-a-date"),
                    b"INTERNALDATE": datetime(2024, 4, 8, 9, 30, tzinfo=UTC),
                }
            }

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()

    results = monitor.fetch_latest_emails(limit=1)

    assert len(results) == 1
    assert results[0]["received_at"] == datetime(2024, 4, 8, 9, 30, tzinfo=UTC)


def test_idle_loop_updates_last_uid_once_per_detected_batch():
    """IDLE processing should persist only the highest UID after each fetched batch."""

    class StopIdleLoop(KeyboardInterrupt):
        """Breaks the infinite IDLE loop after one pass."""

    class FakeClient:
        def __init__(self):
            self.idle_checks = 0

        def idle(self):
            return None

        def idle_check(self, timeout):
            self.idle_checks += 1
            if self.idle_checks == 1:
                return [(1, b"EXISTS")]
            raise StopIdleLoop()

        def idle_done(self):
            return None

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FakeClient()
    monitor.connect = lambda: None
    monitor.fetch_since_uid = MagicMock(
        return_value=[
            {"uid": 101, "subject": "Older"},
            {"uid": 105, "subject": "Newest"},
        ]
    )
    monitor.update_last_uid = MagicMock()
    monitor._write_heartbeat = MagicMock()
    callback = MagicMock()

    with pytest.raises(StopIdleLoop):
        monitor.idle_loop(callback)

    assert callback.call_args_list == [
        (({"uid": 101, "subject": "Older"}, monitor),),
        (({"uid": 105, "subject": "Newest"}, monitor),),
    ]
    monitor.update_last_uid.assert_called_once_with(105)


def test_idle_loop_updates_last_uid_once_after_recovery_batch(monkeypatch):
    """Recovery sync should persist only the highest missed UID after reconnecting."""

    class StopIdleLoop(KeyboardInterrupt):
        """Breaks the infinite IDLE loop after recovery completes."""

    class FailingClient:
        def idle(self):
            raise RuntimeError("idle lost")

    class StoppingClient:
        def idle(self):
            return None

        def idle_check(self, timeout):
            raise StopIdleLoop()

        def idle_done(self):
            return None

    account = models.EmailAccount(id=1, email="test@example.com", password="encrypted")
    monitor = IMAPMonitor(account)
    monitor.client = FailingClient()
    connect_calls = 0

    def fake_connect():
        nonlocal connect_calls
        connect_calls += 1
        if connect_calls > 1:
            monitor.client = StoppingClient()

    monitor.connect = fake_connect
    monitor.fetch_since_uid = MagicMock(
        return_value=[
            {"uid": 202, "subject": "Missed older"},
            {"uid": 210, "subject": "Missed newest"},
        ]
    )
    monitor.update_last_uid = MagicMock()
    monitor._write_heartbeat = MagicMock()
    callback = MagicMock()
    monkeypatch.setattr(imap_service.time, "sleep", lambda _seconds: None)

    with pytest.raises(StopIdleLoop):
        monitor.idle_loop(callback)

    assert callback.call_args_list == [
        (({"uid": 202, "subject": "Missed older"}, monitor),),
        (({"uid": 210, "subject": "Missed newest"}, monitor),),
    ]
    monitor.update_last_uid.assert_called_once_with(210)


def test_worker_manager_only_schedules_one_restart_per_dead_worker(monkeypatch):
    """Dead workers should not queue duplicate restart timers before relaunch."""

    class DeadThread:
        def is_alive(self):
            return False

    manager = WorkerManager(start_supervisor=False)
    scheduled = []
    monkeypatch.setattr(manager, "_schedule_restart", lambda state, delay: scheduled.append((state.label, delay)))
    monkeypatch.setattr(manager, "_log_crash", lambda state: None)

    state = WorkerState(
        label="imap:test@example.com",
        target=lambda: None,
        args=(),
        restart_policy=True,
        thread=DeadThread(),
    )

    manager._handle_dead_worker(state)
    manager._handle_dead_worker(state)

    assert state.restart_count == 1
    assert state.restart_scheduled is True
    assert scheduled == [("imap:test@example.com", 10)]


class _FakeWorkerThread:
    """A fake thread whose `is_alive()` answers follow a scripted sequence."""

    def __init__(self, alive_sequence):
        self._alive_sequence = list(alive_sequence)
        self.join_calls = []

    def is_alive(self):
        if self._alive_sequence:
            return self._alive_sequence.pop(0)
        return False

    def join(self, timeout=None):
        self.join_calls.append(timeout)


def test_worker_manager_start_stops_a_still_live_previous_worker_first(monkeypatch):
    """Restarting a label must stop the old thread before launching a new one, not just overwrite it.

    Overwriting `_workers[label]` unconditionally would silently drop the old WorkerState from
    supervision (a later crash of that orphaned thread is never logged or restarted) while its
    thread may still be running concurrently with the new one.
    """
    manager = WorkerManager(start_supervisor=False)
    old_thread = _FakeWorkerThread(alive_sequence=[True, False])  # alive, then dead after join
    old_state = WorkerState(label="whatsapp:1", target=lambda: None, args=(), restart_policy=True, thread=old_thread)
    manager._workers["whatsapp:1"] = old_state

    launched = []
    monkeypatch.setattr(manager, "_launch", lambda state: launched.append(state))

    def new_target():
        pass

    manager.start("whatsapp:1", new_target, args=(1,))

    assert old_state.stopped is True
    assert old_thread.join_calls == [5.0]
    assert len(launched) == 1
    assert launched[0].target is new_target
    assert manager._workers["whatsapp:1"] is launched[0]


def test_worker_manager_start_refuses_to_duplicate_when_previous_worker_outlives_join_timeout(monkeypatch, caplog):
    """A previous worker that won't stop in time must not get a duplicate launched alongside it.

    WorkerManager can't force a third-party blocking call to return, so if the join times out
    the only safe option is to refuse the replacement (raise) rather than silently run two
    workers under one label against the same underlying resource.
    """
    manager = WorkerManager(start_supervisor=False)
    old_thread = _FakeWorkerThread(alive_sequence=[True, True])  # still alive even after join
    old_state = WorkerState(label="whatsapp:1", target=lambda: None, args=(), restart_policy=True, thread=old_thread)
    manager._workers["whatsapp:1"] = old_state
    launched = []
    monkeypatch.setattr(manager, "_launch", lambda state: launched.append(state))

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="did not stop within the timeout"):
        manager.start("whatsapp:1", lambda: None)

    assert old_thread.join_calls == [5.0]
    assert launched == []
    assert old_state.stopped is False
    assert manager._workers["whatsapp:1"] is old_state
    assert any("did not stop within timeout" in record.message for record in caplog.records)


def test_worker_manager_start_holds_the_label_lock_for_the_whole_replace_sequence(monkeypatch):
    """The label lock must cover check-join-install-launch as one atomic sequence.

    Regression test for a race where start() released its lock between reading the
    existing state and replacing it: two concurrent restarts of the same label could
    each read the same existing state and independently install their own new one,
    leaving two live workers under one label with only the last write supervised.
    """
    manager = WorkerManager(start_supervisor=False)
    lock_held_during_launch = []

    def fake_launch(state):
        lock_held_during_launch.append(manager._lock_for_label("whatsapp:1").locked())

    monkeypatch.setattr(manager, "_launch", fake_launch)

    manager.start("whatsapp:1", lambda: None)

    assert lock_held_during_launch == [True]
    assert not manager._lock_for_label("whatsapp:1").locked()


def test_delayed_restart_does_not_relaunch_a_state_superseded_by_an_explicit_restart(monkeypatch):
    """A pending backoff restart must not relaunch a state an explicit start()/stop() replaced.

    Regression test: `_delayed_restart` used to check `state.thread.is_alive()` without the
    label lock and without confirming its state was still the one registered for that label.
    A crash-triggered backoff timer racing with an explicit restart could relaunch the old,
    already-superseded state as a second, unregistered worker.
    """
    manager = WorkerManager(start_supervisor=False)
    dead_thread = _FakeWorkerThread(alive_sequence=[False])
    old_state = WorkerState(label="whatsapp:1", target=lambda: None, args=(), restart_policy=True, thread=dead_thread)
    manager._workers["whatsapp:1"] = old_state

    # Simulate an explicit restart having already replaced this label's worker while the
    # backoff timer was pending.
    new_state = WorkerState(label="whatsapp:1", target=lambda: None, args=(), restart_policy=True)
    manager._workers["whatsapp:1"] = new_state

    launched = []
    monkeypatch.setattr(manager, "_launch", lambda state: launched.append(state))
    monkeypatch.setattr("janus.worker_manager.time.sleep", lambda _seconds: None)

    created_threads = []
    real_thread_cls = threading.Thread

    def capturing_thread(*args, **kwargs):
        thread = real_thread_cls(*args, **kwargs)
        created_threads.append(thread)
        return thread

    monkeypatch.setattr("janus.worker_manager.threading.Thread", capturing_thread)

    manager._schedule_restart(old_state, 10)
    created_threads[0].join(timeout=2)

    assert launched == []
    assert manager._workers["whatsapp:1"] is new_state


def test_get_initial_history_limit_accepts_existing_session(db, testing_session_local):
    """_get_initial_history_limit() uses the passed session and does not close it."""
    db.add(models.Settings(initial_sync_email_limit=25))
    db.commit()

    account = models.EmailAccount(id=1, email="test@example.com", password="enc")
    monitor = IMAPMonitor(account)

    limit = monitor._get_initial_history_limit(db=db)

    assert limit == 25
    # Session is still usable after the call (was not closed by the method).
    assert db.query(models.Settings).first() is not None


def test_get_initial_history_limit_creates_own_session_when_none_passed(monkeypatch, db, testing_session_local):
    """_get_initial_history_limit() creates and closes its own session when no db given."""
    db.add(models.Settings(initial_sync_email_limit=42))
    db.commit()

    monkeypatch.setattr(
        __import__("janus.database", fromlist=["database"]),
        "SessionLocal",
        testing_session_local,
    )

    account = models.EmailAccount(id=1, email="test@example.com", password="enc")
    monitor = IMAPMonitor(account)

    limit = monitor._get_initial_history_limit()
    assert limit == 42
