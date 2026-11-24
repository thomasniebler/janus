"""Durable trigger storage and atomic execution claims."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from threading import Event
from typing import TypeAlias

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from janus import models
from janus.triggers import (
    TerminalTriggerError,
    TriggerDispatchResult,
    TriggerDispatchStatus,
    TriggerEnvelope,
    TriggerHandler,
    TriggerKind,
    handler_result_is_waiting,
)

SessionFactory: TypeAlias = Callable[[], Session]  # noqa: UP040
logger = logging.getLogger(__name__)

#: Lifecycle states that still consume scheduler/worker attention. A trigger in
#: one of these states can still be claimed and executed (or retried, or
#: resumed once what it waits for has finished), so they are the states the
#: per-owner backlog cap counts.
ACTIVE_TRIGGER_STATUSES: tuple[str, ...] = (
    models.RuntimeTriggerStatus.QUEUED.value,
    models.RuntimeTriggerStatus.RUNNING.value,
    models.RuntimeTriggerStatus.WAITING.value,
    models.RuntimeTriggerStatus.FAILED.value,
)

#: States a trigger may be cancelled from. A ``RUNNING`` trigger holds a live
#: claim token owned by a worker, so cancelling it would race that worker's
#: completion; terminal states (completed/exhausted/cancelled) are immutable.
#: A ``WAITING`` run holds no claim; cancelling it means it is never resumed.
CANCELLABLE_TRIGGER_STATUSES: tuple[str, ...] = (
    models.RuntimeTriggerStatus.QUEUED.value,
    models.RuntimeTriggerStatus.WAITING.value,
    models.RuntimeTriggerStatus.FAILED.value,
)

#: Runaway self-scheduling safeguard (JAN-184). Agent-authored triggers are
#: created by a model, so an unbounded backlog is a plausible failure mode even
#: without chained automation. Any caller that lets a model create triggers must
#: enforce this cap; the durable store itself stays policy-free.
MAX_ACTIVE_TRIGGERS_PER_OWNER = 25


def _database_time(value: datetime | None = None) -> datetime:
    """Normalize UTC timestamps for SQLAlchemy's timezone-naive DateTime columns."""
    return (value or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)


def enqueue_trigger(
    db: Session,
    trigger: TriggerEnvelope,
    *,
    definition_id: int | None = None,
    parent_trigger_id: str | None = None,
    chain_depth: int = 0,
) -> tuple[models.RuntimeTrigger, bool]:
    """Persist a trigger or return the existing task-scoped duplicate.

    ``definition_id`` links the run to its saved definition in the same insert,
    so a worker can never claim the row before the link exists; the
    definition's current name is snapshotted into ``definition_name`` so the
    run keeps its provenance after the definition is deleted (JAN-204).
    ``parent_trigger_id``/``chain_depth`` record the unattended run that
    scheduled this one (JAN-274); the store records them, the ``triggers``
    toolbox decides whether the chain may grow.
    """
    definition = db.get(models.TriggerDefinition, definition_id) if definition_id is not None else None
    record = models.RuntimeTrigger(
        trigger_id=trigger.trigger_id,
        idempotency_key=trigger.idempotency_key,
        task_id=trigger.task_id,
        user_id=trigger.owner_id,
        kind=trigger.kind.value,
        source=trigger.source,
        payload=dict(trigger.payload),
        created_at=_database_time(trigger.created_at),
        definition_id=definition_id,
        definition_name=definition.name if definition is not None else None,
        parent_trigger_id=parent_trigger_id,
        chain_depth=chain_depth,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        record = db.scalar(
            select(models.RuntimeTrigger).where(
                models.RuntimeTrigger.task_id == trigger.task_id,
                models.RuntimeTrigger.idempotency_key == trigger.idempotency_key,
            )
        )
        if record is None:
            raise
        return record, True
    db.refresh(record)
    return record, False


def count_active_triggers(db: Session, owner_id: int) -> int:
    """Count one owner's triggers that can still be claimed or retried."""
    return (
        db.query(models.RuntimeTrigger)
        .filter(
            models.RuntimeTrigger.user_id == owner_id,
            models.RuntimeTrigger.status.in_(ACTIVE_TRIGGER_STATUSES),
        )
        .count()
    )


def count_follow_ups_of(db: Session, owner_id: int, parent_trigger_id: str) -> int:
    """Count the triggers one unattended run has scheduled (JAN-274), cancelled ones included."""
    return (
        db.query(models.RuntimeTrigger)
        .filter(
            models.RuntimeTrigger.user_id == owner_id,
            models.RuntimeTrigger.parent_trigger_id == parent_trigger_id,
        )
        .count()
    )


def count_unattended_follow_ups_since(db: Session, owner_id: int, since: datetime) -> int:
    """Count the follow-ups this owner's unattended runs scheduled since ``since`` (JAN-274).

    A scheduled row's own ``created_at`` holds its *due* time (see
    ``ScheduledTriggerSpec``), so when it was scheduled is read from the
    one-time definition every follow-up is created with, in the same request.
    """
    return (
        db.query(models.RuntimeTrigger)
        .join(models.TriggerDefinition, models.TriggerDefinition.id == models.RuntimeTrigger.definition_id)
        .filter(
            models.RuntimeTrigger.user_id == owner_id,
            models.RuntimeTrigger.parent_trigger_id.is_not(None),
            models.TriggerDefinition.created_at >= _database_time(since),
        )
        .count()
    )


def list_owned_triggers(
    db: Session,
    owner_id: int,
    *,
    status: str | None = None,
    limit: int = 20,
) -> list[models.RuntimeTrigger]:
    """List one owner's durable triggers, newest first.

    The owner filter is applied here rather than by callers so that no caller
    can accidentally read another user's triggers.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    query = select(models.RuntimeTrigger).where(models.RuntimeTrigger.user_id == owner_id)
    if status is not None:
        query = query.where(models.RuntimeTrigger.status == status)
    return list(
        db.scalars(
            query.order_by(models.RuntimeTrigger.created_at.desc(), models.RuntimeTrigger.id.desc()).limit(limit)
        )
    )


def get_owned_trigger(db: Session, owner_id: int, trigger_id: str) -> models.RuntimeTrigger | None:
    """Return one durable trigger only when this owner owns it.

    A trigger owned by somebody else is reported as missing rather than as
    forbidden, so the lookup cannot be used to probe other users' trigger IDs.
    """
    return db.scalar(
        select(models.RuntimeTrigger).where(
            models.RuntimeTrigger.trigger_id == trigger_id,
            models.RuntimeTrigger.user_id == owner_id,
        )
    )


def cancel_owned_trigger(db: Session, owner_id: int, trigger_id: str) -> bool:
    """Atomically cancel one owner's queued or retryable trigger.

    Returns ``False`` when the trigger does not exist, belongs to another
    owner, or is no longer in a cancellable state (a live claim or a terminal
    outcome). ``CANCELLED`` is never claimable, so cancellation is terminal.
    """
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.trigger_id == trigger_id,
            models.RuntimeTrigger.user_id == owner_id,
            models.RuntimeTrigger.status.in_(CANCELLABLE_TRIGGER_STATUSES),
        )
        .values(
            status=models.RuntimeTriggerStatus.CANCELLED.value,
            claim_token=None,
            next_attempt_at=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def claim_trigger(db: Session, trigger_id: int, *, now: datetime | None = None, lease_seconds: int = 300) -> str | None:
    """Atomically claim a trigger and return its private ownership token."""
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")
    claimed_at = _database_time(now)
    expired_at = claimed_at - timedelta(seconds=lease_seconds)
    claim_token = token_urlsafe(24)
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            or_(
                and_(
                    models.RuntimeTrigger.status == models.RuntimeTriggerStatus.QUEUED.value,
                    or_(
                        models.RuntimeTrigger.kind != TriggerKind.SCHEDULED.value,
                        models.RuntimeTrigger.created_at <= claimed_at,
                    ),
                ),
                and_(
                    models.RuntimeTrigger.status == models.RuntimeTriggerStatus.FAILED.value,
                    or_(
                        models.RuntimeTrigger.next_attempt_at.is_(None),
                        models.RuntimeTrigger.next_attempt_at <= claimed_at,
                    ),
                ),
                and_(
                    models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
                    models.RuntimeTrigger.claimed_at < expired_at,
                ),
            ),
        )
        .values(
            status=models.RuntimeTriggerStatus.RUNNING.value,
            claim_token=claim_token,
            claimed_at=claimed_at,
            completed_at=None,
            last_error=None,
            attempt_count=models.RuntimeTrigger.attempt_count + 1,
            next_attempt_at=None,
        )
    )
    db.commit()
    return claim_token if result.rowcount == 1 else None


def _require_claim_token(claim_token: str) -> None:
    """Reject missing claim ownership before attempting a state transition."""
    if not claim_token.strip():
        raise ValueError("claim_token must not be blank")


def complete_trigger(db: Session, trigger_id: int, claim_token: str, *, completed_at: datetime | None = None) -> bool:
    """Mark a trigger completed when this worker owns its current claim."""
    _require_claim_token(claim_token)
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
            models.RuntimeTrigger.claim_token == claim_token,
        )
        .values(
            status=models.RuntimeTriggerStatus.COMPLETED.value,
            claim_token=None,
            completed_at=_database_time(completed_at),
            next_attempt_at=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def park_trigger(db: Session, trigger_id: int, claim_token: str) -> bool:
    """Move a running trigger this worker owns to ``waiting`` (JAN-207), clearing its claim token."""
    _require_claim_token(claim_token)
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
            models.RuntimeTrigger.claim_token == claim_token,
        )
        .values(
            status=models.RuntimeTriggerStatus.WAITING.value,
            claim_token=None,
            next_attempt_at=None,
        )
    )
    db.commit()
    return result.rowcount == 1


def release_waiting_trigger(db: Session, trigger_id: int) -> bool:
    """Move a still-``waiting`` trigger back to ``queued`` (a cancelled one stays cancelled)."""
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.WAITING.value,
        )
        .values(status=models.RuntimeTriggerStatus.QUEUED.value, next_attempt_at=None)
    )
    db.commit()
    return result.rowcount == 1


def _record_success(
    session_factory: SessionFactory, trigger: TriggerEnvelope, trigger_id: int, claim_token: str, value: object
) -> TriggerDispatchResult:
    """Record a handler that returned: park the run when it asked to wait (JAN-207), else complete it."""
    parked = handler_result_is_waiting(value)
    db = session_factory()
    try:
        recorded = (park_trigger if parked else complete_trigger)(db, trigger_id, claim_token)
    finally:
        db.close()
    if not recorded:
        return TriggerDispatchResult(
            trigger,
            TriggerDispatchStatus.FAILED,
            error="durable claim was lost before completion could be recorded",
        )
    status = TriggerDispatchStatus.WAITING if parked else TriggerDispatchStatus.COMPLETED
    return TriggerDispatchResult(trigger, status, value=value)


def fail_trigger(
    db: Session,
    trigger_id: int,
    claim_token: str,
    error: str,
    *,
    retry_after_seconds: float = 0,
    exhausted: bool = False,
    now: datetime | None = None,
) -> bool:
    """Mark an owned trigger failed and eligible for a later retry."""
    _require_claim_token(claim_token)
    if not error.strip():
        raise ValueError("trigger failure must not be blank")
    if retry_after_seconds < 0:
        raise ValueError("retry_after_seconds must not be negative")
    if exhausted and retry_after_seconds:
        raise ValueError("an exhausted trigger cannot have a retry delay")
    retry_at = (
        None if exhausted or retry_after_seconds == 0 else _database_time(now) + timedelta(seconds=retry_after_seconds)
    )
    result = db.execute(
        update(models.RuntimeTrigger)
        .where(
            models.RuntimeTrigger.id == trigger_id,
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
            models.RuntimeTrigger.claim_token == claim_token,
        )
        .values(
            status=(
                models.RuntimeTriggerStatus.EXHAUSTED.value if exhausted else models.RuntimeTriggerStatus.FAILED.value
            ),
            claim_token=None,
            last_error=error,
            next_attempt_at=retry_at,
        )
    )
    db.commit()
    return result.rowcount == 1


def _envelope_from_record(record: models.RuntimeTrigger) -> TriggerEnvelope:
    """Rehydrate a durable row into the transport-neutral handler input."""
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return TriggerEnvelope(
        trigger_id=record.trigger_id,
        idempotency_key=record.idempotency_key,
        task_id=record.task_id,
        kind=TriggerKind(record.kind),
        source=record.source,
        payload=record.payload or {},
        created_at=created_at,
        owner_id=record.user_id,
    )


class DurableTriggerWorker:
    """Poll and execute durable triggers with one short-lived DB session per phase."""

    def __init__(
        self,
        session_factory: SessionFactory,
        handler: TriggerHandler,
        *,
        lease_seconds: int = 300,
        batch_size: int = 10,
        poll_interval_seconds: float = 5.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0,
        scheduler: Callable[[datetime | None], object] | None = None,
    ) -> None:
        """Create an opt-in worker; startup wiring remains the caller's responsibility.

        ``scheduler`` is called at the start of every poll (with the poll's
        ``now``) to enqueue due recurring work -- in production,
        :func:`janus.trigger_definition_service.run_due_schedules`. A failing
        scheduler never stops the worker from executing already-queued runs.
        """
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must not be negative")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must not be negative")
        self._session_factory = session_factory
        self._handler = handler
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._poll_interval_seconds = poll_interval_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._scheduler = scheduler

    def _runnable_ids(self, *, now: datetime | None = None) -> list[int]:
        """Read a bounded candidate batch without holding a session during execution."""
        current = _database_time(now)
        expired_at = current - timedelta(seconds=self._lease_seconds)
        db = self._session_factory()
        try:
            return list(
                db.scalars(
                    select(models.RuntimeTrigger.id)
                    .where(
                        or_(
                            and_(
                                models.RuntimeTrigger.status == models.RuntimeTriggerStatus.QUEUED.value,
                                or_(
                                    models.RuntimeTrigger.kind != TriggerKind.SCHEDULED.value,
                                    models.RuntimeTrigger.created_at <= current,
                                ),
                            ),
                            and_(
                                models.RuntimeTrigger.status == models.RuntimeTriggerStatus.FAILED.value,
                                or_(
                                    models.RuntimeTrigger.next_attempt_at.is_(None),
                                    models.RuntimeTrigger.next_attempt_at <= current,
                                ),
                            ),
                            and_(
                                models.RuntimeTrigger.status == models.RuntimeTriggerStatus.RUNNING.value,
                                models.RuntimeTrigger.claimed_at < expired_at,
                            ),
                        )
                    )
                    .order_by(models.RuntimeTrigger.created_at, models.RuntimeTrigger.id)
                    .limit(self._batch_size)
                )
            )
        finally:
            db.close()

    def _process_one(self, trigger_id: int, *, now: datetime | None = None) -> TriggerDispatchResult | None:
        """Claim one row, execute outside the transaction, and record its outcome."""
        db = self._session_factory()
        try:
            claim_token = claim_trigger(db, trigger_id, now=now, lease_seconds=self._lease_seconds)
            record = db.get(models.RuntimeTrigger, trigger_id)
            if record is None:
                return None
            trigger = _envelope_from_record(record)
        finally:
            db.close()

        if claim_token is None:
            status = (
                TriggerDispatchStatus.DUPLICATE
                if record.status == models.RuntimeTriggerStatus.COMPLETED.value
                else TriggerDispatchStatus.IN_PROGRESS
            )
            if record.status == models.RuntimeTriggerStatus.EXHAUSTED.value:
                return TriggerDispatchResult(trigger, TriggerDispatchStatus.FAILED, error="trigger is exhausted")
            return TriggerDispatchResult(trigger, status)

        try:
            value = self._handler(trigger)
        except Exception as error:
            failure = str(error) or error.__class__.__name__
            attempt_count = record.attempt_count
            # A terminal failure (policy denial, malformed payload) would fail
            # identically on every retry, so it exhausts the row immediately
            # instead of burning the remaining attempt budget (JAN-194).
            exhausted = isinstance(error, TerminalTriggerError) or attempt_count >= self._max_attempts
            db = self._session_factory()
            try:
                recorded = fail_trigger(
                    db,
                    trigger_id,
                    claim_token,
                    failure,
                    retry_after_seconds=0 if exhausted else self._retry_backoff_seconds * (2 ** (attempt_count - 1)),
                    exhausted=exhausted,
                    now=now,
                )
            finally:
                db.close()
            if not recorded:
                failure = f"{failure}; durable claim was lost before failure could be recorded"
            return TriggerDispatchResult(trigger, TriggerDispatchStatus.FAILED, error=failure)

        return _record_success(self._session_factory, trigger, trigger_id, claim_token, value)

    def run_once(self, *, now: datetime | None = None) -> list[TriggerDispatchResult]:
        """Enqueue due scheduled work, then process one bounded batch and return one result per observed row."""
        if self._scheduler is not None:
            try:
                self._scheduler(now)
            except Exception:  # noqa: BLE001 - scheduling must not block queued runs
                logger.exception("Trigger scheduler tick failed")
        results = []
        for trigger_id in self._runnable_ids(now=now):
            result = self._process_one(trigger_id, now=now)
            if result is not None:
                results.append(result)
        return results

    def run_forever(self, stop_event: Event) -> None:
        """Poll until stopped; the event makes shutdown responsive between batches."""
        while not stop_event.is_set():
            self.run_once()
            stop_event.wait(self._poll_interval_seconds)


class DurableTriggerDispatcher:
    """Dispatch triggers through durable, ownership-bound database claims."""

    def __init__(self, session_factory: SessionFactory, handler: TriggerHandler, *, lease_seconds: int = 300) -> None:
        """Create an opt-in dispatcher around a task-runtime handoff callback."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be at least 1")
        self._session_factory = session_factory
        self._handler = handler
        self._lease_seconds = lease_seconds

    def dispatch(self, trigger: TriggerEnvelope, *, definition_id: int | None = None) -> TriggerDispatchResult:
        """Persist, claim, execute, and durably record one trigger attempt."""
        db = self._session_factory()
        try:
            record, _ = enqueue_trigger(db, trigger, definition_id=definition_id)
            if record.status == models.RuntimeTriggerStatus.COMPLETED.value:
                return TriggerDispatchResult(trigger, TriggerDispatchStatus.DUPLICATE)
            claim_token = claim_trigger(db, record.id, lease_seconds=self._lease_seconds)
            if claim_token is None:
                if record.status == models.RuntimeTriggerStatus.EXHAUSTED.value:
                    return TriggerDispatchResult(trigger, TriggerDispatchStatus.FAILED, error="trigger is exhausted")
                return TriggerDispatchResult(trigger, TriggerDispatchStatus.IN_PROGRESS)
            trigger_id = record.id
        finally:
            db.close()

        try:
            value = self._handler(trigger)
        except Exception as error:
            db = self._session_factory()
            try:
                failure = str(error) or error.__class__.__name__
                recorded = fail_trigger(
                    db, trigger_id, claim_token, failure, exhausted=isinstance(error, TerminalTriggerError)
                )
            finally:
                db.close()
            if not recorded:
                failure = f"{failure}; durable claim was lost before failure could be recorded"
            return TriggerDispatchResult(trigger, TriggerDispatchStatus.FAILED, error=failure)

        return _record_success(self._session_factory, trigger, trigger_id, claim_token, value)
