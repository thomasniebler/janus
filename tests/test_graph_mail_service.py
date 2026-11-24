"""Outlook (Graph) mailbox polling."""

from datetime import UTC, datetime

from janus import graph_mail_service, models


class _FakeClient:
    def __init__(self, items):
        self.items = items
        self.since = None
        self.closed = False

    def list_inbox_since(self, since, *, newest_first=False, max_pages=10):
        self.since = since
        self.newest_first = newest_first
        self.max_pages = max_pages
        return list(reversed(self.items)) if newest_first else list(self.items)

    def close(self):
        self.closed = True


def _account(db, cursor=None):
    user = models.User(username="olive", hashed_password="x")
    db.add(user)
    db.flush()
    account = models.EmailAccount(
        user_id=user.id,
        name="Outlook",
        email="me@outlook.com",
        imap_server="graph.microsoft.com",
        imap_port=443,
        username="me@outlook.com",
        auth_type="microsoft",
        sync_cursor=cursor,
    )
    db.add(account)
    db.commit()
    return account


def _item(n, received):
    return {
        "id": f"graph-{n}",
        "internetMessageId": f"<m{n}@x>",
        "subject": f"Hello {n}",
        "from": {"emailAddress": {"address": "a@b.c"}},
        "receivedDateTime": received,
        "body": {"content": "Body text"},
    }


def test_sync_hands_over_mail_and_advances_the_cursor(db, monkeypatch):
    """New mail goes to the callback without a UID, and the cursor moves to the newest message."""
    account = _account(db)
    client = _FakeClient([_item(1, "2026-11-12T10:00:00Z"), _item(2, "2026-11-12T11:30:00Z")])
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: client)
    monkeypatch.setattr(graph_mail_service.database, "SessionLocal", lambda: _SessionProxy(db))
    seen = []

    count = monitor.sync(lambda data, mon: (seen.append(data), _store(db, account, data["message_id"])))

    assert count == 2
    assert [e["message_id"] for e in seen] == ["<m1@x>", "<m2@x>"]
    assert seen[0]["graph_message_id"] == "graph-1"
    assert seen[0]["sender"] == "a@b.c"
    assert "uid" not in seen[0]
    assert account.sync_cursor == "2026-11-12T11:30:00Z"
    assert client.closed is True


def test_next_poll_starts_at_the_cursor(db):
    """Later polls ask Graph only for mail since the stored cursor."""
    account = _account(db, cursor="2026-11-12T11:30:00Z")
    client = _FakeClient([])
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: client)

    assert monitor.fetch_new() == []
    assert client.since == datetime(2026, 11, 12, 11, 30, tzinfo=UTC)


def test_first_sync_keeps_only_the_newest_messages_and_asks_newest_first(db, monkeypatch):
    """The first sync honours the configured cap and takes the newest mail, oldest handed over first."""
    account = _account(db)
    monkeypatch.setattr(graph_mail_service.imap_service, "initial_history_limit", lambda db=None: 2)
    items = [_item(n, f"2026-11-12T0{n}:00:00Z") for n in range(1, 5)]
    client = _FakeClient(items)
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: client)

    assert [e["graph_message_id"] for e in monitor.fetch_new()] == ["graph-3", "graph-4"]
    assert client.newest_first is True
    assert client.max_pages == 1


def test_test_connection_reports_failures(db):
    """A token or network failure comes back as a message, not an exception."""
    account = _account(db)

    def boom():
        raise RuntimeError("no token")

    ok, message = graph_mail_service.GraphMailMonitor(account, client_factory=boom).test_connection()

    assert ok is False
    assert "no token" in message


class _SessionProxy:
    """Lets the monitor's own ``SessionLocal()`` use the test session without closing it."""

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        """Keeps the shared test session open."""

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


def _store(db, account, message_id):
    db.add(models.EmailRecord(account_id=account.id, message_id=message_id, subject="s", sender="a@b.c"))
    db.commit()


def test_already_stored_mail_is_not_handed_over_again(db, monkeypatch):
    """The inclusive cursor re-lists the newest message; it must not reach the callback twice."""
    account = _account(db, cursor="2026-11-12T10:00:00Z")
    _store(db, account, "<m1@x>")
    client = _FakeClient([_item(1, "2026-11-12T10:00:00Z"), _item(2, "2026-11-12T10:05:00Z")])
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: client)
    monkeypatch.setattr(graph_mail_service.database, "SessionLocal", lambda: _SessionProxy(db))
    seen = []

    count = monitor.sync(lambda data, mon: (seen.append(data["message_id"]), _store(db, account, data["message_id"])))

    assert count == 1
    assert seen == ["<m2@x>"]
    assert account.sync_cursor == "2026-11-12T10:05:00Z"


def test_a_failed_message_holds_the_cursor_and_is_skipped_after_three_polls(db, monkeypatch):
    """A swallowed callback error must not lose mail: retry first, then skip a poison message."""
    account = _account(db, cursor="2026-11-12T09:00:00Z")
    client = _FakeClient([_item(1, "2026-11-12T10:00:00Z"), _item(2, "2026-11-12T11:00:00Z")])
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: client)
    monkeypatch.setattr(graph_mail_service.database, "SessionLocal", lambda: _SessionProxy(db))
    seen = []

    def fails_on_the_first(data, mon):
        seen.append(data["message_id"])
        if data["message_id"] != "<m1@x>":
            _store(db, account, data["message_id"])

    monitor.sync(fails_on_the_first)
    assert account.sync_cursor == "2026-11-12T09:00:00Z"
    monitor.sync(fails_on_the_first)
    assert account.sync_cursor == "2026-11-12T09:00:00Z"
    monitor.sync(fails_on_the_first)

    assert seen == ["<m1@x>", "<m1@x>", "<m1@x>", "<m2@x>"]
    assert account.sync_cursor == "2026-11-12T11:00:00Z"


def test_polling_stops_once_the_mailbox_is_deactivated(db, monkeypatch):
    """A disconnect deactivates the mailbox; the worker thread must end instead of retrying forever."""
    account = _account(db)
    account.is_active = False
    db.commit()
    monitor = graph_mail_service.GraphMailMonitor(account, client_factory=lambda: _FakeClient([]))
    monkeypatch.setattr(graph_mail_service.database, "SessionLocal", lambda: _SessionProxy(db))

    monitor.poll_loop(lambda data, mon: None)  # returns instead of looping

    assert monitor._still_wanted() is False
