"""GitHub integration API router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services.github_service import GitHubService

router = APIRouter(prefix="/api/github", tags=["github"])


def _ensure_settings(db: Session) -> models.Settings:
    """Returns the singleton settings row, creating it if needed."""
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _get_github_service(db: Session) -> GitHubService:
    """Resolve the stored GitHub token and return a service wrapper."""
    settings = _ensure_settings(db)
    if not settings.github_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="GitHub token is not configured. Add it in Settings → Connections → Developer.",
        )
    token = security.security_service.decrypt(settings.github_token)
    return GitHubService(token=token)


class GitHubSettingsResponse(BaseModel):
    """Public GitHub settings (no raw token)."""

    token_configured: bool
    default_repo: str | None = None


class GitHubSettingsUpdate(BaseModel):
    """Payload for updating GitHub settings."""

    token: str | None = None
    default_repo: str | None = None


class GitHubIssueCreate(BaseModel):
    """Payload for creating a GitHub issue."""

    repo: str | None = None
    title: str
    body: str
    labels: list[str] | None = None


class GitHubIssueResponse(BaseModel):
    """Minimal GitHub issue response."""

    number: int
    title: str
    url: str
    repo: str


@router.get("/settings", response_model=GitHubSettingsResponse)
def get_github_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns the current GitHub integration settings."""
    settings = _ensure_settings(db)
    return GitHubSettingsResponse(
        token_configured=bool(settings.github_token),
        default_repo=settings.github_default_repo,
    )


@router.patch("/settings", response_model=GitHubSettingsResponse)
def update_github_settings(
    payload: GitHubSettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Updates the instance-wide GitHub integration settings (admin only, JAN-222)."""
    settings = _ensure_settings(db)
    if payload.token is not None:
        settings.github_token = security.security_service.encrypt(payload.token) if payload.token else None
    if payload.default_repo is not None:
        settings.github_default_repo = payload.default_repo or None
    db.commit()
    db.refresh(settings)
    return GitHubSettingsResponse(
        token_configured=bool(settings.github_token),
        default_repo=settings.github_default_repo,
    )


@router.post("/test", response_model=dict[str, str])
def test_github_connection(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Tests the stored GitHub token."""
    with _get_github_service(db) as service:
        success, message = service.test_connection()
    return {"status": "success" if success else "error", "message": message}


@router.post("/issues", response_model=GitHubIssueResponse, status_code=status.HTTP_201_CREATED)
def create_github_issue(
    payload: GitHubIssueCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Creates a GitHub issue in the configured default repository or an explicit repo."""
    settings = _ensure_settings(db)
    repo = payload.repo or settings.github_default_repo
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No repository specified and no default repository configured in GitHub settings.",
        )
    with _get_github_service(db) as service:
        try:
            issue = service.create_issue(repo=repo, title=payload.title, body=payload.body, labels=payload.labels)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.args[0]) from exc
    return GitHubIssueResponse(**issue)
