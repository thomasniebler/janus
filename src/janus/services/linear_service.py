"""Linear integration service for Janus task workflows.

Linear only ships an official SDK for TypeScript, so Janus talks to the
public GraphQL API directly. The documents live in ``linear_queries.py``,
where each one is validated against a pinned snapshot of Linear's schema.

Input the caller can fix (an unknown team or workflow state, an empty update)
raises :class:`LinearInputError`, a ``ValueError`` whose message lists the
valid choices, so the agent toolbox can answer ``REFUSED: ...`` and the model
can retry (JAN-330, JAN-333).
"""

import logging
import re
from types import TracebackType
from typing import Any

import httpx

from janus.services.linear_queries import (
    COMMENT_CREATE_MUTATION,
    ISSUE_CREATE_MUTATION,
    ISSUE_TEAM_QUERY,
    ISSUE_UPDATE_MUTATION,
    TEAM_ISSUES_QUERY,
    TEAM_LOOKUP_QUERY,
    TEAMS_QUERY,
    USERS_QUERY,
    VIEWER_QUERY,
    WORKFLOW_STATES_QUERY,
)

logger = logging.getLogger(__name__)

_LINEAR_API_URL = "https://api.linear.app/graphql"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

_CLOSED_STATE_TYPES = ("completed", "canceled")

#: Workflow-state types an update may name instead of a state name (JAN-333).
STATE_TYPES = ("backlog", "unstarted", "started", "completed", "canceled")

#: Teams fetched for discovery, the single-team fallback, and error hints.
_TEAM_PAGE = 100


class LinearInputError(ValueError):
    """A request Linear cannot act on as given; the message says what is valid."""


def _graphql_error_message(payload: Any) -> str | None:
    """Return a readable message from a GraphQL ``errors`` array, if present."""
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if not errors or not isinstance(errors, list):
        return None
    messages: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        extensions = error.get("extensions") or {}
        # Linear puts the user-facing explanation in extensions.userPresentableMessage.
        message = (extensions.get("userPresentableMessage") if isinstance(extensions, dict) else None) or error.get(
            "message"
        )
        if message and message not in messages:
            messages.append(str(message))
    return "; ".join(messages) or "Unknown Linear API error."


class LinearService:
    """Thin wrapper around the Linear GraphQL API."""

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        """Initializes the service with a Linear API key.

        Personal API keys (``lin_api_…``) are sent verbatim in the
        ``Authorization`` header, as Linear requires; OAuth access tokens must
        already carry their ``Bearer`` prefix. Surrounding whitespace from
        copy/paste is stripped because Linear rejects it.
        """
        self._client = httpx.Client(
            headers={
                "Authorization": (api_key or "").strip(),
                "Content-Type": "application/json",
            },
            timeout=15,
            transport=transport,
        )
        self._team_id_cache: dict[str, str] = {}

    def close(self) -> None:
        """Closes the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "LinearService":
        """Supports use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Closes the HTTP client on context manager exit."""
        self.close()

    def _post(self, query: str, variables: dict | None = None) -> dict:
        """Executes a GraphQL request and validates the response shape.

        Linear reports authentication and query-validation failures as HTTP
        400 with a GraphQL ``errors`` body, so the body is parsed regardless of
        status code and its message is surfaced instead of a bare status.
        """
        try:
            response = self._client.post(
                _LINEAR_API_URL,
                json={
                    "query": query,
                    "variables": variables or {},
                },
            )
        except httpx.RequestError as exc:
            raise ValueError(f"Connection error while calling Linear API: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        error_message = _graphql_error_message(payload)
        if error_message:
            raise ValueError(f"Linear API error: {error_message}")

        if response.status_code != 200:
            raise ValueError(f"Linear API returned HTTP {response.status_code}.")

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Linear API returned an invalid response payload.")

        return data

    def test_connection(self) -> tuple[bool, str]:
        """Verifies the API key by fetching the authenticated user."""
        try:
            data = self._post(query=VIEWER_QUERY)
        except ValueError as exc:
            logger.warning("Linear connection test failed: %s", exc)
            return False, f"Unable to connect to Linear. {exc}"
        viewer = data.get("viewer") or {}
        name = viewer.get("displayName") or viewer.get("name") or "unknown"
        return True, f"Connected as {name}"

    def list_teams(self) -> list[dict[str, Any]]:
        """List the workspace's teams as ``{id, key, name}``, sorted by key (JAN-330)."""
        data = self._post(query=TEAMS_QUERY, variables={"first": _TEAM_PAGE})
        teams = [
            {"id": team.get("id"), "key": team.get("key"), "name": team.get("name")}
            for team in (data.get("teams") or {}).get("nodes") or []
            if isinstance(team, dict) and team.get("id")
        ]
        return sorted(teams, key=lambda team: str(team.get("key") or ""))

    def _team_keys_hint(self) -> str:
        """Return the available team keys for an error message."""
        keys = [str(team["key"]) for team in self.list_teams() if team.get("key")]
        return f"Available team keys: {', '.join(keys)}." if keys else "The API key cannot see any Linear team."

    def resolve_team_id(self, team_ref: str) -> str:
        """Return the team UUID for a team UUID, key (``ENG``) or exact team name, case-insensitively.

        ``issueCreate`` requires the team's UUID, while the Settings UI and
        most users naturally refer to a team by the key shown in issue
        identifiers or by its name, so lookups happen once per service
        instance. A key match wins over a name match.
        """
        ref = (team_ref or "").strip()
        if not ref:
            raise LinearInputError(f"No Linear team specified. {self._team_keys_hint()}")
        if _UUID_RE.match(ref):
            return ref
        cache_key = ref.casefold()
        if cache_key in self._team_id_cache:
            return self._team_id_cache[cache_key]
        data = self._post(query=TEAM_LOOKUP_QUERY, variables={"ref": ref})
        nodes = [node for node in (data.get("teams") or {}).get("nodes") or [] if isinstance(node, dict)]
        by_key = [node for node in nodes if str(node.get("key") or "").casefold() == cache_key]
        by_name = [node for node in nodes if str(node.get("name") or "").casefold() == cache_key]
        match = (by_key or by_name or [{}])[0]
        if not match.get("id"):
            raise LinearInputError(
                f"Linear team '{ref}' was not found. Use a team key (as in issue IDs, e.g. ENG-123), "
                f"the exact team name, or the team UUID. {self._team_keys_hint()}"
            )
        team_id = str(match["id"])
        # Remember the id itself too, so passing a resolved id on never looks it up again.
        self._team_id_cache[cache_key] = self._team_id_cache[team_id.casefold()] = team_id
        return team_id

    def choose_team(self, team_ref: str | None, default_ref: str | None) -> tuple[str, str | None]:
        """Return ``(team UUID, note)`` for an explicit team, else the default, else the only team.

        With neither an explicit nor a configured default team, a workspace
        with exactly one team uses that team, and ``note`` says so (JAN-330).
        """
        ref = (team_ref or "").strip() or (default_ref or "").strip()
        if ref:
            return self.resolve_team_id(ref), None
        teams = self.list_teams()
        if len(teams) == 1:
            only = teams[0]
            note = f"No default team is configured; used the workspace's only team {only['key']} ({only['name']})."
            return str(only["id"]), note
        keys = ", ".join(str(team["key"]) for team in teams if team.get("key")) or "none visible"
        raise LinearInputError(f"No team specified and no default team is configured. Available team keys: {keys}.")

    def list_workflow_states(self, team_id: str) -> list[dict[str, Any]]:
        """List a team's workflow states as ``{id, name, type}`` in board order (JAN-333)."""
        data = self._post(query=WORKFLOW_STATES_QUERY, variables={"teamId": self.resolve_team_id(team_id)})
        nodes = ((data.get("team") or {}).get("states") or {}).get("nodes") or []
        states = sorted((node for node in nodes if isinstance(node, dict)), key=lambda node: node.get("position") or 0)
        return [{"id": state.get("id"), "name": state.get("name"), "type": state.get("type")} for state in states]

    def resolve_state_id(self, issue_id: str, state: str) -> str:
        """Return the id of the issue's team workflow state named ``state`` (name first, then type)."""
        data = self._post(query=ISSUE_TEAM_QUERY, variables={"id": issue_id})
        team_id = ((data.get("issue") or {}).get("team") or {}).get("id")
        if not team_id:
            raise ValueError(f"Linear did not return the team of issue {issue_id}.")
        states = self.list_workflow_states(str(team_id))
        wanted = (state or "").strip().casefold()
        by_name = [item for item in states if str(item.get("name") or "").casefold() == wanted]
        by_type = [item for item in states if str(item.get("type") or "").casefold() == wanted]
        match = (by_name or by_type or [{}])[0]
        if not match.get("id"):
            valid = ", ".join(f"{item['name']} ({item['type']})" for item in states)
            raise LinearInputError(
                f"Unknown workflow state '{state}'. Valid states: {valid}; or a type: {', '.join(STATE_TYPES)}."
            )
        return str(match["id"])

    @staticmethod
    def _normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
        """Return a consistent issue payload for API and toolbox consumers."""
        state = issue.get("state") or {}
        parent = issue.get("parent") or {}
        assignee = issue.get("assignee") or {}
        return {
            "id": issue.get("id"),
            "identifier": issue.get("identifier"),
            "title": issue.get("title"),
            "url": issue.get("url"),
            "description": issue.get("description") or "",
            "updated_at": issue.get("updatedAt"),
            "priority": issue.get("priority"),
            "state": {
                "name": state.get("name"),
                "type": state.get("type"),
            },
            "parent": (
                {
                    "id": parent.get("id"),
                    "identifier": parent.get("identifier"),
                    "title": parent.get("title"),
                }
                if parent
                else None
            ),
            "assignee": (
                {
                    "id": assignee.get("id"),
                    "display_name": assignee.get("displayName") or assignee.get("name"),
                    "name": assignee.get("name"),
                    "email": assignee.get("email"),
                }
                if assignee
                else None
            ),
        }

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        *,
        parent_id: str | None = None,
        assignee_id: str | None = None,
    ) -> dict[str, Any]:
        """Creates a Linear issue in the given team (UUID or team key)."""
        input_payload: dict[str, Any] = {
            "teamId": self.resolve_team_id(team_id),
            "title": title,
            "description": description,
        }
        if parent_id:
            # Linear accepts either the parent UUID or its identifier (e.g. "ENG-123").
            input_payload["parentId"] = parent_id
        if assignee_id:
            input_payload["assigneeId"] = assignee_id
        data = self._post(query=ISSUE_CREATE_MUTATION, variables={"input": input_payload})

        result = data.get("issueCreate") or {}
        issue = result.get("issue")
        if not result.get("success") or not issue:
            raise ValueError("Linear API did not return a created issue.")
        return self._normalize_issue(issue)

    def list_team_issues(
        self,
        team_id: str,
        *,
        limit: int = 20,
        include_completed: bool = False,
    ) -> list[dict[str, Any]]:
        """List the most recently updated issues for a Linear team (UUID or team key)."""
        variables: dict[str, Any] = {
            "teamId": self.resolve_team_id(team_id),
            "first": limit,
        }
        if not include_completed:
            # Filter server-side so closed issues do not crowd open ones out of the page.
            variables["filter"] = {"state": {"type": {"nin": list(_CLOSED_STATE_TYPES)}}}
        data = self._post(query=TEAM_ISSUES_QUERY, variables=variables)
        team = data.get("team") or {}
        issues = [self._normalize_issue(issue) for issue in (team.get("issues") or {}).get("nodes") or []]
        return sorted(issues, key=lambda item: item.get("updated_at") or "", reverse=True)[:limit]

    def list_users(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List workspace users for assignment lookups."""
        data = self._post(query=USERS_QUERY, variables={"first": limit})
        users = (data.get("users") or {}).get("nodes") or []
        return [
            {
                "id": user.get("id"),
                "display_name": user.get("displayName") or user.get("name"),
                "name": user.get("name"),
                "email": user.get("email"),
            }
            for user in users
        ]

    def find_user(self, *candidate_names: str) -> dict[str, Any] | None:
        """Find a workspace user by display name, username, or email."""
        normalized_candidates = [
            candidate.strip().lower() for candidate in candidate_names if candidate and candidate.strip()
        ]
        if not normalized_candidates:
            return None
        users = self.list_users()
        for candidate in normalized_candidates:
            for user in users:
                fields = [user.get("display_name") or "", user.get("name") or "", user.get("email") or ""]
                if any(field.strip().lower() == candidate for field in fields if field):
                    return user
        for candidate in normalized_candidates:
            for user in users:
                fields = [user.get("display_name") or "", user.get("name") or "", user.get("email") or ""]
                if any(candidate in field.strip().lower() for field in fields if field):
                    return user
        return None

    def _update(self, issue_id: str, input_payload: dict[str, Any]) -> dict[str, Any]:
        """Run ``issueUpdate`` and return the normalized issue."""
        data = self._post(query=ISSUE_UPDATE_MUTATION, variables={"id": issue_id, "input": input_payload})
        result = data.get("issueUpdate") or {}
        issue = result.get("issue")
        if not result.get("success") or not issue:
            raise ValueError("Linear API did not return an updated issue.")
        return self._normalize_issue(issue)

    def assign_issue(self, issue_id: str, assignee_id: str) -> dict[str, Any]:
        """Assign an existing issue (UUID or identifier such as ``ENG-123``) to a Linear user."""
        return self._update(issue_id, {"assigneeId": assignee_id})

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        state: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Update an issue's title, description, workflow state (name or type) and/or priority (JAN-333)."""
        input_payload: dict[str, Any] = {
            key: value
            for key, value in (("title", title), ("description", description), ("priority", priority))
            if value is not None
        }
        if state is not None:
            input_payload["stateId"] = self.resolve_state_id(issue_id, state)
        if not input_payload:
            raise LinearInputError("Nothing to update: give at least one of title, description, state, priority.")
        return self._update(issue_id, input_payload)

    def create_comment(self, issue_id: str, body: str) -> dict[str, Any]:
        """Add a Markdown comment to an issue (UUID or identifier) (JAN-333)."""
        data = self._post(query=COMMENT_CREATE_MUTATION, variables={"input": {"issueId": issue_id, "body": body}})
        result = data.get("commentCreate") or {}
        comment = result.get("comment")
        if not result.get("success") or not comment:
            raise ValueError("Linear API did not return a created comment.")
        issue = comment.get("issue") or {}
        return {
            "id": comment.get("id"),
            "body": comment.get("body"),
            "url": comment.get("url"),
            "created_at": comment.get("createdAt"),
            "issue": {"id": issue.get("id"), "identifier": issue.get("identifier")},
        }
