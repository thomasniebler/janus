"""Tests for durable plan graph state snapshots and resumption."""

from unittest.mock import MagicMock

import pytest

from janus.agent_runtime import PlanRunResult
from janus.plan_graph import (
    Plan,
    PlanGraph,
    PlanGraphNode,
    PlanGraphState,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    TaskContext,
)
from janus.plan_state import (
    PlanStateConflictError,
    PlanStateStore,
    StoredPlanState,
    deserialize_plan_state,
    serialize_plan_state,
)
from janus.runtime_trigger_handler import DurablePlanRuntimeTriggerHandler
from janus.triggers import TriggerEnvelope, TriggerKind


def _state() -> PlanGraphState:
    """Build a valid state at the execute-step graph boundary."""
    task = TaskContext(task_id="task-1", objective="Review the inbox", max_steps=4)
    plan = Plan(plan_id="plan-1", task_id=task.task_id, objective=task.objective, steps=(PlanStep("step-1", "Review"),))
    return PlanGraph().select_step(PlanGraph().begin(task, plan))


def _waiting_state() -> PlanGraphState:
    """Build a state that can be handed to PlanDrivenRuntime.resume()."""
    task = TaskContext(task_id="task-1", objective="Review the inbox")
    plan = Plan(
        plan_id="plan-1",
        task_id=task.task_id,
        objective=task.objective,
        status=PlanStatus.ACTIVE,
        steps=(
            PlanStep("running", "Already running", status=PlanStepStatus.RUNNING),
            PlanStep("waiting", "Wait for running", depends_on=("running",)),
        ),
    )
    return PlanGraph().select_step(PlanGraph().begin(task, plan))


def test_plan_state_codec_round_trips_graph_state():
    """A serialized state must restore the same immutable graph cursor."""
    state = _state()

    restored = deserialize_plan_state(serialize_plan_state(state))

    assert restored == state
    assert restored.node is PlanGraphNode.EXECUTE_STEP


def test_plan_state_store_is_owner_scoped(db, testing_session_local):
    """A task state is visible only to the owner that stored it."""
    store = PlanStateStore(testing_session_local)
    state = _state()

    saved = store.save(db, 1, state)

    assert saved.revision == 1
    assert store.load(db, 1, "task-1") == saved
    assert store.load(db, 2, "task-1") is None


def test_plan_state_store_rejects_stale_revision(db, testing_session_local):
    """A stale worker must not overwrite a newer task snapshot."""
    store = PlanStateStore(testing_session_local)
    state = _state()
    store.save(db, 1, state)

    store.save(db, 1, state, expected_revision=1)
    with pytest.raises(PlanStateConflictError, match="revision changed"):
        store.save(db, 1, state, expected_revision=1)


def test_durable_handler_resumes_waiting_state_and_persists_result():
    """The durable adapter resumes waiting state and writes the new revision."""
    runtime = MagicMock()
    store = MagicMock()
    waiting = _waiting_state()
    result = PlanRunResult(state=waiting)
    store.load_for_owner.return_value = StoredPlanState(waiting, revision=3)
    runtime.resume.return_value = result
    handler = DurablePlanRuntimeTriggerHandler(runtime, store)
    trigger = TriggerEnvelope(
        trigger_id="trigger-1",
        idempotency_key="key-1",
        task_id="task-1",
        kind=TriggerKind.MANUAL,
        source="test",
        owner_id=1,
        payload={"objective": "Review the inbox"},
    )

    assert handler(trigger) == result
    runtime.resume.assert_called_once_with(waiting)
    store.save_for_owner.assert_called_once_with(1, waiting, expected_revision=3)


def test_durable_handler_requires_owner():
    """The durable adapter refuses legacy unowned trigger envelopes."""
    handler = DurablePlanRuntimeTriggerHandler(MagicMock(), MagicMock())
    trigger = TriggerEnvelope(
        trigger_id="trigger-1",
        idempotency_key="key-1",
        task_id="task-1",
        kind=TriggerKind.MANUAL,
        source="test",
        payload={"objective": "Review the inbox"},
    )

    with pytest.raises(ValueError, match="owned trigger"):
        handler(trigger)


def test_durable_handler_does_not_mutate_terminal_state():
    """A later trigger for a terminal task must be a read-only no-op."""
    runtime = MagicMock()
    store = MagicMock()
    completed = PlanGraphState(
        task=TaskContext(task_id="task-1", objective="Review the inbox"),
        node=PlanGraphNode.COMPLETE,
    )
    store.load_for_owner.return_value = StoredPlanState(completed, revision=2)
    handler = DurablePlanRuntimeTriggerHandler(runtime, store)
    trigger = TriggerEnvelope(
        trigger_id="trigger-2",
        idempotency_key="key-2",
        task_id="task-1",
        kind=TriggerKind.EVENT,
        source="test",
        owner_id=1,
        payload={"objective": "Review the inbox"},
    )

    assert handler(trigger).state == completed
    runtime.run.assert_not_called()
    runtime.resume.assert_not_called()
    store.save_for_owner.assert_not_called()


def test_durable_handler_reruns_a_failed_task_from_a_fresh_plan():
    """A retried trigger must re-run a FAILED task, not replay the stored failure (JAN-194).

    The trigger worker only invokes the handler again when it has decided a
    retry is warranted; short-circuiting a stored FAILED state would make every
    retry a no-op that just burns attempts. ``run`` (fresh plan), not
    ``resume``, is used, and the result overwrites the stored revision.
    """
    runtime = MagicMock()
    store = MagicMock()
    task = TaskContext(task_id="task-1", objective="Review the inbox")
    failed = PlanGraphState(task=task, node=PlanGraphNode.FAILED, reason="model provider unreachable")
    rerun = PlanRunResult(state=PlanGraphState(task=task, node=PlanGraphNode.COMPLETE))
    store.load_for_owner.return_value = StoredPlanState(failed, revision=4)
    runtime.run.return_value = rerun
    handler = DurablePlanRuntimeTriggerHandler(runtime, store)
    trigger = TriggerEnvelope(
        trigger_id="trigger-1",
        idempotency_key="key-1",
        task_id="task-1",
        kind=TriggerKind.MANUAL,
        source="test",
        owner_id=1,
        payload={"objective": "Review the inbox"},
    )

    assert handler(trigger) == rerun
    runtime.run.assert_called_once()
    runtime.resume.assert_not_called()
    store.save_for_owner.assert_called_once_with(1, rerun.state, expected_revision=4)
