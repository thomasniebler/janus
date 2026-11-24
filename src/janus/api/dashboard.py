"""Dashboard API router for Janus."""

import logging
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from janus import database, models, security, time_utils
from janus.schemas import email as email_schemas
from janus.schemas import planner as planner_schemas
from janus.schemas import topics as topic_schemas
from janus.schemas import whatsapp as whatsapp_schemas
from janus.services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)

WidgetPayload = TypeVar("WidgetPayload")

_WIDGET_ERROR_HINTS: dict[str, str] = {
    "important_emails": "Check recent email records and topic serialization for malformed rows.",
    "whatsapp_messages": "Check recent WhatsApp sync state, account configuration, and message serialization.",
    "schedule": "Check synced calendar events and planner account connectivity.",
    "tasks": "Check planner task records and planner account connectivity.",
    "topic_focus": "Check tracked topic summaries and linked evidence records.",
}


def _raise_widget_load_error(widget_key: str, exc: Exception) -> None:
    """Raise a consistent, widget-scoped dashboard load error."""
    logger.exception("Dashboard widget '%s' failed to load", widget_key)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "dashboard_widget_load_failed",
            "widget": widget_key,
            "message": f"Failed to load the {widget_key.replace('_', ' ')} widget.",
            "hint": _WIDGET_ERROR_HINTS[widget_key],
            "cause": str(exc),
        },
    ) from exc


def _load_widget_or_500(widget_key: str, loader: Callable[[], WidgetPayload]) -> WidgetPayload:  # noqa: UP047
    """Execute one widget loader and convert failures into a helpful HTTP 500."""
    try:
        return loader()
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - exercised through route tests
        _raise_widget_load_error(widget_key, exc)


@router.get("/", response_model=planner_schemas.DashboardData)
async def get_dashboard_data(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Aggregates data for the main dashboard."""
    service = DashboardService(db)
    try:
        return {
            "important_emails": _load_widget_or_500(
                "important_emails",
                lambda: service.get_important_emails_widget_data(user),
            ),
            "whatsapp_messages": _load_widget_or_500(
                "whatsapp_messages",
                lambda: service.get_whatsapp_messages_widget_data(user),
            ),
            "all_emails": service.get_recent_emails_widget_data(user),
            "events": _load_widget_or_500("schedule", lambda: service.get_schedule_widget_data(user)),
            "tasks": _load_widget_or_500("tasks", lambda: service.get_tasks_widget_data(user)),
            "topics": _load_widget_or_500("topic_focus", lambda: service.get_topic_focus_widget_data(user)),
            "now": time_utils.utcnow(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "dashboard_load_failed",
                "message": "Failed to load the dashboard.",
                "cause": str(exc),
            },
        ) from exc


@router.get("/important-emails", response_model=list[email_schemas.EmailRecord])
def get_important_emails_widget(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Return important email data for the dashboard widget."""
    service = DashboardService(db)
    return _load_widget_or_500("important_emails", lambda: service.get_important_emails_widget_data(user))


@router.get("/whatsapp-messages", response_model=list[whatsapp_schemas.WhatsAppMessage])
def get_whatsapp_messages_widget(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return recent WhatsApp messages for the dashboard widget."""
    service = DashboardService(db)
    return _load_widget_or_500("whatsapp_messages", lambda: service.get_whatsapp_messages_widget_data(user))


@router.get("/events", response_model=list[planner_schemas.PlannerEventItem])
def get_schedule_widget(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Return schedule data for the dashboard widget."""
    service = DashboardService(db)
    return _load_widget_or_500("schedule", lambda: service.get_schedule_widget_data(user))


@router.get("/tasks", response_model=list[planner_schemas.PlannerTaskTreeItem])
def get_tasks_widget(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Return task data for the dashboard widget."""
    service = DashboardService(db)
    return _load_widget_or_500("tasks", lambda: service.get_tasks_widget_data(user))


@router.get("/topics", response_model=list[topic_schemas.TopicSummary])
def get_topic_focus_widget(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Return topic-focus data for the dashboard widget."""
    service = DashboardService(db)
    return _load_widget_or_500("topic_focus", lambda: service.get_topic_focus_widget_data(user))


@router.get("/widgets", response_model=list[planner_schemas.DashboardWidgetPreference])
def list_widget_preferences(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the effective dashboard widget preferences for the current user."""
    return DashboardService(db).list_widget_preferences(user)


@router.patch("/widgets", response_model=list[planner_schemas.DashboardWidgetPreference])
def update_widget_preferences(
    payload: planner_schemas.DashboardWidgetPreferenceUpdateRequest,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Persist dashboard widget preference changes for the current user."""
    return DashboardService(db).update_widget_preferences(user, payload.widgets)
