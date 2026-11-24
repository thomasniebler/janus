"""Tests for the per-user "Unattended" and "In chat" switches and documented refusals (JAN-229, JAN-255, JAN-306)."""

from types import SimpleNamespace

import pytest

from janus import models, security, tool_policy
from janus import trigger_definition_service as definitions
from janus.agent_task_runner import AgentTaskRun
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.main import api
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerDispatcher


def _user(db, username="policy-owner", *, with_mail=True) -> models.User:
    user = models.User(username=username, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    if with_mail:
        db.add(models.EmailAccount(name="Mail", email=f"{username}@example.com", password="secret", user_id=user.id))
        db.commit()
    return user


def _confirmed(db, user, definition=None, *, narrowed_to=None) -> frozenset[str]:
    return tool_policy.unattended_tool_sets(
        db,
        user.id,
        DEFAULT_TOOL_REGISTRY.entries(),
        task_mode="automation",
        narrowed_to=narrowed_to,
        definition=definition,
    )[1]


def test_confirmation_required_capabilities_are_off_by_default(db):
    """Confirmation-required capabilities are off for unattended runs by default; the rest are allowed."""
    user = _user(db)
    allowed = tool_policy.unattended_allowed_tool_ids(db, user.id, DEFAULT_TOOL_REGISTRY.entries())
    assert not {"mail.send", "planner.local.write", "rules.write", "github", "linear", "skills"} & allowed
    assert {"core", "mail", "mail.label", "mail.processing", "memory", "topics", "web_search"} <= allowed
    # Owner-scoped (JAN-272) and loop-guarded (JAN-274): no longer locked, allowed by default.
    assert {"triggers", "events"} <= allowed
    # Only the chat-only proposal toolbox (JAN-235), page actions (JAN-251), UI blocks (JAN-252),
    # image generation (JAN-253), UI introspection (JAN-283), and character proposals (JAN-301) stay locked.
    assert set(tool_policy.LOCKED_UNATTENDED) == {
        "triggers.propose",
        "ui_actions",
        "ui_blocks",
        "images",
        "ui_introspection",
        "character.propose",
    }


def test_overrides_switch_on_off_and_reset(db):
    """Overrides turn a tool on or off, and reset to the default."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()

    tool_policy.set_policy(db, user.id, "memory", False)
    tool_policy.set_policy(db, user.id, "mail.send", True)
    allowed = tool_policy.unattended_allowed_tool_ids(db, user.id, entries)
    assert "memory" not in allowed and "mail.send" in allowed

    tool_policy.set_policy(db, user.id, "memory", None)
    assert "memory" in tool_policy.unattended_allowed_tool_ids(db, user.id, entries)


def test_triggers_and_events_follow_the_switch_like_any_tool(db):
    """``triggers`` and ``events`` are default-on and user-switchable (JAN-272, JAN-274)."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, user.id, entries)}
    for tool_id in ("triggers", "events"):
        assert rows[tool_id]["locked_reason"] is None
        assert rows[tool_id]["unattended_allowed"] is True
        assert rows[tool_id]["default_unattended_allowed"] is True
        tool_policy.set_policy(db, user.id, tool_id, False)
    assert not {"triggers", "events"} & tool_policy.unattended_allowed_tool_ids(db, user.id, entries)
    assert rows["memory"]["withheld_unattended_tools"] == ["delete_fact"]


def test_a_locked_tool_cannot_be_switched(db, monkeypatch):
    """The lock mechanism still holds for any entry put in ``LOCKED_UNATTENDED``."""
    user = _user(db)
    monkeypatch.setattr(tool_policy, "LOCKED_UNATTENDED", {"memory": "Never in unattended runs."})
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.set_policy(db, user.id, "memory", True)
    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, user.id, DEFAULT_TOOL_REGISTRY.entries())}
    assert rows["memory"]["locked_reason"] and rows["memory"]["unattended_allowed"] is False
    offered, confirmed = tool_policy.unattended_tool_sets(
        db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode="trigger"
    )
    assert "memory" not in offered and "memory" not in confirmed


@pytest.mark.parametrize("tool_id", ["ui_actions", "ui_blocks", "images", "ui_introspection", "character.propose"])
@pytest.mark.parametrize("task_mode", sorted(tool_policy.UNATTENDED_TASK_MODES))
def test_chat_only_ui_toolboxes_stay_chat_only_even_when_a_user_picks_them(db, task_mode, tool_id):
    """Chat-only toolboxes (page and UI tools, images, character proposals) are never offered unattended."""
    user = _user(db)
    picked = definitions.create_definition(
        db, user.id, origin="user", name="Pages", objective="x", tool_ids=["core", tool_id], kind="manual"
    )
    offered, confirmed = tool_policy.unattended_tool_sets(
        db,
        user.id,
        DEFAULT_TOOL_REGISTRY.entries(),
        task_mode=task_mode,
        narrowed_to=frozenset(picked.tool_ids),
        definition=picked if task_mode == "automation" else None,
    )
    assert tool_id not in offered | confirmed
    assert tool_id not in tool_policy.unattended_allowed_tool_ids(db, user.id, DEFAULT_TOOL_REGISTRY.entries())
    assert tool_id not in tool_policy.delegable_tool_ids(frozenset({tool_id}), DEFAULT_TOOL_REGISTRY.entries())


def test_only_user_authored_definitions_allow_their_selected_tools(db):
    """A user-authored definition's pick allows an off tool for that definition only."""
    user = _user(db)
    mine = definitions.create_definition(
        db, user.id, origin="user", name="Mine", objective="x", tool_ids=["core", "mail.send"], kind="manual"
    )
    seeded = definitions.create_definition(
        db,
        user.id,
        origin="system",
        builtin_key="builtin.test",
        name="Seeded",
        objective="x",
        tool_ids=["core", "mail.send"],
        kind="manual",
    )
    agents = definitions.create_definition(
        db, user.id, origin="agent", name="Janus", objective="x", tool_ids=["core", "mail.send"], kind="manual"
    )

    assert "mail.send" in _confirmed(db, user, mine)
    assert "mail.send" not in _confirmed(db, user, seeded)
    assert "mail.send" not in _confirmed(db, user, agents)
    assert "mail.send" not in _confirmed(db, user)
    # A pick never reaches past the definition's own tools.
    narrow = definitions.create_definition(
        db, user.id, origin="user", name="Narrow", objective="x", tool_ids=["core"], kind="manual"
    )
    assert "mail.send" not in _confirmed(db, user, narrow, narrowed_to=frozenset(narrow.tool_ids))


def test_an_unknown_task_mode_gets_nothing(db):
    """Only the unattended modes are computed here; anything else is empty."""
    user = _user(db)
    assert tool_policy.unattended_tool_sets(db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode="chat") == (
        frozenset(),
        frozenset(),
    )


class _SendingExecutor:
    """Stands in for the agent loop and calls send_email once, like a model would."""

    def __init__(self):
        self.tool_answer = None
        self.tool_names: list[str] = []

    def __call__(self, db, system_prompt, tools, objective):
        self.tool_names = [tool.name for tool in tools]
        send = next(tool for tool in tools if tool.name == "send_email")
        self.tool_answer = send.invoke({"to": ["boss@example.com"], "subject": "Status", "body": "All good."})
        return AgentTaskRun(output="I sent the status mail.", tool_calls=())


def _run(testing_session_local, db, definition, executor):
    envelope = definitions.build_definition_envelope(
        definition, occurrence_key="test-1", kind=definitions.TriggerKind.MANUAL, source="test"
    )
    dispatcher = DurableTriggerDispatcher(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )
    dispatcher.dispatch(envelope, definition_id=definition.id)
    return db.query(models.RuntimeTrigger).filter_by(trigger_id=envelope.trigger_id).one()


def test_a_blocked_tool_is_offered_but_refused_and_the_refusal_is_recorded(db, testing_session_local, monkeypatch):
    """A blocked tool is offered but refused and the refusal is recorded."""
    from janus import database

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    sent = []
    monkeypatch.setattr(
        "janus.services.email_service.EmailService.send_email", lambda self, **kwargs: sent.append(kwargs)
    )
    seeded = definitions.create_definition(
        db,
        user.id,
        origin="system",
        builtin_key="builtin.test",
        name="Seeded",
        objective="Send the status mail.",
        tool_ids=["core", "mail.send"],
        kind="manual",
    )
    executor = _SendingExecutor()

    record = _run(testing_session_local, db, seeded, executor)

    assert sent == [], "a blocked tool must never really run unattended"
    assert executor.tool_answer.startswith("BLOCKED: send_email was not run")
    assert record.status == "completed"
    from janus.plan_state import load_plan_outcomes

    outcome = load_plan_outcomes(db, user.id, [record.task_id])[record.task_id]
    assert "Refused tool calls: send_email (confirmation_required)" in outcome.result
    events = db.query(models.Event).filter(models.Event.message == "Refused tool call send_email").all()
    assert events, "every refusal is written to the event log"


def test_a_user_authored_trigger_may_use_its_selected_blocked_tool(db, testing_session_local, monkeypatch):
    """A user authored trigger may use its selected blocked tool."""
    user = _user(db)
    sent = []
    monkeypatch.setattr(
        "janus.services.email_service.EmailService.send_email",
        lambda self, **kwargs: sent.append(kwargs) or {"sent": True},
    )
    mine = definitions.create_definition(
        db,
        user.id,
        origin="user",
        name="Status mail",
        objective="Send the status mail.",
        tool_ids=["core", "mail.send"],
        kind="manual",
    )
    executor = _SendingExecutor()

    record = _run(testing_session_local, db, mine, executor)

    assert record.status == "completed"
    assert len(sent) == 1 and sent[0]["to"] == ["boss@example.com"]
    assert executor.tool_answer == {"sent": True}
    assert record.output == "I sent the status mail."
    # Only the definition's own tools (plus core) are offered.
    assert "search_emails" not in executor.tool_names


def test_a_failing_tool_answers_error_instead_of_aborting_the_run(db, testing_session_local, monkeypatch):
    """A failing tool answers error instead of aborting the run."""
    user = _user(db)

    def explode(self, **kwargs):
        """Explode."""
        raise RuntimeError("SMTP down")

    monkeypatch.setattr("janus.services.email_service.EmailService.send_email", explode)
    mine = definitions.create_definition(
        db, user.id, origin="user", name="Status", objective="Send it.", tool_ids=["mail.send"], kind="manual"
    )
    executor = _SendingExecutor()

    record = _run(testing_session_local, db, mine, executor)

    assert record.status == "completed"
    assert executor.tool_answer == "ERROR: send_email failed: SMTP down"
    from janus.plan_state import load_plan_outcomes

    assert (
        "Failed tool calls: send_email: SMTP down"
        in load_plan_outcomes(db, user.id, [record.task_id])[record.task_id].result
    )


def test_policy_api_and_registry_report_the_effective_policy(client, db):
    """Policy api and registry report the effective policy."""
    user = _user(db)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=user.id, is_admin=False)
    try:
        registry = client.get("/api/tools/registry").json()
        builtins = {entry["id"]: entry for entry in registry["builtins"]}
        send = builtins["mail.send"]
        assert send["unattended_allowed"] is False
        assert send["default_unattended_allowed"] is False
        assert send["connected"] is True
        assert builtins["github"]["connected"] is False
        assert builtins["triggers"]["locked_reason"] is None

        updated = client.put("/api/tools/policies/mail.send", json={"unattended_allowed": True})
        assert updated.status_code == 200
        assert updated.json() == {
            "tool_id": "mail.send",
            "unattended_allowed": True,
            "default_unattended_allowed": False,
            "chat_mode": "ask",
            "default_chat_mode": "ask",
            "overridden": True,
            "locked_reason": None,
            "effective_unattended_allowed": True,
            "effective_chat_asks": True,
            "withheld_unattended_tools": [],
        }
        reset = client.put("/api/tools/policies/mail.send", json={"unattended_allowed": None})
        assert reset.json()["unattended_allowed"] is False
        assert client.put("/api/tools/policies/nope", json={"unattended_allowed": True}).status_code == 404
        switched = client.put("/api/tools/policies/triggers", json={"unattended_allowed": False})
        assert switched.status_code == 200 and switched.json()["unattended_allowed"] is False
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


# --- Per-tool "In chat" switch (JAN-306) ---------------------------------------------------------


def _entry(tool_id: str):
    return next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == tool_id)


def test_in_chat_switch_defaults_to_ask_every_time_for_required_tools_only(db):
    """Required tools ask every time by default; other tools have no In chat switch and report "allowed"."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    rows = {row["tool_id"]: row for row in tool_policy.describe_policies(db, user.id, entries)}
    for entry in entries:
        expected = "ask" if entry.confirmation == "required" else "allowed"
        assert rows[entry.id]["chat_mode"] == rows[entry.id]["default_chat_mode"] == expected
        assert rows[entry.id]["overridden"] is False
    assert tool_policy.chat_withheld_tool_ids(db, user.id, entries) == frozenset()


def test_off_in_chat_withholds_only_that_users_tool(db):
    """Off in chat is per user, and a reset brings the tool back."""
    alice, bob = _user(db, "alice"), _user(db, "bob", with_mail=False)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    tool_policy.update_policy(db, alice.id, "mail.send", chat_mode="off", entry=_entry("mail.send"))

    assert tool_policy.chat_withheld_tool_ids(db, alice.id, entries) == frozenset({"mail.send"})
    assert tool_policy.chat_withheld_tool_ids(db, bob.id, entries) == frozenset()
    row = next(r for r in tool_policy.describe_policies(db, alice.id, entries) if r["tool_id"] == "mail.send")
    assert (row["chat_mode"], row["overridden"]) == ("off", True)

    tool_policy.update_policy(db, alice.id, "mail.send", chat_mode=None, entry=_entry("mail.send"))
    assert tool_policy.chat_withheld_tool_ids(db, alice.id, entries) == frozenset()
    # Both switches back to their defaults: no override row is left behind.
    assert db.query(models.ToolPolicy).filter_by(user_id=alice.id).count() == 0


def test_in_chat_switch_is_refused_for_tools_without_one(db):
    """Only required tools can be switched off in chat; nothing can pre-approve a required tool."""
    user = _user(db)
    with pytest.raises(tool_policy.ChatSwitchError):
        tool_policy.update_policy(db, user.id, "memory", chat_mode="off", entry=_entry("memory"))
    with pytest.raises(tool_policy.ChatSwitchError):
        tool_policy.update_policy(db, user.id, "mail.send", chat_mode="allowed", entry=_entry("mail.send"))
    with pytest.raises(tool_policy.ChatSwitchError):
        tool_policy.update_policy(db, user.id, "mail.send", chat_mode="off")
    assert db.query(models.ToolPolicy).filter_by(user_id=user.id).count() == 0
    assert frozenset({"ask", "always", "off"}) == tool_policy.CHAT_MODES


def test_in_chat_switch_and_unattended_switch_are_independent(db):
    """Off in chat changes nothing for unattended runs, and the unattended switch leaves chat alone."""
    user = _user(db)
    entries = DEFAULT_TOOL_REGISTRY.entries()
    tool_policy.update_policy(db, user.id, "mail.send", chat_mode="off", entry=_entry("mail.send"))
    # Default unattended (off): still offered as a BLOCKED stub, not withheld like in chat.
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")
    assert "mail.send" in offered and "mail.send" not in confirmed

    tool_policy.set_policy(db, user.id, "mail.send", True)
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")
    assert "mail.send" in confirmed
    assert tool_policy.chat_withheld_tool_ids(db, user.id, entries) == frozenset({"mail.send"})

    # Resetting the unattended switch keeps the chat override (and its row).
    tool_policy.set_policy(db, user.id, "mail.send", None)
    assert "mail.send" not in tool_policy.unattended_allowed_tool_ids(db, user.id, entries)
    assert tool_policy.chat_withheld_tool_ids(db, user.id, entries) == frozenset({"mail.send"})


def test_locked_tools_keep_their_chat_switch(db):
    """A required tool whose unattended switch is fixed (self-evolution) can still be switched off in chat."""
    user = _user(db)
    entry = _entry("self_evolution")
    assert entry.confirmation == "required"
    tool_policy.update_policy(db, user.id, "self_evolution", chat_mode="off", entry=entry)
    assert "self_evolution" in tool_policy.chat_withheld_tool_ids(db, user.id, DEFAULT_TOOL_REGISTRY.entries())
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.update_policy(db, user.id, "self_evolution", unattended_allowed=True, chat_mode="ask", entry=entry)
    # The refused call wrote nothing: the chat switch is still off.
    assert "self_evolution" in tool_policy.chat_withheld_tool_ids(db, user.id, DEFAULT_TOOL_REGISTRY.entries())


def test_policy_api_sets_and_resets_the_in_chat_switch(client, db):
    """PUT changes only the fields sent; chat_mode on a tool without the switch is 422."""
    user = _user(db)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=user.id, is_admin=False)
    try:
        off = client.put("/api/tools/policies/mail.send", json={"chat_mode": "off"})
        assert off.status_code == 200
        body = off.json()
        assert (body["chat_mode"], body["unattended_allowed"], body["overridden"]) == ("off", False, True)
        # Changing the unattended switch alone keeps the chat override.
        both = client.put("/api/tools/policies/mail.send", json={"unattended_allowed": True}).json()
        assert (both["chat_mode"], both["unattended_allowed"]) == ("off", True)
        builtins = {entry["id"]: entry for entry in client.get("/api/tools/registry").json()["builtins"]}
        assert (builtins["mail.send"]["chat_mode"], builtins["mail.send"]["default_chat_mode"]) == ("off", "ask")
        assert builtins["memory"]["chat_mode"] == "allowed"

        reset = client.put("/api/tools/policies/mail.send", json={"unattended_allowed": None, "chat_mode": None})
        assert (reset.json()["chat_mode"], reset.json()["overridden"]) == ("ask", False)

        assert client.put("/api/tools/policies/memory", json={"chat_mode": "off"}).status_code == 422
        assert client.put("/api/tools/policies/mail.send", json={"chat_mode": "allowed"}).status_code == 422
        assert client.put("/api/tools/policies/ui_blocks", json={"unattended_allowed": True}).status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)
