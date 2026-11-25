"""Authorize and execute one human-absent plan step (shared by triggers and delegation).

Extracted from ``trigger_runtime`` (JAN-192) so delegated child tasks (JAN-200)
run through exactly the same authorization, denial policy, execution, and
result-recording path as trigger-driven work, instead of a second copy that
could drift. Both callers keep their own policy decisions -- which task mode,
which guidance, which allowlist -- and pass them in.

The load-bearing safety property lives here: ``confirmed_tool_ids=frozenset()``
is always passed to :meth:`RuntimeAdapter.prepare_step`, so a
``confirmation="required"`` tool is denied, never auto-approved, when no human
is present. Callers cannot opt out of it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass

from langchain_core.tools import BaseTool
from sqlalchemy.orm import Session

from janus import config, llm_service, llm_usage, models, prompt_injection, tool_policy, toolset_selection
from janus.agent_runtime import build_janus_system_prompt, load_skill_library
from janus.agent_task_runner import AgentTaskRun, run_agent_task
from janus.agent_toolboxes.shared import TurnToolCall
from janus.capability_profile import user_connected_capabilities
from janus.plan_graph import PlanStep, TaskContext
from janus.runtime_adapter import RuntimeAdapter, ToolCallRecord
from janus.services import toolset_store
from janus.services.character_state_service import prompt_character
from janus.tool_registry import ToolSelectionDenial
from janus.toolset_selection import ToolsetChoice

#: Upper bound on how much of the model's answer is stored as the plan step's
#: result. The result is persisted into ``PlanRuntimeState.state`` and rendered
#: in history surfaces, so an unbounded answer must not bloat them.
UNATTENDED_RESULT_MAX_CHARS = 4_000

_TRUNCATION_MARKER = "\n[result truncated]"


class UnattendedStepDeniedError(RuntimeError):
    """A step explicitly requested a tool that policy immutably denies (JAN-194).

    Distinct from a provider or tool exception because it is a *policy*
    outcome: re-running the same step would be denied identically, so callers
    with a retry budget (the trigger worker) treat it as terminal instead of
    retrying it. Only raised for denial reasons that cannot change between
    attempts -- see ``_TERMINAL_DENIAL_REASONS``. A denial whose cause can
    change (a missing connection) still fails the step,
    but as a plain :class:`RuntimeError` so the caller retries it instead.
    """


#: Denial reasons a retry cannot fix: the tool doesn't exist, this task mode's
#: policy will never allow it, or confirmation is required and unattended
#: execution never confirms anything (``confirmed_tool_ids=frozenset()``
#: always). Everything else -- ``missing_capability`` (connect an integration
#: later), ``disabled_by_context`` (e.g. a mail account can be connected) -- is
#: retryable: the same request may succeed on a later attempt.
_TERMINAL_DENIAL_REASONS = frozenset({"unknown_tool", "not_allowlisted", "confirmation_required"})


UnattendedTaskExecutor = Callable[[Session, str, Sequence[BaseTool], str], AgentTaskRun]


#: The model choice of the step currently executing (JAN-233). Set by
#: :func:`execute_unattended_step_detailed` around the executor call, so the
#: executor signature (which tests inject) stays unchanged.
_CURRENT_MODEL_CHOICE: ContextVar[llm_service.ModelChoice | None] = ContextVar("unattended_model_choice", default=None)


def run_unattended_task(db: Session, system_prompt: str, tools: Sequence[BaseTool], objective: str) -> AgentTaskRun:
    """Execute one authorized objective with the synchronous agent loop, on the step's chosen model."""
    llm = llm_service.build_llm_service(db, _CURRENT_MODEL_CHOICE.get(), purpose="trigger").llm
    return run_agent_task(llm, system_prompt, tools, objective)


def format_denials(denials: Sequence[ToolSelectionDenial]) -> str:
    """Render tool denials as a stable, auditable ``tool:reason`` list."""
    return ", ".join(f"{denial.tool_id}:{denial.reason}" for denial in denials)


def format_step_result(
    output: str,
    *,
    authorized_tool_ids: Sequence[str],
    invoked_tools: Sequence[str],
    skipped: Sequence[ToolSelectionDenial],
    refused: Sequence[ToolCallRecord] = (),
    failed: Sequence[ToolCallRecord] = (),
    model: str | None = None,
    injections: Sequence[TurnToolCall] = (),
    toolset: ToolsetChoice | None = None,
) -> str:
    """Build the persisted, user- and model-facing record of one executed step.

    This string becomes ``PlanStep.result`` in the durable plan state and is
    what history surfaces show, so it deliberately records *what actually ran*
    (which tools were invoked, which authorized tools were unavailable)
    alongside the model's answer -- a status badge alone cannot answer "what
    happened last time". It never includes a raw trigger/delegation payload;
    only the objective, which those surfaces already expose.
    """
    lines = [f"Model: {model}"] if model else []
    lines += [
        f"Authorized tools: {', '.join(authorized_tool_ids) or 'none'}",
        f"Tools invoked: {', '.join(invoked_tools) or 'none'}",
    ]
    if skipped:
        lines.append(f"Unavailable tools (skipped): {format_denials(skipped)}")
    if refused:
        # A refusal is the model *trying* something policy blocked (JAN-229):
        # it must be visible next to whatever the model claims it did.
        lines.append("Refused tool calls: " + ", ".join(f"{record.tool_name} ({record.detail})" for record in refused))
    if failed:
        lines.append("Failed tool calls: " + "; ".join(f"{record.tool_name}: {record.detail}" for record in failed))
    if injections:
        # JAN-127: which reads looked like a prompt injection, so a reader can
        # judge the answer below and the refusals above.
        flagged = (f"{call.tool_name} ({prompt_injection.describe_rules(call.injection_rules)})" for call in injections)
        lines.append("Suspected prompt injection: " + ", ".join(flagged))
    if toolset is not None:
        # JAN-300: which toolboxes the step was narrowed to, and why.
        lines.append(toolset.describe())
    body = output.strip() or "The automation produced no summary."
    rendered = "\n".join([*lines, "", body])
    if len(rendered) <= UNATTENDED_RESULT_MAX_CHARS:
        return rendered
    # Marked rather than silently cut, so a reader never mistakes a truncated
    # summary for the automation stopping where the text ends.
    return rendered[: UNATTENDED_RESULT_MAX_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def select_unattended_toolset(
    db: Session,
    user: models.User,
    adapter: RuntimeAdapter,
    task: TaskContext,
    step: PlanStep,
    *,
    requested_tool_ids: Sequence[str],
    allowed_tool_ids: frozenset[str],
    granted_capabilities: frozenset[str],
) -> ToolsetChoice | None:
    """Return the brain-selected toolset of one unattended step (JAN-300), or ``None`` when selection is off.

    A retried or resumed step reuses the choice stored for it (or for its
    task). Otherwise the utility model picks from the step's authorized
    entries; any failure yields the fail-safe fallback (the full set).
    """
    if not config.settings.tool_selection_enabled:
        return None
    persisted = toolset_store.load_for_step(db, user.id, task.task_id, step.step_id)
    if persisted is not None:
        return persisted
    candidates = toolset_selection.authorized_candidates(
        adapter,
        task_id=task.task_id,
        step_id=step.step_id,
        task_mode=task.task_mode,
        requested_tool_ids=requested_tool_ids,
        allowed_tool_ids=allowed_tool_ids,
        granted_capabilities=granted_capabilities,
    )
    selector = None
    if toolset_selection.needs_selector(candidates):
        try:
            service = toolset_selection.utility_service(db, config.settings.tool_selection_timeout_seconds)
            selector = toolset_selection.service_selector(service)
        except Exception as error:  # noqa: BLE001 - no usable utility model: offer every authorized tool
            return toolset_selection.fallback(f"The utility model is not usable ({error.__class__.__name__}).")
    return toolset_selection.choose_toolset(
        step.objective, candidates, selector, timeout_seconds=config.settings.tool_selection_timeout_seconds
    )


@dataclass(frozen=True)
class UnattendedOutcome:
    """What one executed unattended step produced."""

    #: The persisted, audit-headed record (see :func:`format_step_result`).
    result: str
    #: The model's final answer alone, for built-in definitions to deliver.
    output: str


def execute_unattended_step(
    db: Session,
    user: models.User,
    task: TaskContext,
    step: PlanStep,
    *,
    guidance: str,
    task_executor: UnattendedTaskExecutor,
    allowed_tool_ids: frozenset[str] | None = None,
    definition: models.TriggerDefinition | None = None,
    model_choice: llm_service.ModelChoice | None = None,
    trigger_id: str | None = None,
) -> str:
    """Authorize and execute one human-absent plan step, returning its recorded result."""
    return execute_unattended_step_detailed(
        db,
        user,
        task,
        step,
        guidance=guidance,
        task_executor=task_executor,
        allowed_tool_ids=allowed_tool_ids,
        definition=definition,
        model_choice=model_choice,
        trigger_id=trigger_id,
    ).result


def execute_unattended_step_detailed(
    db: Session,
    user: models.User,
    task: TaskContext,
    step: PlanStep,
    *,
    guidance: str,
    task_executor: UnattendedTaskExecutor,
    allowed_tool_ids: frozenset[str] | None = None,
    definition: models.TriggerDefinition | None = None,
    model_choice: llm_service.ModelChoice | None = None,
    trigger_id: str | None = None,
) -> UnattendedOutcome:
    """Authorize and execute one human-absent plan step, returning its recorded result.

    ``allowed_tool_ids`` narrows the step (a definition's tools, or what a
    delegated child's parent had). The offered and confirmed tools come from
    :func:`janus.tool_policy.unattended_tool_sets`: every tool except the
    locked ones, narrowed to ``allowed_tool_ids``, and for a delegated child
    to read-only tools; ``allowed_tool_ids`` can never widen that.

    Raises when the step must fail: :class:`UnattendedStepDeniedError` when an
    explicitly requested tool was denied for an immutable reason (a terminal
    policy outcome, see ``_TERMINAL_DENIAL_REASONS``), a plain
    :class:`RuntimeError` when the denial reason could change on a later
    attempt (retryable), or whatever the provider/a tool raised (also
    potentially transient). ``PlanDrivenRuntime`` turns any of these into
    ``fail_step``, so work that did not actually run is never reported
    completed.

    Which tools may run comes from the owner's per-tool unattended switch
    (:mod:`janus.tool_policy`); a user-authored ``definition`` additionally
    allows the tools the user selected for it. Tools that stay
    blocked are still offered, answer ``BLOCKED``, and every such refusal --
    and every tool that raised -- is written into the recorded result, so a
    run can never silently claim an action it was refused (JAN-229). For a
    definition run, denials never fail the step up front: the definition's
    tool list is the user's standing request, and the refusals are the record.

    ``trigger_id`` is the durable trigger run executing this step (``None``
    for a delegated child); the ``triggers`` toolbox records it as the parent
    of any follow-up the step schedules (JAN-274).
    """
    # Under "Allow everything" (JAN-359) the run also sees the owner's offered remote MCP tools.
    allow_all = tool_policy.unattended_allow_all(db, user.id)
    if allow_all:
        from janus.agent_tools import build_managed_registry  # lazy: agent_tools imports every toolbox

        adapter = RuntimeAdapter(db, user, registry=build_managed_registry(db, user))
    else:
        adapter = RuntimeAdapter(db, user)
    registry_entries = adapter.registry.entries()
    effective_allowed, confirmed = tool_policy.unattended_tool_sets(
        db,
        user.id,
        registry_entries,
        task_mode=task.task_mode,
        narrowed_to=allowed_tool_ids,
        definition=definition,
    )
    # A step may explicitly request specific tools -- those go through normal
    # authorization/denial handling below unchanged. When it names none (the
    # realistic default), the fallback requests a definition run's own tools
    # (an off one among them answers BLOCKED, since the definition lists it),
    # or otherwise only the tools the owner allows: offering every switched-off
    # toolbox as a refusal stub to an ad-hoc trigger or a child that never
    # asked for it would only bloat its toolset.
    explicitly_requested = bool(step.required_tool_ids)
    sweep = effective_allowed if definition is not None else confirmed
    requested_tool_ids = step.required_tool_ids or tuple(entry.id for entry in registry_entries if entry.id in sweep)
    granted_capabilities = user_connected_capabilities(db, user)
    # JAN-300: an ad-hoc trigger or a delegated child that named no tools gets a
    # brain-selected subset of its sweep. A definition run keeps the tools its
    # author picked, and an explicit request is already task-scoped.
    toolset = (
        select_unattended_toolset(
            db,
            user,
            adapter,
            task,
            step,
            requested_tool_ids=requested_tool_ids,
            allowed_tool_ids=effective_allowed,
            granted_capabilities=granted_capabilities,
        )
        if definition is None and not explicitly_requested
        else None
    )

    prepared = adapter.prepare_step(
        task_id=task.task_id,
        step_id=step.step_id,
        objective=step.objective,
        requested_tool_ids=requested_tool_ids,
        task_mode=task.task_mode,
        allowed_tool_ids=effective_allowed,
        granted_capabilities=granted_capabilities,
        # No human is present to approve a tool mid-flight, so unlike chat
        # (which pre-confirms its catalog because a human is watching) every
        # offered tool the owner switched off is gated and answers BLOCKED;
        # only a user-authored definition's own tool selection overrides it.
        confirmed_tool_ids=confirmed,
        blocked_tool_ids=effective_allowed - confirmed,
        refusal_stubs=True,
        guard_tool_errors=True,
        model_choice=model_choice,
        # The objective may embed untrusted event data or mail (JAN-127).
        scan_objective=True,
        trigger_id=trigger_id,
        toolset=toolset,
        unattended_allow_all=allow_all,
    )
    if prepared.toolset is not None:
        toolset_store.save(db, user.id, task.task_id, step.step_id, task.task_mode, prepared.toolset)
    # Denial policy depends on who asked for the tool.
    #
    # When specific tools were requested, a denial means the automation asked
    # for something it may not have, and failing the step is the honest
    # outcome -- silently running a narrower toolset would let it report
    # success for work it could not do.
    #
    # When none were requested, ``requested_tool_ids`` is a best-effort sweep
    # of the allowlist, which spans integration-gated entries (``calendar``,
    # ``jira``, ``events``). A user with no calendar connected would otherwise
    # have *every* run fail on a ``missing_capability`` denial for a tool
    # nobody asked for. Those denials are recorded in the step result instead,
    # and the step runs with whatever was authorized. This is not a weakening
    # of the gate: a denied tool is still absent from ``prepared.tools`` and
    # therefore uncallable either way.
    if explicitly_requested and prepared.denials and definition is None:
        message = f"tool authorization denied for step {step.step_id}: {format_denials(prepared.denials)}"
        if any(denial.reason in _TERMINAL_DENIAL_REASONS for denial in prepared.denials):
            raise UnattendedStepDeniedError(message)
        # Every denial here has a reason that can change between attempts
        # (e.g. ``missing_capability``, ``disabled_by_context``) -- fail
        # the step, but as a plain, retryable error rather than the terminal
        # policy outcome above.
        raise RuntimeError(message)

    authorized_tool_ids = tuple(entry.id for entry in prepared.selection.entries)
    # Unattended runs speak as the user's Janus character with the full skill
    # library, exactly like chat; only the run guidance differs.
    # The persisted adaptive state (JAN-301) is rendered too; unattended runs never change it.
    constitution, character_state = prompt_character(db, user.id)
    system_prompt = "\n\n".join(
        [build_janus_system_prompt(constitution, load_skill_library(db), character_state=character_state), guidance]
    )
    token = _CURRENT_MODEL_CHOICE.set(model_choice)
    try:
        # Usage accounting (JAN-408): a trigger run's step, or a delegated child's (no trigger id).
        with llm_usage.usage_surface("trigger" if trigger_id else "delegated"):
            run = task_executor(db, system_prompt, prepared.tools, step.objective)
    finally:
        _CURRENT_MODEL_CHOICE.reset(token)
    result = format_step_result(
        run.output,
        authorized_tool_ids=authorized_tool_ids,
        # Tool *names* only, deliberately: ``AgentTaskRun`` also carries each
        # call's output, but that output is raw tool data (mail subjects,
        # calendar entries, event-log rows) and this result is persisted and
        # rendered in history surfaces. The model's own summary is the curated
        # version of the same information; the name list shows what ran.
        invoked_tools=[call.tool for call in run.tool_calls],
        skipped=prepared.denials,
        refused=prepared.call_log.refused(),
        failed=prepared.call_log.failed(),
        model=llm_service.describe_model(db, model_choice),
        injections=prepared.trace.detections(),
        toolset=prepared.toolset,
    )
    return UnattendedOutcome(result=result, output=run.output.strip())


__all__ = [
    "UNATTENDED_RESULT_MAX_CHARS",
    "UnattendedStepDeniedError",
    "UnattendedOutcome",
    "UnattendedTaskExecutor",
    "execute_unattended_step",
    "execute_unattended_step_detailed",
    "format_denials",
    "format_step_result",
    "run_unattended_task",
    "select_unattended_toolset",
]
