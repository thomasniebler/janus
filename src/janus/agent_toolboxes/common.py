"""Always-on Janus agent tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from janus import time_utils
from janus.agent_toolboxes.shared import ToolContext


def build_common_tools(context: ToolContext) -> list[BaseTool]:
    """Build tools that are always available regardless of agent flags."""

    @tool("get_current_datetime")
    def get_current_datetime() -> str:
        """Returns ISO timestamp of current server time."""
        return time_utils.utcnow().isoformat()

    return [get_current_datetime]
