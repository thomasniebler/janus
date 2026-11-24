"""Authenticated runtime-trigger ingress and status endpoints."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from janus import database, models, security, startup
from janus.api.trigger_definitions import with_outcomes
from janus.schemas.runtime_triggers import (
    RuntimeTriggerCreate,
    RuntimeTriggerPage,
    RuntimeTriggerResponse,
    RuntimeTriggerWorkerStatus,
)
from janus.time_utils import to_naive_utc
from janus.trigger_adapters import EventTriggerSpec, ScheduledTriggerSpec
from janus.trigger_service import enqueue_trigger
from janus.triggers import TriggerEnvelope, TriggerKind

router = APIRouter(prefix="/api/runtime/triggers", tags=["runtime-triggers"])


@router.post("/", response_model=RuntimeTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
def create_runtime_trigger(
    payload: RuntimeTriggerCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Enqueue a user-owned trigger without accepting tool policy from the client."""
    runtime_context = {
        "objective": payload.objective,
        "task_mode": payload.task_mode,
        "max_steps": payload.max_steps,
    }
    if payload.kind is models.RuntimeTriggerKind.SCHEDULED:
        assert payload.schedule_id is not None
        assert payload.scheduled_for is not None
        trigger = ScheduledTriggerSpec(
            schedule_id=payload.schedule_id,
            owner_id=user.id,
            task_id=payload.task_id,
            due_at=payload.scheduled_for,
            source=payload.source,
            **runtime_context,
        ).as_envelope()
    elif payload.kind is models.RuntimeTriggerKind.EVENT:
        assert payload.event_id is not None
        assert payload.occurred_at is not None
        trigger = EventTriggerSpec(
            event_id=payload.event_id,
            owner_id=user.id,
            task_id=payload.task_id,
            occurred_at=payload.occurred_at,
            source=payload.source,
            **runtime_context,
        ).as_envelope()
    else:
        trigger = TriggerEnvelope(
            trigger_id=str(uuid4()),
            idempotency_key=payload.idempotency_key or "",
            task_id=payload.task_id,
            kind=TriggerKind.MANUAL,
            source=payload.source,
            payload=runtime_context,
            owner_id=user.id,
        )
    record, _duplicate = enqueue_trigger(db, trigger)
    if record.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency key is already used by another owner",
        )
    return record


#: ``definition_id`` filter value selecting runs that have no definition.
AD_HOC_DEFINITION_FILTER = "none"


@router.get("/", response_model=RuntimeTriggerPage)
def list_runtime_triggers(
    status_filter: models.RuntimeTriggerStatus | None = Query(default=None, alias="status"),
    kind: models.RuntimeTriggerKind | None = Query(default=None),
    definition_id: str | None = Query(
        default=None,
        pattern=r"^(\d+|none)$",
        description="A definition id, or 'none' for ad-hoc runs without a definition.",
    ),
    start: datetime | None = Query(default=None, description="Inclusive lower created_at bound (UTC if naive)."),
    end: datetime | None = Query(default=None, description="Exclusive upper created_at bound (UTC if naive)."),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one newest-first page of the user's trigger runs, with outcomes (JAN-256)."""
    if start is not None and end is not None and to_naive_utc(start) >= to_naive_utc(end):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="start must be before end")
    conditions = [models.RuntimeTrigger.user_id == user.id]
    if status_filter is not None:
        conditions.append(models.RuntimeTrigger.status == status_filter.value)
    if kind is not None:
        conditions.append(models.RuntimeTrigger.kind == kind.value)
    if definition_id == AD_HOC_DEFINITION_FILTER:
        conditions.append(models.RuntimeTrigger.definition_id.is_(None))
    elif definition_id is not None:
        conditions.append(models.RuntimeTrigger.definition_id == int(definition_id))
    if start is not None:
        conditions.append(models.RuntimeTrigger.created_at >= to_naive_utc(start))
    if end is not None:
        conditions.append(models.RuntimeTrigger.created_at < to_naive_utc(end))
    total = db.scalar(select(func.count()).select_from(models.RuntimeTrigger).where(*conditions)) or 0
    records = list(
        db.scalars(
            select(models.RuntimeTrigger)
            .where(*conditions)
            .order_by(models.RuntimeTrigger.created_at.desc(), models.RuntimeTrigger.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    # One batched plan-state lookup for the page; the trigger row itself holds
    # only lifecycle columns, and what the run produced lives in the durable
    # plan state keyed by the same owner + task_id (JAN-192).
    return RuntimeTriggerPage(
        items=with_outcomes(db, user.id, records),
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(records) < total,
    )


@router.get("/worker", response_model=RuntimeTriggerWorkerStatus)
def get_runtime_trigger_worker_status(
    _user: models.User = Depends(security.get_current_user),
):
    """Report whether the trigger worker is enabled and running in this process."""
    return startup.runtime_trigger_worker_status()
