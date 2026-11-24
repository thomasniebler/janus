"""Tests for opt-in bridges from existing helpers to new runtime contracts."""

from janus import agent_tools, models
from janus.tool_registry import ToolSelectionRequest


def test_authorized_tool_helper_returns_decision_and_only_allowlisted_tools(db):
    """The bridge exposes denials while preserving the default builder path."""
    user = models.User(username="bridge-test", hashed_password="not-a-password", is_admin=False)

    selection, tools = agent_tools.build_authorized_tool_set(
        db,
        user,
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=("core", "mail"),
            allowed_tool_ids=frozenset({"core"}),
            granted_capabilities=frozenset(),
        ),
    )

    assert [entry.id for entry in selection.entries] == ["core"]
    assert [(denial.tool_id, denial.reason) for denial in selection.denials] == [("mail", "not_allowlisted")]
    assert tools
