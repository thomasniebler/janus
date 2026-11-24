"""Guarded adapter from durable triggers into the plan-driven runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from janus.agent_runtime import PlanDrivenRuntime, PlanRunResult
from janus.plan_graph import PlanGraphNode, PlanGraphState, TaskContext
from janus.plan_state import PlanStateStore
from janus.triggers import InvalidTriggerPayloadError, TriggerEnvelope

TaskContextBuilder = Callable[[TriggerEnvelope], TaskContext]
#: Prepares a persisted ``WAIT`` state for :meth:`PlanDrivenRuntime.resume`,
#: e.g. by resolving the external work it waited for (JAN-207).
ResumePreparer = Callable[[int, PlanGraphState], PlanGraphState]


def task_context_from_trigger(trigger: TriggerEnvelope) -> TaskContext:
    """Build task context from the small, validated trigger payload contract."""
    objective = trigger.payload.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise InvalidTriggerPayloadError("trigger payload objective must be a non-blank string")

    task_mode = trigger.payload.get("task_mode", "default")
    if not isinstance(task_mode, str) or not task_mode.strip():
        raise InvalidTriggerPayloadError("trigger payload task_mode must be a non-blank string")

    max_steps: Any = trigger.payload.get("max_steps", 100)
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise InvalidTriggerPayloadError("trigger payload max_steps must be an integer")

    return TaskContext(
        task_id=trigger.task_id,
        objective=objective.strip(),
        task_mode=task_mode.strip(),
        max_steps=max_steps,
    )


class PlanRuntimeTriggerHandler:
    """Invoke one plan-runtime advancement from a durable trigger envelope."""

    def __init__(
        self, runtime: PlanDrivenRuntime, context_builder: TaskContextBuilder = task_context_from_trigger
    ) -> None:
        """Create a handler whose runtime and context policy are explicit dependencies."""
        self._runtime = runtime
        self._context_builder = context_builder

    def __call__(self, trigger: TriggerEnvelope) -> PlanRunResult:
        """Build constrained task context and let the runtime own graph execution."""
        return self._runtime.run(self._context_builder(trigger))


class DurablePlanRuntimeTriggerHandler:
    """Resume or advance a plan runtime and persist its resulting graph state."""

    def __init__(
        self,
        runtime: PlanDrivenRuntime,
        state_store: PlanStateStore,
        context_builder: TaskContextBuilder = task_context_from_trigger,
        before_resume: ResumePreparer | None = None,
    ) -> None:
        """Create an owner-bound handler around the injected runtime and store.

        ``before_resume`` receives the owner ID and the stored ``WAIT`` state
        and returns the state to resume; without it the state is resumed as
        stored.
        """
        self._runtime = runtime
        self._state_store = state_store
        self._context_builder = context_builder
        self._before_resume = before_resume

    def __call__(self, trigger: TriggerEnvelope) -> PlanRunResult:
        """Advance one owner task, failing closed when ownership is absent.

        A ``COMPLETE`` task is a read-only no-op, so a redelivered trigger never
        repeats finished work. A ``FAILED`` task is *re-run from a fresh plan*
        (JAN-194): the trigger boundary only invokes a handler again when it has
        decided a retry is warranted, and returning the stored failure would
        turn every retry into a no-op that merely burns attempts. The fresh run
        overwrites the stored failure under the usual revision check.
        """
        if trigger.owner_id is None:
            raise ValueError("durable plan runtime requires an owned trigger")
        task = self._context_builder(trigger)
        stored = self._state_store.load_for_owner(trigger.owner_id, task.task_id)
        if stored is None or stored.state.node == PlanGraphNode.FAILED:
            result = self._runtime.run(task)
        elif stored.state.node == PlanGraphNode.WAIT:
            waiting = stored.state
            if self._before_resume is not None:
                waiting = self._before_resume(trigger.owner_id, waiting)
            result = self._runtime.resume(waiting)
        elif stored.state.node == PlanGraphNode.COMPLETE:
            return PlanRunResult(state=stored.state)
        else:
            raise RuntimeError(f"unsupported persisted plan graph node: {stored.state.node.value}")
        self._state_store.save_for_owner(
            trigger.owner_id,
            result.state,
            expected_revision=stored.revision if stored else None,
        )
        return result


__all__ = [
    "DurablePlanRuntimeTriggerHandler",
    "PlanRuntimeTriggerHandler",
    "ResumePreparer",
    "TaskContextBuilder",
    "task_context_from_trigger",
]
