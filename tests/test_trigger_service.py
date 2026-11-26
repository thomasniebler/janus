"""Tests for durable trigger storage and atomic claims."""

from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from janus import models, trigger_service
from janus.triggers import TerminalTriggerError, TriggerEnvelope, TriggerKind


def _trigger(key: str = "key-1") -> TriggerEnvelope:
    """Build a valid durable trigger."""
    return TriggerEnvelope(
        trigger_id=f"trigger-{key}",
        idempotency_key=key,
        task_id="task-1",
        kind=TriggerKind.SCHEDULED,
        source="test",
        payload={"source": "test"},
        created_at=datetime(2025, 12, 31, tzinfo=UTC),
    )


def test_enqueue_is_task_scoped_and_idempotent(db):
    """Duplicate delivery returns the existing durable record."""
    first, duplicate = trigger_service.enqueue_trigger(db, _trigger())
    second, was_duplicate = trigger_service.enqueue_trigger(db, _trigger())

    assert duplicate is False
    assert was_duplicate is True
    assert second.id == first.id
    assert second.payload == {"source": "test"}


def test_claim_complete_and_completed_trigger_cannot_be_claimed_again(db):
    """Only one worker claims a queued trigger and completion is terminal."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    now = datetime(2026, 1, 1, tzinfo=UTC)

    claim_token = trigger_service.claim_trigger(db, record.id, now=now)
    assert claim_token
    assert not trigger_service.claim_trigger(db, record.id, now=now)
    assert trigger_service.complete_trigger(db, record.id, claim_token, completed_at=now)
    assert not trigger_service.claim_trigger(db, record.id, now=now + timedelta(hours=1))
    assert db.get(models.RuntimeTrigger, record.id).status == models.RuntimeTriggerStatus.COMPLETED.value


def test_failed_trigger_can_be_reclaimed_and_expired_claim_can_be_reclaimed(db):
    """Failure releases a trigger and an expired lease is recoverable."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first_claim = trigger_service.claim_trigger(db, record.id, now=now)
    assert first_claim
    assert trigger_service.fail_trigger(db, record.id, first_claim, "temporary failure")
    second_claim = trigger_service.claim_trigger(db, record.id, now=now + timedelta(seconds=1))
    assert second_claim
    assert trigger_service.fail_trigger(db, record.id, second_claim, "worker stopped")
    third_claim = trigger_service.claim_trigger(db, record.id, now=now + timedelta(seconds=2))
    assert third_claim
    assert trigger_service.complete_trigger(db, record.id, third_claim, completed_at=now + timedelta(seconds=2))


def test_failed_trigger_respects_retry_delay(db):
    """A delayed retry remains unavailable until its scheduled retry time."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    now = datetime(2026, 1, 1, tzinfo=UTC)

    claim_token = trigger_service.claim_trigger(db, record.id, now=now)
    assert claim_token
    assert trigger_service.fail_trigger(
        db, record.id, claim_token, "temporary failure", retry_after_seconds=30, now=now
    )
    assert not trigger_service.claim_trigger(db, record.id, now=now + timedelta(seconds=29))
    assert trigger_service.claim_trigger(db, record.id, now=now + timedelta(seconds=30))


def test_scheduled_trigger_cannot_be_claimed_before_its_occurrence(db, testing_session_local):
    """A future scheduled occurrence stays queued until its declared time."""
    scheduled_for = datetime(2030, 9, 21, 8, 30, tzinfo=UTC)
    trigger = TriggerEnvelope(
        trigger_id="scheduled-future",
        idempotency_key="schedule:future:2030-09-21T08:30:00+00:00",
        task_id="task-future",
        kind=TriggerKind.SCHEDULED,
        source="settings",
        created_at=scheduled_for,
    )
    record, _ = trigger_service.enqueue_trigger(db, trigger)
    calls = []
    worker = trigger_service.DurableTriggerWorker(testing_session_local, calls.append)

    assert not trigger_service.claim_trigger(db, record.id, now=scheduled_for - timedelta(seconds=1))
    assert worker.run_once(now=scheduled_for - timedelta(seconds=1)) == []
    results = worker.run_once(now=scheduled_for)

    assert [result.status for result in results] == [trigger_service.TriggerDispatchStatus.COMPLETED]
    assert [call.trigger_id for call in calls] == ["scheduled-future"]


def test_event_trigger_with_a_future_provider_timestamp_is_immediately_claimable(db):
    """Provider clock skew must not defer event delivery like a schedule does."""
    future_provider_time = datetime(2030, 9, 21, 8, 30, tzinfo=UTC)
    trigger = TriggerEnvelope(
        trigger_id="event-future",
        idempotency_key="event:calendar:evt-42",
        task_id="task-event",
        kind=TriggerKind.EVENT,
        source="calendar",
        created_at=future_provider_time,
    )
    record, _ = trigger_service.enqueue_trigger(db, trigger)

    assert trigger_service.claim_trigger(db, record.id, now=future_provider_time - timedelta(days=1))


def test_failed_trigger_can_be_exhausted(db):
    """An exhausted trigger is terminal and cannot be claimed again."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    claim_token = trigger_service.claim_trigger(db, record.id)
    assert claim_token

    assert trigger_service.fail_trigger(db, record.id, claim_token, "permanent failure", exhausted=True)
    db.expire_all()
    assert db.get(models.RuntimeTrigger, record.id).status == models.RuntimeTriggerStatus.EXHAUSTED.value
    assert not trigger_service.claim_trigger(db, record.id)


def test_claim_rejects_invalid_lease_and_failure_message(db):
    """Claim and failure inputs fail closed before changing state."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())

    with pytest.raises(ValueError, match="lease_seconds"):
        trigger_service.claim_trigger(db, record.id, lease_seconds=0)
    with pytest.raises(ValueError, match="claim_token"):
        trigger_service.complete_trigger(db, record.id, "")
    with pytest.raises(ValueError, match="failure"):
        trigger_service.fail_trigger(db, record.id, "claim", " ")


def test_durable_dispatcher_records_success_and_failure(db):
    """The opt-in dispatcher uses durable claims and records handler outcomes."""
    attempts = 0

    def handler(_trigger):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return "ok"

    dispatcher = trigger_service.DurableTriggerDispatcher(lambda: db, handler)
    failed = dispatcher.dispatch(_trigger())
    retried = dispatcher.dispatch(_trigger())
    duplicate = dispatcher.dispatch(_trigger())

    assert failed.status.value == "failed"
    assert retried.status.value == "completed"
    assert duplicate.status.value == "duplicate"
    assert attempts == 2


def test_durable_dispatcher_does_not_complete_with_a_replaced_claim(db):
    """A stale worker cannot complete a trigger after another worker reclaims it."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    first_claim = trigger_service.claim_trigger(db, record.id)
    assert first_claim
    second_claim = trigger_service.claim_trigger(db, record.id, now=datetime.now(UTC) + timedelta(minutes=6))
    assert second_claim

    assert not trigger_service.complete_trigger(db, record.id, first_claim)
    assert trigger_service.complete_trigger(db, record.id, second_claim)


def test_durable_worker_processes_queued_rows_and_closes_sessions(db, testing_session_local):
    """The worker claims durable rows, executes outside the DB session, and completes them."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    calls = []

    def handler(trigger):
        calls.append((trigger.trigger_id, dict(trigger.payload)))
        return "processed"

    worker = trigger_service.DurableTriggerWorker(testing_session_local, handler, batch_size=1)
    results = worker.run_once(now=datetime(2026, 1, 1, tzinfo=UTC))

    assert [result.status for result in results] == [trigger_service.TriggerDispatchStatus.COMPLETED]
    assert calls == [("trigger-key-1", {"source": "test"})]
    db.expire_all()
    assert db.get(models.RuntimeTrigger, record.id).status == models.RuntimeTriggerStatus.COMPLETED.value


def test_durable_worker_retries_failed_rows(db, testing_session_local):
    """A handler failure is durable and the next poll can retry the same row."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    attempts = 0

    def handler(_trigger):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return "ok"

    worker = trigger_service.DurableTriggerWorker(testing_session_local, handler)
    first = worker.run_once()
    second = worker.run_once()

    assert first[0].status == trigger_service.TriggerDispatchStatus.FAILED
    assert second[0].status == trigger_service.TriggerDispatchStatus.COMPLETED
    assert attempts == 2
    db.expire_all()
    assert db.get(models.RuntimeTrigger, record.id).status == models.RuntimeTriggerStatus.COMPLETED.value


def test_durable_worker_exhausts_after_configured_attempts(db, testing_session_local):
    """The worker stops retrying a permanently failing trigger at its limit."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    worker = trigger_service.DurableTriggerWorker(
        testing_session_local,
        lambda _trigger: (_ for _ in ()).throw(RuntimeError("permanent failure")),
        max_attempts=2,
    )

    assert worker.run_once()[0].status == trigger_service.TriggerDispatchStatus.FAILED
    assert worker.run_once()[0].status == trigger_service.TriggerDispatchStatus.FAILED
    assert worker.run_once() == []
    db.expire_all()
    assert db.get(models.RuntimeTrigger, record.id).status == models.RuntimeTriggerStatus.EXHAUSTED.value


def test_durable_worker_exhausts_a_terminal_failure_on_the_first_attempt(db, testing_session_local):
    """A failure no retry can fix must not burn the attempt budget (JAN-194)."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    calls = []

    def handler(trigger):
        calls.append(trigger.trigger_id)
        raise TerminalTriggerError("tool authorization denied for step s1: mail:not_allowlisted")

    worker = trigger_service.DurableTriggerWorker(testing_session_local, handler, max_attempts=3)

    results = worker.run_once()
    assert [result.status for result in results] == [trigger_service.TriggerDispatchStatus.FAILED]
    assert worker.run_once() == []
    assert calls == ["trigger-key-1"]
    db.expire_all()
    stored = db.get(models.RuntimeTrigger, record.id)
    assert stored.status == models.RuntimeTriggerStatus.EXHAUSTED.value
    assert stored.attempt_count == 1
    assert stored.last_error == "tool authorization denied for step s1: mail:not_allowlisted"
    assert stored.next_attempt_at is None


def test_durable_dispatcher_exhausts_a_terminal_failure(db):
    """The dispatcher applies the same terminal-vs-retryable split as the worker."""

    def handler(_trigger):
        raise TerminalTriggerError("trigger payload tool_ids must be a list of tool id strings")

    dispatcher = trigger_service.DurableTriggerDispatcher(lambda: db, handler)
    first = dispatcher.dispatch(_trigger())
    second = dispatcher.dispatch(_trigger())

    assert first.status.value == "failed"
    assert second.status.value == "failed"
    assert second.error == "trigger is exhausted"
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    assert record.status == models.RuntimeTriggerStatus.EXHAUSTED.value


def test_durable_worker_stop_event_is_checked_between_polls(monkeypatch, testing_session_local):
    """The polling loop can stop without waiting for another long interval."""
    stop_event = Event()
    worker = trigger_service.DurableTriggerWorker(
        testing_session_local, lambda _trigger: None, poll_interval_seconds=60
    )
    calls = 0

    def run_once():
        nonlocal calls
        calls += 1
        stop_event.set()
        return []

    monkeypatch.setattr(worker, "run_once", run_once)
    worker.run_forever(stop_event)

    assert calls == 1


def test_durable_worker_rejects_invalid_batch_configuration(testing_session_local):
    """Worker configuration fails closed before a background loop can start."""
    with pytest.raises(ValueError, match="batch_size"):
        trigger_service.DurableTriggerWorker(testing_session_local, lambda _trigger: None, batch_size=0)
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        trigger_service.DurableTriggerWorker(testing_session_local, lambda _trigger: None, poll_interval_seconds=-1)
    with pytest.raises(ValueError, match="max_attempts"):
        trigger_service.DurableTriggerWorker(testing_session_local, lambda _trigger: None, max_attempts=0)
    with pytest.raises(ValueError, match="retry_backoff_seconds"):
        trigger_service.DurableTriggerWorker(testing_session_local, lambda _trigger: None, retry_backoff_seconds=-1)


def test_renewing_a_claim_keeps_a_long_run_from_being_reclaimed(db):
    """A renewed lease is not expired, so no second worker re-runs the trigger concurrently."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    start = datetime(2026, 1, 1, tzinfo=UTC)
    token = trigger_service.claim_trigger(db, record.id, now=start)

    assert trigger_service.renew_trigger_claim(db, record.id, token, now=start + timedelta(seconds=280))
    assert not trigger_service.claim_trigger(db, record.id, now=start + timedelta(seconds=400))
    assert not trigger_service.renew_trigger_claim(db, record.id, "someone-else")
    assert trigger_service.claim_trigger(db, record.id, now=start + timedelta(seconds=600))


def test_lease_heartbeat_renews_while_the_block_runs(db, monkeypatch, testing_session_local):
    """The worker's heartbeat pushes ``claimed_at`` forward during a run and stops afterwards."""
    record, _ = trigger_service.enqueue_trigger(db, _trigger())
    token = trigger_service.claim_trigger(db, record.id, now=datetime(2026, 1, 1, tzinfo=UTC))
    renewed = Event()
    real_renew = trigger_service.renew_trigger_claim

    def renew(session, trigger_id, claim_token, **kwargs):
        result = real_renew(session, trigger_id, claim_token, **kwargs)
        renewed.set()
        return result

    monkeypatch.setattr(trigger_service, "renew_trigger_claim", renew)
    with trigger_service.lease_heartbeat(testing_session_local, record.id, token, lease_seconds=3):
        assert renewed.wait(5)

    db.expire_all()
    assert db.get(models.RuntimeTrigger, record.id).claimed_at > datetime(2026, 1, 2)
