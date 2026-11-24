"""Owner-scoped read model for the Tasks page (JAN-204).

The plan-driven runtime persists unattended work in three tables:

* ``runtime_triggers`` -- one row per trigger run (ad hoc, or a run of a saved
  ``TriggerDefinition`` via ``definition_id``), with its lifecycle;
* ``runtime_delegations`` -- one row per delegated child task (JAN-200), linked
  to its parent by ``parent_task_id``;
* ``plan_runtime_states`` -- the durable plan snapshot of either, keyed by the
  owner and the run's ``task_id`` (a delegation's ``child_task_id``). Each
  executed step's ``result`` starts with the audit header written by
  ``unattended_execution.format_step_result`` -- the only persisted tool trace
  of an unattended run.

This module joins them into one paginated, newest-first task list and a detail
view. Chat turns are deliberately absent: their live timeline (JAN-316) and
tool calls (``conversation_tool_calls``) already have their own surfaces.

Every function takes the owner's id and filters by it here, so no caller can
read another user's task; a foreign id reads as missing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from janus import models
from janus.delegation_service import expire_stale_delegations
from janus.plan_graph import PlanGraphState, PlanStepStatus
from janus.plan_state import PlanStateError, deserialize_plan_state
from janus.schemas.runtime_tasks import (
    RuntimeStepTrace,
    RuntimeTaskDetail,
    RuntimeTaskRef,
    RuntimeTaskStep,
    RuntimeTaskSummary,
)

#: ``source`` filter values; ``trigger`` means ad-hoc runs without a definition.
TASK_SOURCES = ("trigger", "definition", "delegated")

#: A run of a saved definition. ``definition_name`` is the name snapshotted at
#: enqueue time; it survives the definition's deletion, which nulls
#: ``definition_id`` (``ON DELETE SET NULL`` where the database enforces it) or
#: leaves it dangling (SQLite without FK enforcement), so a run's provenance
#: never changes after the fact.
_IS_DEFINITION_RUN = or_(
    models.RuntimeTrigger.definition_id.is_not(None), models.RuntimeTrigger.definition_name.is_not(None)
)
_IS_AD_HOC_RUN = and_(models.RuntimeTrigger.definition_id.is_(None), models.RuntimeTrigger.definition_name.is_(None))


def _is_definition_run(trigger: models.RuntimeTrigger) -> bool:
    return trigger.definition_id is not None or trigger.definition_name is not None


#: Longest list title derived from an objective.
_TITLE_MAX_CHARS = 120

#: Upper bound on the children listed on one trigger run's detail.
MAX_CHILDREN_LISTED = 50

_TRIGGER_KIND = "trigger"
_DELEGATED_KIND = "delegated"

# Audit-header labels written by ``unattended_execution.format_step_result``.
_LIST_LABELS = {"Authorized tools": "authorized_tools", "Tools invoked": "invoked_tools"}
_TEXT_LABELS = {
    "Model": "model",
    "Unavailable tools (skipped)": "skipped",
    "Refused tool calls": "refused",
    "Failed tool calls": "failed",
    "Suspected prompt injection": "suspected_injection",
    # The brain-selected toolset and why (JAN-300).
    "Tool selection": "tool_selection",
}


@dataclass(frozen=True)
class TaskListPage:
    """One page of task summaries plus the total matching count."""

    items: list[RuntimeTaskSummary]
    total: int


def parse_step_result(result: str | None) -> tuple[RuntimeStepTrace | None, str | None]:
    """Split a persisted step result into its tool trace and the model's answer.

    A result without the audit header (an older snapshot, or a step recorded
    by another path) is returned whole as the answer with no trace.
    """
    if not result:
        return None, result
    header, separator, body = result.partition("\n\n")
    lines = header.split("\n")
    if not lines[0].startswith(("Model: ", "Authorized tools: ")):
        return None, result
    fields: dict[str, object] = {}
    for line in lines:
        label, colon, value = line.partition(": ")
        if not colon:
            return None, result
        if label in _LIST_LABELS:
            fields[_LIST_LABELS[label]] = [] if value == "none" else [item for item in value.split(", ") if item]
        elif label in _TEXT_LABELS:
            fields[_TEXT_LABELS[label]] = value
        else:
            return None, result
    return RuntimeStepTrace.model_validate(fields), (body if separator else None)


def _title_from(objective: str | None, fallback: str) -> str:
    if objective:
        first_line = objective.strip().split("\n", 1)[0].strip()
        if first_line:
            return first_line if len(first_line) <= _TITLE_MAX_CHARS else first_line[: _TITLE_MAX_CHARS - 1] + "…"
    return fallback


def _load_states(db: Session, user_id: int, task_ids: Iterable[str]) -> dict[str, PlanGraphState]:
    """Load the owner's plan snapshots for ``task_ids`` in one query; unreadable ones are skipped."""
    wanted = list(dict.fromkeys(task_ids))
    if not wanted:
        return {}
    records = db.scalars(
        select(models.PlanRuntimeState).where(
            models.PlanRuntimeState.user_id == user_id,
            models.PlanRuntimeState.task_id.in_(wanted),
        )
    ).all()
    states: dict[str, PlanGraphState] = {}
    for record in records:
        try:
            states[record.task_id] = deserialize_plan_state(record.state)
        except PlanStateError:
            # One unreadable snapshot must not hide the rest of the page.
            continue
    return states


def _plan_fields(state: PlanGraphState | None) -> dict[str, object]:
    if state is None or state.plan is None:
        return {}
    steps = state.plan.steps
    current = next((step for step in steps if step.step_id == state.current_step_id), None)
    in_progress = current is not None and current.status in (PlanStepStatus.PENDING, PlanStepStatus.RUNNING)
    return {
        "plan_status": state.plan.status.value,
        "step_count": len(steps),
        "completed_step_count": sum(step.status is PlanStepStatus.COMPLETED for step in steps),
        "failed_step_count": sum(step.status is PlanStepStatus.FAILED for step in steps),
        "current_step": current.objective if in_progress and current is not None else None,
    }


def _definition_names(db: Session, user_id: int, triggers: Sequence[models.RuntimeTrigger]) -> dict[int, str]:
    ids = {trigger.definition_id for trigger in triggers if trigger.definition_id is not None}
    if not ids:
        return {}
    rows = db.execute(
        select(models.TriggerDefinition.id, models.TriggerDefinition.name).where(
            models.TriggerDefinition.id.in_(ids),
            models.TriggerDefinition.user_id == user_id,
        )
    ).all()
    return {row_id: name for row_id, name in rows}


def _child_counts(db: Session, user_id: int, task_ids: Iterable[str]) -> dict[str, int]:
    wanted = list(dict.fromkeys(task_ids))
    if not wanted:
        return {}
    rows = db.execute(
        select(models.RuntimeDelegation.parent_task_id, func.count())
        .where(
            models.RuntimeDelegation.user_id == user_id,
            models.RuntimeDelegation.parent_task_id.in_(wanted),
        )
        .group_by(models.RuntimeDelegation.parent_task_id)
    ).all()
    return {parent: count for parent, count in rows}


def _trigger_summaries(
    db: Session, user_id: int, triggers: Sequence[models.RuntimeTrigger]
) -> dict[int, RuntimeTaskSummary]:
    states = _load_states(db, user_id, (trigger.task_id for trigger in triggers))
    names = _definition_names(db, user_id, triggers)
    children = _child_counts(db, user_id, (trigger.task_id for trigger in triggers))
    summaries: dict[int, RuntimeTaskSummary] = {}
    for trigger in triggers:
        state = states.get(trigger.task_id)
        # The live name while the definition exists (it may have been renamed),
        # the enqueue-time snapshot once it was deleted.
        live_name = names.get(trigger.definition_id) if trigger.definition_id is not None else None
        definition_name = live_name or trigger.definition_name
        objective = state.task.objective if state is not None else None
        summaries[trigger.id] = RuntimeTaskSummary.model_validate(
            {
                "source": "definition" if _is_definition_run(trigger) else "trigger",
                "ref_id": trigger.trigger_id,
                "task_id": trigger.task_id,
                "title": definition_name or _title_from(objective, trigger.source),
                "status": trigger.status,
                "created_at": trigger.created_at,
                "completed_at": trigger.completed_at,
                # Only a definition that still exists gets a link: SQLite does not
                # enforce the FK, so a deleted definition can leave a dangling id.
                "definition_id": trigger.definition_id if live_name is not None else None,
                "definition_name": definition_name,
                "child_count": children.get(trigger.task_id, 0),
                **_plan_fields(state),
            }
        )
    return summaries


def _delegation_summaries(
    db: Session, user_id: int, delegations: Sequence[models.RuntimeDelegation]
) -> dict[int, RuntimeTaskSummary]:
    states = _load_states(db, user_id, (delegation.child_task_id for delegation in delegations))
    return {
        delegation.id: RuntimeTaskSummary.model_validate(
            {
                "source": "delegated",
                "ref_id": delegation.delegation_id,
                "task_id": delegation.child_task_id,
                "title": _title_from(delegation.objective, "Delegated task"),
                "status": delegation.status,
                "created_at": delegation.created_at,
                "completed_at": delegation.completed_at,
                "conversation_id": delegation.conversation_id,
                **_plan_fields(states.get(delegation.child_task_id)),
            }
        )
        for delegation in delegations
    }


def list_tasks_for_user(
    db: Session,
    user_id: int,
    *,
    source: str | None = None,
    status: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> TaskListPage:
    """Return one newest-first page of the owner's trigger runs and delegated tasks."""
    if limit < 1 or offset < 0:
        raise ValueError("limit must be at least 1 and offset at least 0")
    if source is not None and source not in TASK_SOURCES:
        raise ValueError(f"unknown task source: {source}")
    # A delegation whose run outlived its lease would otherwise show as running forever.
    expire_stale_delegations(db, user_id)

    selects = []
    if source in (None, "trigger", "definition"):
        trigger_conditions = [models.RuntimeTrigger.user_id == user_id]
        if source == "trigger":
            trigger_conditions.append(_IS_AD_HOC_RUN)
        elif source == "definition":
            trigger_conditions.append(_IS_DEFINITION_RUN)
        if status is not None:
            trigger_conditions.append(models.RuntimeTrigger.status == status)
        selects.append(
            select(
                literal(_TRIGGER_KIND).label("kind"),
                models.RuntimeTrigger.id.label("row_id"),
                models.RuntimeTrigger.created_at.label("created_at"),
            ).where(*trigger_conditions)
        )
    if source in (None, "delegated"):
        delegation_conditions = [models.RuntimeDelegation.user_id == user_id]
        if status is not None:
            delegation_conditions.append(models.RuntimeDelegation.status == status)
        selects.append(
            select(
                literal(_DELEGATED_KIND).label("kind"),
                models.RuntimeDelegation.id.label("row_id"),
                models.RuntimeDelegation.created_at.label("created_at"),
            ).where(*delegation_conditions)
        )
    combined = union_all(*selects).subquery()
    total = db.scalar(select(func.count()).select_from(combined)) or 0
    page = db.execute(
        select(combined.c.kind, combined.c.row_id)
        .order_by(combined.c.created_at.desc(), combined.c.kind, combined.c.row_id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    trigger_ids = [row_id for kind, row_id in page if kind == _TRIGGER_KIND]
    delegation_ids = [row_id for kind, row_id in page if kind == _DELEGATED_KIND]
    triggers = (
        db.scalars(
            select(models.RuntimeTrigger).where(
                models.RuntimeTrigger.id.in_(trigger_ids), models.RuntimeTrigger.user_id == user_id
            )
        ).all()
        if trigger_ids
        else []
    )
    delegations = (
        db.scalars(
            select(models.RuntimeDelegation).where(
                models.RuntimeDelegation.id.in_(delegation_ids), models.RuntimeDelegation.user_id == user_id
            )
        ).all()
        if delegation_ids
        else []
    )
    by_trigger = _trigger_summaries(db, user_id, triggers)
    by_delegation = _delegation_summaries(db, user_id, delegations)
    items = [
        (by_trigger if kind == _TRIGGER_KIND else by_delegation)[row_id]
        for kind, row_id in page
        if row_id in (by_trigger if kind == _TRIGGER_KIND else by_delegation)
    ]
    return TaskListPage(items=items, total=total)


def _steps(state: PlanGraphState | None) -> list[RuntimeTaskStep]:
    if state is None or state.plan is None:
        return []
    steps = []
    for step in state.plan.steps:
        trace, output = parse_step_result(step.result)
        steps.append(
            RuntimeTaskStep(
                step_id=step.step_id,
                objective=step.objective,
                status=step.status.value,
                depends_on=list(step.depends_on),
                required_tool_ids=list(step.required_tool_ids),
                trace=trace,
                output=output,
                error=step.error,
            )
        )
    return steps


def _state_fields(state: PlanGraphState | None) -> dict[str, object]:
    if state is None:
        return {}
    return {
        "objective": state.task.objective,
        "task_mode": state.task.task_mode,
        "node": state.node.value,
        "reason": state.reason,
        "steps": _steps(state),
    }


def _owned_trigger_by_task(db: Session, user_id: int, task_id: str) -> models.RuntimeTrigger | None:
    """The owner's newest trigger run for ``task_id`` (agent-scheduled runs may share one)."""
    return db.scalar(
        select(models.RuntimeTrigger)
        .where(models.RuntimeTrigger.user_id == user_id, models.RuntimeTrigger.task_id == task_id)
        .order_by(models.RuntimeTrigger.created_at.desc(), models.RuntimeTrigger.id.desc())
        .limit(1)
    )


def get_trigger_task_for_user(db: Session, user_id: int, trigger_id: str) -> RuntimeTaskDetail | None:
    """Return one trigger run's task detail, or ``None`` when the owner has no such run."""
    trigger = db.scalar(
        select(models.RuntimeTrigger).where(
            models.RuntimeTrigger.trigger_id == trigger_id,
            models.RuntimeTrigger.user_id == user_id,
        )
    )
    if trigger is None:
        return None
    expire_stale_delegations(db, user_id)
    summary = _trigger_summaries(db, user_id, [trigger])[trigger.id]
    state = _load_states(db, user_id, [trigger.task_id]).get(trigger.task_id)
    children = db.scalars(
        select(models.RuntimeDelegation)
        .where(
            models.RuntimeDelegation.user_id == user_id,
            models.RuntimeDelegation.parent_task_id == trigger.task_id,
        )
        .order_by(models.RuntimeDelegation.created_at.asc(), models.RuntimeDelegation.id.asc())
        .limit(MAX_CHILDREN_LISTED)
    ).all()
    parent = None
    if trigger.parent_trigger_id:
        scheduler = db.scalar(
            select(models.RuntimeTrigger.trigger_id).where(
                models.RuntimeTrigger.trigger_id == trigger.parent_trigger_id,
                models.RuntimeTrigger.user_id == user_id,
            )
        )
        if scheduler is not None:
            parent = RuntimeTaskRef(kind="trigger", ref_id=scheduler)
    child_summaries = _delegation_summaries(db, user_id, children)
    return RuntimeTaskDetail.model_validate(
        {
            **summary.model_dump(),
            **_state_fields(state),
            "trigger_source": trigger.source,
            "attempt_count": trigger.attempt_count,
            "last_error": trigger.last_error,
            "parent": parent,
            "children": [child_summaries[child.id] for child in children],
        }
    )


def get_delegated_task_for_user(db: Session, user_id: int, delegation_id: str) -> RuntimeTaskDetail | None:
    """Return one delegated task's detail, or ``None`` when the owner has no such delegation."""
    expire_stale_delegations(db, user_id)
    delegation = db.scalar(
        select(models.RuntimeDelegation).where(
            models.RuntimeDelegation.delegation_id == delegation_id,
            models.RuntimeDelegation.user_id == user_id,
        )
    )
    if delegation is None:
        return None
    summary = _delegation_summaries(db, user_id, [delegation])[delegation.id]
    state = _load_states(db, user_id, [delegation.child_task_id]).get(delegation.child_task_id)
    parent_trigger = _owned_trigger_by_task(db, user_id, delegation.parent_task_id)
    return RuntimeTaskDetail.model_validate(
        {
            **summary.model_dump(),
            **_state_fields(state),
            # The delegation row is the source of truth for what the child was asked to do.
            "objective": delegation.objective,
            "task_mode": state.task.task_mode if state is not None else None,
            "allowed_tool_ids": list(delegation.allowed_tool_ids or []),
            "delegation_result": delegation.result,
            "delegation_error": delegation.error,
            "parent": RuntimeTaskRef(kind="trigger", ref_id=parent_trigger.trigger_id) if parent_trigger else None,
        }
    )


__all__ = [
    "MAX_CHILDREN_LISTED",
    "TASK_SOURCES",
    "TaskListPage",
    "get_delegated_task_for_user",
    "get_trigger_task_for_user",
    "list_tasks_for_user",
    "parse_step_result",
]
