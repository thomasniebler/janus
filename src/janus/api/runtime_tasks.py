"""Read-only Tasks endpoints: persisted plan-runtime task state (JAN-204)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas.runtime_tasks import RuntimeTaskDetail, RuntimeTaskPage, RuntimeTaskStatus
from janus.services import runtime_task_service

router = APIRouter(prefix="/api/runtime/tasks", tags=["runtime-tasks"])

_NOT_FOUND = "task not found"


@router.get("/", response_model=RuntimeTaskPage)
def list_runtime_tasks(
    source: Literal["trigger", "definition", "delegated"] | None = Query(
        default=None,
        description="'trigger' (ad-hoc runs), 'definition' (runs of a saved definition) or 'delegated'.",
    ),
    status_filter: RuntimeTaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one newest-first page of the user's trigger runs and delegated tasks."""
    page = runtime_task_service.list_tasks_for_user(
        db, user.id, source=source, status=status_filter, limit=limit, offset=offset
    )
    return RuntimeTaskPage(
        items=page.items,
        total=page.total,
        limit=limit,
        offset=offset,
        has_more=offset + len(page.items) < page.total,
    )


@router.get("/triggers/{trigger_id}", response_model=RuntimeTaskDetail)
def get_trigger_task(
    trigger_id: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one trigger run's plan steps and tool trace; another user's run is a 404."""
    detail = runtime_task_service.get_trigger_task_for_user(db, user.id, trigger_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return detail


@router.get("/delegations/{delegation_id}", response_model=RuntimeTaskDetail)
def get_delegated_task(
    delegation_id: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one delegated task's plan steps and tool trace; another user's task is a 404."""
    detail = runtime_task_service.get_delegated_task_for_user(db, user.id, delegation_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return detail
