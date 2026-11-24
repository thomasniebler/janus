"""Schemas for the system status overview API."""

from datetime import datetime

from pydantic import BaseModel


class StatusComponent(BaseModel):
    """A single monitored component within a top-level service."""

    id: str
    name: str
    status: str
    message: str
    updated_at: datetime | None = None
    subtitle: str | None = None


class StatusService(BaseModel):
    """A top-level service group shown on the status page."""

    id: str
    name: str
    status: str
    summary: str
    updated_at: datetime | None = None
    components: list[StatusComponent]


class ReleaseChangeGroup(BaseModel):
    """One heading of a release's notes (Features, Bug Fixes, ...)."""

    title: str
    items: list[str]


class ReleaseNotes(BaseModel):
    """What one release changed."""

    version: str
    date: str | None = None
    groups: list[ReleaseChangeGroup]


class VersionInfo(BaseModel):
    """The running Janus version and the releases the caller has not seen yet."""

    version: str
    changelog_url: str
    release_url: str
    releases: list[ReleaseNotes]


class StatusIncident(BaseModel):
    """A recent service incident or recovery."""

    id: int
    timestamp: datetime
    status: str
    title: str
    message: str
    service_id: str
    service_name: str
    component_name: str | None = None


class StatusOverview(BaseModel):
    """The full payload used by the status overview page."""

    overall_status: str
    overall_message: str
    generated_at: datetime
    services: list[StatusService]
    recent_incidents: list[StatusIncident]
