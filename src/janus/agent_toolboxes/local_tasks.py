"""Dedicated read/write tools for user-scoped Janus-owned todo tasks."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models
from janus.agent_toolboxes.shared import ToolContext, parse_task_status, run_isolated_tool_mutation
from janus.services.local_planner_service import LocalPlannerService


def _parse_urgency(value: str | None) -> models.TaskUrgency | None:
    if value is None:
        return None
    cleaned = value.strip().lower()
    for urgency in models.TaskUrgency:
        if cleaned in {urgency.value.lower(), urgency.name.lower()}:
            return urgency
    raise ValueError("urgency must be one of: low, normal, high, critical")


def build_local_task_tools(context: ToolContext, *, include_write: bool = False) -> list[BaseTool]:  # noqa: C901
    """Build isolated local-task reads and, optionally, narrowly scoped writes."""
    service = LocalPlannerService(context.db)

    @tool("list_local_tasks")
    def list_local_tasks(
        status: str | None = None,
        urgency: str | None = None,
        parent_task_id: int | None = None,
        include_completed: bool = False,
        due_before: str | None = None,
        due_after: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """List only the current user's Janus-owned local todo tasks."""
        if limit <= 0:
            return []
        rows = service.list_tasks(
            context.user.id,
            include_completed=include_completed,
            include_imported=False,
            status=parse_task_status(status) if status else None,
            urgency=_parse_urgency(urgency),
            due_before=due_before,
            due_after=due_after,
        )
        if parent_task_id is not None:
            rows = [row for row in rows if row["parent_task_id"] == parent_task_id]
        return rows[:limit]

    @tool("get_local_task")
    def get_local_task(task_id: int) -> dict[str, Any]:
        """Read one current-user local task, never an imported planner record."""
        row = service.get_local_task(context.user.id, task_id)
        if row is None:
            raise ValueError(f"local task {task_id} not found")
        return service._serialize_task(row)

    tools: list[BaseTool] = [list_local_tasks, get_local_task]
    if not include_write:
        return tools

    @tool("create_local_task")
    def create_local_task(
        title: str,
        description: str | None = None,
        urgency: str = "normal",
        status: str = "Todo",
        due_at: str | None = None,
        parent_task_id: int | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        """Create a user-owned local task, optionally nested under an owned parent."""
        return run_isolated_tool_mutation(
            context,
            LocalPlannerService,
            lambda isolated: isolated.create_task(
                context.user.id,
                {
                    "title": title,
                    "description": description,
                    "urgency": _parse_urgency(urgency),
                    "status": parse_task_status(status),
                    "due_at": due_at,
                    "parent_task_id": parent_task_id,
                    "sort_order": sort_order,
                },
            ),
        )

    @tool("update_local_task")
    def update_local_task(
        task_id: int,
        title: str | None = None,
        description: str | None = None,
        urgency: str | None = None,
        status: str | None = None,
        due_at: str | None = None,
        parent_task_id: int | None = None,
        sort_order: int | None = None,
    ) -> dict[str, Any]:
        """Update a user-owned local task while preserving omitted fields."""
        payload: dict[str, Any] = {}
        for key, value in {
            "title": title,
            "description": description,
            "due_at": due_at,
            "parent_task_id": parent_task_id,
            "sort_order": sort_order,
        }.items():
            if value is not None:
                payload[key] = value
        if urgency is not None:
            payload["urgency"] = _parse_urgency(urgency)
        if status is not None:
            payload["status"] = parse_task_status(status)
        if not payload:
            raise ValueError("provide at least one local task field to update")
        return run_isolated_tool_mutation(
            context,
            LocalPlannerService,
            lambda isolated: _require_tool_task(isolated.update_task(context.user.id, task_id, payload), task_id),
        )

    @tool("complete_local_task")
    def complete_local_task(task_id: int) -> dict[str, Any]:
        """Complete exactly one local task without cascading to its children."""
        return run_isolated_tool_mutation(
            context,
            LocalPlannerService,
            lambda isolated: _require_tool_task(isolated.complete_task(context.user.id, task_id), task_id),
        )

    @tool("move_local_task")
    def move_local_task(
        task_id: int, parent_task_id: int | None = None, sort_order: int | None = None
    ) -> dict[str, Any]:
        """Reparent a local task or move it to the user's root task list."""
        return run_isolated_tool_mutation(
            context,
            LocalPlannerService,
            lambda isolated: _require_tool_task(
                isolated.move_task(context.user.id, task_id, parent_task_id=parent_task_id, sort_order=sort_order),
                task_id,
            ),
        )

    @tool("reorder_local_task")
    def reorder_local_task(task_id: int, sort_order: int) -> dict[str, Any]:
        """Set a local task's explicit sibling order."""
        return run_isolated_tool_mutation(
            context,
            LocalPlannerService,
            lambda isolated: _require_tool_task(
                isolated.update_task(context.user.id, task_id, {"sort_order": sort_order}),
                task_id,
            ),
        )

    tools.extend(
        [
            create_local_task,
            update_local_task,
            complete_local_task,
            move_local_task,
            reorder_local_task,
        ]
    )
    return tools


def _require_tool_task(payload: dict[str, Any] | None, task_id: int) -> dict[str, Any]:
    if payload is None:
        raise ValueError(f"local task {task_id} not found")
    return payload
