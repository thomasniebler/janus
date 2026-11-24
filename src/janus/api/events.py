"""Authenticated, filterable read access to the system event log.

Admins see the whole log; every other user sees only the rows recorded as
theirs (JAN-272; see ``services.event_service.visible_events_condition``).
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas.events import SystemEventPage, SystemEventResponse
from janus.services.event_service import EventService

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=SystemEventPage)
def list_events(
    event_type: models.EventType | None = Query(default=None),
    source: str | None = Query(default=None, min_length=1, max_length=200),
    start: datetime | None = Query(default=None, description="Inclusive lower timestamp bound (UTC if naive)."),
    end: datetime | None = Query(default=None, description="Exclusive upper timestamp bound (UTC if naive)."),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one newest-first page of the events this user may read, matching the given filters.

    An admin reads the whole log, system rows included; any other user reads
    only events recorded as theirs. Timestamps are stored as UTC, so
    timezone-aware bounds are converted to UTC before comparison.
    """
    try:
        events, total = EventService(db).list_events(
            user,
            event_type=event_type,
            source=source,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))  # noqa: B904

    return SystemEventPage(
        items=[SystemEventResponse.model_validate(event) for event in events],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(events) < total,
    )


@router.get("/sources", response_model=list[str])
def list_event_sources(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the distinct sources of the events this user may read, alphabetically, for filter controls."""
    return EventService(db).list_sources(user)
