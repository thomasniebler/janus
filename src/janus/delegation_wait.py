"""A trigger run waiting on the children it delegated (JAN-207).

The flow, end to end:

1. The trigger handler opens a per-step wait sink with :func:`accepting_waits`
   around a run's first step (never for a built-in run, never for the
   continuation step). The ``wait_for_delegated_tasks`` tool adds the IDs of
   delegations that same step created to it through :func:`request_wait`.
2. If the sink is not empty when the step finishes, the handler returns
   :class:`~janus.agent_runtime.StepAwait`; ``PlanGraph.await_external``
   appends one ``waiting`` step per delegation plus a continuation step, the
   graph enters ``WAIT``, and the durable boundary parks the trigger row
   ``waiting``.
3. :func:`release_waiting_parents` moves a ``waiting`` row back to ``queued``
   once every delegation it waits for is terminal. It runs after a child
   finishes (for that parent) and on every trigger-worker poll (for all).
4. On the resumed claim, :func:`resolve_awaited_delegations` (the durable
   handler's ``before_resume`` hook) records each child's outcome on its
   await step and rebuilds the continuation step's objective, and
   ``PlanDrivenRuntime.resume()`` runs the continuation.

Everything here is owner-scoped: a run can only wait on delegations its own
owner created from this very task and step.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models
from janus.delegation_service import (
    MAX_ACTIVE_DELEGATIONS_PER_OWNER,
    TERMINAL_DELEGATION_STATUSES,
    expire_stale_delegations,
)
from janus.plan_graph import (
    AWAIT_STEP_INFIX,
    PlanGraph,
    PlanGraphNode,
    PlanGraphState,
    PlanStep,
    PlanStepStatus,
)
from janus.plan_state import PlanStateError, PlanStateStore
from janus.trigger_service import release_waiting_trigger

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

#: How many delegations one run may wait for. Matches the active fan-out cap:
#: a step cannot have more children in flight than that anyway.
MAX_AWAITED_DELEGATIONS = MAX_ACTIVE_DELEGATIONS_PER_OWNER

#: How much of the parked step's own answer is repeated to the continuation.
_MAX_EARLIER_ANSWER_CHARS = 2_000

#: How many waiting runs one release sweep looks at.
_RELEASE_BATCH = 100

WAIT_UNAVAILABLE = (
    "waiting for delegated tasks is only available in the first step of a trigger run "
    "(not in chat, built-in runs, or a run that already waited once); "
    "check on them with get_delegated_task instead"
)


@dataclass
class WaitSink:
    """The delegations one plan step asked to wait for."""

    task_id: str
    step_id: str
    delegation_ids: list[str] = field(default_factory=list)


_CURRENT_SINK: ContextVar[WaitSink | None] = ContextVar("delegation_wait_sink", default=None)


@contextmanager
def accepting_waits(task_id: str, step_id: str) -> Iterator[WaitSink]:
    """Let the step ``task_id``/``step_id`` executing in this context ask to wait."""
    sink = WaitSink(task_id=task_id, step_id=step_id)
    token = _CURRENT_SINK.set(sink)
    try:
        yield sink
    finally:
        _CURRENT_SINK.reset(token)


def _sink_for(task_id: str, step_id: str) -> WaitSink:
    sink = _CURRENT_SINK.get()
    if sink is None or sink.task_id != task_id or sink.step_id != step_id:
        raise ValueError(WAIT_UNAVAILABLE)
    return sink


def ensure_waiting_allowed(task_id: str, step_id: str) -> None:
    """Raise :class:`ValueError` unless the step executing in this context may wait."""
    _sink_for(task_id, step_id)


def request_wait(task_id: str, step_id: str, delegation_ids: list[str]) -> list[str]:
    """Record that this step waits for ``delegation_ids``; return everything it now waits for.

    The caller has already checked that the IDs are this owner's delegations
    created by this step. Raises :class:`ValueError` when waiting is not
    available here or the wait would exceed :data:`MAX_AWAITED_DELEGATIONS`.
    """
    sink = _sink_for(task_id, step_id)
    combined = list(dict.fromkeys([*sink.delegation_ids, *delegation_ids]))
    if len(combined) > MAX_AWAITED_DELEGATIONS:
        raise ValueError(f"a run can wait for at most {MAX_AWAITED_DELEGATIONS} delegated tasks")
    sink.delegation_ids[:] = combined
    return list(combined)


def _awaited(state: PlanGraphState) -> list[tuple[PlanStep, str]]:
    """Return each still-waiting await step with the delegation ID it waits for."""
    if state.plan is None:
        return []
    return [
        (step, step.step_id.rsplit(AWAIT_STEP_INFIX, 1)[1])
        for step in state.plan.steps
        if step.status == PlanStepStatus.WAITING and AWAIT_STEP_INFIX in step.step_id
    ]


def _describe(delegation_id: str, record: models.RuntimeDelegation | None) -> str:
    """Render one child's outcome as the await step's result."""
    if record is None:
        return f"Delegated task {delegation_id} is no longer available; it produced no result."
    if record.status == models.RuntimeDelegationStatus.COMPLETED.value:
        return f"Delegated task {delegation_id} ({record.objective}) completed:\n{record.result or '(no answer)'}"
    return f"Delegated task {delegation_id} ({record.objective}) failed: {record.error or 'no reason recorded'}"


def _continuation_objective(state: PlanGraphState, continuation: PlanStep) -> str | None:
    """Rebuild a continuation's objective from the step it continues and the resolved results."""
    by_id = {step.step_id: step for step in state.plan.steps} if state.plan else {}
    dependencies = [by_id[step_id] for step_id in continuation.depends_on if step_id in by_id]
    if not dependencies or any(step.status != PlanStepStatus.COMPLETED for step in dependencies):
        return None
    origin = next((step for step in dependencies if AWAIT_STEP_INFIX not in step.step_id), None)
    results = [step.result for step in dependencies if AWAIT_STEP_INFIX in step.step_id and step.result]
    if origin is None or not results:
        return None
    earlier = (origin.result or "").strip()
    if len(earlier) > _MAX_EARLIER_ANSWER_CHARS:
        earlier = earlier[:_MAX_EARLIER_ANSWER_CHARS] + "\n[truncated]"
    parts = [
        origin.objective,
        "--- Resuming after delegated tasks ---\n"
        "Earlier in this run you delegated tasks and chose to wait for them. They have now finished. "
        "Continue the objective above using their results. A delegated task that failed did not "
        "produce the information it was asked for: say so rather than guessing.",
    ]
    if earlier:
        parts.append(f"Your answer before waiting:\n{earlier}")
    parts.append("Results of the delegated tasks:\n\n" + "\n\n".join(results))
    return "\n\n".join(parts)


def resolve_awaited_delegations(
    session_factory: SessionFactory, owner_id: int, state: PlanGraphState
) -> PlanGraphState:
    """Resolve every await step whose delegation is terminal, then rebuild ready continuations.

    Used as ``DurablePlanRuntimeTriggerHandler``'s ``before_resume`` hook, so
    the resumed continuation step sees exactly the outcomes persisted with
    the plan. A delegation that is still active leaves its step waiting, and
    the plan simply parks again. Only this owner's delegations created by this
    task count; anything else reads as "no longer available".
    """
    if state.node != PlanGraphNode.WAIT:
        return state
    awaited = _awaited(state)
    if not awaited:
        return state
    db = session_factory()
    try:
        expire_stale_delegations(db, owner_id)
        records = {
            record.delegation_id: record
            for record in db.scalars(
                select(models.RuntimeDelegation).where(
                    models.RuntimeDelegation.user_id == owner_id,
                    models.RuntimeDelegation.parent_task_id == state.task.task_id,
                    models.RuntimeDelegation.delegation_id.in_([delegation_id for _, delegation_id in awaited]),
                )
            )
        }
        graph = PlanGraph()
        for step, delegation_id in awaited:
            record = records.get(delegation_id)
            if record is not None and record.status not in TERMINAL_DELEGATION_STATUSES:
                continue
            state = graph.resolve_external(state, step.step_id, _describe(delegation_id, record))
    finally:
        db.close()

    graph = PlanGraph()
    for step in state.plan.steps if state.plan else ():
        if step.status != PlanStepStatus.PENDING or not any(AWAIT_STEP_INFIX in dep for dep in step.depends_on):
            continue
        objective = _continuation_objective(state, step)
        if objective is not None:
            state = graph.revise_pending_step(state, step.step_id, objective)
    return state


def _ready_to_resume(db: Session, record: models.RuntimeTrigger) -> bool:
    """Whether a waiting run has nothing left to wait for."""
    try:
        stored = PlanStateStore(lambda: db).load(db, record.user_id, record.task_id)
    except PlanStateError:
        logger.warning("Waiting trigger %s has an unreadable plan state; releasing it", record.trigger_id)
        return True
    if stored is None or stored.state.node != PlanGraphNode.WAIT:
        # Nothing parked to wait on; the handler decides what a resume does.
        return True
    awaited_ids = [delegation_id for _, delegation_id in _awaited(stored.state)]
    if not awaited_ids:
        return True
    active = db.scalar(
        select(models.RuntimeDelegation.id)
        .where(
            models.RuntimeDelegation.user_id == record.user_id,
            models.RuntimeDelegation.parent_task_id == record.task_id,
            models.RuntimeDelegation.delegation_id.in_(awaited_ids),
            models.RuntimeDelegation.status.not_in(TERMINAL_DELEGATION_STATUSES),
        )
        .limit(1)
    )
    return active is None


def release_waiting_parents(
    session_factory: SessionFactory,
    *,
    owner_id: int | None = None,
    task_id: str | None = None,
) -> int:
    """Move every ``waiting`` trigger run whose delegations have all finished back to ``queued``.

    With ``owner_id``/``task_id`` only that parent is checked (after one of its
    children finished); without, every waiting run is (the worker sweep).
    Returns how many runs were released.
    """
    db = session_factory()
    try:
        query = select(models.RuntimeTrigger).where(
            models.RuntimeTrigger.status == models.RuntimeTriggerStatus.WAITING.value,
            models.RuntimeTrigger.user_id.is_not(None),
        )
        if owner_id is not None:
            query = query.where(models.RuntimeTrigger.user_id == owner_id)
        if task_id is not None:
            query = query.where(models.RuntimeTrigger.task_id == task_id)
        waiting = list(db.scalars(query.order_by(models.RuntimeTrigger.id).limit(_RELEASE_BATCH)))
        for owner in {record.user_id for record in waiting}:
            # An orphaned child must be requeued or failed before it is judged.
            expire_stale_delegations(db, owner)
        released = 0
        for record in waiting:
            if _ready_to_resume(db, record) and release_waiting_trigger(db, record.id):
                released += 1
        return released
    finally:
        db.close()


__all__ = [
    "MAX_AWAITED_DELEGATIONS",
    "WAIT_UNAVAILABLE",
    "WaitSink",
    "accepting_waits",
    "ensure_waiting_allowed",
    "release_waiting_parents",
    "request_wait",
    "resolve_awaited_delegations",
]
