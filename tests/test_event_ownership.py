"""Tests for recording who an event-log row concerns (JAN-272)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from janus import database, event_logger, models, security, trigger_service
from janus.agent_task_runner import AgentTaskRun
from janus.main import api
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerWorker


@pytest.fixture
def logged_rows(monkeypatch, testing_session_local):
    """Route ``event_logger`` to the test database and return a reader for (message, user_id) rows."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)

    def read() -> list[tuple[str, int | None]]:
        with testing_session_local() as session:
            return [(row.message, row.user_id) for row in session.query(models.Event).order_by(models.Event.id)]

    return read


def _make_user(db, username: str, *, is_admin: bool = False) -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_an_event_without_a_known_owner_is_a_system_row(db, logged_rows):
    """An event logged with no owner in scope is stored as a system row."""
    event_logger.log(models.EventType.INFO, "Test", "system")

    assert logged_rows() == [("system", None)]


def test_an_explicit_owner_is_recorded(db, logged_rows):
    """An explicit ``user_id`` is stored as the event's owner."""
    user = _make_user(db, "explicit")

    event_logger.log(models.EventType.INFO, "Test", "explicit", user_id=user.id)

    assert logged_rows() == [("explicit", user.id)]


def test_owned_by_attributes_nested_events_and_restores_the_previous_owner(db, logged_rows):
    """The enclosing owner is inherited, an explicit owner wins, and the scope unwinds."""
    outer = _make_user(db, "outer")
    inner = _make_user(db, "inner")

    with event_logger.owned_by(outer.id):
        event_logger.log(models.EventType.INFO, "Test", "outer")
        with event_logger.owned_by(inner.id):
            event_logger.log(models.EventType.INFO, "Test", "inner")
        event_logger.log(models.EventType.INFO, "Test", "explicit", user_id=inner.id)
        event_logger.log(models.EventType.INFO, "Test", "outer again")
    event_logger.log(models.EventType.INFO, "Test", "after")

    assert logged_rows() == [
        ("outer", outer.id),
        ("inner", inner.id),
        ("explicit", inner.id),
        ("outer again", outer.id),
        ("after", None),
    ]
    assert event_logger.current_owner() is None


def test_everything_a_trigger_run_logs_belongs_to_its_owner(db, logged_rows, testing_session_local):
    """A tool or service logging mid-run attributes its event to the run's owner."""
    owner = _make_user(db, "trigger-owner")

    def executor(_db, _system_prompt, _tools, _objective):
        event_logger.log(models.EventType.INFO, "SomeService", "logged during the run")
        return AgentTaskRun(output="done", tool_calls=())

    spec = ScheduledTriggerSpec(
        schedule_id="owned-run",
        owner_id=owner.id,
        task_id="task-owned",
        objective="do something",
        due_at=datetime.now(UTC),
        source="test",
    )
    trigger_service.enqueue_trigger(db, spec.as_envelope())
    worker = DurableTriggerWorker(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )
    worker.run_once(now=datetime.now(UTC) + timedelta(minutes=1))

    assert ("logged during the run", owner.id) in logged_rows()
    assert event_logger.current_owner() is None


def test_mail_processing_logs_as_the_accounts_owner(db, logged_rows, monkeypatch):
    """The processor attributes what it logs while filing a message to the account's owner."""
    from janus import processor

    owner = _make_user(db, "mail-owner")
    account = models.EmailAccount(name="Mail", email="owner@example.com", password="x", user_id=owner.id)
    db.add(account)
    db.commit()
    seen: list[int | None] = []
    monkeypatch.setattr(
        processor.EmailProcessor,
        "_process_email",
        lambda self, email_data: seen.append(event_logger.current_owner()),
    )

    processor.EmailProcessor(db).process_email({"account_id": account.id, "message_id": "m-1"})

    assert seen == [owner.id]


def test_deleting_a_user_turns_their_events_into_system_rows(client, db):
    """No dangling owner id survives the user, so a later user with the same id inherits nothing."""
    admin = _make_user(db, "admin", is_admin=True)
    member = _make_user(db, "member")
    db.add_all(
        [
            models.Event(source="T", message="member's", user_id=member.id),
            models.Event(source="T", message="admin's", user_id=admin.id),
        ]
    )
    db.commit()
    api.dependency_overrides[security.admin_required] = lambda: SimpleNamespace(id=admin.id, is_admin=True)
    try:
        assert client.delete("/api/auth/users/member").status_code == 200
    finally:
        api.dependency_overrides.pop(security.admin_required, None)

    db.expire_all()
    owners = {row.message: row.user_id for row in db.query(models.Event)}
    assert owners == {"member's": None, "admin's": admin.id}


def test_an_imap_connection_test_logs_as_the_accounts_owner(client, db, logged_rows, monkeypatch):
    """``POST /api/emails/accounts/{id}/test`` attributes the monitor's events to the account owner."""
    from janus import imap_service

    owner = _make_user(db, "imap-test-owner")
    account = models.EmailAccount(name="Mail", email="imap@example.com", password="x", user_id=owner.id)
    db.add(account)
    db.commit()

    def fake_test_connection(self):
        event_logger.log(models.EventType.INFO, "IMAPMonitor", "connection tested")
        return True, "ok"

    monkeypatch.setattr(imap_service.IMAPMonitor, "test_connection", fake_test_connection)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=owner.id, is_admin=False)
    try:
        response = client.post(f"/api/emails/accounts/{account.id}/test")
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)

    assert response.json()["status"] == "success"
    assert ("connection tested", owner.id) in logged_rows()
