"""Calendar-focused Janus agent tools."""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session

from janus import models, time_utils
from janus.agent_toolboxes.shared import ToolContext, iso


def owned_calendar_events(db: Session, user_id: int) -> Query:
    """Calendar events the user owns: from their planner accounts, or their own reminders.

    Synced events belong to the owner of their planner account. A synthetic
    reminder event has no planner account; it belongs to the owner of the
    reminder's source email account, as reminder memory does (JAN-296).
    """
    planner_accounts = select(models.PlannerAccount.id).where(models.PlannerAccount.user_id == user_id)
    reminder_events = (
        select(models.ReminderMemory.synthetic_calendar_event_id)
        .join(models.EmailRecord, models.ReminderMemory.source_email_id == models.EmailRecord.id)
        .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
        .where(
            models.EmailAccount.user_id == user_id,
            models.ReminderMemory.synthetic_calendar_event_id.is_not(None),
        )
    )
    return db.query(models.CalendarEvent).filter(
        or_(
            models.CalendarEvent.planner_account_id.in_(planner_accounts),
            models.CalendarEvent.id.in_(reminder_events),
        )
    )


def build_calendar_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools for listing and reading the context user's calendar events."""
    db = context.db
    user_id = context.user.id

    @tool("list_calendar_events")
    def list_calendar_events(date: str | None = None, lookahead_days: int = 0) -> list[dict[str, Any]]:
        """Lists CalendarEvent rows for a date range."""
        base_date = date_type.fromisoformat(date) if date else time_utils.utcnow().date()
        start = datetime.combine(base_date, datetime.min.time())
        end = start + timedelta(days=max(0, lookahead_days) + 1)
        rows = (
            owned_calendar_events(db, user_id)
            .filter(models.CalendarEvent.start_time >= start, models.CalendarEvent.start_time < end)
            .order_by(models.CalendarEvent.start_time.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "summary": row.summary,
                "start_time": iso(row.start_time),
                "end_time": iso(row.end_time),
                "location": row.location,
            }
            for row in rows
        ]

    @tool("get_event_detail")
    def get_event_detail(event_id: int) -> dict[str, Any]:
        """Returns full detail for a single event."""
        # Another user's event answers exactly like a missing one (JAN-137).
        row = owned_calendar_events(db, user_id).filter(models.CalendarEvent.id == event_id).first()
        if row is None:
            raise ValueError(f"event {event_id} not found")
        return {
            "id": row.id,
            "planner_account_id": row.planner_account_id,
            "external_id": row.external_id,
            "summary": row.summary,
            "start_time": iso(row.start_time),
            "end_time": iso(row.end_time),
            "location": row.location,
            "description": row.description,
            "source_type": row.source_type.value,
        }

    return [list_calendar_events, get_event_detail]
