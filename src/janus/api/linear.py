"""Linear integration API router."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services.linear_service import LinearService

router = APIRouter(prefix="/api/linear", tags=["linear"])


def _ensure_settings(db: Session) -> models.Settings:
    """Returns the singleton settings row, creating it if needed."""
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _get_linear_service(db: Session) -> LinearService:
    """Resolves the configured Linear API key and returns a LinearService."""
    settings = _ensure_settings(db)
    if not settings.linear_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Linear API key is not configured. Add it in Settings → Linear.",
        )
    api_key = security.security_service.decrypt(settings.linear_api_key)
    return LinearService(api_key=api_key)


class LinearSettingsResponse(BaseModel):
    """Public Linear settings (no raw key)."""

    api_key_configured: bool
    default_team_id: str | None = None


class LinearSettingsUpdate(BaseModel):
    """Payload for updating Linear settings."""

    api_key: str | None = None
    default_team_id: str | None = None


class IssueCreate(BaseModel):
    """Payload for creating a Linear issue."""

    team_id: str | None = None  # Falls back to settings.linear_default_team_id when omitted
    title: str
    description: str


class IssueResponse(BaseModel):
    """Minimal Linear issue response."""

    identifier: str
    url: str
    title: str


@router.get("/settings", response_model=LinearSettingsResponse)
def get_linear_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns the current Linear integration settings."""
    settings = _ensure_settings(db)
    return LinearSettingsResponse(
        api_key_configured=bool(settings.linear_api_key),
        default_team_id=settings.linear_default_team_id,
    )


@router.patch("/settings", response_model=LinearSettingsResponse)
def update_linear_settings(
    payload: LinearSettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Updates the instance-wide Linear integration settings (admin only, JAN-222)."""
    settings = _ensure_settings(db)

    if payload.api_key is not None:
        # Pasted keys often carry trailing whitespace/newlines, which Linear rejects.
        api_key = payload.api_key.strip()
        settings.linear_api_key = security.security_service.encrypt(api_key) if api_key else None
    if payload.default_team_id is not None:
        settings.linear_default_team_id = payload.default_team_id.strip() or None

    db.commit()
    db.refresh(settings)
    return LinearSettingsResponse(
        api_key_configured=bool(settings.linear_api_key),
        default_team_id=settings.linear_default_team_id,
    )


@router.post("/test", response_model=dict)
def test_linear_connection(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Tests the stored Linear API key by fetching the authenticated user."""
    with _get_linear_service(db) as svc:
        success, message = svc.test_connection()
    return {"status": "success" if success else "error", "message": message}


@router.post("/issues", response_model=IssueResponse, status_code=status.HTTP_201_CREATED)
def create_linear_issue(
    payload: IssueCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Creates a new Linear issue."""
    settings = _ensure_settings(db)
    team_id = payload.team_id or settings.linear_default_team_id
    if not team_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No team specified and no default team configured in Linear settings.",
        )

    with _get_linear_service(db) as svc:
        try:
            issue = svc.create_issue(
                team_id=team_id,
                title=payload.title,
                description=payload.description,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.args[0],
            ) from exc

    return IssueResponse(
        identifier=issue["identifier"],
        url=issue["url"],
        title=issue["title"],
    )
