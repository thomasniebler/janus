"""Bulk review of pending planner suggestions: dismiss many, collapse duplicates.

Kept apart from ``planner.py`` (a baselined size hotspot); same ``/api/planner``
prefix, next to the one-at-a-time suggestion routes. Every route is owner-scoped
and never changes planner tasks or events.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import planner as planner_schemas
from janus.services import planner_suggestion_review as review

router = APIRouter(prefix="/api/planner", tags=["planner"])


@router.post("/suggestions/dismiss", response_model=planner_schemas.PlannerSuggestionBulkResult)
def dismiss_planner_suggestions(
    payload: planner_schemas.PlannerSuggestionBulkDismiss,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Dismiss the given pending suggestions, or every pending one when ``all_pending`` is set."""
    dismissed = review.dismiss_suggestions(
        db,
        user.id,
        suggestion_ids=payload.suggestion_ids or None,
        all_pending=payload.all_pending and not payload.suggestion_ids,
    )
    return {"dismissed_ids": dismissed}


@router.get("/suggestions/duplicates", response_model=planner_schemas.PlannerSuggestionDuplicateSummary)
def summarize_duplicate_planner_suggestions(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Count pending suggestions that duplicate another (same dedupe key, or same type and title)."""
    groups = review.duplicate_suggestion_groups(db, user.id)
    return {"groups": len(groups), "duplicates": sum(len(group) - 1 for group in groups)}


@router.post("/suggestions/dismiss-duplicates", response_model=planner_schemas.PlannerSuggestionBulkResult)
def dismiss_duplicate_planner_suggestions(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Keep the newest suggestion of each duplicate group and dismiss the others."""
    return review.dismiss_duplicate_suggestions(db, user.id)
