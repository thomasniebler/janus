"""Shared runtime helpers for Janus chat agents and plan-driven work."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from janus import llm_service, models, prompt_injection
from janus.character import CharacterConstitution, CharacterState
from janus.plan_graph import (
    Plan,
    PlanGraph,
    PlanGraphError,
    PlanGraphNode,
    PlanGraphState,
    PlanStep,
    TaskContext,
)


@dataclass(frozen=True)
class StepAwait:
    """A step executor's answer when the step finished but must wait for external work (JAN-207).

    ``result`` is the step's own recorded result; ``external_ids`` name the
    work to wait for (delegation IDs); ``continuation_objective`` is the
    objective of the step that resumes once all of it has finished.
    """

    result: str | None
    external_ids: tuple[str, ...]
    continuation_objective: str


PlanRetriever = Callable[[TaskContext], Plan | None]
PlanGenerator = Callable[[TaskContext], Plan]
PlanStepExecutor = Callable[[TaskContext, PlanStep], str | StepAwait | None]


@dataclass(frozen=True)
class PlanRunResult:
    """Outcome of one bounded plan-graph advancement."""

    state: PlanGraphState
    executed_step_ids: tuple[str, ...] = ()

    @property
    def is_waiting(self) -> bool:
        """Whether the plan is parked in ``WAIT`` (the durable trigger boundary parks the run)."""
        return self.state.node == PlanGraphNode.WAIT


@dataclass(frozen=True)
class PlanDrivenRuntime:
    """Advance a plan through the stable graph using injected runtime services.

    Retrieval, generation, and execution are injected so the graph boundary
    can be connected to persistence, LLM planning, and tool execution in later
    increments without making those concerns part of the graph itself.
    """

    retrieve_plan: PlanRetriever
    generate_plan: PlanGenerator
    execute_step: PlanStepExecutor
    graph: PlanGraph = field(default_factory=PlanGraph)

    def run(self, task: TaskContext, plan: Plan | None = None) -> PlanRunResult:
        """Retrieve or accept a plan, then advance it until it must pause or ends."""
        return self._advance(self.graph.begin(task, plan))

    def resume(self, state: PlanGraphState) -> PlanRunResult:
        """Resume a waiting graph state after external state becomes available."""
        return self._advance(self.graph.resume(state))

    def _advance(self, state: PlanGraphState) -> PlanRunResult:
        executed_step_ids: list[str] = []

        while state.node not in {
            PlanGraphNode.WAIT,
            PlanGraphNode.COMPLETE,
            PlanGraphNode.FAILED,
        }:
            if state.node == PlanGraphNode.RETRIEVE_PLAN:
                state = self.graph.plan_retrieved(self.graph.begin(state.task), self.retrieve_plan(state.task))
                continue
            if state.node == PlanGraphNode.GENERATE_PLAN:
                state = self.graph.plan_generated(state, self.generate_plan(state.task))
                continue
            if state.node == PlanGraphNode.SELECT_STEP:
                state = self.graph.select_step(state)
                continue
            if state.node == PlanGraphNode.EXECUTE_STEP:
                step = self._current_step(state)
                state = self.graph.start_step(state)
                executed_step_ids.append(step.step_id)
                try:
                    result = self.execute_step(state.task, step)
                except Exception as error:
                    message = str(error).strip() or error.__class__.__name__
                    state = self.graph.fail_step(state, message)
                else:
                    if isinstance(result, StepAwait):
                        try:
                            state = self.graph.await_external(
                                state, result.result, result.external_ids, result.continuation_objective
                            )
                        except PlanGraphError as error:
                            state = self.graph.fail_step(state, str(error))
                    else:
                        state = self.graph.complete_step(state, result)
                continue
            raise RuntimeError(f"unsupported plan graph node: {state.node.value}")

        return PlanRunResult(state=state, executed_step_ids=tuple(executed_step_ids))

    @staticmethod
    def _current_step(state: PlanGraphState) -> PlanStep:
        """Return the selected step before it is marked running."""
        if state.plan is None or state.current_step_id is None:
            raise RuntimeError("execute_step graph state is missing its selected step")
        for step in state.plan.steps:
            if step.step_id == state.current_step_id:
                return step
        raise RuntimeError(f"selected plan step does not exist: {state.current_step_id}")


_RUNTIME_GUARDRAILS = (
    "Use Janus tools when needed and treat tool results as the source of truth. "
    "Do not claim to have fetched, opened, checked, connected to, or verified anything unless it is present "
    "in tool results or prior conversation context. If information is unavailable, say so plainly. "
    "If a tool call is refused or fails, report that plainly instead of claiming the action happened."
)

_JANUS_TOOL_AVAILABILITY = (
    "--- Tool Availability ---\n"
    "- The tools offered to you are authorized fresh for this step from the user's connected integrations "
    "and Janus policy. If a tool you would need is missing, say the related integration is not connected "
    "or not permitted here instead of guessing.\n"
    "- For questions about Janus itself -- how to set something up, what a setting or tool does, why a tool "
    "answered BLOCKED, what you can do -- look it up with the Janus documentation tools (search_docs, read_doc, "
    "list_docs) when they are offered, and answer from what the docs say rather than from memory. A BLOCKED "
    "answer names the page that explains it."
)


#: Chat-only (interactive) narration of multi-step turns. Unattended runs leave it out: their output
#: is a briefing, digest, or trigger result, not a live conversation.
CHAT_PROGRESS_UPDATES = (
    "--- Progress updates ---\n"
    "When a request takes more than one round of tool calls, keep the user posted as you work:\n"
    "- Before your first tool call, write one or two sentences with your plan: what you will look at, "
    "in what order.\n"
    "- After each round of tool results, write one short line: whether the result was what you expected, "
    "the most interesting thing it showed, and what you will do next.\n"
    "- Keep each update to a line or two; the full answer comes at the end.\n"
    "- For a direct answer or a single lookup, skip the plan and just answer.\n"
    "- Never describe a step as done before its tool result has arrived."
)


def load_skill_library(db: Session) -> list[models.AgentSkill]:
    """Return every stored skill as one flat library for the single Janus agent.

    With one agent, every skill applies to every surface.
    """
    return db.query(models.AgentSkill).order_by(models.AgentSkill.name.asc(), models.AgentSkill.id.asc()).all()


def _serialize_skills(skills: Iterable[models.AgentSkill]) -> list[str]:
    """Return formatted skill sections for a collection of skills."""
    skill_sections: list[str] = []

    for skill in skills or []:
        skill_name = (skill.name or "").strip()
        skill_content = (skill.content or "").strip()
        if skill_name and skill_content:
            skill_sections.append(f"## {skill_name}\n\n{skill_content}")
        elif skill_name:
            skill_sections.append(f"## {skill_name}")
        elif skill_content:
            skill_sections.append(skill_content)

    return skill_sections


def build_janus_system_prompt(
    character: CharacterConstitution,
    skills: Iterable[models.AgentSkill] = (),
    *,
    character_state: CharacterState | None = None,
    progress_updates: bool = False,
) -> str:
    """Return the single Janus agent's system prompt, sourced from its character constitution.

    Used by interactive chat and every unattended run (triggers, delegated
    child tasks): identity comes from the user's persisted constitution
    (``GET/PUT /api/character``), skills from the flat skill library, and the
    guardrails are the same ones every surface shares. Unattended callers
    append their own run guidance after this prompt. Interactive chat sets
    ``progress_updates`` so multi-step turns are narrated as they run.
    """
    prompt_parts = [
        character.render_system_prompt(character_state),
        _RUNTIME_GUARDRAILS,
        llm_service.UNTRUSTED_EMAIL_CONTENT_WARNING,
        prompt_injection.UNTRUSTED_CONTENT_POLICY,
        _JANUS_TOOL_AVAILABILITY,
        CHAT_PROGRESS_UPDATES if progress_updates else "",
    ]
    skill_sections = _serialize_skills(skills)
    if skill_sections:
        prompt_parts.extend(["# Agent Skills", "\n\n".join(skill_sections)])
    return "\n\n".join(part for part in prompt_parts if part and part.strip())


def build_janus_mail_ingestion_guidance(
    character: CharacterConstitution,
    skills: Iterable[models.AgentSkill] = (),
    *,
    character_state: CharacterState | None = None,
) -> str:
    """Return mail-classification guidance sourced from the character constitution and skill library."""
    prompt_parts = [character.render_system_prompt(character_state)]
    skill_sections = _serialize_skills(skills)
    if skill_sections:
        prompt_parts.extend(["# Agent Skills", "\n\n".join(skill_sections)])
    return "\n\n".join(part for part in prompt_parts if part and part.strip())
