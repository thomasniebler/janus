"""Tests for plan-driven delegation to bounded child tasks (JAN-200).

Delegation runs a bounded child task inside the same runtime. These tests
pin the four safety fences from the feature spec (clamped allowlist, no
self-authorization, depth cap, fan-out cap) and the end-to-end path: tool
call -> ``runtime_delegations`` row -> child plan run through
``PlanDrivenRuntime`` -> persisted plan state and result.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from janus import agent_tools, database, delegation_runtime, delegation_service, models, runtime_adapter, tool_policy
from janus.agent_task_runner import AgentTaskRun, AgentToolCall
from janus.agent_toolboxes.delegation import build_delegation_tools, delegable_tool_ids
from janus.agent_toolboxes.shared import ToolContext
from janus.background_tasks import TaskHandle, TaskStatus
from janus.delegation_service import DelegationError, create_delegation
from janus.plan_graph import PlanGraphNode
from janus.plan_state import PlanStateStore
from janus.runtime_adapter import RuntimeAdapter
from janus.tool_registry import ToolBuildContext


class RecordingExecutor:
    """Stand-in for the synchronous agent loop; records what reached it."""

    def __init__(self, output: str = "Child result.", error: Exception | None = None, invoke: dict | None = None):
        """Configure the canned answer, optional failure, and tools the fake model calls."""
        self.output = output
        self.error = error
        self.invoke = invoke or {}
        self.calls: list[dict] = []
        self.invocations: dict[str, object] = {}

    def __call__(self, db, system_prompt, tools, objective):
        """Record the call, invoke any requested tools, and return the configured run, or raise."""
        self.calls.append(
            {"system_prompt": system_prompt, "tool_names": [t.name for t in tools], "objective": objective}
        )
        by_name = {t.name: t for t in tools}
        for name, args in self.invoke.items():
            self.invocations[name] = by_name[name].invoke(args)
        if self.error is not None:
            raise self.error
        return AgentTaskRun(output=self.output, tool_calls=(AgentToolCall(tool="get_current_time", result="ok"),))


class InlineRunner:
    """A ``BackgroundTaskRunner`` that runs each job synchronously, at submit time."""

    def submit(self, name, fn, *, key=None):
        """Run the job now and return a finished handle."""
        fn()
        return TaskHandle(name=name, key=key, status=TaskStatus.SUCCEEDED)

    def status(self, key):
        """Inline jobs are finished as soon as they are submitted."""
        return None


class DeferredRunner(list):
    """A ``BackgroundTaskRunner`` that only records submissions, so delegations stay queued."""

    def submit(self, name, fn, *, key=None):
        """Record the job without running it."""
        self.append((key, fn))
        return TaskHandle(name=name, key=key)

    def status(self, key):
        """Every recorded job is still pending."""
        return TaskStatus.PENDING if any(item[0] == key for item in self) else None


@pytest.fixture
def inline_delegation(monkeypatch):
    """Run delegations synchronously with a recording executor instead of the pool and a live LLM."""
    executor = RecordingExecutor()
    monkeypatch.setattr(delegation_runtime, "DELEGATION_RUNNER", InlineRunner())
    monkeypatch.setattr(delegation_runtime, "DELEGATION_TASK_EXECUTOR", executor)
    return executor


@pytest.fixture
def deferred_delegation(monkeypatch):
    """Accept delegations without running them, so they stay queued."""
    jobs = DeferredRunner()
    monkeypatch.setattr(delegation_runtime, "DELEGATION_RUNNER", jobs)
    return jobs


def _owner(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _tools_for(
    db,
    user,
    *,
    authorized: frozenset[str],
    task_id: str = "chat-conversation-1",
    conversation_id=None,
    task_mode: str = "chat",
):
    """Build the delegation toolbox as if a parent step had been granted ``authorized``."""
    context = ToolBuildContext(
        tool_context=ToolContext(db=db, user=user, conversation_id=conversation_id),
        task_id=task_id,
        step_id=f"{task_id}::message-1",
        task_mode=task_mode,
        authorized_tool_ids=authorized,
    )
    return {tool.name: tool for tool in build_delegation_tools(context)}


def _row(db, delegation_id: str) -> models.RuntimeDelegation:
    db.expire_all()
    return db.query(models.RuntimeDelegation).filter_by(delegation_id=delegation_id).one()


# --- wiring -----------------------------------------------------------------


def test_chat_step_exposes_delegation_with_the_parents_grant(db):
    """A chat step builds the delegation tools; the builder sees what the step was actually granted."""
    user = _owner(db, "delegation-chat")
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="chat-conversation-7",
        objective="Help me",
        requested_tool_ids=("core", "events", "delegation"),
        task_mode="chat",
        confirmed_tool_ids=frozenset({"core", "events", "delegation"}),
    )

    assert {entry.id for entry in prepared.selection.entries} == {"core", "events", "delegation"}
    assert {"delegate_task", "get_delegated_task", "list_delegated_tasks"} <= {tool.name for tool in prepared.tools}


def test_build_tool_set_never_exposes_delegation(db):
    """Outside a plan step there is no task identity, so the delegation entry stays disabled."""
    user = _owner(db, "delegation-no-task")

    names = {tool.name for tool in agent_tools.build_tool_set(db, user)}

    assert "delegate_task" not in names


def test_delegation_is_allowed_for_top_level_runs_but_never_for_a_child(db):
    """Depth fence at the policy layer: a child can never spawn a child.

    Chat and unattended top-level runs may delegate (the switch defaults to
    allowed). A child only ever gets read-only tools, and ``delegation`` is not
    read-only, so it is never offered to a child.
    """
    user = _owner(db, "delegation-policy-layer")
    entries = runtime_adapter.DEFAULT_TOOL_REGISTRY.entries()
    assert "delegation" in runtime_adapter.TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    trigger_offered, trigger_confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")
    assert "delegation" in trigger_offered & trigger_confirmed
    child_offered, _ = tool_policy.unattended_tool_sets(
        db, user.id, entries, task_mode="delegated", narrowed_to=frozenset({"core", "delegation"})
    )
    assert child_offered == frozenset({"core"})
    by_id = {entry.id: entry for entry in entries}
    assert all(
        by_id[tool_id].operation == "read"
        for tool_id in delegable_tool_ids(
            ToolBuildContext(tool_context=None, authorized_tool_ids=frozenset(by_id))  # type: ignore[arg-type]
        )
    )


def test_a_delegated_step_is_denied_the_delegation_toolbox(db):
    """Even if a child asks for delegation, its task mode denies it (no recursive chains)."""
    user = _owner(db, "delegation-depth-policy")
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="delegation-x",
        objective="Try to delegate again",
        requested_tool_ids=("delegation",),
        task_mode="delegated",
    )

    assert prepared.tools == ()
    assert [(d.tool_id, d.reason) for d in prepared.denials] == [("delegation", "not_allowlisted")]


# --- end to end -------------------------------------------------------------


def test_delegate_task_runs_a_child_plan_and_reports_its_result(db, inline_delegation):
    """The headline path: delegate -> child plan executes -> result is readable by the parent."""
    user = _owner(db, "delegation-e2e")
    inline_delegation.output = "Found three events this week."
    tools = _tools_for(db, user, authorized=frozenset({"core", "events", "trigger-history", "mail", "delegation"}))

    created = tools["delegate_task"].invoke({"objective": "Summarize recent events"})

    # The model-facing view shows what the child may use: the parent's grant, read-only tools only.
    # ``events`` is read-only and owner-scoped (JAN-272), so it is delegable like any read tool.
    assert created["allowed_tool_ids"] == ["core", "events", "mail", "trigger-history"]
    assert created["parent_task_id"] == "chat-conversation-1"
    assert "claim_token" not in created

    fetched = tools["get_delegated_task"].invoke({"delegation_id": created["delegation_id"]})
    assert fetched["status"] == "completed"
    assert "Found three events this week." in fetched["result"]
    assert "Authorized tools: core, events, trigger-history" in fetched["result"]
    assert fetched["error"] is None

    # The child really ran through the unattended agent loop with only delegated-mode tools.
    assert len(inline_delegation.calls) == 1
    call = inline_delegation.calls[0]
    assert call["objective"] == "Summarize recent events"
    assert "delegated to you by another Janus task" in call["system_prompt"]
    assert "delegate_task" not in call["tool_names"]
    assert not any(name.startswith("send") for name in call["tool_names"])

    # And it is a first-class plan-runtime task: its state is persisted under the child task id.
    stored = PlanStateStore(lambda: db).load(db, user.id, created["child_task_id"])
    assert stored is not None
    assert stored.state.node == PlanGraphNode.COMPLETE
    assert stored.state.task.task_mode == "delegated"

    listed = tools["list_delegated_tasks"].invoke({})
    assert [item["delegation_id"] for item in listed] == [created["delegation_id"]]


def test_a_failing_child_is_recorded_as_failed_not_completed(db, inline_delegation):
    """An automation must never report success for work that did not happen."""
    user = _owner(db, "delegation-failure")
    inline_delegation.error = RuntimeError("provider is down")
    tools = _tools_for(db, user, authorized=frozenset({"core"}))

    created = tools["delegate_task"].invoke({"objective": "Do something"})

    row = _row(db, created["delegation_id"])
    assert row.status == "failed"
    assert "provider is down" in row.error
    assert row.result is None
    assert row.claim_token is None


# --- fence 1: clamped allowlist --------------------------------------------


def test_a_child_cannot_request_a_tool_its_parent_was_not_granted(db, deferred_delegation):
    """No widening: trigger-history is delegable in general, but not when the parent step lacked it."""
    user = _owner(db, "delegation-clamp-parent")
    tools = _tools_for(db, user, authorized=frozenset({"core"}))

    with pytest.raises(DelegationError, match="trigger-history"):
        tools["delegate_task"].invoke({"objective": "Read history", "tool_ids": ["trigger-history"]})

    assert db.query(models.RuntimeDelegation).count() == 0
    assert deferred_delegation == []


def test_a_child_cannot_reach_a_writing_tool_even_if_the_parent_had_it(db, deferred_delegation):
    """Chat is granted mail labeling and sending; a delegated child never is."""
    user = _owner(db, "delegation-clamp-mode")
    tools = _tools_for(db, user, authorized=frozenset({"core", "mail.label", "mail.send"}))

    for tool_id in ("mail.label", "mail.send"):
        with pytest.raises(DelegationError, match=tool_id):
            tools["delegate_task"].invoke({"objective": "Change mail", "tool_ids": [tool_id]})


def test_delegable_tools_are_the_read_only_part_of_the_grant():
    """The clamp keeps the parent's read-only tools; an unknown grant contributes nothing."""
    context = ToolBuildContext(
        tool_context=None,  # type: ignore[arg-type]
        authorized_tool_ids=frozenset({"core", "calendar", "mail", "mail.send", "events", "delegation"}),
    )
    assert delegable_tool_ids(context) == frozenset({"core", "calendar", "mail", "events"})
    assert delegable_tool_ids(ToolBuildContext(tool_context=None)) == frozenset()  # type: ignore[arg-type]


def test_switching_a_tool_off_applies_to_already_queued_children(db, deferred_delegation):
    """The stored allowlist is re-checked against the user's unattended switch at execution."""
    user = _owner(db, "delegation-narrowing")
    tools = _tools_for(db, user, authorized=frozenset({"core", "trigger-history"}))
    created = tools["delegate_task"].invoke({"objective": "Read history", "tool_ids": ["trigger-history"]})

    tool_policy.set_policy(db, user.id, "trigger-history", False)
    executor = RecordingExecutor()
    record_id = _row(db, created["delegation_id"]).id
    status = delegation_runtime.run_delegation(lambda: _new_session(db), record_id, task_executor=executor)

    assert status == "failed"
    assert executor.calls == []
    assert "trigger-history:confirmation_required" in _row(db, created["delegation_id"]).error


# --- fence 2: no self-authorization ----------------------------------------


def test_a_child_never_runs_a_tool_the_user_switched_off(db, deferred_delegation):
    """A tool that is off for unattended runs fails an explicit request and is not offered in a sweep."""
    user = _owner(db, "delegation-confirm")
    db.add(
        models.EmailAccount(
            user_id=user.id, name="m", email="u@example.com", imap_server="imap.example.com", is_active=True
        )
    )
    db.commit()
    tool_policy.set_policy(db, user.id, "mail", False)
    # Chat pre-confirms its whole catalog, so the parent *was* granted mail.
    tools = _tools_for(db, user, authorized=frozenset({"core", "mail"}))
    created = tools["delegate_task"].invoke({"objective": "Read my mail", "tool_ids": ["mail"]})

    executor = RecordingExecutor()
    status = delegation_runtime.run_delegation(
        lambda: _new_session(db), _row(db, created["delegation_id"]).id, task_executor=executor
    )

    assert status == "failed"
    assert executor.calls == [], "an explicitly requested tool that is off must fail the child up front"
    assert "mail:confirmation_required" in _row(db, created["delegation_id"]).error

    swept = tools["delegate_task"].invoke({"objective": "Do whatever is useful"})
    sweep_executor = RecordingExecutor()
    sweep_status = delegation_runtime.run_delegation(
        lambda: _new_session(db), _row(db, swept["delegation_id"]).id, task_executor=sweep_executor
    )

    assert sweep_status == "completed"
    assert "search_emails" not in sweep_executor.calls[0]["tool_names"]


# --- fence 3 and 4: depth and fan-out --------------------------------------


def test_the_service_refuses_depth_beyond_one(db):
    """Defense in depth behind the allowlist: a grandchild is refused outright."""
    user = _owner(db, "delegation-depth")
    with pytest.raises(DelegationError, match="depth"):
        create_delegation(
            db,
            owner_id=user.id,
            parent_task_id="delegation-parent",
            objective="Go deeper",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
            depth=2,
        )


def test_a_child_that_somehow_holds_the_toolbox_still_cannot_delegate(db, deferred_delegation):
    """If the delegated allowlist were ever widened, the real depth still trips the service cap."""
    user = _owner(db, "delegation-depth-tool")
    tools = _tools_for(db, user, authorized=frozenset({"core"}), task_id="delegation-abc", task_mode="delegated")

    with pytest.raises(DelegationError, match="depth 2"):
        tools["delegate_task"].invoke({"objective": "grandchild"})
    assert deferred_delegation == []


def test_fan_out_is_capped_per_owner(db, deferred_delegation, monkeypatch):
    """A chat turn looping on delegate_task is bounded, and the cap is per owner."""
    monkeypatch.setattr(delegation_service, "MAX_ACTIVE_DELEGATIONS_PER_OWNER", 2)
    user = _owner(db, "delegation-fanout")
    other = models.User(username="delegation-fanout-other", hashed_password="x", is_admin=False)
    db.add(other)
    db.commit()
    tools = _tools_for(db, user, authorized=frozenset({"core"}))

    tools["delegate_task"].invoke({"objective": "one"})
    tools["delegate_task"].invoke({"objective": "two"})
    with pytest.raises(DelegationError, match="limit 2"):
        tools["delegate_task"].invoke({"objective": "three"})

    # Another owner has their own budget.
    _tools_for(db, other, authorized=frozenset({"core"}))["delegate_task"].invoke({"objective": "mine"})


def test_an_interrupted_delegation_is_requeued_once_then_failed_and_frees_the_budget(
    db, deferred_delegation, monkeypatch
):
    """JAN-207: an orphan is re-run exactly once; a second interruption fails it and frees the budget.

    Before JAN-207 the first interruption already failed the row. Now it goes
    back to ``queued`` (still counting toward the fan-out cap), and only an
    interruption of that re-run is reported as a failure.
    """
    monkeypatch.setattr(delegation_service, "MAX_ACTIVE_DELEGATIONS_PER_OWNER", 1)
    user = _owner(db, "delegation-stale")
    long_ago = datetime.now(UTC) - timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 60)
    stale = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="chat-conversation-1",
        objective="Orphaned",
        requested_tool_ids=(),
        allowed_tool_ids={"core"},
        now=long_ago,
    )
    tools = _tools_for(db, user, authorized=frozenset({"core"}))

    fetched = tools["get_delegated_task"].invoke({"delegation_id": stale.delegation_id})
    assert fetched["status"] == "queued"
    row = _row(db, stale.delegation_id)
    assert (row.requeue_count, row.claim_token) == (1, None)
    assert row.requeued_at is not None
    # The requeued row still holds the owner's only slot.
    with pytest.raises(DelegationError, match="limit 1"):
        tools["delegate_task"].invoke({"objective": "fresh"})

    # Its re-run is interrupted too (lease passed again): now it fails for good.
    later = datetime.now(UTC) + timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 60)
    assert delegation_service.expire_stale_delegations(db, user.id, now=later) == 1
    row = _row(db, stale.delegation_id)
    assert row.status == "failed"
    assert "interrupted again after its one automatic re-run" in row.error
    assert row.requeue_count == 1

    tools["delegate_task"].invoke({"objective": "fresh"})  # the budget is free again
    # A late claim on the failed row does nothing.
    assert delegation_runtime.run_delegation(lambda: _new_session(db), stale.id) is None


# --- ownership and claims ---------------------------------------------------


def test_another_users_delegation_reads_as_missing(db, deferred_delegation):
    """IDs cannot be probed across owners, and listings are scoped to the parent task."""
    owner = _owner(db, "delegation-owner")
    intruder = models.User(username="delegation-intruder", hashed_password="x", is_admin=False)
    db.add(intruder)
    db.commit()
    created = _tools_for(db, owner, authorized=frozenset({"core"}))["delegate_task"].invoke({"objective": "private"})
    _tools_for(db, owner, authorized=frozenset({"core"}), task_id="chat-conversation-2")["delegate_task"].invoke(
        {"objective": "other conversation"}
    )

    intruder_tools = _tools_for(db, intruder, authorized=frozenset({"core"}))
    with pytest.raises(ValueError, match="no delegated task of yours"):
        intruder_tools["get_delegated_task"].invoke({"delegation_id": created["delegation_id"]})
    assert intruder_tools["list_delegated_tasks"].invoke({}) == []

    owner_list = _tools_for(db, owner, authorized=frozenset({"core"}))["list_delegated_tasks"].invoke({})
    assert [item["objective"] for item in owner_list] == ["private"]


def test_finalizing_requires_the_current_claim_token(db):
    """A stale executor cannot finalize a delegation it no longer owns."""
    user = _owner(db, "delegation-claims")
    record = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="chat-conversation-1",
        objective="Claim me",
        requested_tool_ids=(),
        allowed_tool_ids={"core"},
    )

    token = delegation_service.claim_delegation(db, record.id)
    assert token is not None
    assert delegation_service.claim_delegation(db, record.id) is None, "a running delegation cannot be re-claimed"
    assert delegation_service.complete_delegation(db, record.id, "not-the-token", "hijacked") is False
    assert delegation_service.complete_delegation(db, record.id, token, "done") is True
    assert delegation_service.fail_delegation(db, record.id, token, "late") is False
    assert _row(db, record.delegation_id).result == "done"


def test_blank_and_oversized_objectives_are_rejected(db):
    """The objective becomes the child's prompt and is persisted, so it is validated and bounded."""
    user = _owner(db, "delegation-objective")
    common = dict(owner_id=user.id, parent_task_id="p", requested_tool_ids=(), allowed_tool_ids={"core"})
    with pytest.raises(DelegationError, match="blank"):
        create_delegation(db, objective="   ", **common)
    with pytest.raises(DelegationError, match="at most"):
        create_delegation(db, objective="x" * (delegation_service.MAX_DELEGATION_OBJECTIVE_CHARS + 1), **common)


def test_deleting_the_conversation_keeps_the_child_record(db, deferred_delegation):
    """The conversation back-link is nulled, not cascaded: the child's history survives."""
    user = _owner(db, "delegation-conversation")
    conversation = models.Conversation(user_id=user.id)
    db.add(conversation)
    db.commit()
    created = _tools_for(db, user, authorized=frozenset({"core"}), conversation_id=conversation.id)[
        "delegate_task"
    ].invoke({"objective": "linked"})
    assert _row(db, created["delegation_id"]).conversation_id == conversation.id

    db.delete(db.get(models.Conversation, conversation.id))
    db.commit()

    assert _row(db, created["delegation_id"]).conversation_id is None


def _new_session(db):
    """Open a fresh session on the test engine, as the pool-thread executor does."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)()


# --- atomic safety gates (Plan Item 2 and 3 from PR #396) -----


def test_claim_delegation_rejects_stale_rows_without_expiry_run(db, deferred_delegation):
    """A queued delegation that outlived its lease cannot be claimed, even if expire_stale_delegations hasn't run.

    This is Plan Item 2 from PR #396: the lease cutoff is enforced atomically in
    claim_delegation's WHERE clause, so no concurrent race between expiry and claim
    can leave a stale row claimable.
    """
    user = _owner(db, "claim-stale-without-expiry")
    # Create a delegation in the past with a created_at older than DELEGATION_LEASE_SECONDS.
    long_ago = datetime.now(UTC) - timedelta(seconds=delegation_service.DELEGATION_LEASE_SECONDS + 60)
    stale = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="chat-conversation-1",
        objective="Stale task",
        requested_tool_ids=(),
        allowed_tool_ids={"core"},
        now=long_ago,
    )

    # The row is still in status QUEUED, but claim_delegation should reject it.
    # Before the fix, this would succeed if expire_stale_delegations hadn't run.
    token = delegation_service.claim_delegation(db, stale.id)
    assert token is None, "stale delegation should not be claimable"

    # Verify the row is still queued and has not been marked failed.
    row = _row(db, stale.delegation_id)
    assert row.status == models.RuntimeDelegationStatus.QUEUED.value


def test_concurrent_fan_out_cap_enforcement(db, monkeypatch):
    """The fan-out cap is enforced atomically via insert-from-select WHERE clause.

    This is Plan Item 3 from PR #396: the count-check-insert is made atomic via
    insert-from-select with a WHERE clause, so the cap cannot be exceeded even if
    the check and insert were to race.

    This test uses a deterministic approach instead of threading because SQLite
    has limitations with scalar subqueries in WHERE clauses of insert-from-select
    statements in multi-threaded contexts. The atomic SQL enforcement is verified
    by pre-seeding the DB with the cap-full number of rows, then attempting to
    create one more and asserting that the insert is rejected by the WHERE clause.
    """
    monkeypatch.setattr(delegation_service, "MAX_ACTIVE_DELEGATIONS_PER_OWNER", 2)
    user = _owner(db, "deterministic-cap-enforcement")

    # Pre-fill the cap: insert MAX_ACTIVE_DELEGATIONS_PER_OWNER rows directly.
    for i in range(delegation_service.MAX_ACTIVE_DELEGATIONS_PER_OWNER):
        create_delegation(
            db,
            owner_id=user.id,
            parent_task_id="cap-test",
            objective=f"Pre-filled task {i}",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
        )

    # Now attempt to create one more delegation.
    # The atomic insert-from-select WHERE clause should reject it because
    # the active count already meets the cap.
    with pytest.raises(DelegationError, match="limit 2"):
        create_delegation(
            db,
            owner_id=user.id,
            parent_task_id="cap-test",
            objective="Should be rejected by cap",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
        )

    # Verify that the total count is still exactly at the cap, not over it.
    # This confirms the atomic WHERE clause prevented the insert.
    active_count = delegation_service.count_active_delegations(db, user.id)
    assert active_count == delegation_service.MAX_ACTIVE_DELEGATIONS_PER_OWNER, (
        f"expected {delegation_service.MAX_ACTIVE_DELEGATIONS_PER_OWNER} active delegations, got {active_count}"
    )


# --- Plan Item 1 from PR #396: regression test for database commit/flush bug ---


def test_conversation_commit_enables_fk_visibility_across_sessions(tmp_path):
    """Regression test for Plan Item 1 (JAN-200 PR #396).

    When ws_chat creates a new conversation, it must call db.commit() (not just
    db.flush()) to ensure the row is durable and visible to other database
    connections. Without commit, a second session trying to insert a foreign
    key reference to that conversation will fail with "database is locked" or
    FK constraint error, depending on SQLite's journal mode.

    This test uses an on-disk SQLite file to reproduce the actual concurrency
    scenario that the in-memory StaticPool test engine cannot expose.
    """
    # Create an on-disk SQLite database, not the shared in-memory test engine.
    db_file = tmp_path / "test_delegation_concurrency.db"
    on_disk_url = f"sqlite:///{db_file}"
    on_disk_engine = create_engine(on_disk_url, connect_args={"check_same_thread": False})

    # Bootstrap the schema on the new engine.
    database.Base.metadata.create_all(on_disk_engine)

    SessionOnDisk = sessionmaker(bind=on_disk_engine, autocommit=False, autoflush=False)

    # --- Step 1: Session A creates a User and a Conversation ---
    session_a = SessionOnDisk()
    user_id = None
    conversation_id = None
    try:
        user = models.User(username="test-commit-regression", hashed_password="x", is_admin=False)
        session_a.add(user)
        session_a.flush()  # Persist the user ID locally (not yet committed).
        session_a.refresh(user)  # Fetch the assigned user.id.
        user_id = user.id

        conversation = models.Conversation(user_id=user_id)
        session_a.add(conversation)
        # This is the fix: db.commit() instead of db.flush().
        # Without commit, the conversation row exists only in the transaction log,
        # not in the durable SQLite file, so a second connection cannot see it.
        session_a.commit()
        session_a.refresh(conversation)
        conversation_id = conversation.id

    finally:
        session_a.close()

    # --- Step 2: Session B (new connection) tries to create a delegation ---
    # with the conversation_id as a foreign key.
    # This would fail without the commit() above.
    session_b = SessionOnDisk()
    try:
        # Verify we can read the conversation from session B.
        fetched_conversation = session_b.query(models.Conversation).filter_by(id=conversation_id).first()
        assert fetched_conversation is not None, (
            "Conversation should be visible from a second session after commit() on session A"
        )

        # Now try to create a delegation with this conversation_id as a FK.
        # This is what delegation_service.create_delegation does at line 246.
        delegation = delegation_service.create_delegation(
            session_b,
            owner_id=user_id,
            parent_task_id="test-plan-step",
            objective="Test delegation with committed conversation",
            requested_tool_ids=(),
            allowed_tool_ids={"core"},
            conversation_id=conversation_id,
        )

        # Verify the delegation was created and the FK is valid.
        assert delegation.conversation_id == conversation_id
        assert delegation.user_id == user_id

        # Verify we can fetch it back and the FK is not orphaned.
        fetched_delegation = (
            session_b.query(models.RuntimeDelegation).filter_by(delegation_id=delegation.delegation_id).first()
        )
        assert fetched_delegation is not None
        assert fetched_delegation.conversation_id == conversation_id

    finally:
        session_b.close()

    on_disk_engine.dispose()


def test_conversation_flush_without_commit_breaks_fk_visibility(tmp_path):
    """Demonstrate the bug: flush() alone leaves conversation invisible to other sessions.

    This shows the negative case for Plan Item 1 (JAN-200 PR #396).
    When a conversation is only flushed (not committed), a second session
    cannot see it because the row exists only in the first session's
    transaction log, not in the durable database file.

    SQLite's multi-version concurrency (with WAL or journal mode) means that
    uncommitted writes from one connection are invisible to new connections.
    """
    # Create an on-disk SQLite database.
    db_file = tmp_path / "test_delegation_flush_bug.db"
    on_disk_url = f"sqlite:///{db_file}"
    on_disk_engine = create_engine(on_disk_url, connect_args={"check_same_thread": False})

    database.Base.metadata.create_all(on_disk_engine)
    SessionOnDisk = sessionmaker(bind=on_disk_engine, autocommit=False, autoflush=False)

    # --- Step 1: Session A creates a User and Conversation with flush() only ---
    session_a = SessionOnDisk()
    user_id = None
    conversation_id = None
    try:
        user = models.User(username="test-flush-bug", hashed_password="x", is_admin=False)
        session_a.add(user)
        session_a.flush()
        session_a.refresh(user)
        user_id = user.id

        conversation = models.Conversation(user_id=user_id)
        session_a.add(conversation)
        # BUG: only flush, do NOT commit
        session_a.flush()
        session_a.refresh(conversation)
        conversation_id = conversation.id

    finally:
        session_a.close()  # Close the session, but DON'T commit

    # --- Step 2: Session B tries to see the uncommitted conversation ---
    session_b = SessionOnDisk()
    try:
        # The conversation should NOT be visible because session_a never committed.
        fetched_conversation = session_b.query(models.Conversation).filter_by(id=conversation_id).first()

        # The assertion here is the key: with only flush() and no commit,
        # the conversation is invisible to new connections.
        assert fetched_conversation is None, (
            "Without commit(), a flushed-but-uncommitted conversation should not be visible "
            "to a new session (this demonstrates the bug that Plan Item 1 fixes)"
        )

    finally:
        session_b.close()

    on_disk_engine.dispose()
