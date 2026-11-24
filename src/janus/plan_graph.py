"""Stable plan-driven graph contracts for the Janus runtime.

This module intentionally contains no LLM, database, or transport integration.
It defines the state transitions that a later durable runtime can persist and
resume while keeping graph topology independent from model-generated plans.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TypedDict, Unpack


class PlanGraphError(ValueError):
    """Raised when a plan graph transition is invalid."""


class PlanStatus(str, Enum):
    """Lifecycle state for a generated plan."""

    DRAFT = "draft"
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanStepStatus(str, Enum):
    """Lifecycle state for one plan step."""

    PENDING = "pending"
    RUNNING = "running"
    #: Work happening outside this plan (a delegated child task, JAN-207).
    #: Never selected for execution; resolved by :meth:`PlanGraph.resolve_external`.
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


#: Infixes of the step IDs :meth:`PlanGraph.await_external` creates.
AWAIT_STEP_INFIX = "::await::"
CONTINUATION_STEP_SUFFIX = "::continue"


class PlanGraphNode(str, Enum):
    """Stable nodes in the plan execution graph."""

    RETRIEVE_PLAN = "retrieve_plan"
    GENERATE_PLAN = "generate_plan"
    SELECT_STEP = "select_step"
    EXECUTE_STEP = "execute_step"
    WAIT = "wait"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskContext:
    """Minimal task identity and constraints passed through the graph."""

    task_id: str
    objective: str
    task_mode: str = "default"
    max_steps: int = 100

    def __post_init__(self) -> None:
        """Validate task identifiers and execution limits."""
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if not self.objective.strip():
            raise ValueError("objective must not be blank")
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1")


@dataclass(frozen=True)
class PlanStep:
    """One ordered, dependency-aware unit of planned work."""

    step_id: str
    objective: str
    depends_on: tuple[str, ...] = ()
    required_tool_ids: tuple[str, ...] = ()
    status: PlanStepStatus = PlanStepStatus.PENDING
    result: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate step identity and dependency references."""
        if not self.step_id.strip():
            raise ValueError("step_id must not be blank")
        if not self.objective.strip():
            raise ValueError("step objective must not be blank")
        if self.step_id in self.depends_on:
            raise ValueError(f"step cannot depend on itself: {self.step_id}")


class _StepChanges(TypedDict, total=False):
    """The fields a plan-graph transition may change on one step."""

    status: PlanStepStatus
    result: str | None
    error: str | None


@dataclass(frozen=True)
class Plan:
    """Immutable snapshot of a task plan."""

    plan_id: str
    task_id: str
    objective: str
    version: int = 1
    status: PlanStatus = PlanStatus.DRAFT
    steps: tuple[PlanStep, ...] = ()

    def __post_init__(self) -> None:
        """Validate plan identity, version, and step references."""
        if not self.plan_id.strip():
            raise ValueError("plan_id must not be blank")
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if not self.objective.strip():
            raise ValueError("plan objective must not be blank")
        if self.version < 1:
            raise ValueError("plan version must be at least 1")

        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step IDs must be unique")
        known_step_ids = set(step_ids)
        for step in self.steps:
            unknown_dependencies = set(step.depends_on) - known_step_ids
            if unknown_dependencies:
                names = ", ".join(sorted(unknown_dependencies))
                raise ValueError(f"step {step.step_id} depends on unknown step(s): {names}")

        dependencies = {step.step_id: set(step.depends_on) for step in self.steps}
        resolved: set[str] = set()
        while dependencies:
            ready = {step_id for step_id, required in dependencies.items() if required <= resolved}
            if not ready:
                raise ValueError("plan step dependencies must not contain cycles")
            resolved.update(ready)
            for step_id in ready:
                dependencies.pop(step_id)


@dataclass(frozen=True)
class PlanGraphState:
    """Serializable graph cursor and current plan snapshot."""

    task: TaskContext
    node: PlanGraphNode
    plan: Plan | None = None
    current_step_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PlanGraph:
    """Transition-only interpreter for the stable Janus plan graph."""

    _step_limit: int = field(default=100, repr=False)

    def begin(self, task: TaskContext, plan: Plan | None = None) -> PlanGraphState:
        """Start at plan retrieval or continue from a supplied plan snapshot."""
        if plan is not None and plan.task_id != task.task_id:
            raise PlanGraphError("plan task_id does not match task context")
        if plan is not None and len(plan.steps) > min(self._step_limit, task.max_steps):
            raise PlanGraphError("plan exceeds the graph step limit")
        return PlanGraphState(
            task=task,
            node=PlanGraphNode.RETRIEVE_PLAN if plan is None else PlanGraphNode.SELECT_STEP,
            plan=plan,
        )

    def plan_retrieved(self, state: PlanGraphState, plan: Plan | None) -> PlanGraphState:
        """Route a retrieved plan to execution or self-planning."""
        self._require_node(state, PlanGraphNode.RETRIEVE_PLAN)
        if plan is None:
            return replace(state, node=PlanGraphNode.GENERATE_PLAN, reason="no active plan found")
        self._validate_plan_for_task(state.task, plan)
        if plan.status == PlanStatus.COMPLETED:
            return replace(state, plan=plan, node=PlanGraphNode.COMPLETE)
        if plan.status == PlanStatus.FAILED:
            return replace(state, plan=plan, node=PlanGraphNode.FAILED, reason="retrieved plan has failed")
        return replace(state, plan=plan, node=PlanGraphNode.SELECT_STEP, reason=None)

    def plan_generated(self, state: PlanGraphState, plan: Plan) -> PlanGraphState:
        """Accept a model-generated plan and route it to step selection."""
        self._require_node(state, PlanGraphNode.GENERATE_PLAN)
        self._validate_plan_for_task(state.task, plan)
        if not plan.steps:
            raise PlanGraphError("generated plan must contain at least one step")
        if len(plan.steps) > min(self._step_limit, state.task.max_steps):
            raise PlanGraphError("generated plan exceeds the graph step limit")
        active_plan = replace(plan, status=PlanStatus.ACTIVE)
        return replace(state, plan=active_plan, node=PlanGraphNode.SELECT_STEP, reason=None)

    def select_step(self, state: PlanGraphState) -> PlanGraphState:
        """Select the first pending step whose dependencies are complete."""
        self._require_node(state, PlanGraphNode.SELECT_STEP)
        plan = self._require_plan(state)
        if plan.status == PlanStatus.COMPLETED:
            return replace(state, node=PlanGraphNode.COMPLETE, current_step_id=None)
        if plan.status == PlanStatus.FAILED:
            return replace(state, node=PlanGraphNode.FAILED, current_step_id=None)

        completed = {step.step_id for step in plan.steps if step.status == PlanStepStatus.COMPLETED}
        pending = {step.step_id for step in plan.steps if step.status == PlanStepStatus.PENDING}
        if not pending:
            completed_plan = replace(plan, status=PlanStatus.COMPLETED)
            return replace(state, plan=completed_plan, node=PlanGraphNode.COMPLETE, current_step_id=None)

        for step in plan.steps:
            if step.status == PlanStepStatus.PENDING and set(step.depends_on) <= completed:
                return replace(state, node=PlanGraphNode.EXECUTE_STEP, current_step_id=step.step_id, reason=None)

        waiting_plan = replace(plan, status=PlanStatus.WAITING)
        return replace(
            state,
            plan=waiting_plan,
            node=PlanGraphNode.WAIT,
            current_step_id=None,
            reason="waiting for plan dependencies",
        )

    def start_step(self, state: PlanGraphState) -> PlanGraphState:
        """Mark the selected step as running before tool execution."""
        self._require_node(state, PlanGraphNode.EXECUTE_STEP)
        plan = self._require_plan(state)
        step = self._require_current_step(plan, state.current_step_id)
        if step.status != PlanStepStatus.PENDING:
            raise PlanGraphError(f"step is not pending: {step.step_id}")
        running_plan = self._replace_step(plan, step.step_id, status=PlanStepStatus.RUNNING)
        return replace(state, plan=running_plan, reason=None)

    def complete_step(self, state: PlanGraphState, result: str | None = None) -> PlanGraphState:
        """Mark the running step complete and return to selection."""
        self._require_node(state, PlanGraphNode.EXECUTE_STEP)
        plan = self._require_plan(state)
        step = self._require_current_step(plan, state.current_step_id)
        if step.status != PlanStepStatus.RUNNING:
            raise PlanGraphError(f"step is not running: {step.step_id}")
        completed_plan = self._replace_step(
            plan,
            step.step_id,
            status=PlanStepStatus.COMPLETED,
            result=result,
            error=None,
        )
        return replace(
            state,
            plan=replace(completed_plan, status=PlanStatus.ACTIVE),
            node=PlanGraphNode.SELECT_STEP,
            current_step_id=None,
            reason=None,
        )

    def fail_step(self, state: PlanGraphState, error: str) -> PlanGraphState:
        """Record a terminal step failure."""
        self._require_node(state, PlanGraphNode.EXECUTE_STEP)
        if not error.strip():
            raise PlanGraphError("step error must not be blank")
        plan = self._require_plan(state)
        step = self._require_current_step(plan, state.current_step_id)
        if step.status != PlanStepStatus.RUNNING:
            raise PlanGraphError(f"step is not running: {step.step_id}")
        failed_plan = self._replace_step(plan, step.step_id, status=PlanStepStatus.FAILED, error=error)
        return replace(
            state,
            plan=replace(failed_plan, status=PlanStatus.FAILED),
            node=PlanGraphNode.FAILED,
            current_step_id=None,
            reason=error,
        )

    def await_external(
        self,
        state: PlanGraphState,
        result: str | None,
        external_ids: tuple[str, ...],
        continuation_objective: str,
    ) -> PlanGraphState:
        """Complete the running step, then make the plan wait for external work (JAN-207).

        Appends one ``WAITING`` step per external ID and one pending
        continuation step that depends on the completed step and every await
        step, then returns to selection, which parks the plan in ``WAIT``
        until :meth:`resolve_external` has resolved every await step.
        """
        self._require_node(state, PlanGraphNode.EXECUTE_STEP)
        plan = self._require_plan(state)
        step = self._require_current_step(plan, state.current_step_id)
        if step.status != PlanStepStatus.RUNNING:
            raise PlanGraphError(f"step is not running: {step.step_id}")
        if not external_ids:
            raise PlanGraphError("await_external needs at least one external ID")
        if any(not isinstance(item, str) or not item.strip() for item in external_ids):
            raise PlanGraphError("external IDs must be non-blank strings")
        if len(set(external_ids)) != len(external_ids):
            raise PlanGraphError("external IDs must be unique")
        if not continuation_objective.strip():
            raise PlanGraphError("continuation objective must not be blank")
        if len(plan.steps) + len(external_ids) + 1 > min(self._step_limit, state.task.max_steps):
            raise PlanGraphError("waiting would exceed the plan step limit")

        await_steps = tuple(
            PlanStep(
                step_id=f"{step.step_id}{AWAIT_STEP_INFIX}{external_id}",
                objective=f"Wait for {external_id}",
                status=PlanStepStatus.WAITING,
            )
            for external_id in external_ids
        )
        continuation = PlanStep(
            step_id=f"{step.step_id}{CONTINUATION_STEP_SUFFIX}",
            objective=continuation_objective,
            depends_on=(step.step_id, *(waiting.step_id for waiting in await_steps)),
            required_tool_ids=step.required_tool_ids,
        )
        completed_plan = self._replace_step(
            plan, step.step_id, status=PlanStepStatus.COMPLETED, result=result, error=None
        )
        # Plan's own constructor re-validates ID uniqueness and dependencies.
        extended = Plan(
            plan_id=completed_plan.plan_id,
            task_id=completed_plan.task_id,
            objective=completed_plan.objective,
            version=completed_plan.version,
            status=PlanStatus.ACTIVE,
            steps=(*completed_plan.steps, *await_steps, continuation),
        )
        return replace(state, plan=extended, node=PlanGraphNode.SELECT_STEP, current_step_id=None, reason=None)

    def resolve_external(self, state: PlanGraphState, step_id: str, result: str) -> PlanGraphState:
        """Record that the external work behind one ``WAITING`` step has finished."""
        self._require_node(state, PlanGraphNode.WAIT)
        if not result.strip():
            raise PlanGraphError("external result must not be blank")
        plan = self._require_plan(state)
        step = self._require_current_step(plan, step_id)
        if step.status != PlanStepStatus.WAITING:
            raise PlanGraphError(f"step is not waiting: {step.step_id}")
        resolved = self._replace_step(plan, step.step_id, status=PlanStepStatus.COMPLETED, result=result, error=None)
        return replace(state, plan=resolved)

    def revise_pending_step(self, state: PlanGraphState, step_id: str, objective: str) -> PlanGraphState:
        """Replace a pending step's objective while the plan waits (e.g. with external results)."""
        self._require_node(state, PlanGraphNode.WAIT)
        if not objective.strip():
            raise PlanGraphError("step objective must not be blank")
        plan = self._require_plan(state)
        step = self._require_current_step(plan, step_id)
        if step.status != PlanStepStatus.PENDING:
            raise PlanGraphError(f"step is not pending: {step.step_id}")
        steps = tuple(replace(item, objective=objective) if item.step_id == step_id else item for item in plan.steps)
        return replace(state, plan=replace(plan, steps=steps))

    def resume(self, state: PlanGraphState) -> PlanGraphState:
        """Resume a waiting plan after an external event or schedule."""
        self._require_node(state, PlanGraphNode.WAIT)
        plan = self._require_plan(state)
        if plan.status != PlanStatus.WAITING:
            raise PlanGraphError("only waiting plans can be resumed")
        return replace(state, plan=replace(plan, status=PlanStatus.ACTIVE), node=PlanGraphNode.SELECT_STEP, reason=None)

    @staticmethod
    def _require_node(state: PlanGraphState, expected: PlanGraphNode) -> None:
        if state.node != expected:
            raise PlanGraphError(f"expected graph node {expected.value}, got {state.node.value}")

    @staticmethod
    def _require_plan(state: PlanGraphState) -> Plan:
        if state.plan is None:
            raise PlanGraphError("graph state has no plan")
        return state.plan

    @staticmethod
    def _require_current_step(plan: Plan, step_id: str | None) -> PlanStep:
        if step_id is None:
            raise PlanGraphError("graph state has no current step")
        for step in plan.steps:
            if step.step_id == step_id:
                return step
        raise PlanGraphError(f"unknown current step: {step_id}")

    @staticmethod
    def _replace_step(plan: Plan, step_id: str, **changes: Unpack[_StepChanges]) -> Plan:
        steps = tuple(replace(step, **changes) if step.step_id == step_id else step for step in plan.steps)
        return replace(plan, steps=steps)

    @staticmethod
    def _validate_plan_for_task(task: TaskContext, plan: Plan) -> None:
        if plan.task_id != task.task_id:
            raise PlanGraphError("plan task_id does not match task context")
        if plan.objective.strip() != task.objective.strip():
            raise PlanGraphError("plan objective does not match task objective")


__all__ = [
    "AWAIT_STEP_INFIX",
    "CONTINUATION_STEP_SUFFIX",
    "Plan",
    "PlanGraph",
    "PlanGraphError",
    "PlanGraphNode",
    "PlanGraphState",
    "PlanStatus",
    "PlanStep",
    "PlanStepStatus",
    "TaskContext",
]
