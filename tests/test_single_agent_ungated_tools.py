"""Regression tests: memory, topic, and local-planner tools are ungated (JAN-201).

The single Janus agent gets these tools unconditionally -- the same treatment
github/linear already have. No settings/flag or connection dependency gates
them. The event-log and trigger toolboxes are likewise ungated.
"""

import pytest

from janus import models, security
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_tool_set
from janus.capability_profile import ALWAYS_GRANTED_CAPABILITIES, user_connected_capabilities
from janus.runtime_adapter import RuntimeAdapter

# planner.local is read-only; its writers live in planner.local.write (which is
# confirmation-required, but still ungated by any connection or setting).
UNGATED_ENTRY_IDS = ("memory", "topics", "planner.local", "planner.local.write")
MEMORY_TOOLS = {"list_memory_facts", "search_facts", "add_memory_fact", "update_fact", "delete_fact", "group_facts"}
TOPIC_TOOLS = {"list_topics", "create_topic", "search_topics", "get_topic_detail", "update_topic_state"}
LOCAL_PLANNER_TOOLS = {
    "list_local_tasks",
    "get_local_task",
    "create_local_task",
    "update_local_task",
    "complete_local_task",
    "move_local_task",
    "reorder_local_task",
}


def _make_user(db, username: str) -> models.User:
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_registry_entries_have_no_enabled_when_gate():
    """The entries themselves must not consult any context, like github/linear."""
    entries = {entry.id: entry for entry in DEFAULT_TOOL_REGISTRY.entries()}
    for entry_id in (*UNGATED_ENTRY_IDS, "events", "trigger-history", "triggers"):
        # A context that would blow up on any attribute access proves the gate
        # reads nothing (no settings, no connection state).
        assert entries[entry_id].enabled_when(object()) is True


def test_capabilities_are_always_granted_without_any_connection(db):
    """No connection or settings row is needed for the capability tier either."""
    user = _make_user(db, "no-connections")
    assert {"memory", "topics", "planner.local"} <= ALWAYS_GRANTED_CAPABILITIES
    assert {"memory", "topics", "planner.local"} <= user_connected_capabilities(db, user)


def test_build_tool_set_includes_ungated_tools_without_any_connection(db):
    """With no connection at all, all memory/topic/local-planner tools (incl. writes) are built."""
    user = _make_user(db, "no-connections-build")
    tools = {tool.name for tool in build_tool_set(db, user)}

    assert tools >= MEMORY_TOOLS
    assert tools >= TOPIC_TOOLS
    assert tools >= LOCAL_PLANNER_TOOLS


@pytest.mark.parametrize("username", ["first-user", "second-user"])
def test_runtime_adapter_selects_ungated_tools_for_every_user(db, username):
    """The plan-driven chat path authorizes these tools for any user, with no denial."""
    user = _make_user(db, username)

    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id=f"chat-{username}",
        objective="hello",
        requested_tool_ids=UNGATED_ENTRY_IDS,
        task_mode="chat",
        granted_capabilities=user_connected_capabilities(db, user),
        confirmed_tool_ids=frozenset(UNGATED_ENTRY_IDS),
    )

    assert {entry.id for entry in prepared.selection.entries} == set(UNGATED_ENTRY_IDS)
    assert prepared.denials == ()
    built = {tool.name for tool in prepared.tools}
    assert built >= MEMORY_TOOLS | TOPIC_TOOLS | LOCAL_PLANNER_TOOLS
