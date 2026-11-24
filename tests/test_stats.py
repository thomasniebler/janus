"""Tests for the owner-scoped activity statistics (JAN-258)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from janus import models, security
from janus.main import api
from janus.services.stats_service import StatsError, StatsService

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _user(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    return user


def _seed(db, user: models.User, *, tag: str) -> None:
    """Give ``user`` mail, runs, memory, topics, WhatsApp and chat activity."""
    account = models.EmailAccount(user_id=user.id, name=f"acct-{tag}", email=f"{tag}@example.test")
    db.add(account)
    db.commit()
    for day, priority in [(9, models.Priority.IMPORTANT), (9, models.Priority.SPAM), (10, models.Priority.DIGEST)]:
        db.add(
            models.EmailRecord(
                account_id=account.id,
                message_id=f"{tag}-{day}-{priority.value}",
                subject="s",
                sender="a@b",
                received_at=datetime(2026, 9, day, 8),
                priority=priority,
            )
        )
    definition = models.TriggerDefinition(
        user_id=user.id, name=f"Digest {tag}", objective="o", tool_ids=[], kind="manual", timezone="UTC"
    )
    db.add(definition)
    db.commit()
    for index, (status, definition_id) in enumerate(
        [("completed", definition.id), ("exhausted", definition.id), ("queued", None)]
    ):
        db.add(
            models.RuntimeTrigger(
                user_id=user.id,
                trigger_id=f"{tag}-run-{index}",
                idempotency_key=f"{tag}-{index}",
                task_id=f"{tag}-task-{index}",
                kind="manual",
                source="test",
                payload={},
                status=status,
                definition_id=definition_id,
                created_at=datetime(2026, 9, 10, 7),
                claimed_at=datetime(2026, 9, 10, 7, 0, 0),
                completed_at=datetime(2026, 9, 10, 7, 0, 30),
            )
        )
    db.add(models.AgentMemoryFact(user_id=user.id, summary="likes tea", created_at=datetime(2026, 9, 8, 9)))
    db.add(models.TrackedTopic(user_id=user.id, slug=f"t-{tag}", title="Topic", status=models.TopicStatus.ACTIVE))
    conversation = models.Conversation(user_id=user.id)
    db.add(conversation)
    db.commit()
    db.add(
        models.ConversationMessage(
            conversation_id=conversation.id,
            role=models.MessageRole.USER,
            content="hi",
            created_at=datetime(2026, 9, 10, 9),
        )
    )
    db.add(
        models.WhatsAppMessage(
            user_id=user.id,
            whatsapp_account_id=1,
            whatsapp_conversation_id=1,
            provider_message_id=f"{tag}-wa",
            provider_timestamp=datetime(2026, 9, 9, 18),
            direction=models.WhatsAppMessageDirection.INCOMING,
            snippet="hey",
        )
    )
    db.commit()


def test_stats_are_owner_scoped_and_bucketed_per_day(db):
    """Only the caller's activity counts, and each series has one row per day of the range."""
    owner = _user(db, "stats-owner")
    other = _user(db, "stats-other")
    _seed(db, owner, tag="own")
    _seed(db, other, tag="other")

    summary = StatsService(db).summary(owner.id, days=7, tz_name="UTC", now=NOW)

    assert summary["dates"][0] == "2026-09-04" and summary["dates"][-1] == "2026-09-10"
    assert len(summary["mail"]["per_day"]) == 7
    assert summary["mail"]["totals"] == {"important": 1, "digest": 1, "ignore": 0, "spam": 1}
    assert summary["mail"]["per_day"][-2] == {"date": "2026-09-09", "important": 1, "digest": 0, "ignore": 0, "spam": 1}

    triggers = summary["triggers"]
    assert triggers["totals"] == {"completed": 1, "failed": 1, "cancelled": 0, "pending": 1}
    assert triggers["success_rate"] == 0.5
    by_name = {row["name"]: row for row in triggers["definitions"]}
    assert by_name["Digest own"]["runs"] == 2
    assert by_name["Digest own"]["avg_duration_seconds"] == 30.0
    assert by_name[None]["runs"] == 1  # the ad-hoc run
    assert "Digest other" not in by_name

    memory = summary["memory"]
    assert memory["new_facts"] == 1 and memory["active_facts"] == 1
    assert memory["active_topics"] == 1 and memory["total_topics"] == 1

    assert summary["messaging"]["totals"] == {"whatsapp_in": 1, "whatsapp_out": 0, "chat_user": 1, "chat_assistant": 0}


def test_daily_buckets_follow_the_callers_timezone(db):
    """A mail at 23:00 UTC on the 9th is the 10th in Berlin."""
    owner = _user(db, "stats-tz")
    account = models.EmailAccount(user_id=owner.id, name="tz", email="tz@example.test")
    db.add(account)
    db.commit()
    db.add(models.EmailRecord(account_id=account.id, message_id="late", received_at=datetime(2026, 9, 9, 23)))
    db.commit()

    utc = StatsService(db).summary(owner.id, days=7, tz_name="UTC", now=NOW)
    berlin = StatsService(db).summary(owner.id, days=7, tz_name="Europe/Berlin", now=NOW)

    assert utc["mail"]["per_day"][-2]["digest"] == 1
    assert berlin["mail"]["per_day"][-1]["digest"] == 1


@pytest.mark.parametrize(("days", "tz_name"), [(14, "UTC"), (7, "Mars/Olympus")])
def test_invalid_ranges_and_timezones_are_rejected(db, days, tz_name):
    """Only the offered ranges and real IANA zones are accepted."""
    with pytest.raises(StatsError):
        StatsService(db).summary(1, days=days, tz_name=tz_name)


def test_stats_endpoint_returns_the_summary_and_validates_input(client, db):
    """The API wraps the service and turns bad input into a 422."""
    owner = _user(db, "stats-api")
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=owner.id)
    try:
        response = client.get("/api/stats", params={"days": 30, "timezone": "UTC"})
        assert response.status_code == 200
        assert len(response.json()["dates"]) == 30
        assert client.get("/api/stats", params={"days": 3}).status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_a_waiting_run_counts_as_pending(db):
    """JAN-207: a run parked on its delegated children has not finished, so the chart shows it pending."""
    owner = _user(db, "stats-waiting")
    db.add(
        models.RuntimeTrigger(
            user_id=owner.id,
            trigger_id="waiting-run",
            idempotency_key="waiting-run",
            task_id="waiting-task",
            kind="manual",
            source="test",
            payload={},
            status=models.RuntimeTriggerStatus.WAITING.value,
            created_at=datetime(2026, 9, 10, 7),
        )
    )
    db.commit()

    totals = StatsService(db).summary(owner.id, days=7, tz_name="UTC", now=NOW)["triggers"]["totals"]

    assert totals == {"completed": 0, "failed": 0, "cancelled": 0, "pending": 1}


def _proposal(db, user, *, name, status, proposed_at, decided_at=None):
    definition = models.TriggerDefinition(
        user_id=user.id,
        name=name,
        objective="o",
        tool_ids=[],
        kind="manual",
        timezone="UTC",
        origin="agent",
        enabled=False,
        proposal_status=status,
        proposed_at=proposed_at,
        approved_at=decided_at if status == "approved" else None,
        rejected_at=decided_at if status == "rejected" else None,
    )
    db.add(definition)


def _tool_call(db, message, position, approval):
    db.add(
        models.ConversationToolCall(
            message_id=message.id,
            position=position,
            call_id=f"call-{message.id}-{position}",
            tool_name="send_mail",
            phase="end",
            approval=approval,
            created_at=datetime(2026, 9, 10, 9),
        )
    )


def test_approval_stats_count_outcomes_rates_and_decision_time(db):
    """JAN-371: proposals and chat approvals per outcome, owner-scoped and counted by proposal date."""
    owner = _user(db, "approvals-owner")
    other = _user(db, "approvals-other")
    _proposal(
        db,
        owner,
        name="a1",
        status="approved",
        proposed_at=datetime(2026, 9, 9, 8),
        decided_at=datetime(2026, 9, 9, 10),
    )
    _proposal(
        db,
        owner,
        name="a2",
        status="approved",
        proposed_at=datetime(2026, 9, 9, 8),
        decided_at=datetime(2026, 9, 9, 12),
    )
    _proposal(
        db,
        owner,
        name="r1",
        status="rejected",
        proposed_at=datetime(2026, 9, 9, 8),
        decided_at=datetime(2026, 9, 9, 14),
    )
    _proposal(db, owner, name="p1", status="proposed", proposed_at=datetime(2026, 9, 10, 8))
    _proposal(db, owner, name="w1", status="withdrawn", proposed_at=datetime(2026, 9, 10, 8))
    _proposal(
        db,
        owner,
        name="old",
        status="approved",
        proposed_at=datetime(2026, 8, 1, 8),
        decided_at=datetime(2026, 8, 1, 9),
    )
    _proposal(
        db, other, name="x", status="rejected", proposed_at=datetime(2026, 9, 9, 8), decided_at=datetime(2026, 9, 9, 9)
    )
    # A user-authored definition is not a proposal.
    db.add(
        models.TriggerDefinition(
            user_id=owner.id, name="mine", objective="o", tool_ids=[], kind="manual", timezone="UTC"
        )
    )
    db.add(
        models.CharacterStateProposal(
            user_id=owner.id,
            status="approved",
            changes={},
            rationale="r",
            created_at=datetime(2026, 9, 10, 8),
            decided_at=datetime(2026, 9, 10, 9),
        )
    )
    db.commit()
    for user in (owner, other):
        conversation = models.Conversation(user_id=user.id)
        db.add(conversation)
        db.commit()
        message = models.ConversationMessage(
            conversation_id=conversation.id,
            role=models.MessageRole.ASSISTANT,
            content="ok",
            created_at=datetime(2026, 9, 10, 9),
        )
        db.add(message)
        db.commit()
        approvals = ["approved", "approved", "approved", "denied", "timeout", None] if user is owner else ["denied"]
        for position, approval in enumerate(approvals):
            _tool_call(db, message, position, approval)
    db.commit()

    approvals = StatsService(db).summary(owner.id, days=7, tz_name="UTC", now=NOW)["approvals"]

    automations = approvals["automations"]
    assert automations["totals"] == {"proposed": 1, "approved": 2, "rejected": 1, "withdrawn": 1, "superseded": 0}
    assert automations["total"] == 5
    assert automations["approval_rate"] == round(2 / 3, 4)
    assert automations["median_hours_to_decision"] == 4.0  # decisions after 2 h, 4 h and 6 h

    chat = approvals["chat_tools"]
    assert chat["totals"] == {"approved": 3, "denied": 1, "timeout": 1, "disconnected": 0, "unavailable": 0}
    assert chat["approval_rate"] == 0.75  # the timeout is not a decision
    assert chat["median_hours_to_decision"] is None

    character = approvals["character"]
    assert character["totals"] == {"proposed": 0, "approved": 1, "rejected": 0}
    assert character["approval_rate"] == 1.0
    assert character["median_hours_to_decision"] == 1.0


def test_approval_stats_are_empty_without_proposals(db):
    """No proposals means zero counts and no rates rather than a division by zero."""
    owner = _user(db, "approvals-empty")

    approvals = StatsService(db).summary(owner.id, days=7, tz_name="UTC", now=NOW)["approvals"]

    assert approvals["automations"]["total"] == 0
    assert approvals["automations"]["approval_rate"] is None
    assert approvals["chat_tools"]["approval_rate"] is None
    assert approvals["character"]["median_hours_to_decision"] is None
