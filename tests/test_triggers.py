"""Tests for normalized trigger dispatch semantics."""

from datetime import UTC, datetime
from threading import Event, Thread

from janus.triggers import TriggerDispatcher, TriggerDispatchStatus, TriggerEnvelope, TriggerKind


def _trigger(key: str = "event-1", *, task_id: str = "task-1") -> TriggerEnvelope:
    """Build a valid trigger envelope."""
    return TriggerEnvelope(
        trigger_id=f"trigger-{key}",
        idempotency_key=key,
        task_id=task_id,
        kind=TriggerKind.EVENT,
        source="test",
        payload={"untrusted": "data"},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_envelope_freezes_payload_and_preserves_trigger_kind():
    """Ingress data is copied behind an immutable envelope boundary."""
    payload = {"value": "before"}
    trigger = TriggerEnvelope("id", "key", "task", TriggerKind.SCHEDULED, "scheduler", payload)
    payload["value"] = "after"

    assert trigger.kind == TriggerKind.SCHEDULED
    assert trigger.payload["value"] == "before"


def test_dispatch_deduplicates_completed_trigger_for_same_task():
    """Repeated delivery does not run the task handler twice."""
    calls: list[str] = []
    dispatcher = TriggerDispatcher(lambda trigger: calls.append(trigger.trigger_id))

    first = dispatcher.dispatch(_trigger())
    second = dispatcher.dispatch(_trigger("event-1"))

    assert first.status == TriggerDispatchStatus.COMPLETED
    assert second.status == TriggerDispatchStatus.DUPLICATE
    assert calls == ["trigger-event-1"]


def test_dispatch_does_not_deduplicate_same_key_across_tasks():
    """Idempotency is scoped to the task as well as the source key."""
    calls: list[str] = []
    dispatcher = TriggerDispatcher(lambda trigger: calls.append(trigger.task_id))

    dispatcher.dispatch(_trigger("same", task_id="task-1"))
    result = dispatcher.dispatch(_trigger("same", task_id="task-2"))

    assert result.status == TriggerDispatchStatus.COMPLETED
    assert calls == ["task-1", "task-2"]


def test_concurrent_delivery_returns_in_progress_for_the_second_claim():
    """Concurrent duplicate deliveries cannot enter the handler twice."""
    entered = Event()
    release = Event()
    calls: list[str] = []

    def handler(trigger: TriggerEnvelope) -> None:
        calls.append(trigger.trigger_id)
        entered.set()
        release.wait(timeout=2)

    dispatcher = TriggerDispatcher(handler)
    first_result: list = []
    thread = Thread(target=lambda: first_result.append(dispatcher.dispatch(_trigger())))
    thread.start()
    assert entered.wait(timeout=2)
    second = dispatcher.dispatch(_trigger())
    release.set()
    thread.join(timeout=2)

    assert second.status == TriggerDispatchStatus.IN_PROGRESS
    assert first_result[0].status == TriggerDispatchStatus.COMPLETED
    assert calls == ["trigger-event-1"]


def test_failed_trigger_can_be_retried():
    """Failed handling releases the claim instead of poisoning future retries."""
    attempts = 0

    def handler(_trigger: TriggerEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")

    dispatcher = TriggerDispatcher(handler)

    failed = dispatcher.dispatch(_trigger())
    retried = dispatcher.dispatch(_trigger())

    assert failed.status == TriggerDispatchStatus.FAILED
    assert failed.error == "temporary failure"
    assert retried.status == TriggerDispatchStatus.COMPLETED
