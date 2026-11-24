"""Tests for event-driven mail processing: the mail.received trigger and its tools (JAN-232)."""

from datetime import UTC, datetime

import pytest

from janus import builtin_triggers, config, models, notifications, processor
from janus.agent_toolboxes.mail_processing import build_mail_processing_tools
from janus.agent_toolboxes.shared import ToolContext
from janus.capability_profile import user_connected_capabilities
from janus.trigger_events import EVENTS, TriggerEvent


def _owner_with_mailbox(db, username="mail-owner"):
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    account = models.EmailAccount(
        name=f"Mail {username}", email=f"{username}@example.com", password="x", user_id=user.id
    )
    db.add(account)
    db.commit()
    db.refresh(user)
    db.refresh(account)
    return user, account


def _email(db, account, message_id="<m1>", **overrides):
    record = models.EmailRecord(
        message_id=message_id,
        account_id=account.id,
        subject=overrides.get("subject", "Contract renewal due Friday"),
        sender=overrides.get("sender", "legal@example.com"),
        snippet="Please sign by Friday.",
        body="Please sign the renewal by Friday 2026-10-02.",
        priority=models.Priority.DIGEST,
        received_at=datetime(2026, 9, 28, 9, 0),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _tools(db, user):
    context = ToolContext(db=db, user=user, connected_capabilities=user_connected_capabilities(db, user))
    return {tool.name: tool for tool in build_mail_processing_tools(context)}


def _email_data(account, message_id="<new-1>", sender="boss@example.com", subject="Budget review"):
    return {
        "message_id": message_id,
        "account_id": account.id,
        "uid": 42,
        "subject": subject,
        "sender": sender,
        "snippet": "Can we meet tomorrow?",
        "body": "Can we meet tomorrow at 10?",
        "received_at": datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
    }


@pytest.fixture
def worker_enabled(monkeypatch):
    """Worker enabled."""
    monkeypatch.setattr(config.settings, "runtime_trigger_worker_enabled", True)


def test_new_mail_is_stored_provisionally_and_handed_to_the_mail_trigger(db, worker_enabled):
    """New mail is stored provisionally and handed to the mail trigger."""
    user, account = _owner_with_mailbox(db)
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    proc = processor.EmailProcessor(db)
    proc._get_llm = lambda: pytest.fail("the synchronous classifier must not run in event mode")  # noqa: SLF001

    record = proc.process_email(_email_data(account))

    assert record.priority == models.Priority.DIGEST
    assert record.reasoning == processor.PENDING_TRIGGER_PROCESSING_REASON
    run = db.query(models.RuntimeTrigger).one()
    mail_definition = db.query(models.TriggerDefinition).filter_by(builtin_key="builtin.mail").one()
    assert run.definition_id == mail_definition.id
    assert run.kind == "event"
    assert f"email_id: {record.id}" in run.payload["objective"]
    assert "Budget review" in run.payload["objective"]
    # The producer publishes exactly the payload the event catalog documents (JAN-210).
    event = run.payload["event"]
    assert event["type"] == TriggerEvent.MAIL_RECEIVED
    declared = {field.name for field in EVENTS[TriggerEvent.MAIL_RECEIVED].payload}
    assert set(event) - {"type", "id"} == declared

    # Redelivery of the same message never enqueues a second run.
    proc.process_email(_email_data(account))
    assert db.query(models.RuntimeTrigger).count() == 1


def test_rule_filtered_mail_skips_the_trigger(db, worker_enabled):
    """Rule filtered mail skips the trigger."""
    user, account = _owner_with_mailbox(db)
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    db.add(models.Rule(name="Spam", pattern="spam@", field="sender", priority=models.Priority.SPAM, user_id=user.id))
    db.commit()

    record = processor.EmailProcessor(db).process_email(_email_data(account, sender="spam@example.com"))

    assert record.priority == models.Priority.SPAM
    assert db.query(models.RuntimeTrigger).count() == 0


def test_without_an_enabled_mail_trigger_the_classic_classifier_stays_in_charge(db, worker_enabled, monkeypatch):
    """Without an enabled mail trigger the classic classifier stays in charge."""
    user, account = _owner_with_mailbox(db)
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    mail_definition = db.query(models.TriggerDefinition).filter_by(builtin_key="builtin.mail").one()
    mail_definition.enabled = False
    db.commit()
    proc = processor.EmailProcessor(db)
    monkeypatch.setattr(proc, "_evaluate_rules", lambda email_data: models.Priority.IMPORTANT)
    for name in ("capture_email_reminders",):
        monkeypatch.setattr(f"janus.reminder_service.ReminderService.{name}", lambda self, record: [])
    monkeypatch.setattr("janus.services.topic_service.TopicService.capture_email_topics", lambda *a, **k: [])
    monkeypatch.setattr(
        "janus.services.local_planner_service.LocalPlannerService.capture_email_suggestions", lambda *a, **k: []
    )

    record = proc.process_email(_email_data(account))

    assert record.priority == models.Priority.IMPORTANT
    assert record.reasoning == "Matched user rule"
    assert db.query(models.RuntimeTrigger).count() == 0


def test_set_email_priority_records_reasoning_and_notifies_once(db, monkeypatch):
    """Set email priority records reasoning and notifies once."""
    user, account = _owner_with_mailbox(db)
    record = _email(db, account)
    notified = []
    monkeypatch.setattr(notifications.NotificationService, "notify", staticmethod(notified.append))
    tools = _tools(db, user)

    first = tools["set_email_priority"].invoke(
        {"email_id": record.id, "priority": "important", "reasoning": "Signature deadline Friday."}
    )
    second = tools["set_email_priority"].invoke(
        {"email_id": record.id, "priority": "IMPORTANT", "reasoning": "Still urgent.", "confidence": "high"}
    )

    db.refresh(record)
    assert first == {"email_id": record.id, "priority": "important", "notified": True, "marked_read": False}
    assert second["notified"] is False
    assert record.reasoning == "Still urgent."
    assert record.classification_confidence == models.ConfidenceLevel.HIGH
    assert len(notified) == 1


def test_deciding_a_provisional_digest_marks_it_read_on_the_server(db, monkeypatch):
    """Only a provisional email that Janus files as DIGEST is marked read, like the synchronous path."""
    from janus import imap_service

    user, account = _owner_with_mailbox(db)
    pending = _email(db, account, "<pending>")
    pending.reasoning = processor.PENDING_TRIGGER_PROCESSING_REASON
    pending.imap_uid = 42
    decided = _email(db, account, "<decided>")
    decided.imap_uid = 43
    urgent = _email(db, account, "<urgent>")
    urgent.reasoning = processor.PENDING_TRIGGER_PROCESSING_REASON
    urgent.imap_uid = 44
    db.commit()
    marked = []
    monkeypatch.setattr(
        imap_service, "mark_message_read", lambda account, uid: marked.append((account.id, uid)) or True
    )
    monkeypatch.setattr(notifications.NotificationService, "notify", staticmethod(lambda record: None))
    tools = _tools(db, user)

    result = tools["set_email_priority"].invoke(
        {"email_id": pending.id, "priority": "digest", "reasoning": "Newsletter."}
    )
    tools["set_email_priority"].invoke({"email_id": decided.id, "priority": "digest", "reasoning": "Re-filed."})
    tools["set_email_priority"].invoke({"email_id": urgent.id, "priority": "important", "reasoning": "Deadline."})

    assert result["marked_read"] is True
    assert marked == [(account.id, 42)]


class _FakeIMAPClient:
    def __init__(self, uid_validity):
        self.uid_validity = uid_validity
        self.flagged = []

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, username, password):
        pass

    def select_folder(self, folder):
        pass

    def folder_status(self, folder, items):
        return {b"UIDVALIDITY": self.uid_validity}

    def add_flags(self, uids, flags):
        self.flagged.append((uids, flags))


def test_mark_message_read_refuses_a_rebuilt_mailbox(db, monkeypatch):
    """A changed UIDVALIDITY means the uid names another message, so nothing is flagged."""
    from janus import imap_service

    _, account = _owner_with_mailbox(db)
    account.uid_validity = 7
    monkeypatch.setattr(imap_service.security.security_service, "decrypt", lambda value: "secret")

    same = _FakeIMAPClient(uid_validity=7)
    monkeypatch.setattr(imap_service, "IMAPClient", same)
    assert imap_service.mark_message_read(account, 42) is True
    assert same.flagged == [([42], [b"\\Seen"])]

    rebuilt = _FakeIMAPClient(uid_validity=8)
    monkeypatch.setattr(imap_service, "IMAPClient", rebuilt)
    assert imap_service.mark_message_read(account, 42) is False
    assert rebuilt.flagged == []


def test_mail_tools_are_owner_scoped(db):
    """Mail tools are owner scoped."""
    owner, account = _owner_with_mailbox(db)
    stranger, _ = _owner_with_mailbox(db, "stranger")
    record = _email(db, account)
    tools = _tools(db, stranger)

    with pytest.raises(ValueError, match="not found"):
        tools["set_email_priority"].invoke({"email_id": record.id, "priority": "spam", "reasoning": "x"})


def test_create_reminder_and_suggest_planner_item(db):
    """Create reminder and suggest planner item."""
    user, account = _owner_with_mailbox(db)
    record = _email(db, account)
    tools = _tools(db, user)

    reminder = tools["create_reminder"].invoke(
        {
            "email_id": record.id,
            "kind": "preparation",
            "title": "Sign the contract renewal",
            "due_at": "2026-10-02T17:00:00+00:00",
            "prepare_for_at": "2026-10-01T09:00:00+00:00",
        }
    )
    again = tools["create_reminder"].invoke(
        {
            "email_id": record.id,
            "kind": "preparation",
            "title": "Sign the contract renewal",
            "due_at": "2026-10-02T17:00:00+00:00",
        }
    )
    assert reminder["title"] == "Sign the contract renewal"
    assert reminder["reminder_ids"] == again["reminder_ids"], "re-recording updates instead of duplicating"
    assert db.query(models.ReminderMemory).count() == 1

    with pytest.raises(ValueError, match="kind must be event or preparation"):
        tools["create_reminder"].invoke(
            {"email_id": record.id, "kind": "whenever", "title": "x", "due_at": "2026-10-02"}
        )

    suggestion = tools["suggest_planner_item"].invoke(
        {"email_id": record.id, "suggestion_type": "task", "title": "Sign renewal", "due_at": "2026-10-02T17:00:00"}
    )
    assert suggestion["title"] == "Sign renewal"
    stored = db.query(models.PlannerSuggestion).one()
    assert stored.status == models.PlannerSuggestionStatus.PENDING
    assert stored.source_email_id == record.id


def test_track_email_topic_creates_a_candidate(db):
    """Track email topic creates a candidate."""
    user, account = _owner_with_mailbox(db)
    record = _email(db, account)
    tools = _tools(db, user)

    result = tools["track_email_topic"].invoke(
        {"email_id": record.id, "title": "Contract renewal", "summary": "Annual vendor contract", "confidence": "high"}
    )

    assert result["recorded"] is True
    topic = db.query(models.TrackedTopic).filter_by(user_id=user.id).one()
    assert topic.title == "Contract renewal"
