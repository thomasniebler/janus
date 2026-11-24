"""Tests for deterministic scheduled and external-event trigger adapters."""

from datetime import UTC, datetime

import pytest

from janus.trigger_adapters import EventTriggerSpec, ScheduledTriggerSpec
from janus.triggers import TriggerKind


def test_scheduled_adapter_is_deterministic_per_occurrence():
    """Retrying one occurrence produces the same idempotency and trigger IDs."""
    spec = ScheduledTriggerSpec(
        schedule_id="morning-briefing",
        owner_id=7,
        task_id="task-1",
        objective="Prepare the morning briefing",
        due_at=datetime(2026, 9, 3, 7, 30, tzinfo=UTC),
    )

    first = spec.as_envelope()
    second = spec.as_envelope()

    assert first == second
    assert first.kind is TriggerKind.SCHEDULED
    assert first.idempotency_key == "schedule:morning-briefing:2026-09-03T07:30:00+00:00"
    assert first.payload["max_steps"] == 100


def test_event_adapter_namespaces_event_id_by_source():
    """Two providers may reuse an event ID without colliding in Janus."""
    spec = EventTriggerSpec(
        event_id="evt-1",
        owner_id=7,
        task_id="task-1",
        objective="Review the incoming event",
        source="calendar",
        payload={"title": "Planning"},
        occurred_at=datetime(2026, 9, 3, 8, tzinfo=UTC),
    )

    envelope = spec.as_envelope()

    assert envelope.kind is TriggerKind.EVENT
    assert envelope.idempotency_key == "event:calendar:evt-1"
    assert envelope.payload["title"] == "Planning"
    assert envelope.payload["objective"] == "Review the incoming event"
    assert envelope.owner_id == 7


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("owner_id", 0), ("max_steps", True), ("task_id", " ")],
)
def test_trigger_adapters_reject_invalid_runtime_context(field_name, value):
    """Adapters fail closed before an invalid trigger can enter durable storage."""
    values = {
        "schedule_id": "schedule-1",
        "owner_id": 7,
        "task_id": "task-1",
        "objective": "Run task",
        "due_at": datetime(2026, 9, 3, tzinfo=UTC),
        "max_steps": 10,
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name if field_name != "max_steps" else "max_steps"):
        ScheduledTriggerSpec(**values)


def test_event_adapter_rejects_naive_timestamp():
    """External event timestamps must not be interpreted in the server timezone."""
    with pytest.raises(ValueError, match="occurred_at"):
        EventTriggerSpec(
            event_id="evt-1",
            owner_id=7,
            task_id="task-1",
            objective="Run task",
            occurred_at=datetime(2026, 9, 3),
        )
