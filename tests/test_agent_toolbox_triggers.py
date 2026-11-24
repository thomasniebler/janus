"""Tests for the self-service runtime-trigger toolbox (JAN-184)."""

from datetime import UTC, datetime, timedelta

import pytest

from janus import models, runtime_adapter, tool_policy, trigger_service
from janus.agent_toolboxes.shared import ToolContext
from janus.agent_toolboxes.triggers import build_trigger_tools
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_tool_set
from janus.tool_registry import ToolBuildContext, ToolSelectionRequest
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerWorker

TRIGGER_TOOL_NAMES = {"create_scheduled_trigger", "list_my_triggers", "get_trigger", "cancel_trigger"}


def _make_user(db, username: str) -> models.User:
    """Create and persist a test user."""
    user = models.User(username=username, hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _tools(db, user: models.User) -> dict:
    """Build the trigger toolbox directly, indexed by tool name."""
    context = ToolContext(db=db, user=user)
    return {tool.name: tool for tool in build_trigger_tools(context)}


def _due_in(**delta) -> str:
    """Return an ISO-8601 timestamp offset from now."""
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


# --- registry wiring and authorization -------------------------------------


def test_registry_exposes_the_trigger_entry_with_its_declared_policy():
    """The registry entry must carry the capability/risk metadata policy relies on."""
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == "triggers")

    assert entry.required_capabilities == ("triggers",)
    assert entry.operation == "mixed"
    assert entry.risk_level == "medium"
    assert entry.confirmation == "recommended"


def test_chat_and_unattended_runs_may_both_schedule_follow_ups():
    """Since JAN-274 the toolbox guards chains itself, so it is not locked out of unattended runs."""
    assert "triggers" in runtime_adapter.TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    assert "triggers" not in tool_policy.LOCKED_UNATTENDED


def test_trigger_tools_are_built_without_any_connection(db):
    """Trigger tools are Janus-owned and ungated: every user receives the full toolbox."""
    user = _make_user(db, "with-trigger-access")

    tools = {tool.name for tool in build_tool_set(db, user)}

    assert tools >= TRIGGER_TOOL_NAMES


@pytest.mark.parametrize(
    ("requested_id", "allowed_ids", "expected_reason"),
    [
        ("trigger", frozenset({"triggers"}), "unknown_tool"),
        ("triggers", frozenset({"core"}), "not_allowlisted"),
    ],
)
def test_trigger_authorization_denials(db, requested_id, allowed_ids, expected_reason):
    """Unknown and non-allowlisted requests deny with stable reasons."""
    user = _make_user(db, f"denied-{expected_reason}")
    context = ToolBuildContext(tool_context=ToolContext(db=db, user=user))

    selection = DEFAULT_TOOL_REGISTRY.authorize(
        context,
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=(requested_id,),
            allowed_tool_ids=allowed_ids,
            granted_capabilities=frozenset({"triggers"}),
        ),
    )

    assert selection.entries == ()
    assert [(denial.tool_id, denial.reason) for denial in selection.denials] == [(requested_id, expected_reason)]


def test_trigger_tool_denies_a_missing_capability(db):
    """Without the triggers capability the entry is denied, not silently granted."""
    user = _make_user(db, "no-capability")
    context = ToolBuildContext(tool_context=ToolContext(db=db, user=user))

    selection = DEFAULT_TOOL_REGISTRY.authorize(
        context,
        ToolSelectionRequest(
            task_id="task-1",
            requested_tool_ids=("triggers",),
            allowed_tool_ids=frozenset({"triggers"}),
            granted_capabilities=frozenset(),
        ),
    )

    assert [denial.reason for denial in selection.denials] == ["missing_capability"]


# --- creation ---------------------------------------------------------------


def test_create_scheduled_trigger_persists_an_owned_queued_trigger(db):
    """A created trigger is owned, queued, scheduled, and carries its objective."""
    user = _make_user(db, "scheduler")
    tools = _tools(db, user)

    created = tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=3), "objective": "Check back on the vendor reply", "task_id": "task-vendor"}
    )

    assert created["status"] == models.RuntimeTriggerStatus.QUEUED.value
    assert created["kind"] == models.RuntimeTriggerKind.SCHEDULED.value
    assert created["objective"] == "Check back on the vendor reply"
    assert created["duplicate"] is False
    assert created["scheduled_for"] == created["created_at"]

    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert record.user_id == user.id
    assert record.source == "agent-tool"


def test_serialized_timestamps_are_utc_aware_and_round_trip_into_due_at(db):
    """Serialized timestamps must carry an explicit UTC offset, not read as local time.

    The stored columns are timezone-naive UTC; a naive-looking string would be
    misleading, and could not be fed back into ``due_at`` (which requires an
    explicit offset).
    """
    user = _make_user(db, "utc-scheduler")
    tools = _tools(db, user)

    created = tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "Round-trip me", "task_id": "task-utc"}
    )

    for field in ("scheduled_for", "created_at"):
        value = created[field]
        assert value is not None
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, f"{field} must include a UTC offset: {value!r}"

    # Must be directly reusable as a due_at for a follow-up trigger.
    tools["create_scheduled_trigger"].invoke(
        {"due_at": created["scheduled_for"], "objective": "Follow-up", "task_id": "task-utc-2"}
    )


def test_create_scheduled_trigger_is_idempotent_for_an_identical_request(db):
    """Repeating the same objective for the same moment reuses the existing row."""
    user = _make_user(db, "repeat-scheduler")
    tools = _tools(db, user)
    due_at = _due_in(days=2)
    request = {"due_at": due_at, "objective": "Nudge the release", "task_id": "task-release"}

    first = tools["create_scheduled_trigger"].invoke(request)
    second = tools["create_scheduled_trigger"].invoke(request)

    assert second["trigger_id"] == first["trigger_id"]
    assert second["duplicate"] is True
    assert db.query(models.RuntimeTrigger).count() == 1


@pytest.mark.parametrize(
    ("due_at", "message"),
    [
        ("not-a-timestamp", "ISO-8601"),
        ("2030-01-01T09:00:00", "timezone offset"),
        ("2000-01-01T09:00:00+00:00", "in the future"),
    ],
)
def test_create_scheduled_trigger_rejects_bad_due_timestamps(db, due_at, message):
    """Malformed, naive, and past timestamps are rejected before persistence."""
    user = _make_user(db, f"bad-due-{abs(hash(due_at))}")
    tools = _tools(db, user)

    with pytest.raises(ValueError, match=message):
        tools["create_scheduled_trigger"].invoke({"due_at": due_at, "objective": "x", "task_id": "t"})

    assert db.query(models.RuntimeTrigger).count() == 0


def test_create_scheduled_trigger_rejects_an_unknown_task_mode(db):
    """A task mode no allowlist knows would authorize nothing, so reject it early."""
    user = _make_user(db, "bad-mode")
    tools = _tools(db, user)

    with pytest.raises(ValueError, match="task_mode must be one of"):
        tools["create_scheduled_trigger"].invoke(
            {"due_at": _due_in(days=1), "objective": "x", "task_id": "t", "task_mode": "default"}
        )


def test_create_scheduled_trigger_enforces_the_per_owner_backlog_cap(db, monkeypatch):
    """The runaway self-scheduling safeguard refuses to extend a full backlog."""
    monkeypatch.setattr(trigger_service, "MAX_ACTIVE_TRIGGERS_PER_OWNER", 2)
    monkeypatch.setattr("janus.agent_toolboxes.triggers.MAX_ACTIVE_TRIGGERS_PER_OWNER", 2)
    user = _make_user(db, "runaway")
    tools = _tools(db, user)
    for index in range(2):
        tools["create_scheduled_trigger"].invoke(
            {"due_at": _due_in(days=index + 1), "objective": f"step {index}", "task_id": "task-loop"}
        )

    with pytest.raises(ValueError, match="cancel one before scheduling more"):
        tools["create_scheduled_trigger"].invoke(
            {"due_at": _due_in(days=9), "objective": "one too many", "task_id": "task-loop"}
        )

    assert db.query(models.RuntimeTrigger).count() == 2


def test_the_backlog_cap_is_scoped_per_owner(db, monkeypatch):
    """One user filling their backlog must not block a different user."""
    monkeypatch.setattr(trigger_service, "MAX_ACTIVE_TRIGGERS_PER_OWNER", 1)
    monkeypatch.setattr("janus.agent_toolboxes.triggers.MAX_ACTIVE_TRIGGERS_PER_OWNER", 1)
    owner = _make_user(db, "cap-owner")
    other = _make_user(db, "cap-other")
    _tools(db, owner)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "mine", "task_id": "task-a"}
    )

    created = _tools(db, other)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "theirs", "task_id": "task-b"}
    )

    assert created["status"] == models.RuntimeTriggerStatus.QUEUED.value


def test_retrying_an_identical_request_dedupes_even_at_a_full_backlog(db, monkeypatch):
    """A harmless retry of the same request must not be rejected by the backlog cap.

    Deduping by (task_id, idempotency_key) does not enlarge the backlog, so the
    cap must only ever gate genuinely new triggers.
    """
    monkeypatch.setattr(trigger_service, "MAX_ACTIVE_TRIGGERS_PER_OWNER", 1)
    monkeypatch.setattr("janus.agent_toolboxes.triggers.MAX_ACTIVE_TRIGGERS_PER_OWNER", 1)
    user = _make_user(db, "retry-at-cap")
    tools = _tools(db, user)
    request = {"due_at": _due_in(days=1), "objective": "same request", "task_id": "task-retry"}
    first = tools["create_scheduled_trigger"].invoke(request)
    assert first["duplicate"] is False

    retried = tools["create_scheduled_trigger"].invoke(request)

    assert retried["duplicate"] is True
    assert retried["trigger_id"] == first["trigger_id"]
    assert db.query(models.RuntimeTrigger).count() == 1


# --- ownership scoping ------------------------------------------------------


def test_list_my_triggers_returns_only_the_callers_triggers(db):
    """Listing is filtered by owner, never by the caller's arguments."""
    owner = _make_user(db, "list-owner")
    other = _make_user(db, "list-other")
    _tools(db, other)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "not yours", "task_id": "task-other"}
    )
    mine = _tools(db, owner)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "mine", "task_id": "task-mine"}
    )

    listed = _tools(db, owner)["list_my_triggers"].invoke({})

    assert [item["trigger_id"] for item in listed] == [mine["trigger_id"]]


def test_list_my_triggers_filters_by_status(db):
    """An explicit status filter narrows the owner's own triggers."""
    user = _make_user(db, "list-status")
    tools = _tools(db, user)
    tools["create_scheduled_trigger"].invoke({"due_at": _due_in(days=1), "objective": "a", "task_id": "task-a"})

    assert len(tools["list_my_triggers"].invoke({"status": "queued"})) == 1
    assert tools["list_my_triggers"].invoke({"status": "completed"}) == []


def test_get_trigger_hides_another_users_trigger(db):
    """A foreign trigger reads as missing so IDs cannot be probed."""
    owner = _make_user(db, "get-owner")
    other = _make_user(db, "get-other")
    theirs = _tools(db, other)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "theirs", "task_id": "task-theirs"}
    )

    with pytest.raises(ValueError, match="no trigger of yours"):
        _tools(db, owner)["get_trigger"].invoke({"trigger_id": theirs["trigger_id"]})


def test_cancel_trigger_cannot_cancel_another_users_trigger(db):
    """Cancellation is owner-scoped and leaves the foreign trigger untouched."""
    owner = _make_user(db, "cancel-owner")
    other = _make_user(db, "cancel-other")
    theirs = _tools(db, other)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "theirs", "task_id": "task-theirs"}
    )

    with pytest.raises(ValueError, match="no trigger of yours"):
        _tools(db, owner)["cancel_trigger"].invoke({"trigger_id": theirs["trigger_id"]})

    db.expire_all()
    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=theirs["trigger_id"]).one()
    assert record.status == models.RuntimeTriggerStatus.QUEUED.value


# --- cancellation -----------------------------------------------------------


def test_cancel_trigger_makes_a_queued_trigger_terminal(db):
    """Cancelling a queued trigger is terminal and removes it from the backlog."""
    user = _make_user(db, "canceller")
    tools = _tools(db, user)
    created = tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "drop me", "task_id": "task-drop"}
    )

    cancelled = tools["cancel_trigger"].invoke({"trigger_id": created["trigger_id"]})

    assert cancelled["status"] == models.RuntimeTriggerStatus.CANCELLED.value
    db.expire_all()
    assert trigger_service.count_active_triggers(db, user.id) == 0


def test_cancel_trigger_refuses_an_already_cancelled_trigger(db):
    """A terminal trigger cannot be cancelled twice."""
    user = _make_user(db, "double-canceller")
    tools = _tools(db, user)
    created = tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(days=1), "objective": "drop me", "task_id": "task-drop"}
    )
    tools["cancel_trigger"].invoke({"trigger_id": created["trigger_id"]})

    with pytest.raises(ValueError, match="cannot be cancelled while it is cancelled"):
        tools["cancel_trigger"].invoke({"trigger_id": created["trigger_id"]})


def test_a_cancelled_trigger_is_never_claimed_by_the_durable_worker(db, testing_session_local):
    """Cancellation must actually prevent dispatch, not just relabel the row."""
    user = _make_user(db, "cancel-before-due")
    tools = _tools(db, user)
    created = tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(minutes=5), "objective": "Should never run", "task_id": "task-cancelled"}
    )
    tools["cancel_trigger"].invoke({"trigger_id": created["trigger_id"]})

    worker = DurableTriggerWorker(testing_session_local, build_runtime_trigger_handler(testing_session_local))
    results = worker.run_once(now=datetime.now(UTC) + timedelta(hours=1))

    assert results == []
    db.expire_all()
    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert record.status == models.RuntimeTriggerStatus.CANCELLED.value
    assert record.attempt_count == 0


# --- end-to-end dispatch ----------------------------------------------------


def test_an_agent_created_trigger_is_claimable_and_dispatched_when_due(db, testing_session_local):
    """A tool-created trigger flows through the existing durable worker unchanged."""
    user = _make_user(db, "dispatch-owner")
    created = _tools(db, user)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(minutes=10), "objective": "Review the contract", "task_id": "task-contract"}
    )

    worker = DurableTriggerWorker(testing_session_local, build_runtime_trigger_handler(testing_session_local))

    # Not yet due: the scheduled trigger must not be claimed early.
    assert worker.run_once() == []

    results = worker.run_once(now=datetime.now(UTC) + timedelta(hours=1))

    assert len(results) == 1
    assert results[0].trigger.trigger_id == created["trigger_id"]
    assert results[0].trigger.owner_id == user.id
    db.expire_all()
    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert record.attempt_count == 1


# --- follow-ups as agent-authored one-time definitions (JAN-257) -------------


def _follow_up(db, user, **overrides):
    """Create one follow-up through the tool and return (result, definition)."""
    args = {"due_at": _due_in(hours=2), "objective": "Check back on the vendor reply", "task_id": "task-vendor"}
    created = _tools(db, user)["create_scheduled_trigger"].invoke({**args, **overrides})
    db.expire_all()
    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    definition = db.get(models.TriggerDefinition, record.definition_id)
    return created, definition


def test_a_follow_up_is_recorded_as_an_agent_authored_one_time_definition(db):
    """Every follow-up gets a visible definition that its single queued run points back to."""
    user = _make_user(db, "follow-up-owner")

    _created, definition = _follow_up(db, user)

    assert definition is not None
    assert definition.user_id == user.id
    assert definition.origin == models.TriggerDefinitionOrigin.AGENT.value
    assert definition.kind == models.TriggerDefinitionKind.SCHEDULE.value
    assert definition.schedule["type"] == "once"
    assert definition.name == "Follow-up: Check back on the vendor reply"
    assert definition.enabled is True
    assert definition.tool_ids == []
    # The scheduler never picks it up: its one run is already queued.
    assert definition.next_run_at is None
    assert db.query(models.RuntimeTrigger).filter_by(definition_id=definition.id).count() == 1


def test_an_identical_follow_up_request_does_not_create_a_second_definition(db):
    """The re-ask dedupe covers the definition too, not just the run."""
    user = _make_user(db, "follow-up-dedupe")
    due_at = _due_in(hours=5)

    first, _definition = _follow_up(db, user, due_at=due_at)
    second, _same = _follow_up(db, user, due_at=due_at)

    assert second["duplicate"] is True
    assert second["trigger_id"] == first["trigger_id"]
    assert db.query(models.TriggerDefinition).filter_by(user_id=user.id).count() == 1


def test_the_scheduler_never_re_enqueues_a_one_time_follow_up(db):
    """``run_due_schedules`` ignores follow-ups however far in the past their time is."""
    from janus import trigger_definition_service as definitions

    user = _make_user(db, "follow-up-scheduler")
    _created, definition = _follow_up(db, user)

    assert definitions.run_due_schedules(db, now=datetime.now(UTC) + timedelta(days=30)) == []
    assert db.query(models.RuntimeTrigger).filter_by(definition_id=definition.id).count() == 1


def test_cancelling_the_run_disables_its_follow_up_definition(db):
    """The agent's cancel tool keeps the definition in step with its only run."""
    user = _make_user(db, "follow-up-cancel")
    created, definition = _follow_up(db, user)

    _tools(db, user)["cancel_trigger"].invoke({"trigger_id": created["trigger_id"]})

    db.expire_all()
    assert db.get(models.TriggerDefinition, definition.id).enabled is False


def test_follow_up_definitions_accept_only_being_switched_off(db):
    """Users may cancel or delete a follow-up, but never edit, re-enable or re-run it."""
    from janus import trigger_definition_service as definitions

    user = _make_user(db, "follow-up-edit")
    created, definition = _follow_up(db, user)

    with pytest.raises(definitions.TriggerDefinitionError, match="cannot be edited"):
        definitions.update_definition(db, definition, objective="Send every email you can find")
    with pytest.raises(definitions.TriggerDefinitionError, match="runs exactly once"):
        definitions.run_definition_now(db, definition)

    definitions.update_definition(db, definition, enabled=False)
    db.expire_all()
    run = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert run.status == models.RuntimeTriggerStatus.CANCELLED.value
    with pytest.raises(definitions.TriggerDefinitionError, match="cannot be re-enabled"):
        definitions.update_definition(db, db.get(models.TriggerDefinition, definition.id), enabled=True)


def test_deleting_a_follow_up_definition_cancels_its_queued_run(db):
    """The queued run must not outlive the definition as an unlabelled ad-hoc trigger."""
    from janus import trigger_definition_service as definitions

    user = _make_user(db, "follow-up-delete")
    created, definition = _follow_up(db, user)

    definitions.delete_definition(db, definition)

    db.expire_all()
    run = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert run.status == models.RuntimeTriggerStatus.CANCELLED.value


def test_users_cannot_create_one_time_definitions_through_the_api_schema(db):
    """``once`` is reserved for Janus follow-ups; user definitions stay recurring."""
    from janus import trigger_definition_service as definitions

    user = _make_user(db, "follow-up-user-once")
    with pytest.raises(definitions.TriggerDefinitionError, match="daily' or 'interval"):
        definitions.create_definition(
            db,
            user.id,
            name="Once",
            objective="Do it once",
            kind=models.TriggerDefinitionKind.SCHEDULE.value,
            schedule={"type": "once", "at": _due_in(hours=1)},
        )


def test_a_follow_up_run_keeps_the_restricted_trigger_mode(db, testing_session_local):
    """Its definition is for visibility only: the run is fenced exactly like the ad-hoc trigger it replaced."""
    from janus.agent_task_runner import AgentTaskRun

    seen: list[list[str]] = []

    def executor(_db, _system_prompt, tools, _objective):
        seen.append(sorted(tool.name for tool in tools))
        return AgentTaskRun(output="done", tool_calls=())

    user = _make_user(db, "follow-up-mode")
    created, _definition = _follow_up(db, user, due_at=_due_in(minutes=10))
    worker = DurableTriggerWorker(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )

    results = worker.run_once(now=datetime.now(UTC) + timedelta(hours=1))

    assert [result.trigger.trigger_id for result in results] == [created["trigger_id"]]
    assert seen, "the follow-up never reached the executor"
    # The trigger toolbox is offered (JAN-274); the loop guard, not a lock,
    # bounds how far a follow-up may schedule further follow-ups.
    assert set(seen[0]) >= TRIGGER_TOOL_NAMES


def test_a_follow_up_that_loses_a_dedupe_race_leaves_no_stray_definition(db, monkeypatch):
    """If the enqueue dedupes to another call's run, the provisional definition is removed."""
    from janus.agent_toolboxes import triggers as trigger_tools

    user = _make_user(db, "follow-up-race")
    first, first_definition = _follow_up(db, user)
    real_enqueue = trigger_tools.enqueue_trigger

    def lose_the_race(session, envelope, *, definition_id=None, **_lineage):
        # The concurrent caller's existence check ran before the winner's run
        # was committed; its insert then hits the uniqueness constraint and
        # dedupes to the winner's row, exactly as ``enqueue_trigger`` does.
        winner = session.query(models.RuntimeTrigger).filter_by(trigger_id=first["trigger_id"]).one()
        return winner, True

    monkeypatch.setattr(trigger_tools, "enqueue_trigger", lose_the_race)
    second = _tools(db, user)["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(hours=7), "objective": "Check back on the vendor reply", "task_id": "task-vendor"}
    )
    monkeypatch.setattr(trigger_tools, "enqueue_trigger", real_enqueue)

    assert second["duplicate"] is True and second["trigger_id"] == first["trigger_id"]
    db.expire_all()
    assert [row.id for row in db.query(models.TriggerDefinition).filter_by(user_id=user.id)] == [first_definition.id]


def test_a_failed_enqueue_removes_the_follow_up_definition(db, monkeypatch):
    """A follow-up never keeps a definition whose run could not be queued."""
    from janus.agent_toolboxes import triggers as trigger_tools

    user = _make_user(db, "follow-up-enqueue-fails")

    def boom(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(trigger_tools, "enqueue_trigger", boom)
    with pytest.raises(Exception, match="database unavailable"):
        _tools(db, user)["create_scheduled_trigger"].invoke(
            {"due_at": _due_in(hours=1), "objective": "Ping me", "task_id": "task-ping"}
        )

    db.expire_all()
    assert db.query(models.TriggerDefinition).filter_by(user_id=user.id).count() == 0
