"""Durable parent-task -> child-task delegation records (JAN-200).

This is the persistence and policy half of plan-driven delegation.
There is one Janus brain, so a task does not hand work to another *agent*; it
spawns a bounded *child task* whose identity (``child_task_id``) is also the
``task_id`` of its persisted plan state. Execution lives in
:mod:`janus.delegation_runtime`; the agent-facing tools in
:mod:`janus.agent_toolboxes.delegation`.

The lifecycle mirrors ``runtime_triggers`` (``trigger_service.py``): an atomic
claim moves ``queued`` -> ``running`` and returns a private token, and only the
holder of that token can record ``completed`` or ``failed``. Everything is
owner-scoped here rather than by callers, so no caller can read or finalize
another user's delegation.

Interrupted rows are re-run exactly once (:func:`expire_stale_delegations`,
JAN-207). Two caps bound what a model can create (see "Delegation" in
``docs/docs/tech-spec/background-workers-runtime.md``):

* :data:`MAX_DELEGATION_DEPTH` -- a child task cannot spawn a further child.
  A child only ever gets read-only tools, and the ``delegation`` toolbox is
  not read-only, so this is defense in depth behind that rule.
* :data:`MAX_ACTIVE_DELEGATIONS_PER_OWNER` -- the fan-out cap, following the
  ``MAX_ACTIVE_TRIGGERS_PER_OWNER`` backlog-cap pattern.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from sqlalchemy import String, and_, func, insert, literal, or_, select, update
from sqlalchemy.orm import Session

from janus import models
from janus.agent_task_runner import AGENT_TASK_MAX_SECONDS

#: Task mode every delegated child task runs in. Its tools come from
#: ``tool_policy.unattended_tool_sets`` (the parent's read-only grant).
DELEGATED_TASK_MODE = "delegated"

#: A child is depth 1; a child of a child would be depth 2 and is refused.
MAX_DELEGATION_DEPTH = 1

#: Fan-out cap: at most this many ``queued``/``running`` delegations per owner.
#: Deliberately much smaller than ``MAX_ACTIVE_TRIGGERS_PER_OWNER`` (25): an
#: active trigger is mostly a future schedule entry, whereas every active
#: delegation is an LLM run happening *now*. It matches the executor's worker
#: count, so on a single-user install (the common deployment) an accepted
#: delegation never waits behind a full pool; on a multi-user install queued
#: work waits its turn and the lease below still bounds how long it may wait.
MAX_ACTIVE_DELEGATIONS_PER_OWNER = 4

#: How long a delegation may stay ``queued``/``running`` before it is treated as
#: interrupted (the process stopped before it finished). A live run is bounded
#: by ``AGENT_TASK_MAX_SECONDS``; the margin covers pool queueing and the plan
#: state writes around the run.
DELEGATION_LEASE_SECONDS = int(AGENT_TASK_MAX_SECONDS) + 120

#: Upper bound on a delegated objective. It becomes the child's prompt and is
#: persisted and echoed back to the model, so it must not be unbounded.
MAX_DELEGATION_OBJECTIVE_CHARS = 4_000

#: Upper bound on how much of the child's result is stored on the row.
MAX_DELEGATION_RESULT_CHARS = 4_000

#: How often an interrupted delegation is put back to ``queued`` (JAN-207):
#: exactly one automatic re-run, then an interruption is reported as a failure.
MAX_DELEGATION_REQUEUES = 1

ACTIVE_DELEGATION_STATUSES: tuple[str, ...] = (
    models.RuntimeDelegationStatus.QUEUED.value,
    models.RuntimeDelegationStatus.RUNNING.value,
)

TERMINAL_DELEGATION_STATUSES: tuple[str, ...] = (
    models.RuntimeDelegationStatus.COMPLETED.value,
    models.RuntimeDelegationStatus.FAILED.value,
)

_INTERRUPTED_ERROR = (
    "delegation was interrupted again after its one automatic re-run "
    "(Janus stopped or the run outlived its lease); it is not retried again"
)
_TRUNCATION_MARKER = "\n[truncated]"


class DelegationError(ValueError):
    """Raised when a delegation request violates delegation policy."""


def _database_time(value: datetime | None = None) -> datetime:
    """Normalize UTC timestamps for SQLAlchemy's timezone-naive DateTime columns."""
    return (value or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)


def _bounded(text: str, limit: int) -> str:
    """Truncate text visibly rather than silently."""
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _lease_start():
    """When a row's current lease began: its claim when running, else when it was (re)queued."""
    return func.coalesce(models.RuntimeDelegation.requeued_at, models.RuntimeDelegation.created_at)


def _stale_condition(cutoff: datetime):
    """Rows that are still active but whose lease began before ``cutoff``."""
    return or_(
        and_(
            models.RuntimeDelegation.status == models.RuntimeDelegationStatus.RUNNING.value,
            # A running row is judged by its claim time (never NULL for a claim this
            # service made; a NULL is treated as stale rather than running forever).
            or_(models.RuntimeDelegation.claimed_at.is_(None), models.RuntimeDelegation.claimed_at < cutoff),
        ),
        and_(
            models.RuntimeDelegation.status == models.RuntimeDelegationStatus.QUEUED.value,
            _lease_start() < cutoff,
        ),
    )


def expire_stale_delegations(db: Session, owner_id: int | None, *, now: datetime | None = None) -> int:
    """Requeue or fail delegations that outlived their lease (JAN-207).

    Delegations run on a process-local pool, so a restart orphans whatever was
    queued or running. Without this, an orphan would read as "running" forever
    and permanently consume the owner's fan-out budget. An interrupted row is
    put back to ``queued`` exactly once (:data:`MAX_DELEGATION_REQUEUES`) for
    the trigger worker's poll to re-dispatch; clearing its claim token is the
    hand-over, so the executor that lost it can no longer finalize it. A
    second interruption fails the row with a reason that says so.

    ``owner_id=None`` applies the rule to every owner (the worker sweep);
    reads pass their owner. Returns how many rows were requeued or failed.
    """
    db_time = _database_time(now)
    cutoff = db_time - timedelta(seconds=DELEGATION_LEASE_SECONDS)
    scope = [_stale_condition(cutoff)]
    if owner_id is not None:
        scope.append(models.RuntimeDelegation.user_id == owner_id)
    failed = db.execute(
        update(models.RuntimeDelegation)
        .where(*scope, models.RuntimeDelegation.requeue_count >= MAX_DELEGATION_REQUEUES)
        .values(
            status=models.RuntimeDelegationStatus.FAILED.value,
            claim_token=None,
            error=_INTERRUPTED_ERROR,
            completed_at=db_time,
        )
    )
    requeued = db.execute(
        update(models.RuntimeDelegation)
        .where(*scope, models.RuntimeDelegation.requeue_count < MAX_DELEGATION_REQUEUES)
        .values(
            status=models.RuntimeDelegationStatus.QUEUED.value,
            claim_token=None,
            claimed_at=None,
            requeued_at=db_time,
            requeue_count=models.RuntimeDelegation.requeue_count + 1,
        )
    )
    db.commit()
    return failed.rowcount + requeued.rowcount


def list_claimable_delegation_ids(db: Session, *, limit: int = 50, now: datetime | None = None) -> list[int]:
    """Return queued delegations (every owner) whose lease is still open, oldest first.

    The trigger worker's sweep submits these: after a restart nothing else
    would, and a row that is already pending in this process's runner is
    deduplicated there (and by the atomic claim).
    """
    cutoff = _database_time(now) - timedelta(seconds=DELEGATION_LEASE_SECONDS)
    return list(
        db.scalars(
            select(models.RuntimeDelegation.id)
            .where(
                models.RuntimeDelegation.status == models.RuntimeDelegationStatus.QUEUED.value,
                _lease_start() > cutoff,
            )
            .order_by(models.RuntimeDelegation.created_at, models.RuntimeDelegation.id)
            .limit(limit)
        )
    )


def count_active_delegations(db: Session, owner_id: int) -> int:
    """Count one owner's delegations that are still queued or running."""
    return (
        db.query(models.RuntimeDelegation)
        .filter(
            models.RuntimeDelegation.user_id == owner_id,
            models.RuntimeDelegation.status.in_(ACTIVE_DELEGATION_STATUSES),
        )
        .count()
    )


def create_delegation(
    db: Session,
    *,
    owner_id: int,
    parent_task_id: str,
    objective: str,
    requested_tool_ids: Sequence[str],
    allowed_tool_ids: Iterable[str],
    parent_step_id: str | None = None,
    conversation_id: int | None = None,
    depth: int = 1,
    now: datetime | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> models.RuntimeDelegation:
    """Validate policy and persist one queued delegation.

    ``allowed_tool_ids`` is the caller's already-clamped policy set (parent's
    authorized tools, read-only ones only); this
    function never widens it. Requested tools outside it are rejected here
    rather than accepted and failed later, so the model learns immediately
    what it may not delegate.
    """
    cleaned_objective = " ".join(objective.split()) if isinstance(objective, str) else ""
    if not cleaned_objective:
        raise DelegationError("objective must not be blank")
    if len(cleaned_objective) > MAX_DELEGATION_OBJECTIVE_CHARS:
        raise DelegationError(f"objective must be at most {MAX_DELEGATION_OBJECTIVE_CHARS} characters")
    if not isinstance(parent_task_id, str) or not parent_task_id.strip():
        raise DelegationError("a delegation needs a parent task")
    if depth < 1 or depth > MAX_DELEGATION_DEPTH:
        raise DelegationError(
            f"delegation depth {depth} exceeds the limit of {MAX_DELEGATION_DEPTH}; "
            "a delegated task cannot delegate further"
        )

    allowed = sorted(set(allowed_tool_ids))
    requested = list(dict.fromkeys(tool_id.strip() for tool_id in requested_tool_ids if tool_id.strip()))
    outside = [tool_id for tool_id in requested if tool_id not in allowed]
    if outside:
        raise DelegationError(
            f"cannot delegate tools outside the delegated-task allowlist: {', '.join(outside)}; "
            f"allowed: {', '.join(allowed) or 'none'}"
        )

    expire_stale_delegations(db, owner_id, now=now)

    # Generate the ID before the atomic insert so we can reference it afterward.
    delegation_id = str(uuid.uuid4())
    db_time = _database_time(now)

    # Atomic insert-select: only insert if the fan-out cap check passes, racing
    # against concurrent create_delegation calls for the same owner.
    # If the subquery in WHERE counts >= MAX_ACTIVE_DELEGATIONS_PER_OWNER,
    # SQLite skips the insert and rowcount is 0.
    insert_stmt = insert(models.RuntimeDelegation).from_select(
        [
            models.RuntimeDelegation.delegation_id,
            models.RuntimeDelegation.user_id,
            models.RuntimeDelegation.parent_task_id,
            models.RuntimeDelegation.parent_step_id,
            models.RuntimeDelegation.child_task_id,
            models.RuntimeDelegation.conversation_id,
            models.RuntimeDelegation.depth,
            models.RuntimeDelegation.objective,
            models.RuntimeDelegation.requested_tool_ids,
            models.RuntimeDelegation.allowed_tool_ids,
            models.RuntimeDelegation.llm_provider,
            models.RuntimeDelegation.llm_model,
            models.RuntimeDelegation.status,
            models.RuntimeDelegation.created_at,
            models.RuntimeDelegation.updated_at,
        ],
        select(
            literal(delegation_id),
            literal(owner_id),
            literal(parent_task_id),
            literal(parent_step_id),
            literal(f"delegation-{delegation_id}"),
            literal(conversation_id),
            literal(depth),
            literal(cleaned_objective),
            literal(json.dumps(requested)),
            literal(json.dumps(allowed)),
            literal(llm_provider, type_=String),
            literal(llm_model, type_=String),
            literal(models.RuntimeDelegationStatus.QUEUED.value),
            literal(db_time),
            literal(db_time),
        ).where(
            # Cap check as a WHERE clause: SELECT returns one row only if
            # (active_count < cap). If the subquery returns 0 rows, the
            # INSERT succeeds with 0 rows (atomic gate keeps the cap safe).
            select(func.count())
            .select_from(models.RuntimeDelegation)
            .where(
                models.RuntimeDelegation.user_id == owner_id,
                models.RuntimeDelegation.status.in_(ACTIVE_DELEGATION_STATUSES),
            )
            .scalar_subquery()
            < MAX_ACTIVE_DELEGATIONS_PER_OWNER
        ),
    )
    result = db.execute(insert_stmt)
    db.commit()

    if result.rowcount != 1:
        # Cap check failed; fetch the current count for the error message
        # (this read doesn't need to be race-free since it's just advisory text).
        active = count_active_delegations(db, owner_id)
        raise DelegationError(
            f"you already have {active} delegated tasks in progress "
            f"(limit {MAX_ACTIVE_DELEGATIONS_PER_OWNER}); wait for one to finish before delegating more"
        )

    # Fetch the inserted row by its unique delegation_id.
    record = db.scalar(
        select(models.RuntimeDelegation).where(
            models.RuntimeDelegation.delegation_id == delegation_id,
            models.RuntimeDelegation.user_id == owner_id,
        )
    )
    if record is None:
        # Should not happen; the row was just inserted.
        raise DelegationError("Failed to retrieve the inserted delegation record")
    return record


def get_owned_delegation(db: Session, owner_id: int, delegation_id: str) -> models.RuntimeDelegation | None:
    """Return one delegation only when this owner owns it (missing, not forbidden, otherwise)."""
    expire_stale_delegations(db, owner_id)
    return db.scalar(
        select(models.RuntimeDelegation).where(
            models.RuntimeDelegation.delegation_id == delegation_id,
            models.RuntimeDelegation.user_id == owner_id,
        )
    )


def list_owned_delegations(
    db: Session,
    owner_id: int,
    *,
    parent_task_id: str | None = None,
    limit: int = 20,
) -> list[models.RuntimeDelegation]:
    """List one owner's delegations, newest first, optionally for one parent task."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    expire_stale_delegations(db, owner_id)
    query = select(models.RuntimeDelegation).where(models.RuntimeDelegation.user_id == owner_id)
    if parent_task_id is not None:
        query = query.where(models.RuntimeDelegation.parent_task_id == parent_task_id)
    return list(
        db.scalars(
            query.order_by(models.RuntimeDelegation.created_at.desc(), models.RuntimeDelegation.id.desc()).limit(limit)
        )
    )


def claim_delegation(db: Session, record_id: int, *, now: datetime | None = None) -> str | None:
    """Atomically move a queued delegation to running and return its private token."""
    db_time = _database_time(now)
    claim_token = token_urlsafe(24)
    # Reject rows whose queued lease has passed (from when they were queued or
    # requeued); expiry decides whether such a row is requeued or failed.
    lease_cutoff = db_time - timedelta(seconds=DELEGATION_LEASE_SECONDS)
    result = db.execute(
        update(models.RuntimeDelegation)
        .where(
            models.RuntimeDelegation.id == record_id,
            models.RuntimeDelegation.status == models.RuntimeDelegationStatus.QUEUED.value,
            _lease_start() > lease_cutoff,
        )
        .values(
            status=models.RuntimeDelegationStatus.RUNNING.value,
            claim_token=claim_token,
            claimed_at=db_time,
        )
    )
    db.commit()
    return claim_token if result.rowcount == 1 else None


def _finish(
    db: Session,
    record_id: int,
    claim_token: str,
    *,
    status: models.RuntimeDelegationStatus,
    result: str | None = None,
    error: str | None = None,
) -> bool:
    if not claim_token.strip():
        raise ValueError("claim_token must not be blank")
    outcome = db.execute(
        update(models.RuntimeDelegation)
        .where(
            models.RuntimeDelegation.id == record_id,
            models.RuntimeDelegation.status == models.RuntimeDelegationStatus.RUNNING.value,
            models.RuntimeDelegation.claim_token == claim_token,
        )
        .values(
            status=status.value,
            claim_token=None,
            completed_at=_database_time(),
            result=None if result is None else _bounded(result, MAX_DELEGATION_RESULT_CHARS),
            error=None if error is None else _bounded(error, MAX_DELEGATION_RESULT_CHARS),
        )
    )
    db.commit()
    return outcome.rowcount == 1


def complete_delegation(db: Session, record_id: int, claim_token: str, result: str | None) -> bool:
    """Record a completed child task when this executor still owns the claim."""
    return _finish(db, record_id, claim_token, status=models.RuntimeDelegationStatus.COMPLETED, result=result)


def fail_delegation(db: Session, record_id: int, claim_token: str, error: str) -> bool:
    """Record a failed child task when this executor still owns the claim."""
    cleaned = error.strip() or "delegated task failed"
    return _finish(db, record_id, claim_token, status=models.RuntimeDelegationStatus.FAILED, error=cleaned)


__all__ = [
    "ACTIVE_DELEGATION_STATUSES",
    "DELEGATED_TASK_MODE",
    "DELEGATION_LEASE_SECONDS",
    "DelegationError",
    "MAX_ACTIVE_DELEGATIONS_PER_OWNER",
    "MAX_DELEGATION_DEPTH",
    "MAX_DELEGATION_OBJECTIVE_CHARS",
    "MAX_DELEGATION_REQUEUES",
    "MAX_DELEGATION_RESULT_CHARS",
    "TERMINAL_DELEGATION_STATUSES",
    "claim_delegation",
    "complete_delegation",
    "count_active_delegations",
    "create_delegation",
    "expire_stale_delegations",
    "fail_delegation",
    "get_owned_delegation",
    "list_claimable_delegation_ids",
    "list_owned_delegations",
]
