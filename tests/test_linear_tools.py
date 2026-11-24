"""Linear toolbox behavior over a mocked Linear API (JAN-330, JAN-332, JAN-333).

JAN-330: Janus guessed team names and invented UUIDs because it could not see
which teams exist or which is the default. JAN-332: issues were filed with the
description crammed into the title and an empty body. JAN-333: existing issues
could not be updated or commented on.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from graphql import parse
from pydantic import ValidationError

from janus import models, security
from janus.agent_tools import build_tool_set
from janus.services.linear_service import LinearService

_JAN = {"id": "9cfb482a-81e3-4154-b5b9-2c805e70a02d", "key": "JAN", "name": "Janus"}
_OPS = {"id": "1f0c0a8e-0000-4000-8000-000000000002", "key": "OPS", "name": "Operations"}
_STATES = [
    {"id": "state-todo", "name": "Todo", "type": "unstarted", "position": 1},
    {"id": "state-progress", "name": "In Progress", "type": "started", "position": 2},
    {"id": "state-done", "name": "Done", "type": "completed", "position": 3},
]
_DESCRIPTION = (
    "## Context\nMail search cannot filter by date.\n\n## Change\nAdd a date range.\n\n## Done when\n- [ ] it filters"
)


def _issue(**fields: Any) -> dict[str, Any]:
    return {"id": "issue-uuid", "identifier": "JAN-9", "title": "t", "url": "u", "state": None, **fields}


class FakeLinear:
    """Records operations and answers them like Linear's GraphQL API."""

    def __init__(self, teams: list[dict[str, Any]]):
        """Start with ``teams`` and no recorded calls."""
        self.teams = teams
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Record and answer one GraphQL request."""
        body = json.loads(request.content)
        operation = parse(body["query"]).definitions[0].name.value
        variables = body["variables"]
        self.calls.append((operation, variables))
        return httpx.Response(200, json={"data": self._data(operation, variables)})

    def _data(self, operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "TeamLookup":
            ref = variables["ref"].casefold()
            return {"teams": {"nodes": [t for t in self.teams if ref in {t["key"].casefold(), t["name"].casefold()}]}}
        if operation == "Teams":
            return {"teams": {"nodes": self.teams}}
        if operation == "TeamIssues":
            return {"team": {"issues": {"nodes": [_issue()]}}}
        if operation == "IssueCreate":
            return {"issueCreate": {"success": True, "issue": _issue(title=variables["input"]["title"])}}
        if operation == "IssueTeam":
            return {"issue": {"id": "issue-uuid", "team": {"id": _JAN["id"]}}}
        if operation == "WorkflowStates":
            return {"team": {"id": _JAN["id"], "states": {"nodes": _STATES}}}
        if operation == "IssueUpdate":
            return {"issueUpdate": {"success": True, "issue": _issue(**variables["input"])}}
        if operation == "CommentCreate":
            comment = {"id": "c1", "body": variables["input"]["body"], "url": "u", "createdAt": "now", "issue": None}
            return {"commentCreate": {"success": True, "comment": comment}}
        raise AssertionError(operation)

    def operations(self) -> list[str]:
        """Return the recorded operation names in order."""
        return [operation for operation, _ in self.calls]


def _tools(db, monkeypatch, *, teams=(_JAN, _OPS), default_team: str | None = "JAN"):
    user = models.User(
        username="linear-tools", hashed_password=security.security_service.get_password_hash("x"), is_admin=False
    )
    db.add(user)
    db.add(
        models.Settings(
            linear_api_key=security.security_service.encrypt("lin_api_test"), linear_default_team_id=default_team
        )
    )
    db.commit()
    fake = FakeLinear(list(teams))
    monkeypatch.setattr(
        "janus.agent_toolboxes.linear.LinearService",
        lambda api_key: LinearService(api_key, transport=httpx.MockTransport(fake.handler)),
    )
    return {tool.name: tool for tool in build_tool_set(db, user)}, fake


# --- JAN-330: teams --------------------------------------------------------------


def test_list_linear_teams_marks_the_configured_default(db, monkeypatch):
    """The team list shows every team and marks the configured default, matched by key, name or id."""
    tools, _ = _tools(db, monkeypatch, default_team="janus")
    assert tools["list_linear_teams"].invoke({}) == [{**_JAN, "is_default": True}, {**_OPS, "is_default": False}]


def test_list_linear_teams_marks_the_only_team_without_default(db, monkeypatch):
    """With no default configured, the workspace's only team is the one the tools use."""
    tools, _ = _tools(db, monkeypatch, teams=(_JAN,), default_team=None)
    assert tools["list_linear_teams"].invoke({}) == [{**_JAN, "is_default": True}]


def test_tool_descriptions_name_the_default_team(db, monkeypatch):
    """Tools that take a team say which default team they fall back to."""
    tools, _ = _tools(db, monkeypatch, default_team="JAN")
    for name in ("list_linear_tasks", "add_linear_task", "add_linear_subtask"):
        assert "configured default team `JAN`" in tools[name].description


def test_tool_descriptions_without_default_point_to_team_listing(db, monkeypatch):
    """Without a default the descriptions say to pass a team and where to find one."""
    tools, _ = _tools(db, monkeypatch, default_team=None)
    assert "No default team is configured" in tools["add_linear_task"].description
    assert "list_linear_teams" in tools["add_linear_task"].description


def test_list_tasks_accepts_a_team_name(db, monkeypatch):
    """A team's exact name resolves like its key."""
    tools, fake = _tools(db, monkeypatch)
    tools["list_linear_tasks"].invoke({"team_id": "operations"})
    assert ("TeamIssues", {"teamId": _OPS["id"], "first": 20, "filter": fake.calls[-1][1]["filter"]}) == fake.calls[-1]


def test_single_team_fallback_creates_there_and_says_so(db, monkeypatch):
    """No team and no default in a one-team workspace: use that team and say so in the result."""
    tools, fake = _tools(db, monkeypatch, teams=(_JAN,), default_team=None)
    result = tools["add_linear_task"].invoke({"title": "Add date filters", "description": _DESCRIPTION})
    assert "workspace's only team JAN (Janus)" in result["note"]
    assert fake.calls[-1][1]["input"]["teamId"] == _JAN["id"]
    listed = tools["list_linear_tasks"].invoke({})
    assert "only team JAN" in listed["note"] and listed["issues"][0]["identifier"] == "JAN-9"


def test_no_default_and_several_teams_is_refused_with_keys(db, monkeypatch):
    """Without a team to use, the answer lists the keys to retry with and nothing is created."""
    tools, fake = _tools(db, monkeypatch, default_team=None)
    result = tools["add_linear_task"].invoke({"title": "Add date filters", "description": _DESCRIPTION})
    assert result.startswith("REFUSED: No team specified") and "Available team keys: JAN, OPS." in result
    assert "IssueCreate" not in fake.operations()


def test_unknown_team_is_refused_with_keys(db, monkeypatch):
    """An invented team answers REFUSED with the real keys instead of failing the turn."""
    tools, fake = _tools(db, monkeypatch)
    result = tools["add_linear_task"].invoke(
        {"title": "Add date filters", "description": _DESCRIPTION, "team_id": "Engineering"}
    )
    assert "Linear team 'Engineering' was not found" in result and "Available team keys: JAN, OPS." in result
    assert "IssueCreate" not in fake.operations()


# --- JAN-332: creation validation --------------------------------------------------


def test_missing_description_is_rejected_before_linear(db, monkeypatch):
    """``description`` is a required argument now; omitting it never reaches Linear."""
    tools, fake = _tools(db, monkeypatch)
    with pytest.raises(ValidationError):
        tools["add_linear_task"].invoke({"title": "Add date filters"})
    assert fake.calls == []


@pytest.mark.parametrize(
    ("title", "description", "reason"),
    [
        ("Add date filters", "", "description must be 40"),
        ("Add date filters", "  too short  ", "description must be 40"),
        ("Add date filters", "x" * 20_001, "description must be 40 to 20000"),
        ("Fix", _DESCRIPTION, "title must be 5 to 120"),
        ("x" * 121, _DESCRIPTION, "title must be 5 to 120"),
        ("Add date\nfilters", _DESCRIPTION, "single line"),
        (
            "[Self-Evolution] Implementation: date filters Description: add a date range",
            _DESCRIPTION,
            "move that text into `description`",
        ),
        ("Add date filters (description: soon)", _DESCRIPTION, "inline 'Description:'"),
    ],
)
@pytest.mark.parametrize("tool_name", ["add_linear_task", "add_linear_subtask"])
def test_bad_title_or_description_is_refused(db, monkeypatch, tool_name, title, description, reason):
    """A bad title or description answers REFUSED and creates nothing."""
    tools, fake = _tools(db, monkeypatch)
    arguments = {"title": title, "description": description}
    if tool_name == "add_linear_subtask":
        arguments["parent_issue_id"] = "JAN-1"
    result = tools[tool_name].invoke(arguments)
    assert result.startswith("REFUSED: ") and reason in result and "Nothing was sent to Linear" in result
    assert fake.calls == []


def test_valid_task_passes_title_and_description_to_issue_create(db, monkeypatch):
    """A valid call sends the (trimmed) title and description to issueCreate."""
    tools, fake = _tools(db, monkeypatch)
    tools["add_linear_subtask"].invoke(
        {"parent_issue_id": "JAN-1", "title": "  Add date filters ", "description": f"\n{_DESCRIPTION}\n"}
    )
    assert fake.calls[-1] == (
        "IssueCreate",
        {
            "input": {
                "teamId": _JAN["id"],
                "title": "Add date filters",
                "description": _DESCRIPTION,
                "parentId": "JAN-1",
            }
        },
    )


def test_create_docstrings_explain_a_good_description(db, monkeypatch):
    """The tool descriptions tell the model what a good description contains."""
    tools, _ = _tools(db, monkeypatch)
    for name in ("add_linear_task", "add_linear_subtask"):
        text = tools[name].description
        assert "context and why" in text and "acceptance criteria" in text and "40-20000" in text


# --- JAN-333: update and comment -----------------------------------------------------


def test_update_maps_fields_and_resolves_state(db, monkeypatch):
    """Title, description, priority and a state name map onto one issueUpdate."""
    tools, fake = _tools(db, monkeypatch)
    tools["update_linear_task"].invoke(
        {"issue_id": "JAN-9", "title": "Add date filters", "description": _DESCRIPTION, "state": "in progress"}
    )
    assert fake.operations() == ["IssueTeam", "WorkflowStates", "IssueUpdate"]
    assert fake.calls[-1][1] == {
        "id": "JAN-9",
        "input": {"title": "Add date filters", "description": _DESCRIPTION, "stateId": "state-progress"},
    }
    tools["update_linear_task"].invoke({"issue_id": "JAN-9", "state": "completed", "priority": 1})
    assert fake.calls[-1][1]["input"] == {"priority": 1, "stateId": "state-done"}


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({}, "Nothing to update"),
        ({"priority": 5}, "priority must be 0"),
        ({"priority": -1}, "priority must be 0"),
        ({"state": "  "}, "state must name"),
        ({"title": "Add\nfilters"}, "single line"),
        ({"description": "short"}, "description must be 40"),
    ],
)
def test_bad_update_is_refused_before_linear(db, monkeypatch, arguments, reason):
    """Invalid updates answer REFUSED without any Linear request."""
    tools, fake = _tools(db, monkeypatch)
    result = tools["update_linear_task"].invoke({"issue_id": "JAN-9", **arguments})
    assert result.startswith("REFUSED: ") and reason in result
    assert fake.calls == []


def test_unknown_state_is_refused_with_valid_states(db, monkeypatch):
    """An unknown state answers REFUSED listing the team's states; nothing is updated."""
    tools, fake = _tools(db, monkeypatch)
    result = tools["update_linear_task"].invoke({"issue_id": "JAN-9", "state": "Shipped"})
    assert result.startswith("REFUSED: Unknown workflow state 'Shipped'")
    assert "Todo (unstarted), In Progress (started), Done (completed)" in result
    assert "IssueUpdate" not in fake.operations()


def test_comment_is_created(db, monkeypatch):
    """A comment goes to commentCreate with the issue id and trimmed Markdown body."""
    tools, fake = _tools(db, monkeypatch)
    result = tools["comment_on_linear_task"].invoke({"issue_id": "JAN-9", "body": " Done in **#12**. "})
    assert fake.calls == [("CommentCreate", {"input": {"issueId": "JAN-9", "body": "Done in **#12**."}})]
    assert result["body"] == "Done in **#12**."


@pytest.mark.parametrize("body", ["", "   ", "x" * 10_001])
def test_bad_comment_is_refused(db, monkeypatch, body):
    """An empty or oversized comment answers REFUSED without any Linear request."""
    tools, fake = _tools(db, monkeypatch)
    result = tools["comment_on_linear_task"].invoke({"issue_id": "JAN-9", "body": body})
    assert result.startswith("REFUSED: The comment must be 1 to 10000 characters")
    assert fake.calls == []
