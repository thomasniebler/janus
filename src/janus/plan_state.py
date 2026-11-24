"""Durable, owner-scoped storage for plan graph state snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from janus import models
from janus.plan_graph import (
    Plan,
    PlanGraphNode,
    PlanGraphState,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    TaskContext,
)

SessionFactory: TypeAlias = Callable[[], Session]  # noqa: UP040


class PlanStateError(ValueError):
    """Raised when a durable plan state cannot be validated or updated."""


class PlanStateConflictError(PlanStateError):
    """Raised when another worker has advanced the same task state."""


@dataclass(frozen=True)
class StoredPlanState:
    """A validated graph state plus its optimistic-concurrency revision."""

    state: PlanGraphState
    revision: int


@dataclass(frozen=True)
class PlanStepOutcome:
    """What one executed plan step produced, for read-only history surfaces."""

    step_id: str
    status: str
    result: str | None
    error: str | None


@dataclass(frozen=True)
class PlanOutcome:
    """A read-only summary of how a task's persisted plan actually ended.

    JAN-192 deliberately adds no new storage for "what did this automation do":
    :func:`serialize_plan_state` already persists each step's ``result`` and
    ``error`` inside ``PlanRuntimeState.state``, so history surfaces decode
    that rather than duplicating it into new columns.
    """

    task_id: str
    node: str
    plan_status: str | None
    steps: tuple[PlanStepOutcome, ...] = ()

    @property
    def result(self) -> str | None:
        """Return the last completed step's result, or ``None`` when nothing completed."""
        for step in reversed(self.steps):
            if step.result:
                return step.result
        return None

    @property
    def error(self) -> str | None:
        """Return the first recorded step failure, or ``None`` when nothing failed."""
        for step in self.steps:
            if step.error:
                return step.error
        return None


def summarize_plan_state(state: PlanGraphState) -> PlanOutcome:
    """Reduce a graph state to the read-only outcome shape history surfaces need."""
    plan = state.plan
    return PlanOutcome(
        task_id=state.task.task_id,
        node=state.node.value,
        plan_status=plan.status.value if plan is not None else None,
        steps=()
        if plan is None
        else tuple(
            PlanStepOutcome(
                step_id=step.step_id,
                status=step.status.value,
                result=step.result,
                error=step.error,
            )
            for step in plan.steps
        ),
    )


def _owner_id(user_id: int) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise PlanStateError("user_id must be a positive integer")
    return user_id


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanStateError(f"plan state {field} must be a non-blank string")
    return value


def _integer(value: object, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlanStateError(f"plan state {field} must be an integer of at least {minimum}")
    return value


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PlanStateError(f"plan state {field} must be a list of non-blank strings")
    return tuple(value)


def serialize_plan_state(state: PlanGraphState) -> dict[str, Any]:
    """Convert an immutable graph state into JSON-safe primitives."""
    plan = state.plan
    return {
        "task": {
            "task_id": state.task.task_id,
            "objective": state.task.objective,
            "task_mode": state.task.task_mode,
            "max_steps": state.task.max_steps,
        },
        "node": state.node.value,
        "current_step_id": state.current_step_id,
        "reason": state.reason,
        "plan": None
        if plan is None
        else {
            "plan_id": plan.plan_id,
            "task_id": plan.task_id,
            "objective": plan.objective,
            "version": plan.version,
            "status": plan.status.value,
            "steps": [
                {
                    "step_id": step.step_id,
                    "objective": step.objective,
                    "depends_on": list(step.depends_on),
                    "required_tool_ids": list(step.required_tool_ids),
                    "status": step.status.value,
                    "result": step.result,
                    "error": step.error,
                }
                for step in plan.steps
            ],
        },
    }


def deserialize_plan_state(payload: Mapping[str, Any]) -> PlanGraphState:
    """Validate JSON data and rebuild a graph state using domain constructors."""
    try:
        task_payload = payload["task"]
        if not isinstance(task_payload, Mapping):
            raise PlanStateError("plan state task must be an object")
        task = TaskContext(
            task_id=_text(task_payload.get("task_id"), "task.task_id"),
            objective=_text(task_payload.get("objective"), "task.objective"),
            task_mode=_text(task_payload.get("task_mode"), "task.task_mode"),
            max_steps=_integer(task_payload.get("max_steps"), "task.max_steps"),
        )
        node = PlanGraphNode(_text(payload.get("node"), "node"))
        current_step_id = payload.get("current_step_id")
        if current_step_id is not None:
            current_step_id = _text(current_step_id, "current_step_id")
        reason = payload.get("reason")
        if reason is not None:
            reason = _text(reason, "reason")

        plan_payload = payload.get("plan")
        plan = None
        if plan_payload is not None:
            if not isinstance(plan_payload, Mapping):
                raise PlanStateError("plan state plan must be an object or null")
            raw_steps = plan_payload.get("steps")
            if not isinstance(raw_steps, list):
                raise PlanStateError("plan state plan.steps must be a list")
            steps = tuple(
                PlanStep(
                    step_id=_text(raw_step.get("step_id"), "plan step_id"),
                    objective=_text(raw_step.get("objective"), "plan step objective"),
                    depends_on=_string_tuple(raw_step.get("depends_on", []), "plan depends_on"),
                    required_tool_ids=_string_tuple(raw_step.get("required_tool_ids", []), "plan required_tool_ids"),
                    status=PlanStepStatus(_text(raw_step.get("status"), "plan step status")),
                    result=raw_step.get("result"),
                    error=raw_step.get("error"),
                )
                for raw_step in raw_steps
                if isinstance(raw_step, Mapping)
            )
            if len(steps) != len(raw_steps):
                raise PlanStateError("plan state steps must be objects")
            plan = Plan(
                plan_id=_text(plan_payload.get("plan_id"), "plan_id"),
                task_id=_text(plan_payload.get("task_id"), "plan.task_id"),
                objective=_text(plan_payload.get("objective"), "plan.objective"),
                version=_integer(plan_payload.get("version"), "plan.version"),
                status=PlanStatus(_text(plan_payload.get("status"), "plan.status")),
                steps=steps,
            )
            if plan.task_id != task.task_id:
                raise PlanStateError("plan state plan.task_id does not match task.task_id")
        return PlanGraphState(
            task=task,
            node=node,
            plan=plan,
            current_step_id=current_step_id,
            reason=reason,
        )
    except PlanStateError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PlanStateError("invalid durable plan state") from exc


def load_plan_outcomes(db: Session, user_id: int, task_ids: Sequence[str]) -> dict[str, PlanOutcome]:
    """Load one owner's plan outcomes for several task IDs in a single query.

    Used by the trigger-history surfaces (the ``list_my_trigger_history`` tool
    and ``GET /api/runtime/triggers/``), which list many triggers at once and
    must not issue a query per row. A row whose persisted state no longer
    validates is skipped rather than failing the whole listing: history is a
    read-only observability surface, and one unreadable snapshot must not hide
    every other trigger's outcome.
    """
    _owner_id(user_id)
    wanted = [task_id for task_id in dict.fromkeys(task_ids) if isinstance(task_id, str) and task_id.strip()]
    if not wanted:
        return {}
    records = db.scalars(
        select(models.PlanRuntimeState).where(
            models.PlanRuntimeState.user_id == user_id,
            models.PlanRuntimeState.task_id.in_(wanted),
        )
    ).all()
    outcomes: dict[str, PlanOutcome] = {}
    for record in records:
        try:
            outcomes[record.task_id] = summarize_plan_state(deserialize_plan_state(record.state))
        except PlanStateError:
            continue
    return outcomes


class PlanStateStore:
    """Persist graph snapshots without exposing state across user boundaries."""

    def __init__(self, session_factory: SessionFactory) -> None:
        """Create a store around a caller-owned SQLAlchemy session factory."""
        self._session_factory = session_factory

    def load(self, db: Session, user_id: int, task_id: str) -> StoredPlanState | None:
        """Load one user's task state, returning no data for another owner."""
        _owner_id(user_id)
        task_id = _text(task_id, "task_id")
        record = db.scalar(
            select(models.PlanRuntimeState).where(
                models.PlanRuntimeState.user_id == user_id,
                models.PlanRuntimeState.task_id == task_id,
            )
        )
        if record is None:
            return None
        return StoredPlanState(deserialize_plan_state(record.state), record.revision)

    def save(
        self,
        db: Session,
        user_id: int,
        state: PlanGraphState,
        *,
        expected_revision: int | None = None,
    ) -> StoredPlanState:
        """Insert or atomically advance a task state snapshot."""
        _owner_id(user_id)
        serialized = serialize_plan_state(state)
        existing = db.scalar(
            select(models.PlanRuntimeState).where(
                models.PlanRuntimeState.user_id == user_id,
                models.PlanRuntimeState.task_id == state.task.task_id,
            )
        )
        if existing is None:
            if expected_revision is not None:
                raise PlanStateConflictError("plan state does not exist at the expected revision")
            record = models.PlanRuntimeState(
                user_id=user_id,
                task_id=state.task.task_id,
                node=state.node.value,
                plan_status=state.plan.status.value if state.plan else None,
                revision=1,
                state=serialized,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                raise PlanStateConflictError("plan state was created by another worker") from exc
            return StoredPlanState(state, 1)

        if expected_revision is None:
            expected_revision = existing.revision
        next_revision = expected_revision + 1
        result = db.execute(
            update(models.PlanRuntimeState)
            .where(
                models.PlanRuntimeState.id == existing.id,
                models.PlanRuntimeState.revision == expected_revision,
            )
            .values(
                node=state.node.value,
                plan_status=state.plan.status.value if state.plan else None,
                revision=next_revision,
                state=serialized,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise PlanStateConflictError("plan state revision changed before it could be saved")
        db.commit()
        return StoredPlanState(state, next_revision)

    def load_for_owner(self, user_id: int, task_id: str) -> StoredPlanState | None:
        """Load state using a short-lived session for handler integrations."""
        db = self._session_factory()
        try:
            return self.load(db, user_id, task_id)
        finally:
            db.close()

    def save_for_owner(
        self,
        user_id: int,
        state: PlanGraphState,
        *,
        expected_revision: int | None = None,
    ) -> StoredPlanState:
        """Save state using a short-lived session for handler integrations."""
        db = self._session_factory()
        try:
            return self.save(db, user_id, state, expected_revision=expected_revision)
        finally:
            db.close()


__all__ = [
    "PlanOutcome",
    "PlanStateConflictError",
    "PlanStateError",
    "PlanStateStore",
    "PlanStepOutcome",
    "StoredPlanState",
    "deserialize_plan_state",
    "load_plan_outcomes",
    "serialize_plan_state",
    "summarize_plan_state",
]
