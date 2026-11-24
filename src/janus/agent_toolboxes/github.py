"""GitHub PR tools for agent execution."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus import models, security
from janus.agent_toolboxes.shared import ToolContext
from janus.services.github_service import GitHubService


def _load_github_service(context: ToolContext) -> tuple[GitHubService, str | None]:
    """Return a configured GitHub service and default repository."""
    settings = context.db.query(models.Settings).first()
    if settings is None or not settings.github_token:
        raise ValueError("GitHub token is not configured. Add it in Settings → GitHub.")
    token = security.security_service.decrypt(settings.github_token)
    return GitHubService(token=token), settings.github_default_repo


def build_github_tools(context: ToolContext) -> list[BaseTool]:
    """Build GitHub PR management tools."""

    @tool("list_prs")
    def list_prs(repo: str | None = None, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        """Lists pull requests for the configured GitHub repository."""
        service, default_repo = _load_github_service(context)
        try:
            resolved_repo = repo or default_repo
            if not resolved_repo:
                raise ValueError("No repository specified and no default repository is configured.")
            return service.list_pull_requests(repo=resolved_repo, state=state, limit=limit)
        finally:
            service.close()

    @tool("merge_pr")
    def merge_pr(pull_number: int, repo: str | None = None, merge_method: str = "squash") -> dict[str, Any]:
        """Merges a GitHub pull request."""
        service, default_repo = _load_github_service(context)
        try:
            resolved_repo = repo or default_repo
            if not resolved_repo:
                raise ValueError("No repository specified and no default repository is configured.")
            return service.merge_pull_request(
                repo=resolved_repo,
                pull_number=pull_number,
                merge_method=merge_method,
            )
        finally:
            service.close()

    return [list_prs, merge_pr]
