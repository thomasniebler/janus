"""Regression tests for the Linear GraphQL client (JAN-196 / JAN-181).

Janus could neither read nor write Linear issues because two operations
declared ``ID!`` variables for arguments Linear types as ``String!`` (Linear
rejects the whole document at validation time), the resulting HTTP 400 error
body was discarded, and ``issueCreate`` was handed a team *key* where Linear
requires the team UUID. Later additions: team discovery and resolution by
key, name or UUID (JAN-330), and issue updates, workflow states and comments
(JAN-333).

The schema fixture is the ``schema.graphql`` published with Linear's official
``@linear/sdk`` (linear/linear@41cff77b3a62d93a30c3c3ceabc92ab77333f9da),
gzipped. Refresh it when Linear changes its API.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from graphql import build_schema, parse, validate

from janus.services import linear_queries
from janus.services.linear_service import LinearInputError, LinearService

_SCHEMA_PATH = Path(__file__).parent / "fixtures" / "linear_schema.graphql.gz"
_TEAM_UUID = "9cfb482a-81e3-4154-b5b9-2c805e70a02d"


_STATES = [
    {"id": "state-done", "name": "Done", "type": "completed", "position": 4},
    {"id": "state-todo", "name": "Todo", "type": "unstarted", "position": 1},
    {"id": "state-review", "name": "In Review", "type": "started", "position": 3},
    {"id": "state-progress", "name": "In Progress", "type": "started", "position": 2},
    {"id": "state-canceled", "name": "Canceled", "type": "canceled", "position": 5},
]
_COMMENT = {
    "id": "comment-uuid",
    "body": "Looks good",
    "url": "https://linear.app/acme/issue/ENG-1#comment-1",
    "createdAt": "2026-10-11T00:00:00.000Z",
    "issue": {"id": "issue-uuid", "identifier": "ENG-1"},
}


@pytest.fixture(scope="module")
def linear_schema():
    """Linear's published GraphQL schema."""
    with gzip.open(_SCHEMA_PATH, "rt", encoding="utf-8") as handle:
        return build_schema(handle.read())


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "id": "issue-uuid",
        "identifier": "ENG-1",
        "title": "Title",
        "description": "Body",
        "url": "https://linear.app/acme/issue/ENG-1",
        "updatedAt": "2026-09-01T00:00:00.000Z",
        "state": {"name": "Todo", "type": "unstarted"},
        "parent": None,
        "assignee": None,
    }
    issue.update(overrides)
    return issue


class _Recorder:
    """Mock transport that records requests and answers per operation name."""

    def __init__(self, responder: Callable[[str, dict[str, Any]], httpx.Response]):
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(request)
        self.bodies.append(body)
        operation = parse(body["query"]).definitions[0].name.value
        return self._responder(operation, body["variables"])


def _service(responder, api_key: str = "lin_api_test") -> tuple[LinearService, _Recorder]:
    recorder = _Recorder(responder)
    return LinearService(api_key, transport=httpx.MockTransport(recorder)), recorder


@pytest.mark.parametrize("document", linear_queries.ALL_OPERATIONS)
def test_every_operation_validates_against_linear_schema(linear_schema, document):
    """Each GraphQL document must pass Linear's schema validation.

    Regression: ``$teamId: ID!`` / ``$id: ID!`` were passed to ``team(id:)`` and
    ``issueUpdate(id:)``, which take ``String!``, so Linear rejected every
    list/assign request.
    """
    errors = validate(linear_schema, parse(document))
    assert [error.message for error in errors] == []


def test_all_operations_registry_covers_every_request(linear_schema):
    """Every document actually sent by the service is listed in ALL_OPERATIONS."""

    def responder(operation, _variables):
        data = {
            "Viewer": {"viewer": {"displayName": "Ada"}},
            "TeamLookup": {"teams": {"nodes": [{"id": _TEAM_UUID, "key": "ENG", "name": "Eng"}]}},
            "Teams": {"teams": {"nodes": [{"id": _TEAM_UUID, "key": "ENG", "name": "Eng"}]}},
            "TeamIssues": {"team": {"id": _TEAM_UUID, "name": "Eng", "issues": {"nodes": []}}},
            "IssueTeam": {"issue": {"id": "issue-uuid", "identifier": "ENG-1", "team": {"id": _TEAM_UUID}}},
            "WorkflowStates": {"team": {"id": _TEAM_UUID, "states": {"nodes": _STATES}}},
            "Users": {"users": {"nodes": []}},
            "IssueCreate": {"issueCreate": {"success": True, "issue": _issue()}},
            "IssueUpdate": {"issueUpdate": {"success": True, "issue": _issue()}},
            "CommentCreate": {"commentCreate": {"success": True, "comment": _COMMENT}},
        }[operation]
        return httpx.Response(200, json={"data": data})

    service, recorder = _service(responder)
    service.test_connection()
    service.list_team_issues("ENG")
    service.list_users()
    service.create_issue("ENG", "t", "d")
    service.assign_issue("ENG-1", "user-uuid")
    service.list_teams()
    service.update_issue("ENG-1", title="Renamed", state="Done")
    service.create_comment("ENG-1", "Looks good")

    sent = {body["query"] for body in recorder.bodies}
    assert sent <= set(linear_queries.ALL_OPERATIONS)
    for body in recorder.bodies:
        assert validate(linear_schema, parse(body["query"])) == []


def test_api_key_is_sent_verbatim_and_stripped():
    """Personal API keys go in Authorization without a Bearer prefix; whitespace is stripped."""
    service, recorder = _service(
        lambda _op, _vars: httpx.Response(200, json={"data": {"viewer": {"name": "ada"}}}),
        api_key="  lin_api_secret\n",
    )
    assert service.test_connection() == (True, "Connected as ada")
    assert recorder.requests[0].headers["Authorization"] == "lin_api_secret"
    assert str(recorder.requests[0].url) == "https://api.linear.app/graphql"


def test_http_400_graphql_errors_are_surfaced():
    """Linear returns validation/auth failures as HTTP 400 with an errors body; keep the message."""
    service, _ = _service(
        lambda _op, _vars: httpx.Response(
            400,
            json={
                "errors": [
                    {
                        "message": "Authentication required, not authenticated",
                        "extensions": {"code": "AUTHENTICATION_ERROR"},
                    }
                ]
            },
        )
    )
    with pytest.raises(ValueError, match="Authentication required, not authenticated"):
        service.list_users()


def test_user_presentable_message_is_preferred():
    """Linear's extensions.userPresentableMessage is clearer than the raw message."""
    service, _ = _service(
        lambda _op, _vars: httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "Argument Validation Error",
                        "extensions": {"userPresentableMessage": "teamId must be a UUID."},
                    }
                ],
                "data": None,
            },
        )
    )
    with pytest.raises(ValueError, match="teamId must be a UUID"):
        service.list_users()


def test_non_json_error_reports_status():
    """A non-JSON error page falls back to reporting the HTTP status."""
    service, _ = _service(lambda _op, _vars: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(ValueError, match="HTTP 502"):
        service.list_users()


def test_connection_error_is_wrapped():
    """Network failures become ValueErrors with a clear prefix."""

    def responder(_op, _vars):
        raise httpx.ConnectError("boom")

    service, _ = _service(responder)
    with pytest.raises(ValueError, match="Connection error while calling Linear API"):
        service.list_users()


def test_test_connection_reports_reason_on_failure():
    """The connection test explains why it failed instead of a generic message."""
    service, _ = _service(
        lambda _op, _vars: httpx.Response(400, json={"errors": [{"message": "Invalid API key"}]}),
    )
    ok, message = service.test_connection()
    assert ok is False
    assert "Invalid API key" in message


def test_create_issue_resolves_team_key_to_uuid():
    """A team key such as ``ENG`` (what the Settings UI suggests) is resolved to the UUID Linear requires."""

    def responder(operation, variables):
        if operation == "TeamLookup":
            assert variables == {"ref": "eng"}
            return httpx.Response(
                200, json={"data": {"teams": {"nodes": [{"id": _TEAM_UUID, "key": "ENG", "name": "Eng"}]}}}
            )
        assert operation == "IssueCreate"
        assert variables["input"] == {
            "teamId": _TEAM_UUID,
            "title": "New",
            "description": "Body",
            "parentId": "ENG-7",
        }
        return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": _issue(title="New")}}})

    service, recorder = _service(responder)
    issue = service.create_issue(" eng ", "New", "Body", parent_id="ENG-7")
    assert issue["identifier"] == "ENG-1"
    assert issue["title"] == "New"
    assert [parse(b["query"]).definitions[0].name.value for b in recorder.bodies] == ["TeamLookup", "IssueCreate"]


def test_team_uuid_is_used_without_lookup():
    """A team UUID is passed straight through without a key lookup."""

    def responder(operation, variables):
        assert operation == "IssueCreate"
        assert variables["input"]["teamId"] == _TEAM_UUID
        return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": _issue()}}})

    service, recorder = _service(responder)
    service.create_issue(_TEAM_UUID, "t", "d")
    assert len(recorder.bodies) == 1


def test_team_key_lookup_is_cached_per_service():
    """Team key lookups happen once per service instance, case-insensitively."""

    def responder(operation, _variables):
        if operation == "TeamLookup":
            return httpx.Response(200, json={"data": {"teams": {"nodes": [{"id": _TEAM_UUID, "key": "ENG"}]}}})
        return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": _issue()}}})

    service, recorder = _service(responder)
    service.create_issue("ENG", "a", "b")
    service.create_issue("eng", "c", "d")
    operations = [parse(b["query"]).definitions[0].name.value for b in recorder.bodies]
    assert operations.count("TeamLookup") == 1


def _teams_responder(teams: list[dict[str, Any]]):
    """Answer team lookups and team listings from ``teams``, like Linear's case-insensitive filter."""

    def responder(operation, variables):
        if operation == "TeamLookup":
            ref = variables["ref"].casefold()
            nodes = [team for team in teams if ref in {team["key"].casefold(), team["name"].casefold()}]
            return httpx.Response(200, json={"data": {"teams": {"nodes": nodes}}})
        assert operation == "Teams"
        return httpx.Response(200, json={"data": {"teams": {"nodes": teams}}})

    return responder


_TEAMS = [
    {"id": _TEAM_UUID, "key": "JAN", "name": "Janus"},
    {"id": "1f0c0a8e-0000-4000-8000-000000000002", "key": "OPS", "name": "Operations"},
]


def test_unknown_team_key_raises_actionable_error_listing_keys():
    """An unknown team raises an error telling the model what to enter, with the available keys (JAN-330)."""
    service, _ = _service(_teams_responder(_TEAMS))
    with pytest.raises(LinearInputError, match="Linear team 'NOPE' was not found") as error:
        service.create_issue("NOPE", "t", "d")
    assert "Available team keys: JAN, OPS." in str(error.value)


@pytest.mark.parametrize("ref", ["JAN", "jan", "Janus", "JANUS", _TEAM_UUID])
def test_resolve_team_id_accepts_key_name_or_uuid(ref):
    """A team key or exact team name, case-insensitively, or a UUID resolves to the team UUID (JAN-330)."""
    service, _ = _service(_teams_responder(_TEAMS))
    assert service.resolve_team_id(ref) == _TEAM_UUID


def test_resolve_team_id_prefers_key_over_name():
    """When one team's key equals another team's name, the key match wins."""
    teams = [
        {"id": "1f0c0a8e-0000-4000-8000-000000000003", "key": "CORE", "name": "Ops"},
        {"id": _TEAM_UUID, "key": "OPS", "name": "Operations"},
    ]
    service, _ = _service(_teams_responder(teams))
    assert service.resolve_team_id("ops") == _TEAM_UUID


def test_choose_team_uses_the_only_team_without_default():
    """No team and no default: a single-team workspace uses its team and says so (JAN-330)."""
    service, _ = _service(_teams_responder(_TEAMS[:1]))
    team_id, note = service.choose_team(None, None)
    assert team_id == _TEAM_UUID
    assert note is not None and "JAN (Janus)" in note


def test_choose_team_without_default_and_several_teams_lists_keys():
    """No team, no default, several teams: refuse with the keys to choose from."""
    service, _ = _service(_teams_responder(_TEAMS))
    with pytest.raises(LinearInputError, match="no default team is configured. Available team keys: JAN, OPS"):
        service.choose_team("", None)


def test_choose_team_prefers_explicit_over_default():
    """An explicit team beats the configured default; the default beats the single-team fallback."""
    service, _ = _service(_teams_responder(_TEAMS))
    assert service.choose_team("ops", "JAN") == (_TEAMS[1]["id"], None)
    assert service.choose_team(None, "Janus") == (_TEAM_UUID, None)


def test_list_team_issues_filters_closed_states_server_side():
    """Reading issues sends the team UUID as String and filters closed issues in the query."""

    def responder(operation, variables):
        assert operation == "TeamIssues"
        assert variables == {
            "teamId": _TEAM_UUID,
            "first": 5,
            "filter": {"state": {"type": {"nin": ["completed", "canceled"]}}},
        }
        nodes = [
            _issue(identifier="ENG-1", updatedAt="2026-09-01T00:00:00.000Z"),
            _issue(identifier="ENG-2", updatedAt="2026-09-03T00:00:00.000Z"),
        ]
        return httpx.Response(200, json={"data": {"team": {"id": _TEAM_UUID, "issues": {"nodes": nodes}}}})

    service, _ = _service(responder)
    issues = service.list_team_issues(_TEAM_UUID, limit=5)
    assert [issue["identifier"] for issue in issues] == ["ENG-2", "ENG-1"]


def test_list_team_issues_include_completed_omits_filter():
    """include_completed drops the server-side state filter."""

    def responder(_operation, variables):
        assert "filter" not in variables
        return httpx.Response(200, json={"data": {"team": {"issues": {"nodes": []}}}})

    service, _ = _service(responder)
    assert service.list_team_issues(_TEAM_UUID, include_completed=True) == []


def test_assign_issue_sends_identifier_as_string_id():
    """Assigning sends the issue identifier as the String! id argument."""

    def responder(operation, variables):
        assert operation == "IssueUpdate"
        assert variables == {"id": "ENG-1", "input": {"assigneeId": "user-uuid"}}
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueUpdate": {
                        "success": True,
                        "issue": _issue(assignee={"id": "user-uuid", "displayName": "Copilot"}),
                    }
                }
            },
        )

    service, _ = _service(responder)
    issue = service.assign_issue("ENG-1", "user-uuid")
    assert issue["assignee"]["display_name"] == "Copilot"


def _update_responder(captured: list[dict[str, Any]]):
    def responder(operation, variables):
        captured.append({"operation": operation, "variables": variables})
        if operation == "IssueTeam":
            assert variables == {"id": "ENG-1"}
            return httpx.Response(200, json={"data": {"issue": {"id": "i", "team": {"id": _TEAM_UUID}}}})
        if operation == "WorkflowStates":
            assert variables == {"teamId": _TEAM_UUID}
            return httpx.Response(200, json={"data": {"team": {"id": _TEAM_UUID, "states": {"nodes": _STATES}}}})
        assert operation == "IssueUpdate"
        return httpx.Response(200, json={"data": {"issueUpdate": {"success": True, "issue": _issue(priority=2)}}})

    return responder


def test_update_issue_maps_fields_to_issue_update():
    """Title, description and priority go straight into issueUpdate's input (JAN-333)."""
    captured: list[dict[str, Any]] = []
    service, _ = _service(_update_responder(captured))
    issue = service.update_issue("ENG-1", title="New title", description="Body", priority=0)
    assert captured == [
        {
            "operation": "IssueUpdate",
            "variables": {"id": "ENG-1", "input": {"title": "New title", "description": "Body", "priority": 0}},
        }
    ]
    assert issue["priority"] == 2


@pytest.mark.parametrize(
    ("state", "state_id"),
    [
        ("done", "state-done"),
        ("In Review", "state-review"),
        ("started", "state-progress"),  # by type: the first started state in board order
        ("CANCELED", "state-canceled"),
        ("unstarted", "state-todo"),
    ],
)
def test_update_issue_resolves_state_by_name_or_type(state, state_id):
    """A state is matched by the team's workflow-state name, then by type, case-insensitively (JAN-333)."""
    captured: list[dict[str, Any]] = []
    service, _ = _service(_update_responder(captured))
    service.update_issue("ENG-1", state=state)
    assert [item["operation"] for item in captured] == ["IssueTeam", "WorkflowStates", "IssueUpdate"]
    assert captured[-1]["variables"]["input"] == {"stateId": state_id}


def test_update_issue_unknown_state_is_refused_with_valid_states():
    """An unknown state raises before any update, listing the valid states and types."""
    captured: list[dict[str, Any]] = []
    service, _ = _service(_update_responder(captured))
    with pytest.raises(LinearInputError, match="Unknown workflow state 'Shipped'") as error:
        service.update_issue("ENG-1", state="Shipped")
    assert "Todo (unstarted), In Progress (started), In Review (started), Done (completed)" in str(error.value)
    assert "IssueUpdate" not in [item["operation"] for item in captured]


def test_update_issue_without_fields_is_refused():
    """An empty update never reaches Linear."""
    service, recorder = _service(lambda _op, _vars: pytest.fail("no request expected"))
    with pytest.raises(LinearInputError, match="Nothing to update"):
        service.update_issue("ENG-1")
    assert recorder.bodies == []


def test_list_workflow_states_in_board_order():
    """Workflow states come back as {id, name, type}, sorted by board position."""
    service, _ = _service(_update_responder([]))
    states = service.list_workflow_states(_TEAM_UUID)
    assert [state["name"] for state in states] == ["Todo", "In Progress", "In Review", "Done", "Canceled"]
    assert states[0] == {"id": "state-todo", "name": "Todo", "type": "unstarted"}


def test_create_comment_sends_issue_and_body():
    """A comment is created with the issue id and Markdown body (JAN-333)."""

    def responder(operation, variables):
        assert operation == "CommentCreate"
        assert variables == {"input": {"issueId": "ENG-1", "body": "Looks good"}}
        return httpx.Response(200, json={"data": {"commentCreate": {"success": True, "comment": _COMMENT}}})

    service, _ = _service(responder)
    comment = service.create_comment("ENG-1", "Looks good")
    assert comment == {
        "id": "comment-uuid",
        "body": "Looks good",
        "url": "https://linear.app/acme/issue/ENG-1#comment-1",
        "created_at": "2026-10-11T00:00:00.000Z",
        "issue": {"id": "issue-uuid", "identifier": "ENG-1"},
    }


def test_list_teams_sorted_by_key():
    """Teams are listed as {id, key, name}, sorted by key."""
    service, _ = _service(_teams_responder(list(reversed(_TEAMS))))
    assert [team["key"] for team in service.list_teams()] == ["JAN", "OPS"]
