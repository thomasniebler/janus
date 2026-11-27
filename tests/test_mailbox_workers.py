"""Mailbox monitor threads start when a mailbox is added or switched on and stop when it goes away.

The autouse ``mailbox_workers`` fixture (conftest) records worker_manager calls instead of
opening IMAP or Graph connections.
"""

import threading
from unittest.mock import MagicMock

import pytest
from sqlalchemy import inspect as sa_inspect

from janus import config, graph_mail_service, models, security
from janus.imap_service import IMAPMonitor
from janus.services import google_client, oauth_connection_service
from janus.workers import email as email_worker

_ACCOUNT = {
    "name": "Work",
    "email": "work@example.com",
    "imap_server": "imap.example.com",
    "imap_port": 993,
    "username": "work@example.com",
    "password": "secret",
}


@pytest.fixture
def member(client, db):
    """A signed-in user and its client."""
    user = models.User(username="member", hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    response = client.post("/api/auth/login", json={"username": "member", "password": "pw"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client, user


def _labels(calls):
    return [label for label, _args in calls]


def test_adding_a_mailbox_starts_its_monitor_without_a_restart(member, mailbox_workers):
    """The regression: a mailbox added through the API was only picked up at the next start."""
    client, _ = member

    account = client.post("/api/emails/accounts", json=_ACCOUNT).json()

    assert _labels(mailbox_workers.started) == ["imap:work@example.com"]
    started_account, stop_event = mailbox_workers.started[0][1]
    assert started_account.id == account["id"]
    assert started_account.email == "work@example.com"
    assert sa_inspect(started_account).session is None  # its own copy, not the request's row
    assert stop_event.is_set() is False


def test_adding_an_inactive_mailbox_starts_nothing(member, mailbox_workers):
    """A mailbox saved switched off has no monitor until it is switched on."""
    client, _ = member

    client.post("/api/emails/accounts", json={**_ACCOUNT, "is_active": False})

    assert mailbox_workers.started == []


def test_switching_a_mailbox_off_and_on_stops_and_starts_its_monitor(member, mailbox_workers):
    """Deactivating signals the running monitor to stop; activating starts a fresh one."""
    client, _ = member
    account_id = client.post("/api/emails/accounts", json=_ACCOUNT).json()["id"]
    first_stop = mailbox_workers.started[0][1][1]

    client.patch(f"/api/emails/accounts/{account_id}", json={"is_active": False})

    assert first_stop.is_set()
    assert mailbox_workers.stopped == ["imap:work@example.com"]
    assert len(mailbox_workers.started) == 1

    client.patch(f"/api/emails/accounts/{account_id}", json={"is_active": True})

    assert len(mailbox_workers.started) == 2
    assert mailbox_workers.started[1][1][1].is_set() is False


def test_renaming_a_mailbox_leaves_its_monitor_alone(member, mailbox_workers):
    """Only fields that change how the mailbox connects restart the monitor."""
    client, _ = member
    account_id = client.post("/api/emails/accounts", json=_ACCOUNT).json()["id"]

    client.patch(f"/api/emails/accounts/{account_id}", json={"name": "Office"})

    assert len(mailbox_workers.started) == 1
    assert mailbox_workers.stopped == []


def test_changing_the_address_restarts_the_monitor_under_the_new_label(member, mailbox_workers):
    """The monitor label follows the address, so the old one is stopped and a new one started."""
    client, _ = member
    account_id = client.post("/api/emails/accounts", json=_ACCOUNT).json()["id"]

    client.patch(f"/api/emails/accounts/{account_id}", json={"email": "new@example.com", "username": "new@example.com"})

    assert mailbox_workers.stopped == ["imap:work@example.com"]
    assert _labels(mailbox_workers.started) == ["imap:work@example.com", "imap:new@example.com"]


def test_deleting_a_mailbox_stops_its_monitor(member, mailbox_workers):
    """A deleted mailbox's monitor is signalled to stop and no longer supervised."""
    client, _ = member
    account_id = client.post("/api/emails/accounts", json=_ACCOUNT).json()["id"]
    stop_event = mailbox_workers.started[0][1][1]

    assert client.delete(f"/api/emails/accounts/{account_id}").status_code == 200

    assert stop_event.is_set()
    assert mailbox_workers.stopped == ["imap:work@example.com"]


def test_connected_gmail_mailbox_starts_and_disconnect_stops_it(member, db, mailbox_workers, monkeypatch):
    """Adding a Google mailbox starts its monitor; disconnecting Google stops it."""
    for key in ("google_client_id", "google_client_secret"):
        monkeypatch.setattr(config.settings, key, "")
    client, user = member
    oauth_connection_service.upsert_connection(
        db, user.id, "google", account_email="me@gmail.com", credentials="{}", scopes=[google_client.MAIL_SCOPE]
    )
    db.commit()

    assert client.post("/api/connections/google/mailbox", json={}).status_code == 200
    assert _labels(mailbox_workers.started) == ["imap:me@gmail.com"]

    assert client.delete("/api/connections/google").status_code == 204
    assert mailbox_workers.stopped == ["imap:me@gmail.com"]
    assert mailbox_workers.started[0][1][1].is_set()


def test_idle_loop_ends_and_logs_out_once_stopped():
    """A stopped IMAP monitor leaves IDLE within a second and closes its connection."""
    stop_event = threading.Event()

    class FakeClient:
        def idle(self):
            return None

        def idle_check(self, timeout):
            assert timeout <= 1.0
            stop_event.set()
            return []

        def idle_done(self):
            return None

    monitor = IMAPMonitor(models.EmailAccount(id=1, email="a@example.com"), stop_event=stop_event)
    client = FakeClient()
    monitor.client = client
    monitor.connect = lambda: None
    monitor._write_heartbeat = MagicMock()
    monitor.logout = MagicMock()

    monitor.idle_loop(MagicMock())

    monitor.logout.assert_called_once()


def test_graph_poll_loop_ends_once_stopped():
    """A stopped Outlook monitor stops polling without asking the database whether it is still wanted."""
    stop_event = threading.Event()
    stop_event.set()
    monitor = graph_mail_service.GraphMailMonitor(
        models.EmailAccount(id=1, email="a@example.com"), stop_event=stop_event
    )
    monitor.sync = MagicMock()

    monitor.poll_loop(MagicMock())

    monitor.sync.assert_not_called()


def test_start_survives_a_previous_monitor_that_has_not_finished(mailbox_workers, monkeypatch):
    """worker_manager refuses a replacement while the old thread runs; the API call still succeeds."""

    def refuse(*args, **kwargs):
        raise RuntimeError("did not stop")

    monkeypatch.setattr(mailbox_workers, "start", refuse)

    email_worker.start_mailbox_worker(models.EmailAccount(id=5, email="busy@example.com", is_active=True))
