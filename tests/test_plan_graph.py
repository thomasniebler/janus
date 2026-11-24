"""Tests for the dependency-free Janus plan graph contract."""

import pytest

from janus.plan_graph import (
    Plan,
    PlanGraph,
    PlanGraphError,
    PlanGraphNode,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    TaskContext,
)


def _task() -> TaskContext:
    """Build a valid task context for graph tests."""
    return TaskContext(task_id="task-1", objective="Prepare the launch plan")


def _plan() -> Plan:
    """Build a two-step dependency chain."""
    return Plan(
        plan_id="plan-1",
        task_id="task-1",
        objective="Prepare the launch plan",
        steps=(
            PlanStep(step_id="research", objective="Research the launch constraints"),
            PlanStep(step_id="draft", objective="Draft the launch plan", depends_on=("research",)),
        ),
    )


def test_begin_and_retrieve_route_to_generation_or_selection():
    """The graph explicitly distinguishes plan retrieval from plan generation."""
    graph = PlanGraph()

    missing = graph.plan_retrieved(graph.begin(_task()), None)
    assert missing.node == PlanGraphNode.GENERATE_PLAN

    retrieved = graph.plan_retrieved(graph.begin(_task()), _plan())
    assert retrieved.node == PlanGraphNode.SELECT_STEP
    assert retrieved.plan == _plan()


def test_generated_plan_is_activated_and_dependencies_are_enforced():
    """Generated plans become active and only ready steps can run."""
    graph = PlanGraph()
    state = graph.plan_generated(graph.plan_retrieved(graph.begin(_task()), None), _plan())

    selected = graph.select_step(state)
    assert selected.node == PlanGraphNode.EXECUTE_STEP
    assert selected.current_step_id == "research"

    running = graph.start_step(selected)
    assert running.plan.steps[0].status == PlanStepStatus.RUNNING
    completed = graph.complete_step(running, "Constraints collected")
    assert completed.node == PlanGraphNode.SELECT_STEP

    next_step = graph.select_step(completed)
    assert next_step.current_step_id == "draft"


def test_completion_and_failure_are_terminal():
    """The graph records terminal completion and failure states."""
    graph = PlanGraph()
    state = graph.plan_generated(graph.plan_retrieved(graph.begin(_task()), None), _plan())
    first = graph.start_step(graph.select_step(state))
    second = graph.complete_step(first)
    second = graph.start_step(graph.select_step(second))
    completed = graph.complete_step(second)
    terminal = graph.select_step(completed)
    assert terminal.node == PlanGraphNode.COMPLETE
    assert terminal.plan.status == PlanStatus.COMPLETED

    failing = graph.start_step(graph.select_step(state))
    failed = graph.fail_step(failing, "research service unavailable")
    assert failed.node == PlanGraphNode.FAILED
    assert failed.plan.status == PlanStatus.FAILED


def test_unready_dependencies_wait_and_can_resume():
    """A plan with no ready step waits for an event before retrying selection."""
    graph = PlanGraph()
    blocked_plan = Plan(
        plan_id="plan-2",
        task_id="task-1",
        objective="Prepare the launch plan",
        steps=(
            PlanStep(step_id="research", objective="Research", status=PlanStepStatus.RUNNING),
            PlanStep(step_id="draft", objective="Draft", depends_on=("research",)),
        ),
    )
    state = graph.plan_retrieved(graph.begin(_task()), blocked_plan)

    waiting = graph.select_step(state)
    assert waiting.node == PlanGraphNode.WAIT
    resumed = graph.resume(waiting)
    assert resumed.node == PlanGraphNode.SELECT_STEP


def test_graph_rejects_wrong_task_and_invalid_transitions():
    """Plans cannot cross task boundaries and nodes cannot be skipped."""
    graph = PlanGraph()
    with pytest.raises(PlanGraphError, match="does not match task"):
        graph.begin(_task(), Plan(plan_id="other", task_id="task-2", objective=_task().objective))

    with pytest.raises(PlanGraphError, match="expected graph node"):
        graph.select_step(graph.begin(_task()))


def test_plan_rejects_cycles_and_graph_respects_task_step_limit():
    """Invalid dependency cycles and oversized generated plans fail closed."""
    with pytest.raises(ValueError, match="must not contain cycles"):
        Plan(
            plan_id="cyclic",
            task_id="task-1",
            objective="Prepare the launch plan",
            steps=(
                PlanStep(step_id="a", objective="A", depends_on=("b",)),
                PlanStep(step_id="b", objective="B", depends_on=("a",)),
            ),
        )

    constrained_task = TaskContext(task_id="task-1", objective="Prepare the launch plan", max_steps=1)
    graph = PlanGraph()
    state = graph.plan_retrieved(graph.begin(constrained_task), None)
    with pytest.raises(PlanGraphError, match="exceeds the graph step limit"):
        graph.plan_generated(state, _plan())
