"""Authenticated API surfaces for lightweight desktop integrations."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import planner as planner_schemas
from janus.services.desktop_service import DesktopService

router = APIRouter(prefix="/api/desktop", tags=["desktop"])


@router.get("/summary", response_model=planner_schemas.DesktopSummary)
def get_desktop_summary(
    include_unread_emails: bool = True,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return aggregate counts for authenticated desktop status widgets."""
    return DesktopService(db).get_summary(user, include_unread_emails=include_unread_emails)
