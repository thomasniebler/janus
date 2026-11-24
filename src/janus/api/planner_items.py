"""Deleting Janus-owned planner tasks and events (JAN-221).

Kept apart from ``planner.py`` (a baselined size hotspot); same ``/api/planner``
prefix, so the routes sit next to the local task and event CRUD.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import planner_item_deletion

router = APIRouter(prefix="/api/planner", tags=["planner"])

_DELETERS = {"tasks": planner_item_deletion.delete_local_task, "events": planner_item_deletion.delete_local_event}


@router.delete("/local/{kind}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_local_item(
    kind: Literal["tasks", "events"],
    item_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> None:
    """Delete one of the caller's local tasks (its subtasks move up a level) or events; 404 otherwise."""
    if not _DELETERS[kind](db, user.id, item_id):
        noun = "task" if kind == "tasks" else "event"
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Local planner {noun} not found.")
