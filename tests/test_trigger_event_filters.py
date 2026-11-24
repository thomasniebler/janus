"""Payload filters and rate limits on event trigger definitions (JAN-363)."""

import zlib
from datetime import UTC, datetime, timedelta

import pytest

from janus import builtin_triggers, config, models, processor, trigger_approval
from janus import trigger_definition_service as definitions
from janus import trigger_proposal_service as proposals
from janus.trigger_event_filters import (
    EventFilterError,
    matches,
    validate_event_filter,
    validate_max_runs_per_hour,
)
from janus.trigger_events import TriggerEvent

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
GROUP = TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED


def _user(db, username="owner") -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _definition(db, user_id, event=GROUP, **fields) -> models.TriggerDefinition:
    values = {
        "name": "React",
        "objective": "React.",
        "tool_ids": ["core"],
        "kind": "event",
        "schedule": None,
        "event_type": event.value,
        "timezone": "UTC",
    }
    values.update(fields)
    return definitions.create_definition(db, user_id, now=NOW, **values)


def _emit(db, user_id, event_id, event=GROUP, at=NOW, **data):
    return definitions.emit_event(db, user_id, event, event_id, data, occurred_at=at)


# --- validation ---------------------------------------------------------------------------------


def test_filters_only_accept_the_events_own_payload_fields():
    """A condition names a payload field of the event, a known operator and at least one value."""
    cleaned = validate_event_filter([{"field": "chat_id", "op": "in", "values": [" 491@s.whatsapp.net ", ""]}], GROUP)
    assert cleaned == [{"field": "chat_id", "op": "in", "values": ["491@s.whatsapp.net"]}]
    assert validate_event_filter([], GROUP) is None
    for bad in (
        [{"field": "subject", "op": "in", "values": ["x"]}],  # a mail field, not a WhatsApp one
        [{"field": "chat_id", "op": "equals", "values": ["x"]}],
        [{"field": "chat_id", "op": "in", "values": ["  "]}],
        [{"field": "chat_id", "op": "in", "values": "x"}],
        [{"field": "chat_id", "op": "in", "values": ["x"]}] * 11,
    ):
        with pytest.raises(EventFilterError):
            validate_event_filter(bad, GROUP)


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), (5, 5), ("7", 7)])
def test_rate_limit_values(value, expected):
    """The limit is a whole number from 1 to 1000, or nothing."""
    assert validate_max_runs_per_hour(value) == expected


@pytest.mark.parametrize("value", [0, -1, 1001, 2.5, "many"])
def test_rate_limit_rejects_out_of_range(value):
    """Zero, negatives, fractions and huge values are refused."""
    with pytest.raises(EventFilterError):
        validate_max_runs_per_hour(value)


def test_matching_is_case_insensitive_and_treats_missing_fields_as_empty():
    """in/not_in compare whole values, contains a substring; a missing field never matches in/contains."""
    data = {"chat_id": "Family@g.us", "sender": "Alice", "body": "Dinner at 7?"}
    assert matches([{"field": "chat_id", "op": "in", "values": ["family@g.us"]}], data)
    assert not matches([{"field": "chat_id", "op": "not_in", "values": ["FAMILY@g.us"]}], data)
    assert matches([{"field": "body", "op": "contains", "values": ["dinner"]}], data)
    assert not matches([{"field": "attachment", "op": "contains", "values": ["pdf"]}], data)
    assert matches([{"field": "attachment", "op": "not_in", "values": ["pdf"]}], data)
    both = [
        {"field": "chat_id", "op": "in", "values": ["family@g.us"]},
        {"field": "sender", "op": "in", "values": ["bob"]},
    ]
    assert not matches(both, data)
    assert matches(None, data)


# --- definitions --------------------------------------------------------------------------------


def test_new_definitions_get_the_events_default_limit_unless_one_is_given(db):
    """WhatsApp group events start at 10 runs/hour; mail has no default; an explicit None means no limit."""
    user = _user(db)
    assert _definition(db, user.id).max_runs_per_hour == 10
    assert _definition(db, user.id, event=TriggerEvent.MAIL_RECEIVED).max_runs_per_hour is None
    assert _definition(db, user.id, max_runs_per_hour=None).max_runs_per_hour is None
    assert _definition(db, user.id, max_runs_per_hour=3).max_runs_per_hour == 3
    manual = definitions.create_definition(
        db, user.id, now=NOW, name="M", objective="o", tool_ids=["core"], kind="manual", max_runs_per_hour=5
    )
    assert manual.max_runs_per_hour is None and manual.event_filter is None


def test_an_invalid_filter_is_a_definition_error(db):
    """The service turns filter errors into definition errors (422 at the API)."""
    user = _user(db)
    with pytest.raises(definitions.TriggerDefinitionError, match="not a field"):
        _definition(db, user.id, event_filter=[{"field": "nope", "op": "in", "values": ["x"]}])


def test_update_sets_and_clears_filter_and_limit(db):
    """A partial update keeps, replaces or clears the filter and the limit."""
    user = _user(db)
    definition = _definition(db, user.id)
    definitions.update_definition(db, definition, event_filter=[{"field": "chat", "op": "contains", "values": ["fam"]}])
    assert definition.event_filter == [{"field": "chat", "op": "contains", "values": ["fam"]}]
    assert definition.max_runs_per_hour == 10
    definitions.update_definition(db, definition, event_filter=None, max_runs_per_hour=None)
    assert definition.event_filter is None and definition.max_runs_per_hour is None


# --- emit_event ---------------------------------------------------------------------------------


def test_a_filter_decides_which_events_start_a_run(db):
    """Only events passing every condition are enqueued."""
    user = _user(db)
    _definition(db, user.id, event_filter=[{"field": "chat_id", "op": "in", "values": ["family@g.us"]}])
    assert len(_emit(db, user.id, "1", chat_id="family@g.us", body="hi")) == 1
    assert _emit(db, user.id, "2", chat_id="work@g.us", body="hi") == []


def test_the_rate_limit_drops_and_counts_events_over_it_per_rolling_hour(db):
    """Runs past the limit within an hour are dropped and recorded; an hour later they run again."""
    user = _user(db)
    definition = _definition(db, user.id, max_runs_per_hour=2)
    started = [_emit(db, user.id, str(n), at=NOW + timedelta(minutes=n), chat_id="g") for n in range(4)]
    assert [len(runs) for runs in started] == [1, 1, 0, 0]
    db.refresh(definition)
    assert definition.rate_limited_count == 2
    assert definition.last_rate_limited_at == (NOW + timedelta(minutes=3)).replace(tzinfo=None)
    assert len(_emit(db, user.id, "later", at=NOW + timedelta(minutes=61), chat_id="g")) == 1


def test_limits_are_per_definition(db):
    """One definition hitting its limit does not stop another."""
    user = _user(db)
    _definition(db, user.id, name="A", max_runs_per_hour=1)
    _definition(db, user.id, name="B", max_runs_per_hour=None)
    assert len(_emit(db, user.id, "1", chat_id="g")) == 2
    assert len(_emit(db, user.id, "2", chat_id="g")) == 1


# --- approval -----------------------------------------------------------------------------------


def test_unset_filter_and_limit_keep_existing_approval_fingerprints(db):
    """Approvals recorded before JAN-363 (no filter, no limit) keep their fingerprint."""
    user = _user(db)
    definition = _definition(db, user.id, event=TriggerEvent.MAIL_RECEIVED)
    before = {field: getattr(definition, field) for field in trigger_approval.FINGERPRINT_FIELDS}
    assert trigger_approval.approval_fingerprint(definition) == trigger_approval.approval_fingerprint(before)
    definition.max_runs_per_hour = 5
    assert trigger_approval.approval_fingerprint(definition) != trigger_approval.approval_fingerprint(before)


def test_janus_can_propose_a_filter_and_the_user_approves_it(db):
    """A proposal carries filter and default limit; changing either afterwards voids the approval."""
    user = _user(db)
    proposal = proposals.create_proposal(
        db,
        user.id,
        rationale="Only the family group.",
        name="Family",
        objective="Summarize family messages.",
        tool_ids=["core"],
        kind="event",
        event_type=GROUP.value,
        event_filter=[{"field": "chat_id", "op": "in", "values": ["family@g.us"]}],
    )
    assert proposal.max_runs_per_hour == 10
    approved = proposals.approve_proposal(db, proposal, user.id, edits={"max_runs_per_hour": 4})
    assert approved.max_runs_per_hour == 4
    assert trigger_approval.approval_fingerprint(approved) == approved.approval_fingerprint
    approved.event_filter = None
    assert trigger_approval.approval_fingerprint(approved) != approved.approval_fingerprint


# --- mail ---------------------------------------------------------------------------------------


@pytest.fixture
def mail_owner(db, monkeypatch):
    """A mailbox owner with the built-in Mail processing trigger and the side-capturers stubbed."""
    monkeypatch.setattr(config.settings, "runtime_trigger_worker_enabled", True)
    user = _user(db, "mail-owner")
    account = models.EmailAccount(name="Mail", email="me@example.com", password="x", user_id=user.id)
    db.add(account)
    db.commit()
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    for name in ("capture_email_reminders",):
        monkeypatch.setattr(f"janus.reminder_service.ReminderService.{name}", lambda self, record: [])
    monkeypatch.setattr("janus.services.topic_service.TopicService.capture_email_topics", lambda *a, **k: [])
    monkeypatch.setattr(
        "janus.services.local_planner_service.LocalPlannerService.capture_email_suggestions", lambda *a, **k: []
    )
    return user, account


def _mail(account, sender):
    return {
        "message_id": f"<{sender}>",
        "account_id": account.id,
        "uid": zlib.crc32(sender.encode()),
        "subject": "Hello",
        "sender": sender,
        "snippet": "Hi",
        "body": "Hi",
        "received_at": NOW,
    }


def test_mail_no_mail_trigger_accepts_is_classified_synchronously(db, mail_owner):
    """Mail every mail.received definition filters out is not left waiting for a run that never comes."""
    user, account = mail_owner
    builtin = db.query(models.TriggerDefinition).filter_by(builtin_key="builtin.mail").one()
    definitions.update_definition(
        db, builtin, event_filter=[{"field": "from", "op": "contains", "values": ["@work.example"]}]
    )
    proc = processor.EmailProcessor(db)
    proc._get_llm = lambda: type(  # noqa: SLF001
        "LLM", (), {"classify_email": lambda self, *a, **k: (models.Priority.IMPORTANT, "LLM said so", "high")}
    )()

    handed_over = proc.process_email(_mail(account, "boss@work.example"))
    classified = proc.process_email(_mail(account, "friend@home.example"))

    assert handed_over.reasoning == processor.PENDING_TRIGGER_PROCESSING_REASON
    assert classified.reasoning == "LLM said so"
    assert db.query(models.RuntimeTrigger).count() == 1
    assert builtin.rate_limited_count == 0  # the pre-check never records a drop


# --- API ----------------------------------------------------------------------------------------


def test_api_omitted_limit_takes_the_default_and_null_means_none(client, db):
    """POST without max_runs_per_hour gets the event's default; an explicit null stores no limit."""
    from types import SimpleNamespace

    from janus import security
    from janus.main import api

    user = _user(db)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=user.id, is_admin=True)
    base = {"name": "G", "objective": "o", "tool_ids": ["core"], "kind": "event", "event_type": GROUP.value}
    try:
        defaulted = client.post("/api/runtime/definitions/", json=base)
        unlimited = client.post("/api/runtime/definitions/", json={**base, "max_runs_per_hour": None})
        filtered = client.post(
            "/api/runtime/definitions/",
            json={**base, "event_filter": [{"field": "chat_id", "op": "in", "values": ["fam@g.us"]}]},
        )
        bad = client.post(
            "/api/runtime/definitions/",
            json={**base, "event_filter": [{"field": "subject", "op": "in", "values": ["x"]}]},
        )
        assert defaulted.status_code == 201, defaulted.text
        assert defaulted.json()["max_runs_per_hour"] == 10
        assert defaulted.json()["rate_limited_count"] == 0
        assert unlimited.json()["max_runs_per_hour"] is None
        assert filtered.json()["event_filter"] == [{"field": "chat_id", "op": "in", "values": ["fam@g.us"]}]
        assert bad.status_code == 422
        cleared = client.patch(f"/api/runtime/definitions/{filtered.json()['id']}", json={"event_filter": None})
        assert cleared.json()["event_filter"] is None
        catalog = client.get("/api/runtime/definitions/event-catalog").json()
        limits = {event["event_type"]: event["default_max_runs_per_hour"] for row in catalog for event in row["events"]}
        assert limits[GROUP.value] == 10 and limits["mail.received"] is None
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_mail_turned_away_after_the_pre_check_is_classified_not_stranded(db, mail_owner, monkeypatch):
    """When the limit fills between the read-only pre-check and the hand-off, the email is classified synchronously."""
    user, account = mail_owner
    builtin = db.query(models.TriggerDefinition).filter_by(builtin_key="builtin.mail").one()
    definitions.update_definition(db, builtin, max_runs_per_hour=1)
    proc = processor.EmailProcessor(db)
    proc._get_llm = lambda: type(  # noqa: SLF001
        "LLM", (), {"classify_email": lambda self, *a, **k: (models.Priority.IMPORTANT, "LLM said so", "high")}
    )()
    original = proc._mail_event_owner  # noqa: SLF001

    def race(email_data, rule_priority):
        owner = original(email_data, rule_priority)  # the pre-check still sees a free slot ...
        if email_data["sender"] == "second@example.com":
            definitions.emit_event(db, user.id, "mail.received", "<concurrent>", {"from": "x"})  # ... another takes it
        return owner

    monkeypatch.setattr(proc, "_mail_event_owner", race)
    stranded_candidate = proc.process_email(_mail(account, "second@example.com"))

    assert stranded_candidate.reasoning == "LLM said so"
    assert stranded_candidate.priority == models.Priority.IMPORTANT
    assert db.query(models.EmailRecord).filter_by(message_id="<second@example.com>").count() == 1
