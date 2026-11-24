"""REST API router for agent memory management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services.memory_service import MemoryFactNotFoundError, MemoryService

router = APIRouter(prefix="/api/memory", tags=["memory"])


class AgentMemoryFactResponse(BaseModel):
    """Serialized response for a stored memory fact."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    summary: str
    detail: str | None
    source: str
    status: str
    source_conversation_id: int | None
    compacted_into_id: int | None
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime | None


class AgentMemoryFactCreate(BaseModel):
    """Request schema for creating a user-authored fact."""

    summary: str
    detail: str | None = None

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, v: str) -> str:
        """Reject summaries that are empty or contain only whitespace."""
        if not v.strip():
            raise ValueError("summary must not be blank")
        return v


class AgentMemoryFactUpdate(BaseModel):
    """Request schema for editing a fact's summary and/or detail (JAN-224).

    An omitted field is left unchanged; an empty ``detail`` clears it.
    """

    summary: str | None = None
    detail: str | None = None

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_blank(cls, v: str | None) -> str | None:
        """Reject a summary that is empty or contains only whitespace."""
        if v is not None and not v.strip():
            raise ValueError("summary must not be blank")
        return v


class AgentMemoryCompactionRunResponse(BaseModel):
    """Serialized response for a compaction run."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    result_summary: str | None
    error_message: str | None
    facts_considered: int
    facts_archived: int
    facts_created: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@router.get("/", response_model=list[AgentMemoryFactResponse])
def list_memory_facts(
    query: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    source: models.AgentMemorySource | None = Query(default=None, description="Only facts from this origin"),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List or search memory facts for the authenticated user."""
    service = MemoryService(db)
    return service.list_facts(user.id, query=query, include_archived=include_archived, source=source, limit=limit)


@router.post("/", response_model=AgentMemoryFactResponse, status_code=status.HTTP_201_CREATED)
def create_memory_fact(
    payload: AgentMemoryFactCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a new user-authored memory fact."""
    service = MemoryService(db)
    try:
        return service.create_fact(
            user_id=user.id,
            summary=payload.summary,
            detail=payload.detail,
            source=models.AgentMemorySource.USER,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/compact/latest", response_model=AgentMemoryCompactionRunResponse | None)
def get_latest_compaction_run(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the user's most recent compaction run, if any."""
    service = MemoryService(db)
    return service.get_latest_compaction_run(user.id)


@router.post("/compact", response_model=AgentMemoryCompactionRunResponse, status_code=status.HTTP_202_ACCEPTED)
def compact_memory(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Start an on-demand background memory compaction run."""
    service = MemoryService(db)
    run, _created = service.start_compaction(user.id)
    return run


@router.patch("/{fact_id}", response_model=AgentMemoryFactResponse)
def update_memory_fact(
    fact_id: int,
    payload: AgentMemoryFactUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Edit the summary and/or detail of a memory fact owned by the authenticated user."""
    service = MemoryService(db)
    try:
        return service.update_fact(user_id=user.id, fact_id=fact_id, summary=payload.summary, detail=payload.detail)
    except MemoryFactNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory fact not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.delete("/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory_fact(
    fact_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Delete a memory fact owned by the authenticated user."""
    service = MemoryService(db)
    if not service.delete_fact(user_id=user.id, fact_id=fact_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory fact not found")
