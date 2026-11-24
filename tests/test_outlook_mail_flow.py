"""Outlook mailbox plumbing: sign-in scopes, token access, worker hand-off, connection test, paging."""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from janus import database, graph_mail_service, imap_service, models, security
from janus.connectors.microsoft_graph import MicrosoftGraphClient
from janus.services import mailbox_oauth_service, microsoft_connect_service, oauth_connection_service
from janus.workers import email as email_worker


class _SessionProxy:
    """Lets code that opens its own ``SessionLocal()`` use the test session without closing it."""

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


@pytest.fixture
def owner(db):
    """A plain user who owns the mailbox."""
    user = models.User(username="olive", hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def outlook(db, owner, monkeypatch):
    """A connected Outlook mailbox; code under test sees the test session."""
    connection = oauth_connection_service.upsert_connection(
        db, owner.id, "microsoft", account_email="me@outlook.com", credentials="{}", scopes=[]
    )
    account = models.EmailAccount(
        user_id=owner.id,
        name="Outlook",
        email="me@outlook.com",
        imap_server="graph.microsoft.com",
        imap_port=443,
        username="me@outlook.com",
        auth_type="microsoft",
        connection_id=connection.id,
        is_active=True,
    )
    db.add(account)
    db.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: _SessionProxy(db))
    return account


def test_complete_login_stores_scopes_as_full_uris(db, owner, monkeypatch):
    """Graph may answer with bare scope names; the stored scopes still match MAIL_SCOPE."""

    class _App:
        def acquire_token_by_auth_code_flow(self, flow, query):
            return {
                "access_token": "t",
                "id_token_claims": {"preferred_username": "me@outlook.com"},
                "scope": "Mail.Read Calendars.Read.Shared openid profile",
            }

    monkeypatch.setattr(microsoft_connect_service, "_application", lambda cache=None: _App())

    connection = microsoft_connect_service.complete_login(db, owner.id, "{}", {})

    assert microsoft_connect_service.MAIL_SCOPE in connection.scopes
    assert microsoft_connect_service.GRAPH_SCOPES[0] in connection.scopes
    assert "openid" in connection.scopes
    assert connection.account_email == "me@outlook.com"


def test_outlook_mailboxes_have_no_imap_token(outlook):
    """The IMAP path refuses Outlook mailboxes with a clear message."""
    with pytest.raises(oauth_connection_service.ConnectionUnusableError, match="Graph"):
        mailbox_oauth_service.imap_access_token(outlook)


def test_graph_client_fetches_the_mail_token_for_the_connection(outlook, monkeypatch):
    """The monitor's client asks MSAL for the Mail.Read token of the account's own connection."""
    asked = {}

    def fake_access_token(db, connection, scopes):
        asked["scopes"] = scopes
        return "mail-token"

    monkeypatch.setattr(microsoft_connect_service, "access_token", fake_access_token)
    client = graph_mail_service.GraphMailMonitor(outlook)._graph_client()
    try:
        assert client._token_provider() == "mail-token"
    finally:
        client.close()
    assert asked["scopes"] == [microsoft_connect_service.MAIL_SCOPE]


def test_graph_client_reports_a_disconnected_account(outlook, db):
    """Without a connection the token provider raises the reconnect message."""
    outlook.connection_id = None
    db.commit()
    client = graph_mail_service.GraphMailMonitor(outlook)._graph_client()
    try:
        with pytest.raises(oauth_connection_service.ConnectionUnusableError, match="disconnected"):
            client._token_provider()
    finally:
        client.close()


def test_poll_loop_syncs_beats_and_stops_when_the_mailbox_is_deactivated(outlook, db, monkeypatch):
    """One pass syncs, writes the heartbeat and sleeps; the loop then ends because the mailbox is off."""
    monitor = graph_mail_service.GraphMailMonitor(outlook)
    beats = []
    monkeypatch.setattr(graph_mail_service.imap_service, "write_heartbeat", lambda *a, **k: beats.append(a[1]))
    synced = []
    monkeypatch.setattr(monitor, "sync", lambda callback: synced.append(callback) or 0)

    def deactivate(seconds):
        outlook.is_active = False
        db.commit()

    monkeypatch.setattr(monitor, "_nap", deactivate)

    monitor.poll_loop(lambda data, mon: None)

    assert len(synced) == 1
    assert beats == [models.MonitorStatus.RUNNING]


def test_poll_loop_records_an_error_heartbeat_and_backs_off(outlook, db, monkeypatch):
    """A failing poll reports ERROR for the account and waits before trying again."""
    monitor = graph_mail_service.GraphMailMonitor(outlook)
    beats = []
    monkeypatch.setattr(
        graph_mail_service.imap_service,
        "write_heartbeat",
        lambda a, status, error=None, **k: beats.append((status, error)),
    )

    def boom(callback):
        raise RuntimeError("graph down")

    monkeypatch.setattr(monitor, "sync", boom)

    def deactivate(seconds):
        outlook.is_active = False
        db.commit()

    monkeypatch.setattr(graph_mail_service.time, "sleep", deactivate)

    monitor.poll_loop(lambda data, mon: None)

    assert beats == [(models.MonitorStatus.ERROR, "graph down")]


def test_nap_sleeps_in_heartbeat_steps(outlook, monkeypatch):
    """A 70 second nap is three sleeps (30, 30, 10) so the heartbeat can be refreshed in between."""
    monitor = graph_mail_service.GraphMailMonitor(outlook)
    slept = []
    monkeypatch.setattr(graph_mail_service.time, "sleep", slept.append)
    monkeypatch.setattr(monitor, "_beat", lambda force=False: None)

    monitor._nap(70)

    assert slept == [30, 30, 10]


def test_worker_polls_outlook_mailboxes_instead_of_idling(outlook, monkeypatch):
    """The email worker hands a Microsoft mailbox to the Graph monitor, not to IMAP."""
    polled = []
    monkeypatch.setattr(
        graph_mail_service.GraphMailMonitor, "poll_loop", lambda self, callback: polled.append((self.account, callback))
    )

    email_worker._sync_and_idle(outlook)

    assert polled == [(outlook, email_worker.email_callback)]


def test_connection_test_uses_the_graph_monitor_for_outlook(outlook, client, db, monkeypatch):
    """Testing an Outlook mailbox from the UI goes through Graph and reports its message."""
    db.add(
        models.User(
            username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]
    outlook.user_id = db.query(models.User).filter_by(username="admin").one().id
    db.commit()
    monkeypatch.setattr(graph_mail_service.GraphMailMonitor, "test_connection", lambda self: (True, "Graph ok"))

    response = client.post(f"/api/emails/accounts/{outlook.id}/test")

    assert response.json() == {"status": "success", "message": "Graph ok"}


def test_connection_test_reports_a_monitor_that_raises(outlook, client, db, monkeypatch):
    """An unexpected error becomes an error message, not a 500."""
    db.add(
        models.User(
            username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
        )
    )
    db.commit()
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = login.json()["csrf_token"]
    outlook.user_id = db.query(models.User).filter_by(username="admin").one().id
    db.commit()

    def boom(self):
        raise RuntimeError("kaput")

    monkeypatch.setattr(graph_mail_service.GraphMailMonitor, "test_connection", boom)

    response = client.post(f"/api/emails/accounts/{outlook.id}/test")

    assert response.json() == {"status": "error", "message": "kaput"}


def test_inbox_listing_follows_next_links_and_can_ask_newest_first():
    """Both pages are collected, and newest_first asks Graph for descending order."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page2" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "b"}]})
        return httpx.Response(200, json={"value": [{"id": "a"}], "@odata.nextLink": "https://graph.test/page2"})

    client = MicrosoftGraphClient(lambda: "tok", transport=httpx.MockTransport(handler))

    items = client.list_inbox_since(datetime(2026, 11, 1, tzinfo=UTC), newest_first=True)

    assert [item["id"] for item in items] == ["a", "b"]
    assert seen[0].url.params["$orderby"] == "receivedDateTime desc"
    assert seen[0].url.params["$filter"] == "receivedDateTime ge 2026-11-01T00:00:00Z"


def test_heartbeat_failures_never_raise(monkeypatch):
    """A broken database must not kill a monitor thread."""

    def broken():
        raise RuntimeError("db gone")

    monkeypatch.setattr(imap_service.database, "SessionLocal", broken)

    imap_service.write_heartbeat(SimpleNamespace(id=1, email="x@y.z"), models.MonitorStatus.RUNNING)
