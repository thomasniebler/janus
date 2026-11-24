"""Self-evolution loop settings and audit API (JAN-320).

``GET`` returns the caller's own settings (never the token or header values),
the daily caps, today's usage, the scope fence and the recent actions -- the
humans-observe view. ``PATCH`` is admin-only: the loop acts with the
instance-wide GitHub and Linear credentials, so only an administrator may
configure or switch it on, and only for themselves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import self_evolution_service as service

router = APIRouter(prefix="/api/self-evolution", tags=["self-evolution"])


class SelfEvolutionAction(BaseModel):
    """One recorded action of the loop."""

    id: int
    action: str
    status: str
    subject: str | None = None
    detail: str | None = None
    data: dict
    created_at: str | None = None


class SelfEvolutionSettingsResponse(BaseModel):
    """Public settings (never the token), caps, usage and audit trail."""

    enabled: bool
    routine_trigger_id: str | None = None
    routine_token_configured: bool
    github_repo: str | None = None
    production_base_url: str | None = None
    daily_caps: dict[str, int]
    usage_last_24h: dict[str, int]
    scope_fence: list[str]
    recent_actions: list[SelfEvolutionAction]


class SelfEvolutionSettingsUpdate(BaseModel):
    """Partial update; omitted/``None`` leaves a field alone, ``""`` (or ``{}``) clears it."""

    enabled: bool | None = None
    routine_trigger_id: str | None = None
    routine_token: str | None = None
    github_repo: str | None = None
    production_base_url: str | None = None


@router.get("/settings", response_model=SelfEvolutionSettingsResponse)
def get_self_evolution_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the caller's self-evolution settings and recent actions."""
    return service.describe_settings(db, user)


@router.patch("/settings", response_model=SelfEvolutionSettingsResponse)
def update_self_evolution_settings(
    payload: SelfEvolutionSettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Update the caller's self-evolution settings (admin-only)."""
    try:
        service.update_settings(db, user, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return service.describe_settings(db, user)
