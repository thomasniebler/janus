"""The safe, user-facing error type of the remote MCP registry services."""

from __future__ import annotations

#: Status text of an upstream ``401`` (no token, or OAuth tokens the server no longer accepts).
AUTH_REQUIRED = "Remote MCP server requires authorization."


class MCPServiceError(ValueError):
    """Safe, user-facing error from MCP validation or upstream handling."""

    def __init__(self, message: str, *, auth_required: bool = False, status_code: int | None = None):
        """Initialize a safe error, the authorization-required marker, and the upstream HTTP status if any."""
        super().__init__(message)
        self.auth_required = auth_required
        self.status_code = status_code
