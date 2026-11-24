"""Schemas for the system event log API."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from janus.models import EventType


class SystemEventResponse(BaseModel):
    """One row of the system event log."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime | None = None
    event_type: EventType | None = None
    source: str | None = None
    message: str | None = None
    details: str | None = None


class SystemEventPage(BaseModel):
    """One filtered, newest-first page of system events plus pagination metadata."""

    items: list[SystemEventResponse]
    total: int
    limit: int
    offset: int
    has_more: bool
