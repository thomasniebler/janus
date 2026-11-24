"""Execute delegated child tasks through the plan-driven runtime (JAN-200).

A delegation (see :mod:`janus.delegation_service`) is run as its own task: a
``TaskContext`` in the ``delegated`` task mode whose single-step ``Plan`` is
advanced by :class:`~janus.agent_runtime.PlanDrivenRuntime` and persisted with
:class:`~janus.plan_state.PlanStateStore` under the delegation's
``child_task_id`` -- the same graph, store, and unattended execution path
(:func:`janus.unattended_execution.execute_unattended_step`) that
trigger-driven work uses.

Runs happen on a small process-local pool (a
:class:`~janus.background_tasks.ThreadPoolTaskRunner`) so a chat turn that
delegates is not blocked for up to ``AGENT_TASK_MAX_SECONDS``. A chat parent
polls ``get_delegated_task``; a trigger parent may wait for its children
(:mod:`janus.delegation_wait`, JAN-207), and every finished child releases a
parent that waited for it. The pool is not durable: after a restart,
:func:`run_delegation_maintenance` (run on every trigger-worker poll)
requeues or fails orphaned rows and re-submits the claimable ones.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session

from janus import delegation_wait, event_logger, models
from janus.agent_runtime import PlanDrivenRuntime
from janus.background_tasks import BackgroundTaskRunner, ThreadPoolTaskRunner
from janus.delegation_service import (
    DELEGATED_TASK_MODE,
    MAX_ACTIVE_DELEGATIONS_PER_OWNER,
    claim_delegation,
    complete_delegation,
    expire_stale_delegations,
    fail_delegation,
    list_claimable_delegation_ids,
)
from janus.llm_service import ModelChoice
from janus.plan_graph import Plan, PlanGraphNode, PlanStep, TaskContext
from janus.plan_state import PlanStateStore
from janus.unattended_execution import UnattendedTaskExecutor, execute_unattended_step, run_unattended_task

_DELEGATION_SOURCE = "RuntimeDelegation"

#: Guidance appended to the system prompt of a delegated child task. Like a
#: trigger run, no human is present; unlike one, the reader of the answer is the
#: parent task, so the answer must be self-contained enough to act on.
_DELEGATION_GUIDANCE = (
    "You are completing a task delegated to you by another Janus task. No human is present: you "
    "cannot ask a follow-up question, and no one will approve an action mid-run. Complete the "
    "objective using only the read-only tools you have been given, then reply with a concise, "
    "self-contained result the delegating task can use directly. If the objective cannot be "
    "completed with the available tools, say exactly what was missing instead of guessing or "
    "claiming work you did not do."
)

SessionFactory = Callable[[], Session]

logger = logging.getLogger(__name__)

#: Seams for tests: where a queued delegation runs, and which synchronous
#: agent loop executes it. The runner is bounded to the per-owner fan-out cap,
#: so on a single-user install an accepted delegation never waits for a slot.
DELEGATION_RUNNER: BackgroundTaskRunner = ThreadPoolTaskRunner(
    MAX_ACTIVE_DELEGATIONS_PER_OWNER, thread_name_prefix="janus-delegation"
)
DELEGATION_TASK_EXECUTOR: UnattendedTaskExecutor = run_unattended_task


def _single_step_plan(task: TaskContext, requested_tool_ids: tuple[str, ...]) -> Plan:
    """Build the one-step plan a delegated child executes."""
    return Plan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        objective=task.objective,
        steps=(
            PlanStep(
                step_id=f"{task.task_id}::step-1",
                objective=task.objective,
                required_tool_ids=requested_tool_ids,
            ),
        ),
    )


def _execute_child_step(
    session_factory: SessionFactory,
    owner_id: int,
    allowed_tool_ids: frozenset[str],
    task: TaskContext,
    step: PlanStep,
    task_executor: UnattendedTaskExecutor,
    model_choice: ModelChoice | None = None,
) -> str:
    db = session_factory()
    try:
        user = db.get(models.User, owner_id)
        if user is None:
            raise ValueError(f"unknown delegation owner: {owner_id}")
        return execute_unattended_step(
            db,
            user,
            task,
            step,
            guidance=_DELEGATION_GUIDANCE,
            task_executor=task_executor,
            # Clamp to what was stored at delegation time (the parent's
            # read-only authorized tools). The shared executor checks it
            # against the owner's *current* unattended switch again, so
            # turning a tool off applies to queued work.
            allowed_tool_ids=allowed_tool_ids,
            model_choice=model_choice,
        )
    finally:
        db.close()


def run_delegation(
    session_factory: SessionFactory,
    record_id: int,
    *,
    task_executor: UnattendedTaskExecutor | None = None,
) -> str | None:
    """Claim, execute, and durably record one delegated child task.

    Returns the delegation's final status, or ``None`` when it could not be
    claimed (already running, finished, or expired). Never raises: this runs
    on a pool thread where an exception would vanish into an unobserved
    future, so every failure is recorded on the delegation row instead.
    """
    executor = task_executor if task_executor is not None else DELEGATION_TASK_EXECUTOR
    db = session_factory()
    try:
        claim_token = claim_delegation(db, record_id)
        if claim_token is None:
            return None
        record = db.get(models.RuntimeDelegation, record_id)
        if record is None:  # pragma: no cover - the claim just matched this row
            return None
        owner_id = record.user_id
        delegation_id = record.delegation_id
        parent_task_id = record.parent_task_id
        task = TaskContext(
            task_id=record.child_task_id,
            objective=record.objective,
            task_mode=DELEGATED_TASK_MODE,
            max_steps=1,
        )
        requested_tool_ids = tuple(record.requested_tool_ids or ())
        allowed_tool_ids = frozenset(record.allowed_tool_ids or ())
        model_choice = ModelChoice(provider=record.llm_provider, model=record.llm_model)
    finally:
        db.close()

    try:
        runtime = PlanDrivenRuntime(
            retrieve_plan=lambda _task: None,
            generate_plan=lambda child: _single_step_plan(child, requested_tool_ids),
            execute_step=lambda child, step: _execute_child_step(
                session_factory, owner_id, allowed_tool_ids, child, step, executor, model_choice
            ),
        )
        # The child's events belong to its owner (JAN-272).
        with event_logger.owned_by(owner_id):
            outcome = runtime.run(task)
        PlanStateStore(session_factory).save_for_owner(owner_id, outcome.state)
    except Exception as error:  # noqa: BLE001 - recorded on the row, see docstring
        outcome = None
        failure = str(error).strip() or error.__class__.__name__
    else:
        failure = None
        if outcome.state.node != PlanGraphNode.COMPLETE:
            failure = outcome.state.reason or "delegated task did not complete"

    db = session_factory()
    try:
        if failure is None:
            steps = outcome.state.plan.steps if outcome.state.plan is not None else ()
            result = next((step.result for step in reversed(steps) if step.result), None)
            recorded = complete_delegation(db, record_id, claim_token, result)
            status = models.RuntimeDelegationStatus.COMPLETED.value
        else:
            recorded = fail_delegation(db, record_id, claim_token, failure)
            status = models.RuntimeDelegationStatus.FAILED.value
    finally:
        db.close()

    if not recorded:
        # The row's lease passed while this run was still going: it was
        # requeued for a re-run or failed as interrupted, and the claim token
        # this run held is gone, so this run must not overwrite that.
        event_logger.log(
            models.EventType.ERROR,
            _DELEGATION_SOURCE,
            "Delegated task finished after its claim was lost",
            {"delegation_id": delegation_id, "user_id": owner_id},
            user_id=owner_id,
        )
        return models.RuntimeDelegationStatus.FAILED.value

    _release_parent(session_factory, owner_id, parent_task_id)
    event_logger.log(
        models.EventType.SUCCESS if failure is None else models.EventType.ERROR,
        _DELEGATION_SOURCE,
        "Delegated task completed" if failure is None else "Delegated task failed",
        {"delegation_id": delegation_id, "user_id": owner_id, **({"error": failure} if failure else {})},
        user_id=owner_id,
    )
    return status


def _release_parent(session_factory: SessionFactory, owner_id: int, parent_task_id: str) -> None:
    """Resume a trigger run waiting on this child, if it has nothing else to wait for (JAN-207)."""
    try:
        delegation_wait.release_waiting_parents(session_factory, owner_id=owner_id, task_id=parent_task_id)
    except Exception:  # noqa: BLE001 - the worker sweep releases it on its next poll
        logger.exception("Releasing the parent of a finished delegation failed")


def dispatch_delegation(session_factory: SessionFactory, record_id: int) -> None:
    """Schedule a persisted, queued delegation for execution (once per row while pending)."""
    DELEGATION_RUNNER.submit(
        "delegation",
        lambda: run_delegation(session_factory, record_id),
        key=f"delegation:{record_id}",
    )


def run_delegation_maintenance(session_factory: SessionFactory, *, now: datetime | None = None) -> int:
    """Durable re-pickup and parent release; called on every trigger-worker poll (JAN-207).

    Requeues (once) or fails every owner's delegations that outlived their
    lease, submits every claimable queued row (a restart left nothing in the
    pool), and releases waiting trigger runs whose children have all
    finished. Returns how many delegations were submitted.
    """
    db = session_factory()
    try:
        expire_stale_delegations(db, None, now=now)
        claimable = list_claimable_delegation_ids(db, now=now)
    finally:
        db.close()
    for record_id in claimable:
        dispatch_delegation(session_factory, record_id)
    delegation_wait.release_waiting_parents(session_factory)
    return len(claimable)


__all__ = [
    "DELEGATION_RUNNER",
    "DELEGATION_TASK_EXECUTOR",
    "dispatch_delegation",
    "run_delegation",
    "run_delegation_maintenance",
]
