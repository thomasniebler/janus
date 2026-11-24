"""Owner-scoped activity statistics for the Settings → Stats tab (JAN-258)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas.stats import StatsSummary
from janus.services.stats_service import StatsError, StatsService

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsSummary)
def get_stats(
    days: int = Query(default=30, description="Range length in days: 7, 30, or 90."),
    timezone: str | None = Query(default=None, description="The browser's IANA timezone for daily buckets."),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the current user's mail, trigger, memory/knowledge, and messaging statistics."""
    try:
        return StatsService(db).summary(user.id, days=days, tz_name=timezone)
    except StatsError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
