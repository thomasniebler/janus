"""Tests for Janus's injected plan-driven runtime boundary."""

from dataclasses import replace

import pytest

from janus import models
from janus.agent_runtime import PlanDrivenRuntime
from janus.character import CharacterConstitution, CharacterRuntime, CharacterTrait
from janus.plan_graph import Plan, PlanGraphNode, PlanStatus, PlanStep, PlanStepStatus, TaskContext


def _task() -> TaskContext:
    """Build a valid runtime task."""
    return TaskContext(task_id="task-1", objective="Prepare the launch plan")


def _plan() -> Plan:
    """Build a two-step plan with one dependency."""
    return Plan(
        plan_id="plan-1",
        task_id="task-1",
        objective="Prepare the launch plan",
        steps=(
            PlanStep(step_id="research", objective="Research constraints"),
            PlanStep(step_id="draft", objective="Draft the plan", depends_on=("research",)),
        ),
    )


def test_runtime_retrieves_or_generates_and_executes_plan() -> None:
    """The runtime drives retrieval, generation, selection, and execution."""
    executed: list[str] = []
    runtime = PlanDrivenRuntime(
        retrieve_plan=lambda _task: None,
        generate_plan=lambda _task: _plan(),
        execute_step=lambda _task, step: executed.append(step.step_id),
    )

    result = runtime.run(_task())

    assert result.state.node == PlanGraphNode.COMPLETE
    assert result.state.plan is not None
    assert result.state.plan.status == PlanStatus.COMPLETED
    assert result.executed_step_ids == ("research", "draft")
    assert executed == ["research", "draft"]


def test_runtime_can_start_from_a_retrieved_plan() -> None:
    """A persisted plan can be supplied without invoking the generator."""

    def should_not_generate(_task: TaskContext) -> Plan:
        """Fail loudly if retrieval incorrectly falls through to generation."""
        pytest.fail("generator must not be called")

    runtime = PlanDrivenRuntime(
        retrieve_plan=lambda _task: _plan(),
        generate_plan=should_not_generate,
        execute_step=lambda _task, _step: None,
    )

    result = runtime.run(_task())

    assert result.state.node == PlanGraphNode.COMPLETE


def test_runtime_records_executor_failures_as_terminal_graph_state() -> None:
    """Executor errors become graph failures instead of escaping the loop."""
    runtime = PlanDrivenRuntime(
        retrieve_plan=lambda _task: _plan(),
        generate_plan=lambda _task: _plan(),
        execute_step=lambda _task, _step: (_ for _ in ()).throw(RuntimeError("tool unavailable")),
    )

    result = runtime.run(_task())

    assert result.state.node == PlanGraphNode.FAILED
    assert result.state.plan is not None
    assert result.state.plan.status == PlanStatus.FAILED
    assert result.state.plan.steps[0].status == PlanStepStatus.FAILED
    assert result.state.reason == "tool unavailable"
    assert result.executed_step_ids == ("research",)


def test_runtime_resumes_after_external_state_updates_a_waiting_plan() -> None:
    """Waiting is resumable after persistence or an event supplies new state."""
    waiting_plan = Plan(
        plan_id="plan-2",
        task_id="task-1",
        objective="Prepare the launch plan",
        steps=(
            PlanStep(step_id="research", objective="Research", status=PlanStepStatus.RUNNING),
            PlanStep(step_id="draft", objective="Draft", depends_on=("research",)),
        ),
    )
    runtime = PlanDrivenRuntime(
        retrieve_plan=lambda _task: waiting_plan,
        generate_plan=lambda _task: _plan(),
        execute_step=lambda _task, _step: None,
    )

    waiting = runtime.run(_task())
    assert waiting.state.node == PlanGraphNode.WAIT

    resumed_plan = replace(
        waiting.state.plan,
        status=PlanStatus.WAITING,
        steps=(
            replace(waiting.state.plan.steps[0], status=PlanStepStatus.COMPLETED, result="ready"),
            waiting.state.plan.steps[1],
        ),
    )
    resumed = runtime.resume(replace(waiting.state, plan=resumed_plan))

    assert resumed.state.node == PlanGraphNode.COMPLETE
    assert resumed.executed_step_ids == ("draft",)


def test_janus_system_prompt_renders_the_character_and_shared_guardrails() -> None:
    """The single-agent prompt leads with the constitution and carries the shared guardrails."""
    constitution = CharacterConstitution(
        constitution_id="test",
        version=1,
        name="Janus",
        identity="A candid companion.",
        core_values=("Honesty",),
        non_negotiables=("Do not invent results",),
        traits=(CharacterTrait("initiative", 0.5),),
    )

    from janus.agent_runtime import build_janus_system_prompt

    prompt = build_janus_system_prompt(
        constitution,
        character_state=CharacterRuntime(constitution).initial_state(),
    )

    assert prompt.startswith("# Character constitution: Janus")
    assert "A candid companion." in prompt
    assert "Do not invent results" in prompt
    assert "If a tool call is refused or fails, report that plainly" in prompt
    assert "handoff" not in prompt.lower()
    assert "# Agent Skills" not in prompt


def test_janus_system_prompt_collapses_nullish_skill_content() -> None:
    """Skills with blank names or content render cleanly instead of as empty headings."""
    from janus.agent_runtime import build_janus_system_prompt

    constitution = CharacterConstitution(
        constitution_id="test",
        version=1,
        name="Janus",
        identity="A candid companion.",
        core_values=("Honesty",),
        non_negotiables=("Do not invent results",),
        traits=(),
    )
    skills = [
        models.AgentSkill(name="Empty Skill", description="Empty content", content=None),
        models.AgentSkill(name="", description="Content only", content="  Useful content.  "),
    ]

    prompt = build_janus_system_prompt(constitution, skills)

    assert "# Agent Skills" in prompt
    assert "## Empty Skill" in prompt
    assert "\n\nUseful content." in prompt
    assert "  Useful content.  " not in prompt
