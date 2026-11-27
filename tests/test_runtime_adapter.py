"""Tests for the runtime adapter: task/message -> plan step -> authorized toolset."""

from janus import models
from janus.plan_graph import PlanStepStatus
from janus.runtime_adapter import RuntimeAdapter


def _make_user(**overrides):
    defaults = {"username": "adapter-test", "hashed_password": "not-a-password", "is_admin": False}
    defaults.update(overrides)
    return models.User(**defaults)


def _make_connected_user(db, **overrides):
    """Persist a user with a real, active EmailAccount (the "mail" capability's real gate)."""
    overrides.setdefault("username", "adapter-connected-test")
    user = _make_user(**overrides)
    db.add(user)
    db.commit()
    db.add(
        models.EmailAccount(
            user_id=user.id,
            name=f"mail-{user.id}",
            email=f"user{user.id}@example.com",
            imap_server="imap.example.com",
            is_active=True,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def test_prepare_step_builds_task_and_step_from_message(db):
    """An inbound message becomes a validated TaskContext/PlanStep pair."""
    adapter = RuntimeAdapter(db, _make_user())
    prepared = adapter.prepare_step(
        task_id="chat-1",
        objective="Summarize today's inbox",
        requested_tool_ids=("core",),
        task_mode="chat",
    )

    assert prepared.task.task_id == "chat-1"
    assert prepared.task.task_mode == "chat"
    assert prepared.step.objective == "Summarize today's inbox"
    assert prepared.step.status == PlanStepStatus.PENDING
    assert prepared.step.required_tool_ids == ("core",)


def test_allowed_tool_is_selected_and_built(db):
    """A requested tool inside the task-mode allowlist is authorized and built."""
    adapter = RuntimeAdapter(db, _make_user())
    prepared = adapter.prepare_step(
        task_id="chat-2",
        objective="Look something up",
        requested_tool_ids=("core",),
        task_mode="chat",
    )

    assert [entry.id for entry in prepared.selection.entries] == ["core"]
    assert prepared.tools
    assert prepared.denials == ()
    assert prepared.awaiting_confirmation_tool_ids == ()


def test_denied_tool_reasons_are_stable_and_auditable(db):
    """Each denial reason from the registry is surfaced verbatim."""
    adapter = RuntimeAdapter(db, _make_user())
    prepared = adapter.prepare_step(
        task_id="chat-3",
        objective="Try several tools",
        requested_tool_ids=("core", "does-not-exist", "skills"),
        task_mode="briefing",  # briefing's default allowlist excludes skills
    )

    denial_reasons = {denial.tool_id: denial.reason for denial in prepared.denials}
    assert denial_reasons["does-not-exist"] == "unknown_tool"
    assert denial_reasons["skills"] == "not_allowlisted"
    assert [entry.id for entry in prepared.selection.entries] == ["core"]


def test_capability_intersection_denies_missing_capability(db):
    """A requested tool missing its required capability is denied, not built."""
    adapter = RuntimeAdapter(db, _make_connected_user(db))
    prepared = adapter.prepare_step(
        task_id="chat-4",
        objective="Read mail",
        requested_tool_ids=("mail",),
        task_mode="chat",
        allowed_tool_ids=frozenset({"mail"}),
        granted_capabilities=frozenset(),  # "mail" capability withheld
        confirmed_tool_ids=frozenset({"mail"}),
    )

    assert prepared.selection.entries == ()
    assert [(d.tool_id, d.reason) for d in prepared.denials] == [("mail", "missing_capability")]


def test_disabled_by_missing_connection_is_denied(db):
    """A user with no connected EmailAccount is denied mail tools (JAN-171): a real connection is the gate."""
    adapter = RuntimeAdapter(db, _make_user())
    prepared = adapter.prepare_step(
        task_id="chat-5",
        objective="Read mail",
        requested_tool_ids=("mail",),
        task_mode="chat",
        allowed_tool_ids=frozenset({"mail"}),
        granted_capabilities=frozenset({"mail"}),
        confirmed_tool_ids=frozenset({"mail"}),
    )

    assert [(d.tool_id, d.reason) for d in prepared.denials] == [("mail", "disabled_by_context")]


def test_confirmation_required_tool_is_withheld_until_confirmed(db):
    """A confirmation-gated tool is authorized but withheld pre-confirmation."""
    user = _make_connected_user(db)
    adapter = RuntimeAdapter(db, user)

    unconfirmed = adapter.prepare_step(
        task_id="chat-6",
        objective="Send an email",
        requested_tool_ids=("mail.send",),
        task_mode="chat",
        allowed_tool_ids=frozenset({"mail.send"}),
        granted_capabilities=frozenset({"mail"}),
    )
    assert unconfirmed.selection.entries == ()
    assert unconfirmed.tools == ()
    assert unconfirmed.awaiting_confirmation_tool_ids == ("mail.send",)
    assert [(d.tool_id, d.reason) for d in unconfirmed.denials] == [("mail.send", "confirmation_required")]

    confirmed = adapter.prepare_step(
        task_id="chat-6",
        objective="Send an email",
        requested_tool_ids=("mail.send",),
        task_mode="chat",
        allowed_tool_ids=frozenset({"mail.send"}),
        granted_capabilities=frozenset({"mail"}),
        confirmed_tool_ids=frozenset({"mail.send"}),
    )
    assert [entry.id for entry in confirmed.selection.entries] == ["mail.send"]
    assert {tool.name for tool in confirmed.tools} == {"send_email"}
    assert confirmed.awaiting_confirmation_tool_ids == ()
    assert confirmed.denials == ()


def test_no_confirmation_never_gated_tool_is_not_withheld(db):
    """Tools with confirmation="never" build immediately with no gate."""
    adapter = RuntimeAdapter(db, _make_user())
    prepared = adapter.prepare_step(
        task_id="chat-7",
        objective="Use a core tool",
        requested_tool_ids=("core",),
        task_mode="chat",
    )
    assert prepared.awaiting_confirmation_tool_ids == ()
    assert prepared.tools


def test_rules_follow_the_mail_connection(db):
    """Rule tools are denied without an active mailbox and selected once one is connected."""
    requested = {
        "task_mode": "chat",
        "objective": "Add a rule",
        "requested_tool_ids": ("rules", "rules.write"),
        "granted_capabilities": frozenset({"rules"}),
        "confirmed_tool_ids": frozenset({"rules.write"}),
    }

    disconnected = RuntimeAdapter(db, _make_user()).prepare_step(task_id="chat-rules-1", **requested)
    assert [(d.tool_id, d.reason) for d in disconnected.denials] == [
        ("rules", "disabled_by_context"),
        ("rules.write", "disabled_by_context"),
    ]

    connected = RuntimeAdapter(db, _make_connected_user(db)).prepare_step(task_id="chat-rules-2", **requested)
    assert [entry.id for entry in connected.selection.entries] == ["rules", "rules.write"]
    names = {tool.name for tool in connected.tools}
    assert {"list_mail_rules", "create_mail_rule"} <= names


def test_read_only_rules_do_not_need_confirmation_but_rule_writers_do(db):
    """Only rules.write is confirmation-gated; the read-only rules toolbox builds immediately."""
    prepared = RuntimeAdapter(db, _make_connected_user(db)).prepare_step(
        task_id="chat-rules-3",
        task_mode="chat",
        objective="Show and change rules",
        requested_tool_ids=("rules", "rules.write"),
        granted_capabilities=frozenset({"rules"}),
    )
    assert [entry.id for entry in prepared.selection.entries] == ["rules"]
    assert prepared.awaiting_confirmation_tool_ids == ("rules.write",)
    names = {tool.name for tool in prepared.tools}
    assert "list_mail_rules" in names
    assert "create_mail_rule" not in names
