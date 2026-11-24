"""Planner task-focused Janus agent tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from janus import models
from janus.agent_toolboxes.shared import ToolContext, iso, parse_task_status


def owned_tasks(db: Session, user_id: int) -> Query:
    """Synced planner tasks the user owns, i.e. those of their own planner accounts."""
    planner_accounts = select(models.PlannerAccount.id).where(models.PlannerAccount.user_id == user_id)
    return db.query(models.TaskRecord).filter(models.TaskRecord.planner_account_id.in_(planner_accounts))


def build_task_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools for listing and reading the context user's synced planner tasks."""
    db = context.db
    user_id = context.user.id

    @tool("list_tasks")
    def list_tasks(status: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        """Lists TaskRecord rows filtered by status."""
        if limit <= 0:
            return []
        stmt = owned_tasks(db, user_id)
        if status:
            stmt = stmt.filter(models.TaskRecord.status == parse_task_status(status))
        rows = stmt.order_by(models.TaskRecord.updated_at.desc()).limit(limit).all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "status": row.status.value,
                "due_date": iso(row.due_date),
                "updated_at": iso(row.updated_at),
            }
            for row in rows
        ]

    @tool("get_task_detail")
    def get_task_detail(task_id: int) -> dict[str, Any]:
        """Returns full detail for a single task."""
        # Another user's task answers exactly like a missing one (JAN-137).
        row = owned_tasks(db, user_id).filter(models.TaskRecord.id == task_id).first()
        if row is None:
            raise ValueError(f"task {task_id} not found")
        return {
            "id": row.id,
            "planner_account_id": row.planner_account_id,
            "external_id": row.external_id,
            "title": row.title,
            "status": row.status.value,
            "due_date": iso(row.due_date),
            "updated_at": iso(row.updated_at),
        }

    return [list_tasks, get_task_detail]
