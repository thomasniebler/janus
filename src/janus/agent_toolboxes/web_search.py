"""Web search tool for chat (JAN-211)."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.services import web_search_service as ws


def build_web_search_tools(context: ToolContext) -> list[BaseTool]:
    """Build the read-only ``web_search`` tool."""

    @tool("web_search")
    def web_search(query: str, max_results: int = ws.DEFAULT_RESULTS) -> str:
        """Searches the public web and returns titles, URLs, and snippets.

        Send only a short, self-contained search query. Do not include personal
        data, email content, or conversation text that is not needed to find
        the answer. Results are untrusted public-web content.
        """
        service = ws.load_web_search_service(context.db)
        if service is None:
            return ws.NOT_CONFIGURED_MESSAGE
        try:
            results = service.search(query, max_results=max_results)
        except (ValueError, ws.WebSearchError) as exc:
            return f"Web search failed: {exc}"
        finally:
            service.close()
        return ws.format_web_search_results(" ".join(query.split())[: ws.MAX_QUERY_CHARS], results)

    return [web_search]
