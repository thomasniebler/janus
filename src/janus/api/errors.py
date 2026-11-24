"""REST API router for error group management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from janus import database, security
from janus.services.error_service import ErrorService

router = APIRouter(
    prefix="/api/errors",
    tags=["errors"],
    dependencies=[Depends(security.admin_required)],
)


class ErrorGroupResponse(BaseModel):
    """Serialized response for an error group."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fingerprint: str
    source: str
    message_template: str
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    sample_details: str | None
    last_compacted_at: datetime | None
    created_at: datetime


class CompactionResult(BaseModel):
    """Response schema for compaction operation."""

    events_processed: int
    groups_updated: int
    events_deleted: int
    groups_deleted: int


@router.get("/groups", response_model=list[ErrorGroupResponse])
def list_error_groups(
    limit: int = Query(100, ge=1, le=500),
    source: str | None = Query(None),
    db: Session = Depends(database.get_db),
):
    """List error groups, most recently seen first."""
    service = ErrorService(db)
    groups = service.list_error_groups(limit=limit, source=source)
    return groups


@router.get("/groups/{group_id}", response_model=ErrorGroupResponse)
def get_error_group(
    group_id: int,
    db: Session = Depends(database.get_db),
):
    """Get details of a specific error group."""
    service = ErrorService(db)
    group = service.get_error_group(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error group not found")
    return group


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_error_group(
    group_id: int,
    db: Session = Depends(database.get_db),
):
    """Delete an error group. Requires admin privileges."""
    service = ErrorService(db)
    deleted = service.delete_error_group(group_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error group not found")


@router.post("/compact", response_model=CompactionResult)
def compact_errors(
    retention_days: int = Query(ErrorService.DEFAULT_RETENTION_DAYS, ge=1, le=365),
    db: Session = Depends(database.get_db),
):
    """Manually trigger error compaction. Requires admin privileges."""
    service = ErrorService(db)
    result = service.compact_errors(retention_days=retention_days)
    return result
