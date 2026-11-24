"""Tests for the unattended self-scheduling loop guard on the trigger toolbox (JAN-274).

An unattended run may schedule follow-ups, but only within a bounded chain:
each follow-up records its parent run and depth, and the toolbox -- not the
prompt -- refuses chains that are too deep, too wide, too frequent, or too
soon. Cancellation from an unattended run is limited to Janus's own follow-ups.
"""

from datetime import UTC, datetime, timedelta

import pytest

from janus import models, trigger_service
from janus.agent_task_runner import AgentTaskRun
from janus.agent_toolboxes import triggers as trigger_tools
from janus.agent_toolboxes.shared import ToolContext
from janus.agent_toolboxes.triggers import AGENT_TRIGGER_SOURCE, MAX_UNATTENDED_CHAIN_DEPTH, build_trigger_tools
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerWorker


def _make_user(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _due_in(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def _run(db, user: models.User, name: str, *, depth: int = 0, source: str = "api") -> models.RuntimeTrigger:
    """Persist one durable run of ``user`` at ``depth`` that an unattended step can belong to."""
    spec = ScheduledTriggerSpec(
        schedule_id=f"run-{name}",
        owner_id=user.id,
        task_id=f"task-{name}",
        objective=f"run {name}",
        due_at=datetime.now(UTC) + timedelta(hours=1),
        source=source,
    )
    record, _ = trigger_service.enqueue_trigger(db, spec.as_envelope(), chain_depth=depth)
    return record


def _unattended(db, user: models.User, run_trigger_id: str | None, *, task_mode: str = "trigger") -> dict:
    tools = build_trigger_tools(ToolContext(db=db, user=user), task_mode=task_mode, run_trigger_id=run_trigger_id)
    return {tool.name: tool for tool in tools}


def _schedule(tools: dict, objective: str, **delta) -> dict:
    return tools["create_scheduled_trigger"].invoke(
        {"due_at": _due_in(**(delta or {"hours": 2})), "objective": objective, "task_id": "task-chain"}
    )


@pytest.mark.parametrize("task_mode", ["trigger", "automation"])
def test_a_follow_up_scheduled_unattended_records_its_parent_and_depth(db, task_mode):
    """The follow-up is a child of the run that scheduled it, one level deeper."""
    user = _make_user(db, f"lineage-{task_mode}")
    parent = _run(db, user, "root")

    created = _schedule(_unattended(db, user, parent.trigger_id, task_mode=task_mode), "check again")

    assert created["parent_trigger_id"] == parent.trigger_id
    assert created["chain_depth"] == 1
    row = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    assert (row.parent_trigger_id, row.chain_depth) == (parent.trigger_id, 1)


def test_chat_follow_ups_are_roots(db):
    """Chat is supervised: no lineage, depth 0, and no extra limits."""
    user = _make_user(db, "lineage-chat")

    created = _schedule(_unattended(db, user, None, task_mode="chat"), "remind me", minutes=5)

    assert created["parent_trigger_id"] is None and created["chain_depth"] == 0


def test_the_chain_depth_is_capped(db):
    """A run already at the maximum depth cannot schedule another follow-up."""
    user = _make_user(db, "depth-cap")
    allowed = _run(db, user, "allowed", depth=MAX_UNATTENDED_CHAIN_DEPTH - 1)
    deepest = _run(db, user, "deepest", depth=MAX_UNATTENDED_CHAIN_DEPTH)

    assert _schedule(_unattended(db, user, allowed.trigger_id), "last link")["chain_depth"] == 2
    with pytest.raises(ValueError, match="at most 2 follow-ups"):
        _schedule(_unattended(db, user, deepest.trigger_id), "one link too many")


def test_a_real_chain_stops_at_the_cap_when_each_run_reschedules_itself(db):
    """Walking the chain through the tool itself: root -> 1 -> 2, then refused."""
    user = _make_user(db, "depth-walk")
    current = _run(db, user, "root").trigger_id
    for depth in range(1, MAX_UNATTENDED_CHAIN_DEPTH + 1):
        current = _schedule(_unattended(db, user, current), f"poll {depth}")["trigger_id"]

    with pytest.raises(ValueError, match="cannot schedule another one"):
        _schedule(_unattended(db, user, current), "poll forever")


def test_one_run_may_schedule_only_a_few_follow_ups(db, monkeypatch):
    """The per-run fan-out cap counts cancelled follow-ups too."""
    monkeypatch.setattr(trigger_tools, "MAX_FOLLOW_UPS_PER_RUN", 2)
    user = _make_user(db, "fan-out")
    parent = _run(db, user, "root")
    tools = _unattended(db, user, parent.trigger_id)
    first = _schedule(tools, "a")
    _schedule(tools, "b")
    tools["cancel_trigger"].invoke({"trigger_id": first["trigger_id"]})

    with pytest.raises(ValueError, match="already scheduled 2 follow-ups"):
        _schedule(tools, "c")


def test_the_owners_unattended_follow_ups_are_capped_per_day(db, monkeypatch):
    """Across chains, an owner's unattended runs share one rolling daily budget; chat does not use it."""
    monkeypatch.setattr(trigger_tools, "MAX_UNATTENDED_FOLLOW_UPS_PER_DAY", 2)
    user = _make_user(db, "daily-cap")
    other = _make_user(db, "daily-cap-other")
    for name in ("a", "b"):
        _schedule(_unattended(db, user, _run(db, user, name).trigger_id), f"from {name}")
    _schedule(_unattended(db, user, None, task_mode="chat"), "from chat")

    with pytest.raises(ValueError, match="in the last day"):
        _schedule(_unattended(db, user, _run(db, user, "c").trigger_id), "from c")
    # Another owner's budget is untouched.
    assert _schedule(_unattended(db, other, _run(db, other, "d").trigger_id), "from d")["chain_depth"] == 1


def test_the_daily_cap_only_counts_the_last_day(db, monkeypatch):
    """A follow-up scheduled more than a day ago no longer counts."""
    monkeypatch.setattr(trigger_tools, "MAX_UNATTENDED_FOLLOW_UPS_PER_DAY", 1)
    user = _make_user(db, "daily-window")
    old = _schedule(_unattended(db, user, _run(db, user, "old").trigger_id), "yesterday")
    row = db.query(models.RuntimeTrigger).filter_by(trigger_id=old["trigger_id"]).one()
    definition = db.get(models.TriggerDefinition, row.definition_id)
    definition.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    db.commit()

    assert _schedule(_unattended(db, user, _run(db, user, "new").trigger_id), "today")["chain_depth"] == 1


def test_an_unattended_follow_up_must_not_be_due_too_soon(db):
    """No spinning at worker-poll speed: the minimum delay applies to unattended runs only."""
    user = _make_user(db, "too-soon")
    parent = _run(db, user, "root")

    with pytest.raises(ValueError, match="at least 15 minutes ahead"):
        _schedule(_unattended(db, user, parent.trigger_id), "now-ish", minutes=5)
    assert db.query(models.RuntimeTrigger).filter_by(parent_trigger_id=parent.trigger_id).count() == 0


@pytest.mark.parametrize("run_trigger_id", [None, "no-such-run"])
def test_an_unattended_step_without_its_own_run_cannot_schedule(db, run_trigger_id):
    """Fail closed when there is no durable parent to chain from."""
    user = _make_user(db, f"no-parent-{run_trigger_id}")

    with pytest.raises(PermissionError):
        _schedule(_unattended(db, user, run_trigger_id), "orphan")


def test_another_users_run_cannot_be_used_as_a_parent(db):
    """The parent lookup is owner-scoped, so a foreign run id reads as missing."""
    owner = _make_user(db, "parent-owner")
    intruder = _make_user(db, "parent-intruder")
    foreign = _run(db, owner, "foreign")

    with pytest.raises(PermissionError, match="could not be found"):
        _schedule(_unattended(db, intruder, foreign.trigger_id), "borrowed lineage")


def test_an_unattended_run_may_cancel_only_janus_follow_ups(db):
    """It may withdraw Janus's own follow-ups but never a run the user or an integration queued."""
    user = _make_user(db, "cancel-scope")
    parent = _run(db, user, "root")
    tools = _unattended(db, user, parent.trigger_id)
    follow_up = _schedule(tools, "maybe later")
    from_api = _run(db, user, "from-api", source="api")

    assert tools["cancel_trigger"].invoke({"trigger_id": follow_up["trigger_id"]})["status"] == "cancelled"
    with pytest.raises(PermissionError, match="only cancel follow-ups Janus scheduled"):
        tools["cancel_trigger"].invoke({"trigger_id": from_api.trigger_id})
    db.expire_all()
    assert db.get(models.RuntimeTrigger, from_api.id).status == models.RuntimeTriggerStatus.QUEUED.value
    # Chat keeps the full owner-scoped cancel.
    chat = _unattended(db, user, None, task_mode="chat")
    assert chat["cancel_trigger"].invoke({"trigger_id": from_api.trigger_id})["status"] == "cancelled"


def test_a_trigger_run_schedules_its_follow_up_as_its_own_child(db, testing_session_local):
    """End to end: the worker threads the run's trigger_id into the toolbox, so lineage is real."""
    user = _make_user(db, "end-to-end")
    root = _run(db, user, "root", source=AGENT_TRIGGER_SOURCE)
    scheduled: list[dict] = []

    def executor(_db, _system_prompt, tools, _objective):
        create = next(tool for tool in tools if tool.name == "create_scheduled_trigger")
        scheduled.append(create.invoke({"due_at": _due_in(hours=3), "objective": "look again", "task_id": "t"}))
        return AgentTaskRun(output="scheduled a follow-up", tool_calls=())

    worker = DurableTriggerWorker(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )
    results = worker.run_once(now=datetime.now(UTC) + timedelta(hours=2))

    assert [result.trigger.trigger_id for result in results] == [root.trigger_id]
    assert len(scheduled) == 1 and not str(scheduled[0]).startswith(("BLOCKED", "ERROR"))
    db.expire_all()
    child = db.query(models.RuntimeTrigger).filter_by(parent_trigger_id=root.trigger_id).one()
    assert child.chain_depth == 1
    assert child.source == AGENT_TRIGGER_SOURCE
