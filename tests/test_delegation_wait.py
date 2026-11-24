"""Tests for JAN-207: a trigger run waits on its delegated children, and interrupted delegations are re-run once.

Covers, end to end through the real trigger handler and ``DurableTriggerWorker``:
parking a run in the plan graph's ``WAIT`` node, releasing it when its children
finish, resuming it deterministically with their results persisted in its plan
state, and durable re-pickup of interrupted delegations with claim-token
handover. The JAN-200 safety fences (clamped allowlist, no pre-confirmed tools,
depth 1, per-owner active cap) are re-checked on the new paths.
"""

from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from janus import (
    builtin_triggers,
    delegation_runtime,
    delegation_service,
    delegation_wait,
    models,
    startup,
    tool_policy,
    trigger_service,
)
from janus.agent_runtime import PlanDrivenRuntime, StepAwait
from janus.agent_task_runner import AgentTaskRun, AgentToolCall
from janus.agent_toolboxes.delegation import build_delegation_tools
from janus.agent_toolboxes.shared import ToolContext
from janus.background_tasks import TaskHandle, TaskStatus, ThreadPoolTaskRunner
from janus.delegation_service import DelegationError, create_delegation
from janus.plan_graph import (
    Plan,
    PlanGraph,
    PlanGraphError,
    PlanGraphNode,
    PlanStatus,
    PlanStep,
    PlanStepStatus,
    TaskContext,
)
from janus.plan_state import PlanStateStore, deserialize_plan_state, serialize_plan_state
from janus.tool_registry import ToolBuildContext
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerWorker, enqueue_trigger
from janus.triggers import TriggerDispatchStatus, TriggerEnvelope, TriggerKind

# --- helpers ------------------------------------------------------------------


class InlineRunner:
    """Runs each submitted job synchronously."""

    def submit(self, name, fn, *, key=None):
        """Run now."""
        fn()
        return TaskHandle(name=name, key=key, status=TaskStatus.SUCCEEDED)

    def status(self, key):
        """Nothing is ever pending."""
        return None


class DeferredRunner(list):
    """Records submissions without running them; ``run_all`` runs them later."""

    def submit(self, name, fn, *, key=None):
        """Record the job, deduplicating by key like the real runner."""
        if key is not None and any(existing == key for existing, _ in self):
            return TaskHandle(name=name, key=key)
        self.append((key, fn))
        return TaskHandle(name=name, key=key)

    def status(self, key):
        """Recorded jobs are pending."""
        return TaskStatus.PENDING if any(existing == key for existing, _ in self) else None

    def run_all(self):
        """Run and forget every recorded job."""
        jobs = list(self)
        self.clear()
        for _, fn in jobs:
            fn()


class ChildExecutor:
    """Stand-in for a delegated child's agent loop."""

    def __init__(self, output="It is sunny in Berlin.", error=None):
        """Configure the child's answer or failure."""
        self.output = output
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, db, system_prompt, tools, objective):
        """Record the tools the child was offered, then answer or fail."""
        self.calls.append({"tool_names": sorted(t.name for t in tools), "objective": objective})
        if self.error is not None:
            raise self.error
        return AgentTaskRun(output=self.output, tool_calls=(AgentToolCall(tool="get_current_time", result="ok"),))


class ParentExecutor:
    """Stand-in for a trigger run's agent loop that delegates and then waits.

    The first call delegates ``children`` tasks and waits for them; every later
    call (the continuation) tries to wait again, which must be refused, and
    answers.
    """

    def __init__(self, children=1, wait=True):
        """Configure how many children to delegate and whether to wait."""
        self.children = children
        self.wait = wait
        self.calls: list[dict] = []
        self.delegated: list[str] = []
        self.wait_answers: list[object] = []

    def __call__(self, db, system_prompt, tools, objective):
        """Delegate and wait on the first call; answer on the continuation."""
        by_name = {tool.name: tool for tool in tools}
        self.calls.append({"objective": objective, "tool_names": sorted(by_name)})
        if len(self.calls) == 1:
            for index in range(self.children):
                created = by_name["delegate_task"].invoke({"objective": f"Look up the weather #{index}"})
                self.delegated.append(created["delegation_id"])
            if self.wait:
                self.wait_answers.append(
                    by_name["wait_for_delegated_tasks"].invoke({"delegation_ids": list(self.delegated)})
                )
            return AgentTaskRun(output="Started the lookups; waiting for them.", tool_calls=())
        self.wait_answers.append(by_name["wait_for_delegated_tasks"].invoke({"delegation_ids": list(self.delegated)}))
        return AgentTaskRun(output="Final answer: it is sunny.", tool_calls=())


def _owner(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _enqueue(db, owner_id: int, *, task_id="weather-task", payload=None) -> models.RuntimeTrigger:
    envelope = TriggerEnvelope(
        trigger_id=f"trigger-{task_id}",
        idempotency_key=f"key-{task_id}",
        task_id=task_id,
        kind=TriggerKind.MANUAL,
        source="test",
        payload=payload or {"objective": "Tell me the weather"},
        owner_id=owner_id,
    )
    record, _ = enqueue_trigger(db, envelope)
    return record


def _trigger_row(db, record_id: int) -> models.RuntimeTrigger:
    db.expire_all()
    return db.get(models.RuntimeTrigger, record_id)


def _delegation(db, delegation_id: str) -> models.RuntimeDelegation:
    db.expire_all()
    return db.query(models.RuntimeDelegation).filter_by(delegation_id=delegation_id).one()


@pytest.fixture
def runner(monkeypatch):
    """Keep delegations queued until the test runs them."""
    deferred = DeferredRunner()
    monkeypatch.setattr(delegation_runtime, "DELEGATION_RUNNER", deferred)
    return deferred


@pytest.fixture
def child(monkeypatch):
    """The delegated children's executor."""
    executor = ChildExecutor()
    monkeypatch.setattr(delegation_runtime, "DELEGATION_TASK_EXECUTOR", executor)
    return executor


def _worker(session_factory, parent: ParentExecutor) -> DurableTriggerWorker:
    return DurableTriggerWorker(session_factory, build_runtime_trigger_handler(session_factory, task_executor=parent))


# --- plan graph -----------------------------------------------------------------


def _running_state(max_steps=100):
    graph = PlanGraph()
    task = TaskContext(task_id="t", objective="Do it", max_steps=max_steps)
    plan = Plan(plan_id="p", task_id="t", objective="Do it", steps=(PlanStep(step_id="t::step-1", objective="Do it"),))
    state = graph.select_step(graph.begin(task, plan))
    return graph, graph.start_step(state)


def test_await_external_parks_the_plan_with_waiting_and_continuation_steps():
    """The waiting step completes; one waiting step per external ID and a dependent continuation follow."""
    graph, state = _running_state()

    state = graph.select_step(graph.await_external(state, "partial", ("d1", "d2"), "Do it"))

    assert state.node == PlanGraphNode.WAIT
    assert state.plan.status == PlanStatus.WAITING
    steps = {step.step_id: step for step in state.plan.steps}
    assert steps["t::step-1"].status == PlanStepStatus.COMPLETED
    assert steps["t::step-1"].result == "partial"
    assert steps["t::step-1::await::d1"].status == PlanStepStatus.WAITING
    continuation = steps["t::step-1::continue"]
    assert continuation.status == PlanStepStatus.PENDING
    assert continuation.depends_on == ("t::step-1", "t::step-1::await::d1", "t::step-1::await::d2")
    # The new step status survives the durable round trip.
    assert deserialize_plan_state(serialize_plan_state(state)) == state


def test_resolving_some_external_steps_keeps_waiting_and_all_of_them_resumes():
    """Resume is deterministic: the continuation only becomes runnable once every await step resolved."""
    graph, state = _running_state()
    state = graph.select_step(graph.await_external(state, "partial", ("d1", "d2"), "Do it"))

    state = graph.resolve_external(state, "t::step-1::await::d1", "one")
    assert graph.select_step(graph.resume(state)).node == PlanGraphNode.WAIT

    state = graph.resolve_external(state, "t::step-1::await::d2", "two")
    state = graph.revise_pending_step(state, "t::step-1::continue", "Do it with one and two")
    resumed = graph.select_step(graph.resume(state))
    assert resumed.node == PlanGraphNode.EXECUTE_STEP
    assert resumed.current_step_id == "t::step-1::continue"


def test_graph_transitions_reject_invalid_waits():
    """Blank, duplicate, or oversized waits and wrong-state resolutions are refused."""
    graph, state = _running_state()
    with pytest.raises(PlanGraphError, match="at least one"):
        graph.await_external(state, "r", (), "Do it")
    with pytest.raises(PlanGraphError, match="unique"):
        graph.await_external(state, "r", ("d1", "d1"), "Do it")
    with pytest.raises(PlanGraphError, match="non-blank"):
        graph.await_external(state, "r", (" ",), "Do it")
    _, small = _running_state(max_steps=2)
    with pytest.raises(PlanGraphError, match="step limit"):
        graph.await_external(small, "r", ("d1",), "Do it")

    parked = graph.select_step(graph.await_external(state, "r", ("d1",), "Do it"))
    with pytest.raises(PlanGraphError, match="not waiting"):
        graph.resolve_external(parked, "t::step-1", "x")
    with pytest.raises(PlanGraphError, match="not pending"):
        graph.revise_pending_step(parked, "t::step-1::await::d1", "x")
    with pytest.raises(PlanGraphError, match="expected graph node wait"):
        graph.resolve_external(state, "t::step-1::await::d1", "x")


def test_runtime_turns_a_step_await_into_wait_and_fails_a_wait_beyond_the_step_limit():
    """``PlanDrivenRuntime`` parks on ``StepAwait``; a wait that cannot fit fails the step instead."""
    runtime = PlanDrivenRuntime(
        retrieve_plan=lambda _task: None,
        generate_plan=lambda task: Plan(
            plan_id="p",
            task_id=task.task_id,
            objective=task.objective,
            steps=(PlanStep(step_id="s1", objective=task.objective),),
        ),
        execute_step=lambda _task, _step: StepAwait(result="r", external_ids=("d1",), continuation_objective="Go"),
    )

    assert runtime.run(TaskContext(task_id="t", objective="Go")).is_waiting
    failed = runtime.run(TaskContext(task_id="t2", objective="Go", max_steps=1))
    assert failed.state.node == PlanGraphNode.FAILED
    assert "step limit" in failed.state.reason
    assert not failed.is_waiting


# --- trigger boundary -------------------------------------------------------------


def test_park_and_release_are_token_and_state_guarded(db):
    """Only the claim holder parks; only a waiting row is released; waiting is not claimable."""
    user = _owner(db, "park-guard")
    record = _enqueue(db, user.id)
    token = trigger_service.claim_trigger(db, record.id)

    assert trigger_service.park_trigger(db, record.id, "not-the-token") is False
    assert trigger_service.park_trigger(db, record.id, token) is True
    row = _trigger_row(db, record.id)
    assert (row.status, row.claim_token) == ("waiting", None)
    assert trigger_service.claim_trigger(db, record.id) is None, "a waiting run is not claimable"
    assert trigger_service.complete_trigger(db, record.id, token) is False, "the old token cannot finish it"
    assert trigger_service.count_active_triggers(db, user.id) == 1, "waiting counts toward the backlog cap"

    assert trigger_service.release_waiting_trigger(db, record.id) is True
    assert trigger_service.release_waiting_trigger(db, record.id) is False, "releasing twice is a no-op"
    assert _trigger_row(db, record.id).status == "queued"


def test_a_run_cancelled_while_waiting_is_never_resumed(db, testing_session_local, runner, child):
    """Cancelling a waiting run is final; its child still finishes, but nothing resumes the parent."""
    user = _owner(db, "cancel-waiting")
    record = _enqueue(db, user.id)
    parent = ParentExecutor()
    _worker(testing_session_local, parent).run_once()
    assert _trigger_row(db, record.id).status == "waiting"

    assert trigger_service.cancel_owned_trigger(db, user.id, record.trigger_id) is True
    runner.run_all()  # the child completes and tries to release its parent

    assert _delegation(db, parent.delegated[0]).status == "completed"
    assert _trigger_row(db, record.id).status == "cancelled"
    assert delegation_wait.release_waiting_parents(testing_session_local) == 0
    assert len(parent.calls) == 1


# --- end to end ------------------------------------------------------------------


def test_a_trigger_run_waits_for_its_child_and_resumes_with_the_result(db, testing_session_local, runner, child):
    """The JAN-207 headline: park in WAIT, release on child completion, resume with the persisted result."""
    user = _owner(db, "wait-e2e")
    record = _enqueue(db, user.id)
    parent = ParentExecutor()
    worker = _worker(testing_session_local, parent)

    [first] = worker.run_once()
    assert first.status == TriggerDispatchStatus.WAITING
    row = _trigger_row(db, record.id)
    assert row.status == "waiting"
    assert row.output == "Started the lookups; waiting for them."
    assert parent.wait_answers[0]["waiting_for"] == parent.delegated
    stored = PlanStateStore(testing_session_local).load(db, user.id, "weather-task")
    assert stored.state.node == PlanGraphNode.WAIT
    [delegation_id] = parent.delegated
    await_step_id = f"weather-task::step-1::await::{delegation_id}"
    assert {step.step_id: step.status for step in stored.state.plan.steps}[await_step_id] == PlanStepStatus.WAITING

    # Nothing to do while the child is still queued: the run stays parked.
    assert worker.run_once() == []
    assert delegation_wait.release_waiting_parents(testing_session_local) == 0
    assert _trigger_row(db, record.id).status == "waiting"

    runner.run_all()  # the child runs and, on completion, releases its parent
    assert _delegation(db, delegation_id).status == "completed"
    assert _trigger_row(db, record.id).status == "queued"

    [second] = worker.run_once()
    assert second.status == TriggerDispatchStatus.COMPLETED
    row = _trigger_row(db, record.id)
    assert row.status == "completed"
    assert row.output == "Final answer: it is sunny."
    assert row.attempt_count == 2, "the resume is an ordinary claim"

    continuation_objective = parent.calls[1]["objective"]
    assert continuation_objective.startswith("Tell me the weather")
    assert "It is sunny in Berlin." in continuation_objective
    assert "Started the lookups; waiting for them." in continuation_objective
    # The continuation cannot wait a second time.
    assert "only available in the first step" in str(parent.wait_answers[1])

    final = PlanStateStore(testing_session_local).load(db, user.id, "weather-task").state
    assert final.node == PlanGraphNode.COMPLETE
    steps = {step.step_id: step for step in final.plan.steps}
    assert steps[await_step_id].status == PlanStepStatus.COMPLETED
    assert "It is sunny in Berlin." in steps[await_step_id].result
    assert "Final answer: it is sunny." in steps["weather-task::step-1::continue"].result
    assert len(child.calls) == 1


def test_a_failed_child_is_reported_to_the_continuation_not_turned_into_a_parent_failure(
    db, testing_session_local, runner, child
):
    """A child that fails releases the parent too; the continuation is told why."""
    user = _owner(db, "wait-child-fails")
    record = _enqueue(db, user.id)
    child.error = RuntimeError("weather service is down")
    parent = ParentExecutor()
    worker = _worker(testing_session_local, parent)

    worker.run_once()
    runner.run_all()
    worker.run_once()

    assert _trigger_row(db, record.id).status == "completed"
    assert "failed: weather service is down" in parent.calls[1]["objective"]


def test_a_run_waits_for_all_of_its_children(db, testing_session_local, runner, child):
    """With two children the parent resumes only after both finished."""
    user = _owner(db, "wait-two")
    record = _enqueue(db, user.id)
    parent = ParentExecutor(children=2)
    worker = _worker(testing_session_local, parent)
    worker.run_once()

    first_key, first_job = runner.pop(0)
    first_job()
    assert _trigger_row(db, record.id).status == "waiting"
    runner.run_all()
    assert _trigger_row(db, record.id).status == "queued"
    worker.run_once()

    objective = parent.calls[1]["objective"]
    assert objective.count("It is sunny in Berlin.") == 2
    assert first_key.startswith("delegation:")


def test_a_child_that_finished_before_its_parent_parked_is_picked_up_by_the_worker_sweep(
    db, testing_session_local, monkeypatch, child
):
    """The release after a child finishes finds no parked parent yet; the next poll's sweep does."""
    monkeypatch.setattr(delegation_runtime, "DELEGATION_RUNNER", InlineRunner())
    user = _owner(db, "wait-race")
    record = _enqueue(db, user.id)
    parent = ParentExecutor()
    worker = _worker(testing_session_local, parent)

    worker.run_once()  # the child completes inside delegate_task, before the parent parks
    assert _delegation(db, parent.delegated[0]).status == "completed"
    assert _trigger_row(db, record.id).status == "waiting"

    delegation_runtime.run_delegation_maintenance(testing_session_local)
    assert _trigger_row(db, record.id).status == "queued"
    worker.run_once()
    assert _trigger_row(db, record.id).status == "completed"


def test_a_run_without_room_for_the_wait_fails_instead_of_parking(db, testing_session_local, runner, child):
    """``max_steps=1`` cannot hold the await and continuation steps: the run fails, it never parks."""
    user = _owner(db, "wait-step-limit")
    record = _enqueue(db, user.id, payload={"objective": "Tell me the weather", "max_steps": 1})
    _worker(testing_session_local, ParentExecutor()).run_once()

    row = _trigger_row(db, record.id)
    assert row.status in {"failed", "exhausted"}
    assert "step limit" in row.last_error


def test_a_built_in_run_cannot_wait(db, testing_session_local, runner, child, monkeypatch):
    """Built-in runs deliver their answer in ``finalize``, so the wait tool refuses there."""

    class FakeBuiltin:
        key = "fake"
        prepare = None
        finalize = None

    monkeypatch.setattr(builtin_triggers, "builtin_for", lambda definition: FakeBuiltin())
    user = _owner(db, "wait-builtin")
    record = _enqueue(db, user.id)
    parent = ParentExecutor()
    _worker(testing_session_local, parent).run_once()

    assert _trigger_row(db, record.id).status == "completed"
    assert "only available in the first step" in str(parent.wait_answers[0])


# --- the wait tool ---------------------------------------------------------------


def _tool_context(db, user, *, trigger_id, task_id="weather-task", step_id="weather-task::step-1"):
    return ToolBuildContext(
        tool_context=ToolContext(db=db, user=user),
        task_id=task_id,
        step_id=step_id,
        task_mode="trigger",
        trigger_id=trigger_id,
        authorized_tool_ids=frozenset({"core"}),
    )


def test_chat_and_unowned_steps_are_not_offered_the_wait_tool(db):
    """Only a trigger-driven step (it has a ``trigger_id``) gets ``wait_for_delegated_tasks``."""
    user = _owner(db, "wait-tool-chat")
    chat_tools = {tool.name for tool in build_delegation_tools(_tool_context(db, user, trigger_id=None))}
    trigger_tools = {tool.name for tool in build_delegation_tools(_tool_context(db, user, trigger_id="trigger-x"))}

    assert "wait_for_delegated_tasks" not in chat_tools
    assert "wait_for_delegated_tasks" in trigger_tools


def test_the_wait_tool_accepts_only_this_steps_own_children(db, runner):
    """Another step's, another task's, or another owner's delegation reads as not found."""
    user = _owner(db, "wait-tool-owner")
    other = _owner(db, "wait-tool-other")
    tools = {tool.name: tool for tool in build_delegation_tools(_tool_context(db, user, trigger_id="trigger-x"))}
    mine = tools["delegate_task"].invoke({"objective": "mine"})
    other_step = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="weather-task",
        parent_step_id="weather-task::step-9",
        objective="other step",
        requested_tool_ids=(),
        allowed_tool_ids={"core"},
    )
    foreign = create_delegation(
        db,
        owner_id=other.id,
        parent_task_id="weather-task",
        parent_step_id="weather-task::step-1",
        objective="other owner",
        requested_tool_ids=(),
        allowed_tool_ids={"core"},
    )

    with delegation_wait.accepting_waits("weather-task", "weather-task::step-1") as sink:
        for delegation_id in (other_step.delegation_id, foreign.delegation_id, "unknown"):
            with pytest.raises(ValueError, match="no task delegated by this run"):
                tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [delegation_id]})
        with pytest.raises(ValueError, match="at least one"):
            tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [" "]})
        answer = tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [mine["delegation_id"]] * 2})

    assert answer["waiting_for"] == [mine["delegation_id"]]
    assert sink.delegation_ids == [mine["delegation_id"]]


def test_the_wait_tool_refuses_without_a_sink_and_beyond_the_cap(db, runner, monkeypatch):
    """No sink (chat-like context, built-in, continuation) or too many children: refused."""
    user = _owner(db, "wait-tool-cap")
    tools = {tool.name: tool for tool in build_delegation_tools(_tool_context(db, user, trigger_id="trigger-x"))}
    first = tools["delegate_task"].invoke({"objective": "one"})["delegation_id"]
    second = tools["delegate_task"].invoke({"objective": "two"})["delegation_id"]

    with pytest.raises(ValueError, match="only available in the first step"):
        tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [first]})

    monkeypatch.setattr(delegation_wait, "MAX_AWAITED_DELEGATIONS", 1)
    with delegation_wait.accepting_waits("weather-task", "weather-task::step-1"):
        tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [first]})
        with pytest.raises(ValueError, match="at most 1"):
            tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [second]})
    # A sink for a different step does not accept this step's waits either.
    with (
        delegation_wait.accepting_waits("weather-task", "weather-task::step-2"),
        pytest.raises(ValueError, match="only available in the first step"),
    ):
        tools["wait_for_delegated_tasks"].invoke({"delegation_ids": [first]})


# --- durable re-pickup ----------------------------------------------------------


def _stale_running_delegation(db, user, *, allowed=frozenset({"core"}), requested=()):
    """A delegation that was claimed and then orphaned by a 'restart'."""
    record = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="chat-conversation-1",
        objective="Summarize my history",
        requested_tool_ids=requested,
        allowed_tool_ids=allowed,
    )
    token = delegation_service.claim_delegation(db, record.id)
    assert token is not None
    return record, token


def test_an_interrupted_delegation_is_re_dispatched_by_the_worker_sweep(db, testing_session_local, runner, child):
    """After a restart the sweep requeues the orphan once and re-submits it; it then completes."""
    user = _owner(db, "repickup")
    record, old_token = _stale_running_delegation(db, user)
    after_lease = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)

    submitted = delegation_runtime.run_delegation_maintenance(testing_session_local, now=after_lease)

    assert submitted == 1
    assert [key for key, _ in runner] == [f"delegation:{record.id}"]
    row = _delegation(db, record.delegation_id)
    assert (row.status, row.requeue_count, row.claim_token) == ("queued", 1, None)
    # Claim-token handover: the executor that lost the row can no longer finish it.
    assert delegation_service.complete_delegation(db, record.id, old_token, "stale result") is False

    # A second sweep before the job runs does not submit it twice.
    delegation_runtime.run_delegation_maintenance(testing_session_local, now=after_lease)
    assert len(runner) == 1

    runner.run_all()
    row = _delegation(db, record.delegation_id)
    assert row.status == "completed"
    assert "It is sunny in Berlin." in row.result
    assert len(child.calls) == 1


def test_a_lost_run_finishing_late_cannot_overwrite_the_requeued_row(db, testing_session_local, runner, child):
    """The old executor finishes after the requeue: its result is dropped, the re-run's counts."""
    user = _owner(db, "repickup-late")
    record, old_token = _stale_running_delegation(db, user)
    after_lease = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)
    delegation_service.expire_stale_delegations(db, None, now=after_lease)

    new_token = delegation_service.claim_delegation(db, record.id, now=after_lease)
    assert new_token is not None and new_token != old_token
    assert delegation_service.fail_delegation(db, record.id, old_token, "late failure") is False
    assert delegation_service.complete_delegation(db, record.id, new_token, "fresh result") is True
    assert _delegation(db, record.delegation_id).result == "fresh result"


def test_the_re_run_keeps_the_jan_200_fences(db, testing_session_local, runner, child):
    """Same clamped allowlist, delegated mode with nothing pre-confirmed, depth 1, no new row."""
    user = _owner(db, "repickup-fences")
    record, _ = _stale_running_delegation(
        db, user, allowed=frozenset({"core", "trigger-history"}), requested=("trigger-history",)
    )
    tool_policy.set_policy(db, user.id, "trigger-history", False)  # switched off after it was queued
    after_lease = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)

    delegation_runtime.run_delegation_maintenance(testing_session_local, now=after_lease)
    runner.run_all()

    row = _delegation(db, record.delegation_id)
    assert row.status == "failed"
    assert "trigger-history:confirmation_required" in row.error, "no pre-confirmed tools on a re-run"
    assert child.calls == []
    assert db.query(models.RuntimeDelegation).count() == 1, "a re-run never creates a row"

    # A re-run that does execute sees only the stored, read-only grant; never delegation tools.
    tool_policy.set_policy(db, user.id, "trigger-history", True)
    swept, _ = _stale_running_delegation(db, user, allowed=frozenset({"core", "trigger-history"}))
    delegation_runtime.run_delegation_maintenance(testing_session_local, now=after_lease)
    runner.run_all()
    assert _delegation(db, swept.delegation_id).status == "completed"
    names = child.calls[0]["tool_names"]
    assert "delegate_task" not in names and "wait_for_delegated_tasks" not in names
    assert _delegation(db, swept.delegation_id).depth == 1


def test_requeued_delegations_still_hold_the_active_cap(db, runner, monkeypatch):
    """A requeue keeps the row active, so the per-owner cap cannot be bypassed by orphaning rows."""
    monkeypatch.setattr(delegation_service, "MAX_ACTIVE_DELEGATIONS_PER_OWNER", 1)
    user = _owner(db, "repickup-cap")
    record, _ = _stale_running_delegation(db, user)
    after_lease = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)
    delegation_service.expire_stale_delegations(db, None, now=after_lease)

    assert _delegation(db, record.delegation_id).status == "queued"
    with pytest.raises(DelegationError, match="limit 1"):
        create_delegation(
            db,
            owner_id=user.id,
            parent_task_id="chat-conversation-1",
            objective="one more",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
            now=after_lease,
        )


def test_the_depth_fence_still_refuses_a_grandchild(db):
    """Depth 1 is unchanged by JAN-207."""
    user = _owner(db, "repickup-depth")
    with pytest.raises(DelegationError, match="depth"):
        create_delegation(
            db,
            owner_id=user.id,
            parent_task_id="delegation-x",
            objective="deeper",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
            depth=2,
        )


def test_a_parent_waiting_on_an_orphaned_child_resumes_after_its_second_interruption(
    db, testing_session_local, runner, child
):
    """A child that never finishes cannot park its parent forever: it fails after one re-run."""
    user = _owner(db, "wait-orphan")
    record = _enqueue(db, user.id)
    parent = ParentExecutor()
    worker = _worker(testing_session_local, parent)
    worker.run_once()
    runner.clear()  # the process "dies" before the child runs
    [delegation_id] = parent.delegated

    first_lease = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)
    delegation_runtime.run_delegation_maintenance(testing_session_local, now=first_lease)
    assert _delegation(db, delegation_id).status == "queued"
    assert _trigger_row(db, record.id).status == "waiting"
    runner.clear()  # ...and dies again

    second_lease = first_lease + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 1)
    delegation_runtime.run_delegation_maintenance(testing_session_local, now=second_lease)
    assert _delegation(db, delegation_id).status == "failed"
    assert _trigger_row(db, record.id).status == "queued"

    worker.run_once()
    assert _trigger_row(db, record.id).status == "completed"
    assert "interrupted again" in parent.calls[1]["objective"]


def test_the_scheduler_tick_runs_delegation_maintenance_without_letting_it_block_schedules(monkeypatch):
    """A failing maintenance pass is logged; scheduling already happened and the tick does not raise."""
    calls: list[str] = []
    monkeypatch.setattr(builtin_triggers, "ensure_builtin_definitions_for_all_users", lambda db: calls.append("seed"))
    from janus import trigger_definition_service

    monkeypatch.setattr(trigger_definition_service, "run_due_schedules", lambda db, now=None: calls.append("due"))

    def boom(session_factory, now=None):
        calls.append("maintenance")
        raise RuntimeError("database is locked")

    monkeypatch.setattr(delegation_runtime, "run_delegation_maintenance", boom)

    startup._run_trigger_scheduler_tick()

    assert calls == ["seed", "due", "maintenance"]


# --- background task runner (JAN-138) -----------------------------------------------


def test_thread_pool_runner_reports_status_and_deduplicates_by_key():
    """A keyed job is not submitted twice while pending or running; status follows the job."""
    runner = ThreadPoolTaskRunner(1, thread_name_prefix="test-runner")
    started, release = Event(), Event()
    runs: list[str] = []

    def blocking():
        started.set()
        release.wait(5)
        runs.append("first")

    first = runner.submit("job", blocking, key="k")
    assert started.wait(5)
    assert runner.status("k") == TaskStatus.RUNNING
    assert runner.submit("job", lambda: runs.append("dup"), key="k") is first
    release.set()
    for _ in range(100):
        if runner.status("k") == TaskStatus.SUCCEEDED:
            break
        Event().wait(0.02)
    assert runner.status("k") == TaskStatus.SUCCEEDED
    assert runs == ["first"]
    assert runner.status("unknown") is None

    # Once finished, the same key can be submitted again.
    done = Event()
    runner.submit("job", done.set, key="k")
    assert done.wait(5)


def test_thread_pool_runner_records_a_failing_job_without_raising():
    """A job's exception is recorded on its handle; the pool keeps working."""
    runner = ThreadPoolTaskRunner(1, thread_name_prefix="test-runner-fail")

    def explode():
        raise RuntimeError("boom")

    handle = runner.submit("explode", explode, key="bad")
    for _ in range(100):
        if handle.status == TaskStatus.FAILED:
            break
        Event().wait(0.02)
    assert (handle.status, handle.error) == (TaskStatus.FAILED, "boom")
    done = Event()
    runner.submit("after", done.set)
    assert done.wait(5)


def test_a_new_runner_after_a_restart_knows_nothing_so_rows_drive_the_re_pickup():
    """The runner is not durable: a fresh instance has no keys. Re-pickup comes from the database sweep."""
    assert ThreadPoolTaskRunner(1, thread_name_prefix="test-runner-fresh").status("delegation:1") is None
    with pytest.raises(ValueError, match="at least 1"):
        ThreadPoolTaskRunner(0, thread_name_prefix="never")
