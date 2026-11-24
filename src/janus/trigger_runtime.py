"""Connects durable trigger ingress to the plan-driven runtime adapter (JAN-165).

The durable trigger boundary (``trigger_service.py``, ``trigger_adapters.py``)
and the plan graph/runtime (``plan_graph.py``, ``agent_runtime.py``,
``runtime_trigger_handler.py``) already existed as separate, tested layers with
no production adapter connecting them: ``startup.configure_runtime_trigger_handler``
had nothing registered, so the opt-in ``runtime_trigger_worker_enabled`` worker
could never actually start.

This module provides that missing production ``TriggerHandler``: for each
trigger it builds a single-step ``Plan`` from the trigger's objective, and
authorizes the step's requested tools through the same
:class:`~janus.runtime_adapter.RuntimeAdapter` used by chat, briefing, and
ingestion, so triggered work is authorized identically and is visible in the
same durable plan/task state model (``PlanRuntimeState``) as every other
runtime entrypoint.

Authorization denials fail the *plan step* (via ``PlanDrivenRuntime``'s
existing exception-to-``fail_step`` behavior), and the denial is recorded and
auditable in the persisted plan state.

Since JAN-192 this module also *executes* the authorized step, via
``agent_task_runner.run_agent_task`` -- the synchronous, non-streaming sibling
of the websocket-bound chat tool loop. A step that authorizes and
executes is now honestly reported ``completed``, with the model's answer and
the trace of tools that actually ran persisted into the plan step's ``result``
so both the agent-facing ``list_my_trigger_history`` tool and the Settings ->
Runtime UI can show what happened. A step whose execution fails is recorded as
a *failed plan step*, never as a completed one.

The trigger row's status follows the plan outcome (JAN-194).
``PlanDrivenRuntime`` converts a step exception into ``fail_step`` and returns
normally, while ``DurableTriggerWorker`` decides a row's status by whether the
handler *raised*. So when the plan ends ``FAILED`` this handler raises, and the
exception type tells the worker whether a retry can help:

* :class:`TerminalTriggerRunFailedError` (a :class:`TerminalTriggerError`) for
  a policy outcome -- an explicitly requested tool was denied, the owner no
  longer exists, or the payload is malformed. Retrying would fail identically,
  so the row goes straight to ``exhausted``.
* :class:`TriggerRunFailedError` for everything else (provider outage, a tool
  raising, a run hitting its iteration/time ceiling, missing runtime
  configuration). The row goes to ``failed`` and uses the retry budget; a
  retry re-runs the task from a fresh plan rather than returning the stored
  failure (see ``DurablePlanRuntimeTriggerHandler``).

Either way the failure reason becomes the row's ``last_error``, so no surface
can report ``completed`` for work that failed.

Two safety properties are load-bearing here and must not be weakened:

* only the tools the owner allows in unattended runs are confirmed
  (:func:`janus.tool_policy.unattended_tool_sets`, JAN-255); every other
  offered tool -- by default the ``confirmation="required"`` ones -- is
  *denied* and answers ``BLOCKED``, never auto-approved, for human-absent
  execution. Only a user-authored definition's own picks override that.
* every step knows its durable ``trigger_id``, which the ``triggers``
  toolbox records as the parent of any follow-up the run schedules, so the
  chained self-scheduling guard (depth, fan-out, daily cap, minimum delay;
  JAN-274) can hold; the ``events`` tool reads only the owner's rows here
  (JAN-272); and ad-hoc ``trigger`` runs never get the tool names in
  ``TASK_MODE_WITHHELD_TOOL_NAMES["trigger"]`` (no memory delete, no widget
  prompt-override changes).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import builtin_triggers, delegation_wait, event_logger, models, trigger_approval
from janus import trigger_definition_service as definitions
from janus.agent_runtime import PlanDrivenRuntime, PlanRunResult, StepAwait
from janus.llm_service import ModelChoice
from janus.plan_graph import Plan, PlanGraphNode, PlanStep, TaskContext
from janus.plan_state import PlanStateStore
from janus.runtime_trigger_handler import DurablePlanRuntimeTriggerHandler, task_context_from_trigger
from janus.trigger_service import DurableTriggerDispatcher
from janus.triggers import InvalidTriggerPayloadError, TerminalTriggerError, TriggerEnvelope, TriggerKind
from janus.unattended_execution import (
    UnattendedStepDeniedError,
    UnattendedTaskExecutor,
    execute_unattended_step_detailed,
    format_step_result,
    run_unattended_task,
)

#: Trigger dispatch is the production entrypoint that owns which task mode
#: authorizes trigger-driven work -- it must never trust a trigger payload's
#: (or the shared schema default's) ``task_mode`` field, since the durable
#: trigger payload contract is generic, user-authored input (see
#: ``schemas.runtime_triggers.RuntimeTriggerCreate``) that defaults to
#: ``"default"``, an unregistered task mode that resolves to an *empty*
#: ``TASK_MODE_ALLOWED_TOOL_IDS`` allowlist (denying every tool, including
#: ``core``). Forcing ``"trigger"`` here -- regardless of what the payload
#: says -- is both the fix for that always-fails case and a fail-closed
#: guard against a trigger payload trying to widen its own allowlist (e.g.
#: by requesting ``task_mode="chat"``).
_TRIGGER_TASK_MODE = "trigger"


#: Guidance prepended to the resolved agent's own system prompt for a
#: human-absent run. Trigger execution has no conversation to ask a follow-up
#: question in and no approval UI, so the model is told plainly what it can and
#: cannot do rather than being left to discover it through tool denials.
_TRIGGER_EXECUTION_GUIDANCE = (
    "--- Trigger run ---\n"
    "You are running as a Janus trigger (an automation). No human is present: you cannot ask a "
    "follow-up question, and no one will approve an action mid-run. Complete the objective using "
    "only the tools you have been given. Any change you make takes effect without review, so only "
    "make changes the objective clearly calls for. If a tool answers BLOCKED or ERROR, do not retry "
    "it and do not pretend it worked: say so in your answer. Then reply with a short, self-contained "
    "answer covering what you found and every change you actually made. If the objective cannot be "
    "completed with the available tools, say exactly what was missing instead of guessing or claiming "
    "work you did not do."
)

#: Kept as a public alias: the executor seam predates the shared
#: ``unattended_execution`` module and is what trigger tests inject.
TriggerTaskExecutor = UnattendedTaskExecutor

#: Step failures that are policy outcomes rather than transient faults: the
#: same trigger re-run would fail the same way, so it must not be retried.
_TERMINAL_STEP_ERRORS: tuple[type[Exception], ...] = (UnattendedStepDeniedError, TerminalTriggerError)


class TriggerRunFailedError(RuntimeError):
    """The trigger's plan ran and ended ``FAILED``; retrying may succeed.

    Carries the :class:`PlanRunResult` so callers (and tests) can still inspect
    the persisted failure without re-reading plan state.
    """

    def __init__(self, result: PlanRunResult) -> None:
        """Wrap a failed run, using the plan's recorded reason as the message."""
        self.result = result
        super().__init__(result.state.reason or "trigger run failed")


class TerminalTriggerRunFailedError(TriggerRunFailedError, TerminalTriggerError):
    """The trigger's plan ended ``FAILED`` for a reason no retry can fix."""


def _generate_single_step_plan(task: TaskContext, trigger: TriggerEnvelope) -> Plan:
    """Build the one-step plan a trigger authorizes and executes.

    Trigger payloads may include ``tool_ids`` (the specific tools this
    automation wants); when absent, the step requests every tool the run is
    offered (see ``janus.tool_policy.unattended_tool_sets``).
    """
    # A payload is generic, user-authored JSON, so ``tool_ids`` may be any
    # type. Coercing blindly would silently explode a bare string into
    # per-character "tool ids" ("mail" -> 'm','a','i','l') or raise an opaque
    # TypeError on a number; both end in a failed step, but neither tells the
    # author what was actually wrong.
    raw_tool_ids = trigger.payload.get("tool_ids", ())
    if isinstance(raw_tool_ids, str) or not isinstance(raw_tool_ids, (list, tuple)):
        raise InvalidTriggerPayloadError("trigger payload tool_ids must be a list of tool id strings")
    requested_tool_ids = tuple(raw_tool_ids)
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


def _execute_authorized_step(
    session_factory: Callable[[], Session],
    trigger: TriggerEnvelope,
    task: TaskContext,
    step: PlanStep,
    task_executor: TriggerTaskExecutor,
) -> str | StepAwait | None:
    """Authorize and execute one trigger-driven plan step.

    Authorization, the fail-closed confirmation gate, the denial policy, and
    result recording are shared with delegated child tasks in
    :func:`janus.unattended_execution.execute_unattended_step`; this function
    only owns the trigger-specific policy (owner lookup and guidance).

    A run's first step may wait for the children it delegated (JAN-207): it
    then returns :class:`StepAwait` and the plan parks. A built-in run never
    waits (its ``finalize`` delivers the answer), and neither does a
    continuation step (it has dependencies), so a run waits at most once.
    """
    db = session_factory()
    try:
        user = db.get(models.User, trigger.owner_id)
        if user is None:
            raise TerminalTriggerError(f"unknown trigger owner: {trigger.owner_id}")
        definition = definitions.definition_for_trigger(db, trigger.trigger_id)
        if definition is not None and not trigger_approval.is_runnable(definition):
            # Defense in depth (JAN-235): a Janus proposal that is not approved never runs,
            # even if a run for it was queued by some path that bypassed the guards.
            raise TerminalTriggerError(
                f"trigger definition {definition.id} is a suggested automation that is {definition.proposal_status}"
            )
        builtin = builtin_triggers.builtin_for(definition)
        # A Janus follow-up (JAN-257) has a definition only so it is visible;
        # it runs exactly as the ad-hoc trigger it used to be, with the
        # ``trigger`` mode's allowlist, plain tool policy and default model.
        automation = None if definitions.is_agent_follow_up(definition) else definition
        prepared = builtin_triggers.PreparedRun()
        if builtin is not None and builtin.prepare is not None:
            prepared = builtin.prepare(db, user, definition)
        if prepared.skip_output is not None:
            # Nothing for the model to do (e.g. no digest emails): complete
            # honestly without an LLM call.
            result = format_step_result(prepared.skip_output, authorized_tool_ids=(), invoked_tools=(), skipped=())
            output = prepared.skip_output
        else:
            if prepared.context:
                objective = f"{step.objective}\n\n{prepared.context}"
                task = replace(task, objective=objective)
                step = replace(step, objective=objective)
            may_wait = builtin is None and not step.depends_on
            with delegation_wait.accepting_waits(task.task_id, step.step_id) if may_wait else nullcontext() as sink:
                outcome = execute_unattended_step_detailed(
                    db,
                    user,
                    task,
                    step,
                    guidance=_TRIGGER_EXECUTION_GUIDANCE,
                    task_executor=task_executor,
                    allowed_tool_ids=(frozenset(automation.tool_ids or ()) | {"core"}) if automation else None,
                    definition=automation,
                    model_choice=(
                        ModelChoice(provider=automation.llm_provider, model=automation.llm_model)
                        if automation
                        else None
                    ),
                    trigger_id=trigger.trigger_id,
                )
            result, output = outcome.result, outcome.output
            if sink is not None and sink.delegation_ids:
                _record_output(db, trigger.trigger_id, output)
                return StepAwait(
                    result=result,
                    external_ids=tuple(sink.delegation_ids),
                    continuation_objective=step.objective,
                )
        _record_output(db, trigger.trigger_id, output)
        if builtin is not None and builtin.finalize is not None:
            try:
                builtin.finalize(db, user, output, prepared.state)
            except Exception as error:  # noqa: BLE001 - delivery failure must not undo a completed run
                db.rollback()
                event_logger.log(
                    models.EventType.ERROR,
                    "TriggerRuntime",
                    f"Delivering the result of {builtin.key} failed: {error}",
                    {"trigger_id": trigger.trigger_id},
                )
        return result
    finally:
        db.close()


def _record_output(db: Session, trigger_id: str, output: str) -> None:
    """Persist a run's raw final answer on its durable row (JAN-231)."""
    record = db.scalar(select(models.RuntimeTrigger).where(models.RuntimeTrigger.trigger_id == trigger_id))
    if record is not None:
        record.output = output
        db.commit()


def build_runtime_trigger_handler(
    session_factory: Callable[[], Session],
    *,
    context_builder: Callable[[TriggerEnvelope], TaskContext] = task_context_from_trigger,
    task_executor: TriggerTaskExecutor = run_unattended_task,
) -> Callable[[TriggerEnvelope], PlanRunResult]:
    """Build the production trigger handler backing ``configure_runtime_trigger_handler``.

    Each invocation builds a per-trigger :class:`PlanDrivenRuntime` bound to
    that trigger's owner and payload, so tool authorization is always
    evaluated for the correct user. Plan/task state is persisted through
    :class:`PlanStateStore`, the same durable store used by
    :class:`DurablePlanRuntimeTriggerHandler` for resume/retry.

    ``task_executor`` is the synchronous agent loop that performs an
    authorized step; it is injected so authorization and execution policy can
    be exercised without a live LLM provider, exactly as ``context_builder``
    makes task-context policy testable.
    """

    def _trigger_context_builder(trigger: TriggerEnvelope) -> TaskContext:
        """Build the task context, then force the trigger task mode.

        See ``_TRIGGER_TASK_MODE`` above: trigger dispatch owns this policy
        decision itself rather than trusting the (optional, defaulted)
        ``task_mode`` field on the generic trigger payload. A run of a saved
        definition -- established from the durable row, never the payload --
        uses the ``automation`` mode, whose fence is the definition's tools --
        except Janus's own one-time chat follow-ups (JAN-257), which keep the
        ``trigger`` mode they had before they were given a definition.
        """
        db = session_factory()
        try:
            definition = definitions.definition_for_trigger(db, trigger.trigger_id)
            is_automation_run = definition is not None and not definitions.is_agent_follow_up(definition)
        finally:
            db.close()
        mode = definitions.AUTOMATION_TASK_MODE if is_automation_run else _TRIGGER_TASK_MODE
        return replace(context_builder(trigger), task_mode=mode)

    def handler(trigger: TriggerEnvelope) -> PlanRunResult:
        if trigger.owner_id is None:
            raise ValueError("the runtime trigger handler requires an owned trigger")
        # Everything this run logs belongs to its owner (JAN-272), so the
        # owner's own unattended runs can read it back and nobody else's can.
        with event_logger.owned_by(trigger.owner_id):
            return _handle(trigger)

    def _handle(trigger: TriggerEnvelope) -> PlanRunResult:

        # ``PlanDrivenRuntime`` records a step exception as ``fail_step`` and
        # returns normally, which erases its type. Remember it here so the
        # failure can be classified as terminal or retryable afterwards.
        step_errors: list[Exception] = []

        def execute_step(task: TaskContext, step: PlanStep) -> str | StepAwait | None:
            try:
                return _execute_authorized_step(session_factory, trigger, task, step, task_executor)
            except Exception as error:
                step_errors.append(error)
                raise

        runtime = PlanDrivenRuntime(
            retrieve_plan=lambda _task: None,
            generate_plan=lambda task: _generate_single_step_plan(task, trigger),
            execute_step=execute_step,
        )
        durable_handler = DurablePlanRuntimeTriggerHandler(
            runtime,
            PlanStateStore(session_factory),
            context_builder=_trigger_context_builder,
            # A run parked on its delegated children resumes with their
            # outcomes resolved into its plan (JAN-207).
            before_resume=lambda owner_id, state: delegation_wait.resolve_awaited_delegations(
                session_factory, owner_id, state
            ),
        )
        result = durable_handler(trigger)
        if result.state.node == PlanGraphNode.FAILED:
            # Raised only after the durable handler persisted the failed state,
            # so both history surfaces still show the step's own error.
            if step_errors and isinstance(step_errors[-1], _TERMINAL_STEP_ERRORS):
                raise TerminalTriggerRunFailedError(result)
            raise TriggerRunFailedError(result)
        return result

    return handler


def run_definition_inline(
    session_factory: Callable[[], Session],
    definition_id: int,
    *,
    task_executor: TriggerTaskExecutor = run_unattended_task,
) -> models.RuntimeTrigger:
    """Run one definition now, in this thread, through the durable trigger path.

    Used where a caller waits for the answer (the dashboard briefing, the
    ``janus brief``/``janus digest`` CLI). The run is an ordinary durable row
    claimed atomically by :class:`~janus.trigger_service.DurableTriggerDispatcher`,
    so the background worker can never execute it a second time, and it works
    even when the worker is disabled.
    """
    db = session_factory()
    try:
        definition = db.get(models.TriggerDefinition, definition_id)
        if definition is None:
            raise ValueError(f"unknown trigger definition: {definition_id}")
        moment = datetime.now(UTC)
        envelope = definitions.build_definition_envelope(
            definition,
            occurrence_key=f"inline:{moment.isoformat()}",
            kind=TriggerKind.MANUAL,
            source="definition-inline",
            created_at=moment,
        )
        definition.last_run_at = moment.replace(tzinfo=None)
        db.commit()
    finally:
        db.close()
    dispatcher = DurableTriggerDispatcher(
        session_factory, build_runtime_trigger_handler(session_factory, task_executor=task_executor)
    )
    dispatcher.dispatch(envelope, definition_id=definition_id)
    db = session_factory()
    try:
        record = db.scalar(select(models.RuntimeTrigger).where(models.RuntimeTrigger.trigger_id == envelope.trigger_id))
        if record is None:
            raise RuntimeError("inline trigger run was not recorded")
        db.expunge(record)
        return record
    finally:
        db.close()


__all__ = [
    "TerminalTriggerRunFailedError",
    "TriggerRunFailedError",
    "TriggerTaskExecutor",
    "build_runtime_trigger_handler",
    "run_definition_inline",
]
