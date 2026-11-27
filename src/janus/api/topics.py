"""REST API router for structured topic tracking."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import topics as topic_schemas
from janus.services.topic_service import TOPIC_UPDATE_UNSET, TopicService

router = APIRouter(prefix="/api/topics", tags=["topics"])


def _parse_status(raw_status: str | None) -> models.TopicStatus | None:
    if raw_status is None:
        return None
    try:
        return models.TopicStatus(raw_status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be one of: candidate, active, snoozed, resolved, ignored",
        ) from exc


@router.get("/", response_model=list[topic_schemas.TopicSummary])
def list_topics(
    query: str | None = Query(default=None),
    status_value: str | None = Query(alias="status", default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List tracked topics for the authenticated user."""
    service = TopicService(db)
    return service.list_topics(user.id, query=query, status=_parse_status(status_value), limit=limit)


@router.get("/{topic_id}", response_model=topic_schemas.TopicDetail)
def get_topic_detail(
    topic_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one topic plus supporting evidence."""
    service = TopicService(db)
    try:
        return service.get_topic_detail(user.id, topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/", response_model=topic_schemas.TopicSummary, status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: topic_schemas.TopicCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create one manual tracked topic."""
    service = TopicService(db)
    try:
        topic = service.create_topic(
            user_id=user.id,
            title=payload.title,
            summary=payload.summary,
            marker_keywords=payload.marker_keywords,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return service.serialize_topic_summary(topic)


@router.patch("/{topic_id}", response_model=topic_schemas.TopicSummary)
def update_topic(
    topic_id: int,
    payload: topic_schemas.TopicUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update one tracked topic."""
    service = TopicService(db)
    try:
        topic = service.update_topic(
            user_id=user.id,
            topic_id=topic_id,
            title=payload.title if payload.includes("title") else TOPIC_UPDATE_UNSET,
            summary=payload.summary if payload.includes("summary") else TOPIC_UPDATE_UNSET,
            marker_keywords=payload.marker_keywords if payload.includes("marker_keywords") else TOPIC_UPDATE_UNSET,
            status=payload.status if payload.includes("status") else TOPIC_UPDATE_UNSET,
            pinned=payload.pinned if payload.includes("pinned") else TOPIC_UPDATE_UNSET,
            snoozed_until=payload.snoozed_until if payload.includes("snoozed_until") else TOPIC_UPDATE_UNSET,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = status.HTTP_404_NOT_FOUND if "not found" in message else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status_code, detail=message) from exc
    return service.serialize_topic_summary(topic)


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    topic_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Delete a tracked topic owned by the authenticated user."""
    service = TopicService(db)
    if not service.delete_topic(user_id=user.id, topic_id=topic_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
