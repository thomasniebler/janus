"""Topic-focused Janus agent tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models
from janus.agent_toolboxes.shared import ToolContext, run_isolated_tool_mutation
from janus.services.topic_service import TopicService


def build_topic_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools for reading and updating tracked topics."""
    service = TopicService(context.db)
    user_id = context.user.id

    @tool("list_topics")
    def list_topics(status: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """List tracked topics for the current user."""
        parsed_status = models.TopicStatus(status) if status is not None else None
        return run_isolated_tool_mutation(
            context,
            TopicService,
            lambda mutation_service: mutation_service.list_topics(user_id, status=parsed_status, limit=limit),
        )

    @tool("create_topic")
    def create_topic(title: str, summary: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
        """Create or confirm a manual tracked topic for the current user."""
        return run_isolated_tool_mutation(
            context,
            TopicService,
            lambda mutation_service: mutation_service.serialize_topic_summary(
                mutation_service.create_topic(
                    user_id=user_id,
                    title=title,
                    summary=summary,
                    pinned=pinned,
                )
            ),
        )

    @tool("search_topics")
    def search_topics(query: str, include_inactive: bool = False, limit: int = 10) -> list[dict[str, Any]]:
        """Search tracked topics by title or summary text."""
        return run_isolated_tool_mutation(
            context,
            TopicService,
            lambda mutation_service: mutation_service.list_topics(
                user_id,
                query=query,
                status=None if include_inactive else models.TopicStatus.ACTIVE,
                limit=limit,
            ),
        )

    @tool("get_topic_detail")
    def get_topic_detail(topic_id: int) -> dict[str, Any]:
        """Return one tracked topic with evidence."""
        return service.get_topic_detail(user_id, topic_id)

    @tool("update_topic_state")
    def update_topic_state(topic_id: int, status: str, pinned: bool | None = None) -> dict[str, Any]:
        """Update a tracked topic's lifecycle state and optional pin flag."""
        return run_isolated_tool_mutation(
            context,
            TopicService,
            lambda mutation_service: mutation_service.get_topic_detail(
                user_id,
                mutation_service.update_topic(
                    user_id=user_id,
                    topic_id=topic_id,
                    status=models.TopicStatus(status),
                    pinned=pinned,
                ).id,
            ),
        )

    return [list_topics, create_topic, search_topics, get_topic_detail, update_topic_state]
