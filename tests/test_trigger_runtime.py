"""Tests for trigger-driven authorization (JAN-165) and execution (JAN-192)."""

import pytest

from janus import models, runtime_adapter, tool_policy, trigger_service
from janus.agent_task_runner import AgentTaskRun, AgentToolCall
from janus.capability_profile import user_connected_capabilities
from janus.plan_graph import PlanGraphNode, PlanStatus, PlanStepStatus
from janus.plan_state import PlanStateStore
from janus.services.email_service import EmailService
from janus.trigger_runtime import TerminalTriggerRunFailedError, TriggerRunFailedError, build_runtime_trigger_handler
from janus.triggers import TerminalTriggerError, TriggerDispatchStatus, TriggerEnvelope, TriggerKind


def _trigger(owner_id: int | None, payload: dict, task_id: str = "task-1") -> TriggerEnvelope:
    return TriggerEnvelope(
        trigger_id="trigger-1",
        idempotency_key="key-1",
        task_id=task_id,
        kind=TriggerKind.EVENT,
        source="test",
        payload=payload,
        owner_id=owner_id,
    )


class RecordingExecutor:
    """A stand-in for the synchronous agent loop that records what it was handed.

    Trigger execution is what is under test here, not the LLM provider, so the
    executor seam ``build_runtime_trigger_handler`` exposes is used instead of
    reaching a real model -- the assertions are about which tools reached the
    loop and what the runtime did with its answer.
    """

    def __init__(
        self,
        output: str = "All clear.",
        tool_calls: tuple[str, ...] = (),
        error: Exception | None = None,
        invoke: dict[str, dict] | None = None,
    ):
        """Configure the canned answer, tool trace, optional failure, and tools to call.

        ``invoke`` maps a tool name to the arguments the fake "model" calls it
        with, so a test can observe what a handed-over tool actually does.
        """
        self.output = output
        self.tool_calls = tool_calls
        self.error = error
        self.invoke = invoke or {}
        self.calls: list[dict] = []
        self.invocations: dict[str, object] = {}

    def __call__(self, db, system_prompt, tools, objective):
        """Record the call, invoke any requested tools, and return the configured run, or raise."""
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "tool_names": [tool.name for tool in tools],
                "objective": objective,
            }
        )
        by_name = {tool.name: tool for tool in tools}
        for name, args in self.invoke.items():
            self.invocations[name] = by_name[name].invoke(args)
        if self.error is not None:
            raise self.error
        return AgentTaskRun(
            output=self.output,
            tool_calls=tuple(AgentToolCall(tool=name, result="ok") for name in self.tool_calls),
        )


def _failed_run(handler, trigger, *, terminal: bool):
    """Run a trigger whose plan must end FAILED and return the carried result.

    A failed plan must *raise* out of the handler (JAN-194) so the durable
    worker never records it ``completed``; the exception type says whether a
    retry can help.
    """
    with pytest.raises(TriggerRunFailedError) as caught:
        handler(trigger)
    assert isinstance(caught.value, TerminalTriggerError) is terminal
    assert caught.value.result.state.node == PlanGraphNode.FAILED
    return caught.value.result


def _connect_mail(db, user: models.User) -> None:
    """Give ``user`` an active mailbox so the mail toolboxes' enabled_when passes."""
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


def _record_real_sends(monkeypatch) -> list[dict]:
    """Replace the real SMTP send path with a recorder; it must stay empty."""
    sent: list[dict] = []
    monkeypatch.setattr(EmailService, "send_email", lambda self, **kwargs: sent.append(kwargs) or {"sent": True})
    return sent


_SEND_ARGS = {"to": ["someone@example.com"], "subject": "Hi", "body": "Hello"}


def _owner(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_handler_requires_an_owned_trigger(db, testing_session_local):
    """A trigger without an owner_id must fail closed rather than proceed unauthorized."""
    handler = build_runtime_trigger_handler(testing_session_local)

    with pytest.raises(ValueError, match="owned trigger"):
        handler(_trigger(None, {"objective": "Do something"}))


def test_handler_executes_an_authorized_step_and_reports_a_real_result(db, testing_session_local):
    """The JAN-192 headline: a trigger that authorizes and executes is honestly completed.

    Before this, ``_execute_authorized_step`` always raised, so a trigger could
    never do anything. Now a successful run must reach ``COMPLETE`` with the
    automation's actual answer -- and the trace of which tools ran -- persisted
    as the plan step's result.
    """
    user = _owner(db, "trigger-owner-executes")
    executor = RecordingExecutor(output="Nothing needs your attention.", tool_calls=("list_recent_events",))

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Sync the inbox", "task_mode": "trigger", "tool_ids": ["core"]},
        task_id="task-executes",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE
    assert result.state.plan is not None
    assert result.state.plan.status == PlanStatus.COMPLETED
    step = result.state.plan.steps[0]
    assert step.status == PlanStepStatus.COMPLETED
    assert step.error is None
    assert "Nothing needs your attention." in (step.result or "")
    assert "Tools invoked: list_recent_events" in (step.result or "")
    assert "Authorized tools: core" in (step.result or "")

    # The executor really was handed the authorized toolset and the objective.
    assert len(executor.calls) == 1
    assert executor.calls[0]["objective"] == "Sync the inbox"
    assert executor.calls[0]["tool_names"], "the authorized core toolset must reach the agent loop"

    stored = PlanStateStore(testing_session_local).load(db, user.id, "task-executes")
    assert stored is not None
    assert stored.state.node == PlanGraphNode.COMPLETE
    assert "Nothing needs your attention." in (stored.state.plan.steps[0].result or "")


def test_a_very_long_answer_is_marked_truncated_rather_than_silently_cut(db, testing_session_local):
    """A truncated summary must not read as the automation having stopped there."""
    user = _owner(db, "trigger-owner-long-answer")
    executor = RecordingExecutor(output="x" * 10_000)

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    result = handler(
        _trigger(
            user.id,
            {"objective": "Write at length", "task_mode": "trigger", "tool_ids": ["core"]},
            task_id="task-long-answer",
        )
    )

    step_result = result.state.plan.steps[0].result or ""
    assert len(step_result) <= 4_000
    assert step_result.endswith("[result truncated]")


def test_handler_fails_the_step_when_execution_raises(db, testing_session_local):
    """A provider or tool failure must fail the step, never be reported as completed."""
    user = _owner(db, "trigger-owner-exec-fails")
    executor = RecordingExecutor(error=RuntimeError("model provider unreachable"))

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Sync the inbox", "task_mode": "trigger", "tool_ids": ["core"]},
        task_id="task-exec-fails",
    )

    result = _failed_run(handler, trigger, terminal=False)

    assert result.state.node == PlanGraphNode.FAILED
    assert "model provider unreachable" in (result.state.reason or "")
    assert result.state.plan.steps[0].error == "model provider unreachable"


def test_handler_defaults_tool_ids_to_the_task_modes_allowlist_not_the_full_registry(db, testing_session_local):
    """The realistic default case: a trigger payload with no ``tool_ids`` at all.

    Every trigger created through ``POST /api/runtime/triggers`` omits
    ``tool_ids``. Falling back to "every registry entry" would request tools
    like "mail" that the "trigger" task mode's allowlist does not permit,
    producing spurious ``not_allowlisted`` denials for a step that never asked
    for those tools. The fallback must instead default to exactly the task
    mode's own allowlist.
    """
    user = _owner(db, "trigger-owner-no-tool-ids")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    # No "tool_ids" key at all -- exactly what the real API produces today.
    trigger = _trigger(
        user.id,
        {"objective": "Sync the inbox", "task_mode": "trigger"},
        task_id="task-no-tool-ids",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE
    assert "not_allowlisted" not in (result.state.plan.steps[0].result or "")


def test_unconnected_integrations_are_skipped_rather_than_failing_the_whole_trigger(db, testing_session_local):
    """A widened allowlist must not make every trigger fail for an unconnected integration.

    The "trigger" allowlist now spans ``calendar``/``jira``/``events``, which
    are gated on real connection state. A user with no calendar connected never
    asked for the calendar tool -- the fallback sweep did -- so that denial is
    recorded in the result, not turned into a step failure.
    """
    user = _owner(db, "trigger-owner-unconnected")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Summarize today", "task_mode": "trigger"},
        task_id="task-unconnected",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE
    step_result = result.state.plan.steps[0].result or ""
    assert "Unavailable tools (skipped)" in step_result
    assert "calendar" in step_result


def test_handler_fails_the_step_for_an_explicitly_requested_denied_tool(db, testing_session_local):
    """A tool the trigger explicitly asked for but may not have fails the step.

    An automation that named ``tool_ids`` and cannot have them must not quietly
    run with a narrower toolset and report success.
    """
    user = _owner(db, "trigger-owner-2")
    # The owner switched this tool off for unattended runs; nobody can confirm it mid-run.
    tool_policy.set_policy(db, user.id, "trigger-history", False)
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Read my history", "task_mode": "trigger", "tool_ids": ["trigger-history"]},
        task_id="task-denied-tool",
    )

    result = _failed_run(handler, trigger, terminal=True)

    assert result.state.node == PlanGraphNode.FAILED
    assert "trigger-history" in (result.state.reason or "")
    assert "confirmation_required" in (result.state.reason or "")
    assert executor.calls == [], "a denied step must never reach the agent loop"


def test_a_disabled_by_context_denial_is_retryable_not_terminal(db, testing_session_local):
    """Unlike a policy denial, a connection-state denial may resolve later.

    "calendar" is allowlisted for trigger mode, but this owner has no calendar
    connected, so an explicit request for it is denied ``disabled_by_context``.
    That cause can change (a calendar may be connected before the next retry),
    so this must fail as a plain, retryable error -- not the terminal
    ``UnattendedStepDeniedError`` a policy-level denial like
    ``not_allowlisted`` raises.
    """
    user = _owner(db, "trigger-owner-disabled-by-context")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Check my calendar", "task_mode": "trigger", "tool_ids": ["calendar"]},
        task_id="task-disabled-by-context",
    )

    result = _failed_run(handler, trigger, terminal=False)

    assert "calendar:disabled_by_context" in (result.state.reason or "")
    assert executor.calls == [], "a denied step must never reach the agent loop"


def test_handler_denies_a_confirmation_required_tool_instead_of_auto_confirming(db, testing_session_local, monkeypatch):
    """A trigger must never auto-confirm a confirmation-required tool (fail closed).

    Triggers have no human in the loop to approve a tool mid-flight, so a tool
    marked ``confirmation="required"`` (e.g. ``mail.send``, which sends email)
    must be denied for trigger-driven execution rather than silently granted.
    ``mail.send`` is offered to unattended runs but switched off by default, so
    an explicit request for it (outside a user-authored definition) fails the
    whole step.
    """
    user = _owner(db, "trigger-owner-confirm")
    # A real connection so "mail.send"'s enabled_when passes and the step
    # reaches the confirmation gate under test, rather than being denied
    # earlier (JAN-171).
    _connect_mail(db, user)
    sent = _record_real_sends(monkeypatch)

    executor = RecordingExecutor(invoke={"send_email": _SEND_ARGS})
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Send a risky email", "task_mode": "trigger", "tool_ids": ["mail.send"]},
        task_id="task-confirmation-required",
    )

    result = _failed_run(handler, trigger, terminal=True)

    assert result.state.node == PlanGraphNode.FAILED
    assert "mail.send" in (result.state.reason or "")
    assert "confirmation_required" in (result.state.reason or "")
    assert executor.calls == [], "a confirmation-required tool must never be auto-run"
    assert sent == []


def test_an_off_tool_is_not_offered_in_a_fallback_sweep(db, testing_session_local, monkeypatch):
    """The fallback path tolerates denials -- but must never become a back door to an off tool.

    A trigger that names no tools requests only what the owner allows in
    unattended runs, so a tool that is off (by default the
    ``confirmation="required"`` ones, like ``mail.send``) is not offered at
    all and cannot run. A definition that lists an off tool, or an explicit
    request for one, still gets a ``BLOCKED`` refusal stub instead (see
    ``test_tool_policy``).
    """
    user = _owner(db, "trigger-owner-fallback-confirm")
    _connect_mail(db, user)
    sent = _record_real_sends(monkeypatch)

    executor = RecordingExecutor()
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Check my mail", "task_mode": "trigger"},
        task_id="task-fallback-confirm",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE
    step_result = result.state.plan.steps[0].result or ""
    authorized = step_result.split("Authorized tools:")[1].split("\n")[0]
    assert "mail.send" not in authorized
    assert "mail" in authorized, "reading mail is allowed unattended by default"
    assert len(executor.calls) == 1
    assert "send_email" not in executor.calls[0]["tool_names"]
    assert "search_emails" in executor.calls[0]["tool_names"]
    assert sent == [], "the real send must never run unattended without confirmation"


def test_unattended_defaults_allow_everything_but_confirmation_required_tools(db):
    """The unattended switch's defaults are pinned (JAN-255, JAN-272, JAN-274).

    Every registry entry is offered, and every entry is allowed except the
    ``confirmation="required"`` ones (off by default). Only the chat-only
    ``triggers.propose``, ``ui_actions``, ``ui_blocks``, ``images``, ``ui_introspection``, and ``character.propose``
    are locked:
    ``events`` reads only the owner's rows and ``triggers`` carries its own chain guard. This is the rule saved
    automations always had; ad-hoc triggers and delegated tasks follow it too. Any change must be as
    deliberate and update this test.
    """
    user = _owner(db, "trigger-owner-defaults")
    entries = runtime_adapter.DEFAULT_TOOL_REGISTRY.entries()
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")

    locked = {"triggers.propose", "ui_actions", "ui_blocks", "images", "ui_introspection", "character.propose"}
    assert set(tool_policy.LOCKED_UNATTENDED) == locked
    assert offered == frozenset(entry.id for entry in entries) - locked
    assert offered - confirmed == frozenset(
        # self_evolution follows its own opt-in switch, off by default (JAN-320).
        {
            "mail.send",
            # Sending as the user over Microsoft Graph always needs a human OK, so it is off unattended.
            "microsoft.mail.send",
            "microsoft.teams.send",
            "whatsapp.send",
            "planner.local.write",
            "github",
            "linear",
            "web_fetch",
            "wiki.propose",
            "rules.write",
            "skills",
            "self_evolution",
            "shell",
        }
    )
    assert confirmed == frozenset(
        {
            "core",
            "events",
            "triggers",
            "trigger-history",
            "mail",
            "mail.label",
            "mail.processing",
            # Read-only WhatsApp (JAN-347).
            "whatsapp",
            # The Secretary's alert to the owner's own number: Allowed by default; the real gates are the
            # owner's opt-in and number (``enabled_when``) and the per-run/per-day caps.
            "whatsapp.notify_me",
            "calendar",
            # Microsoft 365 reads follow the unattended default (Allowed) like the IMAP mail tools.
            "microsoft.mail",
            "microsoft.calendar",
            "microsoft.teams",
            "azure.resources",
            "jira",
            "planner.local",
            "dashboard",
            "web_search",
            "memory",
            "topics",
            "knowledge",
            "wiki",
            "rules",
            "delegation",
            "docs",
            # Janus-started conversations (JAN-319): Allowed by default, bounded by their own limits.
            "conversations.start",
            # Read-only character state (JAN-301); proposing a change is locked above.
            "character",
        }
    )


def test_trigger_mode_withholds_the_destructive_tools_of_admitted_toolboxes():
    """The narrowing half of JAN-193: write yes, delete/prompt-rewrite no."""
    assert runtime_adapter.TASK_MODE_WITHHELD_TOOL_NAMES["trigger"] == frozenset(
        {"delete_fact", "set_dashboard_widget_prompt", "clear_dashboard_widget_prompt"}
    )
    # Interactive chat is not narrowed -- a human is present there.
    assert "chat" not in runtime_adapter.TASK_MODE_WITHHELD_TOOL_NAMES


def _connect_calendar(db, user: models.User) -> None:
    """Give ``user`` a calendar connection so the ``dashboard`` entry is enabled."""
    db.add(
        models.PlannerAccount(
            name="Google Calendar",
            provider=models.Provider.GOOGLE_CALENDAR,
            user_id=user.id,
            token="{}",
        )
    )
    db.commit()


def test_recommended_tier_writers_now_run_in_trigger_mode(db, testing_session_local):
    """Positive half of JAN-193: memory/topics/dashboard/delegation reach the agent loop.

    Before JAN-193 each of these was denied ``not_allowlisted`` and, being
    explicitly requested, failed the step. Now the step completes with their
    write tools built -- minus the withheld destructive ones.
    """
    user = _owner(db, "trigger-owner-recommended-tier")
    _connect_calendar(db, user)
    executor = RecordingExecutor(tool_calls=("add_memory_fact",))

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {
            "objective": "Remember that the quarterly review moved",
            "task_mode": "trigger",
            "tool_ids": ["memory", "topics", "dashboard", "delegation"],
        },
        task_id="task-recommended-tier",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE, result.state.reason
    step_result = result.state.plan.steps[0].result or ""
    authorized_line = step_result.split("Authorized tools:")[1].split("\n")[0]
    assert all(tool_id in authorized_line for tool_id in ("memory", "topics", "dashboard", "delegation"))
    assert "Tools invoked: add_memory_fact" in step_result

    names = set(executor.calls[0]["tool_names"])
    assert {"add_memory_fact", "update_fact", "group_facts"} <= names
    assert {"create_topic", "update_topic_state"} <= names
    assert {"update_dashboard_widget", "get_daily_briefing"} <= names
    assert {"delegate_task", "get_delegated_task", "list_delegated_tasks"} <= names
    # Narrowed: destructive / prompt-steering tools never reach the loop.
    assert names.isdisjoint(runtime_adapter.TASK_MODE_WITHHELD_TOOL_NAMES["trigger"])


def test_withheld_tools_are_still_available_to_interactive_chat(db):
    """The by-name narrowing is trigger-only; chat keeps delete and prompt tools."""
    user = _owner(db, "chat-owner-unnarrowed")
    _connect_calendar(db, user)

    prepared = runtime_adapter.RuntimeAdapter(db, user).prepare_step(
        task_id="chat-unnarrowed",
        objective="Tidy my memory",
        requested_tool_ids=("memory", "dashboard"),
        task_mode="chat",
        granted_capabilities=user_connected_capabilities(db, user),
    )

    names = {tool.name for tool in prepared.tools}
    assert {"delete_fact", "set_dashboard_widget_prompt", "clear_dashboard_widget_prompt"} <= names


@pytest.mark.parametrize(
    ("tool_id", "tool_names"),
    [
        ("triggers", {"create_scheduled_trigger", "list_my_triggers", "get_trigger", "cancel_trigger"}),
        ("events", {"list_recent_events"}),
    ],
)
def test_formerly_locked_tools_follow_the_users_switch(db, tool_id, tool_names):
    """``triggers``/``events`` are offered and run by default, and answer BLOCKED once switched off."""
    user = _owner(db, f"trigger-owner-unlocked-{tool_id}")

    def prepare():
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, runtime_adapter.DEFAULT_TOOL_REGISTRY.entries(), task_mode="trigger"
        )
        return runtime_adapter.RuntimeAdapter(db, user).prepare_step(
            task_id=f"trigger-unlocked-{tool_id}",
            objective="Use the formerly locked tool",
            requested_tool_ids=(tool_id,),
            task_mode="trigger",
            allowed_tool_ids=offered,
            confirmed_tool_ids=confirmed,
            blocked_tool_ids=offered - confirmed,
            refusal_stubs=True,
            trigger_id="some-run",
            granted_capabilities=user_connected_capabilities(db, user),
        )

    allowed = prepare()
    assert [entry.id for entry in allowed.selection.entries] == [tool_id]
    assert {tool.name for tool in allowed.tools} == tool_names

    tool_policy.set_policy(db, user.id, tool_id, False)
    blocked = prepare()
    assert blocked.selection.entries == ()
    assert [(denial.tool_id, denial.reason) for denial in blocked.denials] == [(tool_id, "confirmation_required")]
    assert {tool.name for tool in blocked.tools} == tool_names, "a switched-off tool is still offered as a stub"


def test_a_delegated_child_never_gets_the_trigger_tools(db):
    """``triggers`` is not read-only, so a delegated child cannot schedule anything."""
    user = _owner(db, "trigger-owner-delegated-no-triggers")
    offered, _ = tool_policy.unattended_tool_sets(
        db, user.id, runtime_adapter.DEFAULT_TOOL_REGISTRY.entries(), task_mode="delegated"
    )
    assert "triggers" not in offered
    assert "events" in offered


@pytest.mark.parametrize("tool_id", ["mail.send", "planner.local.write", "github", "linear", "rules.write", "skills"])
def test_a_tool_switched_on_for_unattended_runs_is_offered_and_confirmed(db, tool_id):
    """The user's switch is the whole answer for unlocked tools: turning one on confirms it."""
    user = _owner(db, f"trigger-owner-on-{tool_id}")
    entries = runtime_adapter.DEFAULT_TOOL_REGISTRY.entries()
    assert tool_id not in tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")[1]
    tool_policy.set_policy(db, user.id, tool_id, True)
    assert tool_id in tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")[1]


def test_a_confirmation_required_tool_is_still_denied_alongside_the_widened_set(db, testing_session_local, monkeypatch):
    """Fence 2 still holds after JAN-193, independent of fence 1.

    ``planner.local.write`` (always capability-granted,
    ``confirmation="required"``) is offered next to the allowed writers, but
    the owner's default switch keeps it off, and an explicit request for it
    must fail the whole step.
    """
    user = _owner(db, "trigger-owner-required-in-widened")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    trigger = _trigger(
        user.id,
        {"objective": "Create a task", "task_mode": "trigger", "tool_ids": ["memory", "planner.local.write"]},
        task_id="task-required-in-widened",
    )

    result = _failed_run(handler, trigger, terminal=True)

    assert "planner.local.write:confirmation_required" in (result.state.reason or "")
    assert executor.calls == [], "a confirmation-required tool must never be auto-run"

    # And the tolerant fallback sweep does not offer it at all, so nothing is created.
    sweep_executor = RecordingExecutor()
    sweep_handler = build_runtime_trigger_handler(testing_session_local, task_executor=sweep_executor)
    sweep = _trigger(
        user.id,
        {"objective": "Do whatever is useful", "task_mode": "trigger"},
        task_id="task-required-in-widened-sweep",
    )
    sweep_result = sweep_handler(sweep)
    assert sweep_result.state.node == PlanGraphNode.COMPLETE
    step_result = sweep_result.state.plan.steps[0].result or ""
    assert "planner.local.write" not in step_result.split("Authorized tools:")[1].split("\n")[0]
    sweep_tool_names = sweep_executor.calls[0]["tool_names"]
    assert "add_memory_fact" in sweep_tool_names
    assert "create_local_task" not in sweep_tool_names
    db.expire_all()
    assert db.query(models.PlannerTask).filter(models.PlannerTask.user_id == user.id).count() == 0


def test_a_trigger_cannot_launder_its_wider_grant_into_a_delegated_child(db):
    """A trigger may now delegate, but its child stays on the read-only set."""
    from janus.agent_toolboxes.delegation import delegable_tool_ids
    from janus.agent_toolboxes.shared import ToolContext
    from janus.tool_registry import ToolBuildContext

    user = _owner(db, "trigger-owner-delegation-clamp")
    _, confirmed = tool_policy.unattended_tool_sets(
        db, user.id, runtime_adapter.DEFAULT_TOOL_REGISTRY.entries(), task_mode="trigger"
    )
    context = ToolBuildContext(
        tool_context=ToolContext(db=db, user=user),
        task_mode="trigger",
        authorized_tool_ids=confirmed,
    )

    assert delegable_tool_ids(context) == frozenset(
        {
            "core",
            "events",
            "trigger-history",
            "mail",
            "whatsapp",
            "calendar",
            # Read-only Microsoft 365 tools are delegable like the IMAP mail tool: a child is read-only
            # and clamped to its parent's grant. The send tools are write tools and never are.
            "microsoft.mail",
            "microsoft.calendar",
            "microsoft.teams",
            "azure.resources",
            "jira",
            "planner.local",
            "web_search",
            "knowledge",
            "wiki",
            "rules",
            "docs",
            "character",
        }
    )


def test_handler_defaults_to_the_trigger_task_mode_with_no_explicit_payload_value(db, testing_session_local):
    """The production entrypoint must own its own task_mode, not trust the payload default.

    ``task_context_from_trigger`` (and the ``RuntimeTriggerCreate`` schema
    behind the real API) default an unset ``task_mode`` to ``"default"``, which
    has no entry in ``TASK_MODE_ALLOWED_TOOL_IDS`` and therefore resolves to an
    *empty* allowlist -- denying even ``core``.
    """
    user = _owner(db, "trigger-owner-default-mode")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    # No "task_mode" key at all.
    trigger = _trigger(
        user.id,
        {"objective": "Sync the inbox", "tool_ids": ["core"]},
        task_id="task-default-task-mode",
    )

    result = handler(trigger)

    assert result.state.node == PlanGraphNode.COMPLETE
    assert "Authorized tools: core" in (result.state.plan.steps[0].result or "")


def test_a_malformed_tool_ids_payload_fails_with_an_actionable_message(db, testing_session_local):
    """A bare string must not be exploded into per-character "tool ids"."""
    user = _owner(db, "trigger-owner-bad-tool-ids")
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)

    # Malformed input is terminal: a retry of the same payload cannot succeed.
    with pytest.raises(TerminalTriggerError, match="tool_ids must be a list"):
        handler(
            _trigger(
                user.id,
                {"objective": "Check mail", "task_mode": "trigger", "tool_ids": "mail"},
                task_id="task-bad-tool-ids",
            )
        )
    assert executor.calls == []


def test_handler_fails_closed_for_an_unknown_owner(db, testing_session_local):
    """A trigger owned by a user id that no longer exists fails the step, not a crash."""
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=RecordingExecutor())
    trigger = _trigger(999999, {"objective": "Ghost owner task"}, task_id="task-ghost-owner")

    result = _failed_run(handler, trigger, terminal=True)

    assert result.state.node == PlanGraphNode.FAILED
    assert "unknown trigger owner" in (result.state.reason or "")


# --- Trigger-row status follows the plan outcome (JAN-194 / JAN-206) ---------
#
# The tests above exercise the handler; these drive the real
# ``DurableTriggerWorker`` so the assertion is on the persisted
# ``RuntimeTrigger`` row -- the thing Settings -> Runtime and
# ``list_my_trigger_history`` actually read. Before JAN-194 every one of these
# rows ended ``status="completed"``, because the handler returned normally for
# a failed plan.


class FlakyExecutor(RecordingExecutor):
    """Fails the first ``failures`` calls, then answers normally."""

    def __init__(self, failures: int, error: Exception):
        """Configure how many calls fail before the executor recovers."""
        super().__init__(output="Recovered.")
        self.failures = failures
        self.failure = error

    def __call__(self, db, system_prompt, tools, objective):
        """Raise until the failure budget is spent, then return the canned run."""
        self.error = self.failure if len(self.calls) < self.failures else None
        return super().__call__(db, system_prompt, tools, objective)


def _worker(testing_session_local, executor, *, max_attempts: int = 3) -> trigger_service.DurableTriggerWorker:
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    return trigger_service.DurableTriggerWorker(testing_session_local, handler, max_attempts=max_attempts)


def _row(db, record_id: int) -> models.RuntimeTrigger:
    db.expire_all()
    return db.get(models.RuntimeTrigger, record_id)


def test_a_trigger_whose_execution_raises_is_persisted_failed_not_completed(db, testing_session_local):
    """JAN-194 headline: a provider failure leaves the row ``failed`` with its reason, and is retried."""
    user = _owner(db, "trigger-owner-row-fails")
    executor = RecordingExecutor(error=RuntimeError("model provider unreachable"))
    record, _ = trigger_service.enqueue_trigger(
        db, _trigger(user.id, {"objective": "Sync the inbox", "tool_ids": ["core"]}, task_id="task-row-fails")
    )
    worker = _worker(testing_session_local, executor, max_attempts=2)

    first = worker.run_once()

    assert [result.status for result in first] == [TriggerDispatchStatus.FAILED]
    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.FAILED.value
    assert row.attempt_count == 1
    assert "model provider unreachable" in (row.last_error or "")
    assert row.completed_at is None

    # The retry really re-executes (it is not a replay of the stored FAILED
    # plan state), and the attempt limit then exhausts the row honestly.
    worker.run_once()
    assert len(executor.calls) == 2
    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.EXHAUSTED.value
    assert row.attempt_count == 2
    assert "model provider unreachable" in (row.last_error or "")

    stored = PlanStateStore(testing_session_local).load(db, user.id, "task-row-fails")
    assert stored.state.node == PlanGraphNode.FAILED
    assert stored.state.plan.steps[0].error == "model provider unreachable"


def test_a_transient_failure_that_recovers_on_retry_ends_completed(db, testing_session_local):
    """A retry starts a fresh plan, so a recovered provider produces a real ``completed`` run."""
    user = _owner(db, "trigger-owner-row-recovers")
    executor = FlakyExecutor(failures=1, error=RuntimeError("model provider unreachable"))
    record, _ = trigger_service.enqueue_trigger(
        db, _trigger(user.id, {"objective": "Sync the inbox", "tool_ids": ["core"]}, task_id="task-row-recovers")
    )
    worker = _worker(testing_session_local, executor)

    worker.run_once()
    assert _row(db, record.id).status == models.RuntimeTriggerStatus.FAILED.value

    second = worker.run_once()

    assert [result.status for result in second] == [TriggerDispatchStatus.COMPLETED]
    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.COMPLETED.value
    assert row.attempt_count == 2
    stored = PlanStateStore(testing_session_local).load(db, user.id, "task-row-recovers")
    assert stored.state.node == PlanGraphNode.COMPLETE
    assert "Recovered." in (stored.state.plan.steps[0].result or "")
    assert stored.state.plan.steps[0].error is None


def test_a_policy_denial_exhausts_the_trigger_without_retrying(db, testing_session_local):
    """An explicitly requested denied tool is terminal: exhausted on attempt one, never executed."""
    user = _owner(db, "trigger-owner-row-denied")
    tool_policy.set_policy(db, user.id, "trigger-history", False)
    executor = RecordingExecutor()
    record, _ = trigger_service.enqueue_trigger(
        db,
        _trigger(user.id, {"objective": "Read my history", "tool_ids": ["trigger-history"]}, task_id="task-row-denied"),
    )
    worker = _worker(testing_session_local, executor)

    worker.run_once()
    assert worker.run_once() == []

    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.EXHAUSTED.value
    assert row.attempt_count == 1
    assert "trigger-history:confirmation_required" in (row.last_error or "")
    assert executor.calls == []


def test_a_disabled_by_context_denial_retries_instead_of_exhausting(db, testing_session_local):
    """Unlike a policy denial, a connection-state denial uses the retry budget."""
    user = _owner(db, "trigger-owner-row-disabled-by-context")
    executor = RecordingExecutor()
    record, _ = trigger_service.enqueue_trigger(
        db,
        _trigger(
            user.id,
            {"objective": "Check my calendar", "tool_ids": ["calendar"]},
            task_id="task-row-disabled-by-context",
        ),
    )
    worker = _worker(testing_session_local, executor)

    worker.run_once()

    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.FAILED.value
    assert row.attempt_count == 1
    assert "calendar:disabled_by_context" in (row.last_error or "")
    assert executor.calls == []


def test_a_malformed_payload_exhausts_the_trigger_without_retrying(db, testing_session_local):
    """A payload that can never be planned is terminal too, and says why."""
    user = _owner(db, "trigger-owner-row-bad-payload")
    record, _ = trigger_service.enqueue_trigger(
        db, _trigger(user.id, {"objective": "Check mail", "tool_ids": "mail"}, task_id="task-row-bad-payload")
    )

    _worker(testing_session_local, RecordingExecutor()).run_once()

    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.EXHAUSTED.value
    assert row.attempt_count == 1
    assert "tool_ids must be a list" in (row.last_error or "")


def test_a_fresh_install_runs_triggers_without_any_agent_configuration(db, testing_session_local):
    """A fresh database with just a user runs a trigger to completion (no agent row needed).

    Before JAN-169 an unattended run needed an ``AgentDefinition`` row to
    resolve a capability profile and failed on a fresh install; identity now
    comes from the user's character constitution, which always has a default.
    """
    user = models.User(username="fresh-install-owner", hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    executor = RecordingExecutor()
    record, _ = trigger_service.enqueue_trigger(
        db, _trigger(user.id, {"objective": "Summarize today"}, task_id="task-fresh-install")
    )

    results = _worker(testing_session_local, executor).run_once()

    assert [result.status for result in results] == [TriggerDispatchStatus.COMPLETED]
    row = _row(db, record.id)
    assert row.status == models.RuntimeTriggerStatus.COMPLETED.value
    assert len(executor.calls) == 1


def test_unattended_runs_speak_as_the_users_character_with_the_skill_library(db, testing_session_local):
    """The system prompt handed to the agent loop renders the owner's constitution and every skill."""
    from janus.schemas.character import CharacterProfilePayload
    from janus.services.character_service import save_character_profile

    user = _owner(db, "trigger-owner-character")
    save_character_profile(
        db,
        user.id,
        CharacterProfilePayload(
            name="Night Watch",
            identity="Keeps an eye on overnight automations.",
            core_values=["Be accurate"],
            non_negotiables=["Never invent results"],
        ),
    )
    db.add(models.AgentSkill(name="Overnight triage", description="Triage", content="Check the backlog first."))
    db.commit()
    executor = RecordingExecutor()

    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    result = handler(_trigger(user.id, {"objective": "Summarize today", "tool_ids": ["core"]}, task_id="task-char"))

    assert result.state.node == PlanGraphNode.COMPLETE
    system_prompt = executor.calls[0]["system_prompt"]
    assert "# Character constitution: Night Watch" in system_prompt
    assert "Keeps an eye on overnight automations." in system_prompt
    assert "Never invent results" in system_prompt
    assert "## Overnight triage" in system_prompt
    assert "Check the backlog first." in system_prompt
    assert "If a tool call is refused or fails, report that plainly" in system_prompt
    assert "handoff" not in system_prompt.lower()


def test_unattended_runs_render_the_persisted_character_state_but_cannot_propose_changes(db, testing_session_local):
    """JAN-301: a trigger run renders the owner's adaptive state, and never gets the proposal tool."""
    from janus.services import character_state_service

    user = _owner(db, "trigger-owner-character-state")
    character_state_service.set_state(
        db,
        user.id,
        {"tone": "warm", "verbosity": "brief", "proactivity": "proactive", "current_focus": "Quarterly report"},
    )
    executor = RecordingExecutor()
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)

    handler(
        _trigger(
            user.id, {"objective": "Summarize today", "tool_ids": ["core", "character"]}, task_id="task-char-state"
        )
    )

    system_prompt = executor.calls[0]["system_prompt"]
    assert "- tone: warm" in system_prompt
    assert "- verbosity: brief" in system_prompt
    assert "<character_focus>Quarterly report</character_focus>" in system_prompt
    assert "get_character_state" in executor.calls[0]["tool_names"]
    assert "propose_character_state" not in executor.calls[0]["tool_names"]

    # A run that names the chat-only proposal toolbox is refused before the model runs; nothing is proposed.
    executor = RecordingExecutor()
    handler = build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    with pytest.raises(TerminalTriggerRunFailedError, match="character.propose:not_allowlisted"):
        handler(
            _trigger(
                user.id,
                {"objective": "Adapt yourself", "tool_ids": ["core", "character.propose"]},
                task_id="task-char-propose",
            )
        )
    assert executor.calls == []
    assert db.query(models.CharacterStateProposal).filter_by(user_id=user.id).count() == 0
