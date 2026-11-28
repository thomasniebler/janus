"""Web search integration settings API (JAN-211)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import web_search_service as ws

router = APIRouter(prefix="/api/web-search", tags=["web-search"])


class WebSearchSettingsResponse(BaseModel):
    """Public web search settings (never the raw API key)."""

    provider: str | None = None
    api_key_configured: bool
    base_url: str | None = None
    #: Model for the provider-native backends; ``None`` = that provider's default.
    model: str | None = None
    default_models: dict[str, str] = Field(default_factory=dict)
    #: Whether each native provider already has the chat API key it reuses.
    provider_keys: dict[str, bool] = Field(default_factory=dict)
    configured: bool


class WebSearchSettingsUpdate(BaseModel):
    """Partial update; ``""`` clears a field, omitted/``None`` leaves it unchanged."""

    provider: Literal["brave", "searxng", "openai", "gemini", "anthropic", "openrouter", ""] | None = None
    api_key: str | None = None
    base_url: str | None = None
    #: Model for the provider-native backends; ``""`` = the provider's default.
    model: str | None = None


@router.get("/settings", response_model=WebSearchSettingsResponse)
def get_web_search_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the current web search settings."""
    return ws.describe_settings(ws.get_or_create_settings(db))


@router.patch("/settings", response_model=WebSearchSettingsResponse)
def update_web_search_settings(
    payload: WebSearchSettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Update web search settings. Admin-only: this is an instance-wide outbound integration."""
    settings = ws.get_or_create_settings(db)
    if payload.base_url is not None:
        try:
            settings.web_search_base_url = ws.normalize_base_url(payload.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if payload.provider is not None:
        new_provider = payload.provider or None
        if new_provider != settings.web_search_provider and payload.model is None:
            # A model belongs to its provider: switching provider falls back to the default.
            settings.web_search_model = None
        settings.web_search_provider = new_provider
    if payload.model is not None:
        settings.web_search_model = ws.normalize_model(payload.model)
    if payload.api_key is not None:
        settings.web_search_api_key = (
            security.security_service.encrypt(payload.api_key.strip()) if payload.api_key.strip() else None
        )
    db.commit()
    db.refresh(settings)
    return ws.describe_settings(settings)
