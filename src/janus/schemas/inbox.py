"""Schemas for the action inbox and device tokens."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

InboxKind = Literal["conversation", "planner_suggestion", "topic_suggestion", "email_clarification", "trigger_proposal"]
InboxAction = Literal["mark_read", "dismiss"]


def _as_utc(value: datetime | None) -> datetime | None:
    """Stored datetimes are naive UTC; serialize them with an explicit offset (JAN-394)."""
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class InboxItem(BaseModel):
    """One thing waiting for the owner. ``title`` and ``summary`` are untrusted plain text: render them as text."""

    kind: InboxKind
    id: int
    title: str
    summary: str
    created_at: datetime
    #: ``low``: the listed ``actions`` are safe from a device token. ``needs_web``: review it in the web app.
    risk: Literal["low", "needs_web"]
    actions: list[InboxAction]
    #: An app path (never an external URL) where the owner reviews the item.
    open_url: str

    _utc = field_validator("created_at")(_as_utc)


class InboxResponse(BaseModel):
    """The owner's open inbox, newest first."""

    items: list[InboxItem]


class DeviceTokenCreate(BaseModel):
    """Create a device token."""

    name: str = Field(min_length=1, max_length=80)


class DeviceTokenInfo(BaseModel):
    """A device token without its secret."""

    id: int
    name: str
    token_prefix: str
    created_at: datetime
    last_used_at: datetime | None = None

    model_config = {"from_attributes": True}

    _utc = field_validator("created_at", "last_used_at")(_as_utc)


class DeviceTokenCreated(DeviceTokenInfo):
    """A freshly created token: ``token`` is shown once and cannot be read again."""

    token: str
