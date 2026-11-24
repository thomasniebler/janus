"""Tests for the blanket permission modes and *Always allow* in chat (JAN-359)."""

import asyncio
import json
from types import SimpleNamespace

from janus import models, security, tool_policy
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.delegation_service import DELEGATED_TASK_MODE
from janus.main import api
from janus.services.tool_approval import APPROVED, TOOL_APPROVAL_PREFIX, ToolApprovalBroker, ToolApprovalRequest
from janus.tool_registry import ToolRegistryEntry


def _user(db, username="modes-owner") -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _entry(tool_id: str) -> ToolRegistryEntry:
    return next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == tool_id)


def _remote_entry(tool_id="mcp.1.search", confirmation="required") -> ToolRegistryEntry:
    return ToolRegistryEntry(
        id=tool_id,
        display_name="Remote: search",
        description="remote",
        required_capabilities=("mcp",),
        operation="read" if confirmation == "never" else "mixed",
        risk_level="high",
        confirmation=confirmation,
        docs_ref=None,
        builder=lambda context: [],
    )


def _sets(db, user, task_mode="automation", entries=None):
    return tool_policy.unattended_tool_sets(
        db, user.id, entries if entries is not None else DEFAULT_TOOL_REGISTRY.entries(), task_mode=task_mode
    )


def test_modes_default_to_per_tool_and_reject_unknown_values(db):
    """Without a row both modes are per tool; an unknown value is refused before anything is written."""
    user = _user(db)
    assert tool_policy.get_modes(db, user.id) == {"unattended_mode": "per_tool", "chat_mode": "per_tool"}
    try:
        tool_policy.set_modes(db, user.id, unattended_mode="yolo")
    except tool_policy.ModeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ModeError")
    assert db.query(models.ToolPolicyMode).count() == 0


def test_allow_everything_confirms_every_switchable_tool_and_unlocks_images_and_mcp(db):
    """Allow everything overrides Off switches and lifts the image/MCP locks, but not the page-bound ones."""
    user = _user(db)
    entries = [*DEFAULT_TOOL_REGISTRY.entries(), _remote_entry()]
    tool_policy.set_policy(db, user.id, "memory", False)
    offered, confirmed = _sets(db, user, entries=entries)
    assert "mail.send" not in confirmed and "memory" not in confirmed
    assert "images" not in offered and "mcp.1.search" not in offered

    tool_policy.set_modes(db, user.id, unattended_mode="allow_all")
    offered, confirmed = _sets(db, user, entries=entries)
    assert {"mail.send", "memory", "github", "whatsapp.send", "images", "mcp.1.search"} <= confirmed
    assert offered - confirmed == {"self_evolution"}
    for still_locked in ("ui_actions", "ui_blocks", "ui_introspection", "triggers.propose", "character.propose"):
        assert still_locked not in offered
    # The self-evolution switch keeps governing its own entry.
    assert "self_evolution" not in confirmed

    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, user.id, entries)}
    assert rows["memory"]["unattended_allowed"] is False
    assert rows["memory"]["effective_unattended_allowed"] is True
    assert rows["images"]["effective_unattended_allowed"] is True
    assert rows["ui_blocks"]["effective_unattended_allowed"] is False


def test_allow_everything_keeps_delegated_children_read_only(db):
    """A delegated child still only gets read-only entries under Allow everything."""
    user = _user(db)
    tool_policy.set_modes(db, user.id, unattended_mode="allow_all")
    offered, _ = _sets(db, user, task_mode=DELEGATED_TASK_MODE)
    read_only = {entry.id for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.operation == "read"}
    assert offered <= read_only
    assert "mail.send" not in offered


def test_chat_preconfirmation_follows_always_allow_and_the_chat_mode(db):
    """Always allow pre-confirms one required tool; Allow all every one; Off in chat stays withheld."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    base = tool_policy.chat_preconfirmed_tool_ids(db, user.id, entries)
    assert "memory" in base and "mail.send" not in base and "github" not in base

    tool_policy.update_policy(db, user.id, "mail.send", chat_mode="always", entry=_entry("mail.send"))
    assert "mail.send" in tool_policy.chat_preconfirmed_tool_ids(db, user.id, entries)
    assert "github" not in tool_policy.chat_preconfirmed_tool_ids(db, user.id, entries)

    tool_policy.update_policy(db, user.id, "linear", chat_mode="off", entry=_entry("linear"))
    tool_policy.set_modes(db, user.id, chat_mode="allow_all")
    preconfirmed = tool_policy.chat_preconfirmed_tool_ids(db, user.id, entries)
    assert {"mail.send", "github", "whatsapp.send"} <= preconfirmed
    assert "linear" not in preconfirmed
    assert "linear" in tool_policy.chat_withheld_tool_ids(db, user.id, entries)


def test_policy_modes_api_and_always_allow_switch(client, db):
    """The modes endpoints round-trip; chat_mode "always" is accepted only on required tools."""
    user = _user(db)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=user.id, is_admin=False)
    try:
        assert client.get("/api/tools/policy-modes").json() == {"unattended_mode": "per_tool", "chat_mode": "per_tool"}
        updated = client.put("/api/tools/policy-modes", json={"chat_mode": "allow_all"})
        assert updated.status_code == 200
        assert updated.json() == {"unattended_mode": "per_tool", "chat_mode": "allow_all"}
        assert client.put("/api/tools/policy-modes", json={"unattended_mode": "nope"}).status_code == 422

        always = client.put("/api/tools/policies/mail.send", json={"chat_mode": "always"})
        assert always.status_code == 200
        assert (always.json()["chat_mode"], always.json()["effective_chat_asks"]) == ("always", False)
        assert client.put("/api/tools/policies/memory", json={"chat_mode": "always"}).status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_broker_always_answer_approves_and_remembers_the_tool():
    """An "always" answer approves the call and calls the remember hook with its entry once the call runs."""
    remembered: list[str] = []
    entry = _entry("mail.send")

    async def scenario():
        sent: list[str] = []

        async def send(frame):
            sent.append(frame)

        broker = ToolApprovalBroker(
            send, user_id=1, timeout_seconds=5, on_always_allow=lambda e: remembered.append(e.id)
        )
        broker.note_started("call-1")
        request = ToolApprovalRequest(
            call_id="call-1",
            tool_id=entry.id,
            tool_name="send_email",
            display_name=entry.display_name,
            risk_level="high",
            reason="Send mail.",
            args={},
            args_truncated=False,
            timeout_seconds=5,
        )
        pending = asyncio.create_task(broker.request(request))
        while not sent:
            await asyncio.sleep(0)
        assert sent[0].startswith(TOOL_APPROVAL_PREFIX)
        assert broker.handle_frame(json.dumps({"type": "tool_approval", "call_id": "call-1", "decision": "always"}))
        assert await pending == APPROVED
        assert broker.decisions == {"call-1": APPROVED}
        broker._apply_always("call-1", entry)
        broker._apply_always("call-1", entry)  # only once

    asyncio.run(scenario())
    assert remembered == ["mail.send"]
