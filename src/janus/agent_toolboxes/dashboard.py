"""Dashboard-focused Janus agent tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import ValidationError

from janus import models
from janus.agent_toolboxes.shared import (
    ToolContext,
    parse_dashboard_widget_key,
    require_prompt_override_widget_key,
    require_widget_preference,
)
from janus.schemas import planner as planner_schemas
from janus.services.dashboard_service import DashboardService

_UNSET = object()


def build_dashboard_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools for briefings and dashboard widget configuration."""
    db = context.db
    dashboard = DashboardService(db)

    @tool("get_daily_briefing")
    def get_daily_briefing(force_refresh: bool = False) -> dict[str, Any]:
        """Returns today's planner summary plus today's briefing from the Morning briefing trigger.

        With ``force_refresh`` the briefing trigger runs again now (slow: it is a
        full Janus run); otherwise the briefing is whatever today's run produced,
        or ``None`` when it has not run yet today.
        """
        # Imported lazily: the trigger runtime imports the tool registry.
        from janus import builtin_triggers, database
        from janus import trigger_definition_service as definitions
        from janus.trigger_runtime import run_definition_inline

        briefing_context = dashboard.get_briefing_generation_context(
            context.user,
            include_calendar="calendar" in context.connected_capabilities,
            include_tasks="jira" in context.connected_capabilities,
        )
        builtin_triggers.ensure_builtin_definitions(db, context.user.id)
        definition = definitions.get_builtin_definition(db, context.user.id, builtin_triggers.BUILTIN_BRIEFING)
        briefing: str | None = None
        if definition is not None:
            if force_refresh:
                record = run_definition_inline(database.SessionLocal, definition.id)
                briefing = record.output if record.status == models.RuntimeTriggerStatus.COMPLETED.value else None
            else:
                todays = builtin_triggers.todays_briefing(db, definition)
                briefing = todays.output if todays is not None else None
        return {
            "summary": str(briefing_context["summary"]),
            "briefing": briefing,
            "prompt_override": briefing_context["prompt_override"],
        }

    @tool("list_dashboard_widgets")
    def list_dashboard_widgets() -> list[dict[str, Any]]:
        """Lists the current user's dashboard widget preferences."""
        return [preference.model_dump(mode="json") for preference in dashboard.list_widget_preferences(context.user)]

    @tool("update_dashboard_widget")
    def update_dashboard_widget(
        widget_key: str,
        is_visible: bool | None = None,
        is_collapsed: bool | None = None,
    ) -> dict[str, Any]:
        """Updates visibility or collapsed state for one dashboard widget."""
        resolved_key = parse_dashboard_widget_key(widget_key)
        preferences = dashboard.update_widget_preferences(
            context.user,
            [
                models_to_schema_update(
                    widget_key=resolved_key,
                    is_visible=is_visible,
                    is_collapsed=is_collapsed,
                )
            ],
        )
        updated = require_widget_preference(preferences, resolved_key)
        return updated.model_dump(mode="json")

    @tool("set_dashboard_widget_prompt")
    def set_dashboard_widget_prompt(widget_key: str, prompt_override: str) -> dict[str, Any]:
        """Stores a custom prompt override for one dashboard widget."""
        resolved_key = parse_dashboard_widget_key(widget_key)
        require_prompt_override_widget_key(resolved_key)
        preferences = dashboard.update_widget_preferences(
            context.user,
            [
                models_to_schema_update(
                    widget_key=resolved_key,
                    prompt_override=prompt_override,
                )
            ],
        )
        updated = require_widget_preference(preferences, resolved_key)
        return updated.model_dump(mode="json")

    @tool("clear_dashboard_widget_prompt")
    def clear_dashboard_widget_prompt(widget_key: str) -> dict[str, Any]:
        """Clears any custom prompt override for one dashboard widget."""
        resolved_key = parse_dashboard_widget_key(widget_key)
        require_prompt_override_widget_key(resolved_key)
        preferences = dashboard.update_widget_preferences(
            context.user,
            [
                models_to_schema_update(
                    widget_key=resolved_key,
                    prompt_override=None,
                )
            ],
        )
        updated = require_widget_preference(preferences, resolved_key)
        return updated.model_dump(mode="json")

    return [
        get_daily_briefing,
        list_dashboard_widgets,
        update_dashboard_widget,
        set_dashboard_widget_prompt,
        clear_dashboard_widget_prompt,
    ]


def models_to_schema_update(
    *,
    widget_key: models.DashboardWidgetKey,
    is_visible: bool | None = None,
    is_collapsed: bool | None = None,
    prompt_override: str | None | object = _UNSET,
):
    """Build a dashboard widget update schema while preserving explicit nulls."""
    payload: dict[str, Any] = {"widget_key": widget_key}
    if is_visible is not None:
        payload["is_visible"] = is_visible
    if is_collapsed is not None:
        payload["is_collapsed"] = is_collapsed
    if prompt_override is not _UNSET:
        payload["prompt_override"] = prompt_override
    try:
        return planner_schemas.DashboardWidgetPreferenceUpdate(**payload)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise ValueError(messages) from exc
