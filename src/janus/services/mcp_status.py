"""Discovery and health status of a remote MCP server: resetting it and recording a failed contact."""

from __future__ import annotations

import json

import httpx

from janus import models, time_utils
from janus.services.mcp_errors import AUTH_REQUIRED, MCPServiceError


def safe_error(exc: Exception) -> str:
    """Convert transport exceptions into non-sensitive status text."""
    if isinstance(exc, httpx.TimeoutException):
        return "Remote MCP server timed out."
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 401:
            return AUTH_REQUIRED
        return f"Remote MCP server returned HTTP {exc.response.status_code}."
    if isinstance(exc, MCPServiceError):
        return str(exc)
    if isinstance(exc, (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return "Remote MCP server returned an invalid response."
    return "Remote MCP discovery failed."


def reset_discovery(server: models.MCPServer, *, only_if_failed: bool = False) -> None:
    """Forget the last discovery outcome.

    Always after an endpoint change (the old tool list no longer applies); after a
    credential change only when the last contact failed, so a good token clears a
    stale "rejected" or HTTP error.
    """
    failed = server.discovery_status in (models.MCPDiscoveryStatus.AUTH_REQUIRED, models.MCPDiscoveryStatus.ERROR)
    if only_if_failed and not failed:
        return
    server.discovery_status = models.MCPDiscoveryStatus.NEVER
    server.last_error = None


def mark_failed(server: models.MCPServer, exc: Exception) -> None:
    """Record a failed contact: ``auth_required`` or ``error``, unhealthy now, with the redacted reason."""
    auth_required = isinstance(exc, MCPServiceError) and exc.auth_required
    server.discovery_status = (
        models.MCPDiscoveryStatus.AUTH_REQUIRED if auth_required else models.MCPDiscoveryStatus.ERROR
    )
    server.last_health_status = "error"
    server.last_health_at = time_utils.utcnow()
    server.last_error = safe_error(exc)
