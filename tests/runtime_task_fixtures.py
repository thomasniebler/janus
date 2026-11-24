"""Seed helpers for the Tasks page tests (JAN-204): trigger runs, delegations and plan state."""

from __future__ import annotations

from datetime import datetime, timedelta

from janus import models
from janus.plan_graph import Plan, PlanGraphNode, PlanGraphState, PlanStatus, PlanStep, PlanStepStatus, TaskContext
from janus.plan_state import PlanStateStore
from janus.time_utils import utcnow
from janus.unattended_execution import format_step_result

BASE_TIME = datetime(2026, 10, 1, 9, 0, 0)


def step_result(answer: str = "Inbox reviewed.") -> str:
    """A step result exactly as the unattended runtime records it."""
    return format_step_result(
        answer,
        authorized_tool_ids=["mail.read", "calendar.read"],
        invoked_tools=["search_emails"],
        skipped=(),
        model="local/test-model",
    )


def save_plan(
    db,
    user_id: int,
    task_id: str,
    objective: str,
    *,
    step_status: PlanStepStatus = PlanStepStatus.COMPLETED,
    result: str | None = None,
    error: str | None = None,
    task_mode: str = "trigger",
) -> None:
    """Persist a one-step plan snapshot for ``task_id``."""
    done = step_status is PlanStepStatus.COMPLETED
    failed = step_status is PlanStepStatus.FAILED
    plan = Plan(
        plan_id=f"plan-{task_id}",
        task_id=task_id,
        objective=objective,
        status=PlanStatus.COMPLETED if done else PlanStatus.FAILED if failed else PlanStatus.ACTIVE,
        steps=(
            PlanStep(
                step_id="step-1",
                objective=objective,
                required_tool_ids=("mail.read",),
                status=step_status,
                result=result if result is not None else (step_result() if done else None),
                error=error,
            ),
        ),
    )
    node = PlanGraphNode.COMPLETE if done else PlanGraphNode.FAILED if failed else PlanGraphNode.EXECUTE_STEP
    state = PlanGraphState(
        task=TaskContext(task_id=task_id, objective=objective, task_mode=task_mode, max_steps=4),
        node=node,
        plan=plan,
        current_step_id="step-1",
        reason="step failed" if failed else None,
    )
    PlanStateStore(lambda: db).save(db, user_id, state)


def add_trigger(
    db,
    user_id: int,
    trigger_id: str,
    *,
    task_id: str | None = None,
    status: str = "completed",
    minutes: int = 0,
    definition_id: int | None = None,
    parent_trigger_id: str | None = None,
) -> models.RuntimeTrigger:
    """Insert one trigger run created ``minutes`` after ``BASE_TIME``."""
    record = models.RuntimeTrigger(
        user_id=user_id,
        trigger_id=trigger_id,
        idempotency_key=f"key-{trigger_id}",
        task_id=task_id or f"task-{trigger_id}",
        kind="manual",
        source="settings",
        payload={"objective": "secret payload"},
        status=status,
        definition_id=definition_id,
        parent_trigger_id=parent_trigger_id,
        attempt_count=1,
        created_at=BASE_TIME + timedelta(minutes=minutes),
    )
    db.add(record)
    db.commit()
    return record


def add_delegation(
    db,
    user_id: int,
    delegation_id: str,
    *,
    parent_task_id: str,
    status: str = "completed",
    minutes: int = 0,
    conversation_id: int | None = None,
    result: str | None = "Child summary.",
) -> models.RuntimeDelegation:
    """Insert one delegated child task; active ones are created now so they are within their lease."""
    active = status in ("queued", "running")
    record = models.RuntimeDelegation(
        delegation_id=delegation_id,
        user_id=user_id,
        parent_task_id=parent_task_id,
        child_task_id=f"child-{delegation_id}",
        conversation_id=conversation_id,
        objective=f"Look up {delegation_id}",
        requested_tool_ids=["mail.read"],
        allowed_tool_ids=["mail.read"],
        status=status,
        result=None if active else result,
        created_at=utcnow() if active else BASE_TIME + timedelta(minutes=minutes),
    )
    db.add(record)
    db.commit()
    return record


def add_definition(db, user_id: int, name: str = "Weekly review") -> models.TriggerDefinition:
    """Insert one manual trigger definition."""
    definition = models.TriggerDefinition(user_id=user_id, name=name, objective="Review the week", tool_ids=[])
    db.add(definition)
    db.commit()
    return definition
