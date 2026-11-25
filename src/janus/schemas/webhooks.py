"""Pydantic contracts for inbound webhook endpoints (JAN-406)."""

from datetime import datetime

from pydantic import BaseModel, Field


class WebhookCreate(BaseModel):
    """Create a webhook endpoint."""

    name: str = Field(min_length=1, max_length=80)


class WebhookUpdate(BaseModel):
    """Rename or switch a webhook on/off."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None


class WebhookResponse(BaseModel):
    """A webhook endpoint; its full URL and secret are never returned after creation."""

    id: int
    name: str
    key_prefix: str
    enabled: bool
    received_count: int
    last_received_at: datetime | None = None
    created_at: datetime


class WebhookSecretResponse(BaseModel):
    """Returned once, at creation (``path`` and ``secret``) or after a rotation (``secret`` only)."""

    webhook: WebhookResponse
    path: str | None = None
    secret: str


class WebhookDeliveryResponse(BaseModel):
    """What an accepted delivery started."""

    accepted: bool
    runs: int
