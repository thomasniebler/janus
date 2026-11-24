"""Tests for the event-log and trigger-history introspection tools."""

import json
from datetime import timedelta

import pytest

from janus import models, security, time_utils, tool_policy
from janus.agent_toolboxes.events import MAX_INTROSPECTION_LIMIT
from janus.agent_tools import DEFAULT_TOOL_REGISTRY, build_tool_set
from janus.runtime_adapter import TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter


def _make_user(db, username: str, *, is_admin: bool = False) -> models.User:
    """Create and persist a test user."""
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _tools(db, user: models.User) -> dict:
    """Build the tool set for a user and index it by tool name."""
    return {tool.name: tool for tool in build_tool_set(db, user)}


def _add_event(
    db,
    *,
    event_type: models.EventType,
    source: str,
    message: str,
    offset_minutes: int,
    user_id: int | None,
) -> models.Event:
    """Persist one event owned by ``user_id`` (``None``: a system row) at a deterministic relative timestamp."""
    event = models.Event(
        timestamp=time_utils.utcnow().replace(tzinfo=None) - timedelta(minutes=offset_minutes),
        event_type=event_type,
        source=source,
        message=message,
        details='{"attempt": 1}',
        user_id=user_id,
    )
    db.add(event)
    db.commit()
    return event


def _add_trigger(
    db,
    *,
    user_id: int | None,
    trigger_id: str,
    status: models.RuntimeTriggerStatus,
    last_error: str | None = None,
    attempt_count: int = 0,
    offset_minutes: int = 0,
    task_id: str | None = None,
    payload: dict | None = None,
) -> models.RuntimeTrigger:
    """Persist one runtime trigger row owned by the given user."""
    now = time_utils.utcnow().replace(tzinfo=None)
    trigger = models.RuntimeTrigger(
        user_id=user_id,
        trigger_id=trigger_id,
        idempotency_key=trigger_id,
        task_id=task_id or f"task-{trigger_id}",
        kind=models.RuntimeTriggerKind.SCHEDULED.value,
        source="test",
        payload=payload or {"objective": "do the thing"},
        status=status.value,
        last_error=last_error,
        attempt_count=attempt_count,
        completed_at=now if status is models.RuntimeTriggerStatus.COMPLETED else None,
        created_at=now - timedelta(minutes=offset_minutes),
        updated_at=now - timedelta(minutes=offset_minutes),
    )
    db.add(trigger)
    db.commit()
    return trigger


def test_event_tools_are_available_without_any_connection(db):
    """Introspection tools are ungated: a user with no connected integrations still gets them."""
    user = _make_user(db, "events-ungated")

    assert {"list_recent_events", "list_my_trigger_history"} <= set(_tools(db, user))


def test_list_recent_events_returns_newest_first_with_decoded_details(db):
    """Events come back newest first with their JSON details parsed."""
    user = _make_user(db, "events-order")
    _add_event(
        db, event_type=models.EventType.INFO, source="PlannerService", message="old", offset_minutes=10, user_id=user.id
    )
    _add_event(
        db, event_type=models.EventType.ERROR, source="IMAPMonitor", message="new", offset_minutes=1, user_id=user.id
    )

    results = _tools(db, user)["list_recent_events"].invoke({})

    assert [entry["message"] for entry in results] == ["new", "old"]
    assert results[0]["event_type"] == "ERROR"
    assert results[0]["source"] == "IMAPMonitor"
    assert results[0]["details"] == {"attempt": 1}


def test_list_recent_events_filters_by_type_source_and_limit(db):
    """Optional filters narrow the event log and the limit caps the rows."""
    user = _make_user(db, "events-filter")
    _add_event(
        db,
        event_type=models.EventType.ERROR,
        source="PlannerService",
        message="planner boom",
        offset_minutes=3,
        user_id=user.id,
    )
    _add_event(
        db,
        event_type=models.EventType.ERROR,
        source="IMAPMonitor",
        message="imap boom",
        offset_minutes=2,
        user_id=user.id,
    )
    _add_event(
        db,
        event_type=models.EventType.INFO,
        source="PlannerService",
        message="planner ok",
        offset_minutes=1,
        user_id=user.id,
    )

    tools = _tools(db, user)

    by_type = tools["list_recent_events"].invoke({"event_type": "ERROR"})
    assert sorted(entry["message"] for entry in by_type) == ["imap boom", "planner boom"]

    by_source = tools["list_recent_events"].invoke({"source": "PlannerService"})
    assert sorted(entry["message"] for entry in by_source) == ["planner boom", "planner ok"]

    assert len(tools["list_recent_events"].invoke({"limit": 1})) == 1


def test_list_recent_events_clamps_out_of_range_limits(db):
    """Absurd limits are clamped instead of dumping or returning nothing."""
    user = _make_user(db, "events-clamp")
    for index in range(3):
        _add_event(
            db,
            event_type=models.EventType.INFO,
            source="PlannerService",
            message=f"event-{index}",
            offset_minutes=index,
            user_id=user.id,
        )

    tools = _tools(db, user)

    assert len(tools["list_recent_events"].invoke({"limit": 0})) == 1
    assert len(tools["list_recent_events"].invoke({"limit": MAX_INTROSPECTION_LIMIT + 500})) == 3


def test_list_recent_events_rejects_unknown_event_type(db):
    """An unparseable event type names the supported values."""
    user = _make_user(db, "events-bad-type")

    with pytest.raises(ValueError, match="event_type must be one of"):
        _tools(db, user)["list_recent_events"].func(event_type="NOPE")


def test_list_my_trigger_history_reports_outcome_fields(db):
    """Trigger history exposes status, error, attempts and completion time."""
    user = _make_user(db, "triggers-outcome")
    _add_trigger(
        db,
        user_id=user.id,
        trigger_id="failed-one",
        status=models.RuntimeTriggerStatus.FAILED,
        last_error="upstream timeout",
        attempt_count=3,
        offset_minutes=5,
    )
    _add_trigger(
        db,
        user_id=user.id,
        trigger_id="done-one",
        status=models.RuntimeTriggerStatus.COMPLETED,
        attempt_count=1,
        offset_minutes=1,
    )

    results = _tools(db, user)["list_my_trigger_history"].invoke({})

    assert [entry["trigger_id"] for entry in results] == ["done-one", "failed-one"]
    assert results[0]["status"] == "completed"
    assert results[0]["completed_at"] is not None
    assert results[1]["last_error"] == "upstream timeout"
    assert results[1]["attempt_count"] == 3
    assert results[1]["completed_at"] is None
    assert results[1]["objective"] == "do the thing"
    # The raw payload must never be exposed -- scheduled/event integrations can
    # attach arbitrary custom data to it (JAN-185 review fix).
    assert "payload" not in results[1]


def test_list_my_trigger_history_filters_by_status(db):
    """The status filter accepts the persisted lifecycle values."""
    user = _make_user(db, "triggers-status")
    _add_trigger(db, user_id=user.id, trigger_id="a", status=models.RuntimeTriggerStatus.FAILED, offset_minutes=2)
    _add_trigger(db, user_id=user.id, trigger_id="b", status=models.RuntimeTriggerStatus.COMPLETED, offset_minutes=1)

    results = _tools(db, user)["list_my_trigger_history"].invoke({"status": "failed"})

    assert [entry["trigger_id"] for entry in results] == ["a"]


def test_list_my_trigger_history_rejects_unknown_status(db):
    """An unparseable trigger status names the supported values."""
    user = _make_user(db, "triggers-bad-status")

    with pytest.raises(ValueError, match="status must be one of"):
        _tools(db, user)["list_my_trigger_history"].func(status="exploded")


def test_list_my_trigger_history_never_leaks_other_owners(db):
    """Triggers owned by another user or by nobody stay invisible."""
    user = _make_user(db, "triggers-mine")
    other = _make_user(db, "triggers-theirs")
    _add_trigger(db, user_id=user.id, trigger_id="mine", status=models.RuntimeTriggerStatus.COMPLETED)
    _add_trigger(db, user_id=other.id, trigger_id="theirs", status=models.RuntimeTriggerStatus.FAILED)
    _add_trigger(db, user_id=None, trigger_id="ownerless", status=models.RuntimeTriggerStatus.QUEUED)

    tools = _tools(db, user)

    assert [entry["trigger_id"] for entry in tools["list_my_trigger_history"].invoke({})] == ["mine"]
    assert [entry["trigger_id"] for entry in tools["list_my_trigger_history"].invoke({"status": "failed"})] == []

    other_tools = _tools(db, other)
    assert [entry["trigger_id"] for entry in other_tools["list_my_trigger_history"].invoke({})] == ["theirs"]


def test_trigger_history_is_available_in_chat_and_every_unattended_mode(db):
    """The owner-scoped history is what lets an unattended run check its prior outcomes (JAN-192/195)."""
    user = _make_user(db, "history-everywhere")
    assert "trigger-history" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    for task_mode in sorted(tool_policy.UNATTENDED_TASK_MODES):
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode=task_mode
        )
        assert "trigger-history" in offered & confirmed, task_mode


def test_the_event_log_is_offered_and_allowed_in_every_unattended_mode(db):
    """Owner-scoped since JAN-272, so it follows the switch, default allowed, like trigger history."""
    user = _make_user(db, "events-unattended")
    assert "events" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    assert "events" not in tool_policy.LOCKED_UNATTENDED
    for task_mode in sorted(tool_policy.UNATTENDED_TASK_MODES):
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode=task_mode
        )
        assert "events" in offered & confirmed, task_mode


def _seed_three_owners(db) -> tuple[models.User, models.User]:
    """Seed one row each for an owner, another user, and the system; return (owner, other)."""
    owner = _make_user(db, "event-owner", is_admin=True)
    other = _make_user(db, "event-other")
    _add_event(db, event_type=models.EventType.INFO, source="S", message="mine", offset_minutes=3, user_id=owner.id)
    _add_event(db, event_type=models.EventType.INFO, source="S", message="theirs", offset_minutes=2, user_id=other.id)
    _add_event(db, event_type=models.EventType.ERROR, source="S", message="system", offset_minutes=1, user_id=None)
    return owner, other


def _event_tool_for(db, user: models.User, task_mode: str):
    """Prepare a step in ``task_mode`` and return its ``list_recent_events`` tool."""
    if task_mode == "chat":
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id="chat-events", objective="What happened?", requested_tool_ids=("events",), task_mode="chat"
        )
    else:
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode=task_mode
        )
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id=f"{task_mode}-events",
            objective="Check what my last run did",
            requested_tool_ids=("events",),
            task_mode=task_mode,
            allowed_tool_ids=offered,
            confirmed_tool_ids=confirmed,
            blocked_tool_ids=offered - confirmed,
        )
    assert prepared.denials == ()
    return next(tool for tool in prepared.tools if tool.name == "list_recent_events")


def _step_rows(tool) -> list[dict]:
    """Invoke a plan-step event tool and unwrap its spotlighted output (JAN-127) back into rows."""
    output = tool.invoke({})
    if isinstance(output, str):
        output = json.loads(output.split("\n", 1)[1].rsplit("\n", 1)[0])
    return output


@pytest.mark.parametrize("task_mode", sorted(tool_policy.UNATTENDED_TASK_MODES))
def test_an_unattended_run_reads_only_its_owners_events(db, task_mode):
    """Cross-user isolation (JAN-272): never another user's rows, never system rows -- even for an admin owner."""
    owner, other = _seed_three_owners(db)

    assert [row["message"] for row in _step_rows(_event_tool_for(db, owner, task_mode))] == ["mine"]
    assert [row["message"] for row in _step_rows(_event_tool_for(db, other, task_mode))] == ["theirs"]


def test_chat_shows_an_admin_the_whole_log_and_anyone_else_their_own_rows(db):
    """Chat matches GET /api/events: admins read everything, a non-admin only their rows."""
    owner, other = _seed_three_owners(db)

    assert [row["message"] for row in _step_rows(_event_tool_for(db, owner, "chat"))] == ["system", "theirs", "mine"]
    assert [row["message"] for row in _step_rows(_event_tool_for(db, other, "chat"))] == ["theirs"]


def test_a_tool_built_outside_a_plan_step_fails_closed_to_the_owners_rows(db):
    """With no task mode (introspection), even an admin sees only their own rows."""
    owner, _ = _seed_three_owners(db)

    assert [row["message"] for row in _tools(db, owner)["list_recent_events"].invoke({})] == ["mine"]


def _add_plan_state(db, user: models.User, task_id: str, *, result: str | None, error: str | None) -> None:
    """Persist a finished one-step plan state for a task, as trigger execution does."""
    from janus.plan_graph import (
        Plan,
        PlanGraphNode,
        PlanGraphState,
        PlanStatus,
        PlanStep,
        PlanStepStatus,
        TaskContext,
    )
    from janus.plan_state import PlanStateStore

    failed = error is not None
    state = PlanGraphState(
        task=TaskContext(task_id=task_id, objective="Check the day", task_mode="trigger"),
        node=PlanGraphNode.FAILED if failed else PlanGraphNode.COMPLETE,
        plan=Plan(
            plan_id=f"plan-{task_id}",
            task_id=task_id,
            objective="Check the day",
            status=PlanStatus.FAILED if failed else PlanStatus.COMPLETED,
            steps=(
                PlanStep(
                    step_id=f"{task_id}::step-1",
                    objective="Check the day",
                    status=PlanStepStatus.FAILED if failed else PlanStepStatus.COMPLETED,
                    result=result,
                    error=error,
                ),
            ),
        ),
    )
    PlanStateStore(lambda: db).save(db, user.id, state)


def test_trigger_history_surfaces_what_the_run_actually_produced(db):
    """JAN-192: history must answer "what happened", not just "which status".

    The result and the step error come from the durable plan state, which is
    the only place that records what the automation actually did.
    """
    user = _make_user(db, "history-result-user")
    _add_trigger(db, user_id=user.id, trigger_id="done", status=models.RuntimeTriggerStatus.COMPLETED)
    _add_plan_state(
        db,
        user,
        "task-done",
        result="Authorized tools: core\nTools invoked: list_recent_events\n\nNothing needs you.",
        error=None,
    )
    _add_trigger(db, user_id=user.id, trigger_id="broken", status=models.RuntimeTriggerStatus.EXHAUSTED)
    _add_plan_state(db, user, "task-broken", result=None, error="model provider unreachable")

    by_id = {entry["trigger_id"]: entry for entry in _tools(db, user)["list_my_trigger_history"].invoke({})}

    assert "Nothing needs you." in by_id["done"]["result"]
    assert "Tools invoked: list_recent_events" in by_id["done"]["result"]
    assert by_id["done"]["step_error"] is None
    assert by_id["broken"]["step_error"] == "model provider unreachable"
    assert by_id["broken"]["result"] is None


def test_trigger_history_still_never_exposes_the_raw_payload(db):
    """JAN-185's rule survives the new result field: the ingress payload stays hidden."""
    user = _make_user(db, "history-payload-user")
    _add_trigger(
        db,
        user_id=user.id,
        trigger_id="secretive",
        status=models.RuntimeTriggerStatus.COMPLETED,
        payload={"objective": "Check the day", "customer_secret": "do-not-leak"},
    )

    entry = _tools(db, user)["list_my_trigger_history"].invoke({})[0]

    assert "payload" not in entry
    assert "do-not-leak" not in str(entry)
    assert entry["objective"] == "Check the day"


def test_trigger_history_reports_no_outcome_for_another_users_plan_state(db):
    """Plan state is owner-scoped: one user's result must never leak into another's history."""
    user = _make_user(db, "history-owner")
    other = _make_user(db, "history-other")
    _add_trigger(
        db,
        user_id=user.id,
        trigger_id="mine",
        task_id="shared-task-id",
        status=models.RuntimeTriggerStatus.COMPLETED,
    )
    _add_plan_state(db, other, "shared-task-id", result="the other user's private summary", error=None)

    entry = _tools(db, user)["list_my_trigger_history"].invoke({})[0]

    assert entry["result"] is None
