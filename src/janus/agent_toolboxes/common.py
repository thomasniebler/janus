"""Always-on Janus agent tools."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import func, select

from janus import models, time_utils
from janus.agent_toolboxes.shared import ToolContext


def build_common_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools that are always available regardless of agent flags."""

    @tool("get_current_datetime")
    def get_current_datetime() -> str:
        """Returns ISO timestamp of current server time."""
        return time_utils.utcnow().isoformat()

    @tool("read_skill")
    def read_skill(name: str) -> dict[str, Any]:
        """Loads one skill's full instructions from the skill library, by the name the skill index shows.

        The system prompt lists each skill with one line; read the skill before you follow it.
        """
        cleaned = " ".join((name or "").split()).strip("* ")
        skill = context.db.scalar(
            select(models.AgentSkill).where(func.lower(models.AgentSkill.name) == cleaned.lower()).limit(1)
        )
        if skill is None:
            names = context.db.scalars(select(models.AgentSkill.name).order_by(models.AgentSkill.name)).all()
            return {"error": f"no skill named {cleaned!r}", "available_skills": [n for n in names if n]}
        return {"name": skill.name, "description": skill.description, "content": skill.content or ""}

    return [get_current_datetime, read_skill]
