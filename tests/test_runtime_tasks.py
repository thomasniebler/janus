"""Tests for the Tasks page read model and endpoints (JAN-204)."""

import pytest
from runtime_task_fixtures import add_definition, add_delegation, add_trigger, save_plan, step_result

from janus import models, security
from janus.plan_graph import PlanStepStatus
from janus.runtime_adapter import ToolCallRecord
from janus.services import runtime_task_service
from janus.toolset_selection import ToolsetChoice
from janus.unattended_execution import format_step_result


def _user(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    return user


def _login(client, db, username: str) -> models.User:
    user = _user(db, username)
    response = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert response.status_code == 200, response.text
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return user


# --- parse_step_result -------------------------------------------------------


def test_parse_step_result_splits_the_recorded_audit_header_from_the_answer():
    """The trace is decoded from the header the unattended runtime really writes."""
    result = format_step_result(
        "Two mails need a reply.\n\nDetails: see below.",
        authorized_tool_ids=["mail.read", "mail.send"],
        invoked_tools=["search_emails", "send_email"],
        skipped=(),
        refused=(
            ToolCallRecord(
                tool_name="send_email", tool_id="mail.send", outcome="refused", detail="off for unattended runs"
            ),
        ),
        model="local/test-model",
    )
    trace, output = runtime_task_service.parse_step_result(result)
    assert trace is not None
    assert trace.model == "local/test-model"
    assert trace.authorized_tools == ["mail.read", "mail.send"]
    assert trace.invoked_tools == ["search_emails", "send_email"]
    assert trace.refused == "send_email (off for unattended runs)"
    assert trace.failed is None
    assert output == "Two mails need a reply.\n\nDetails: see below."


def test_parse_step_result_reads_the_tool_selection_line():
    """JAN-300: the brain-selected toolset line is part of the trace, not a reason to drop it."""
    choice = ToolsetChoice(
        source="selected",
        reason="Topics only.",
        tool_ids=("core", "topics"),
        trimmed_tool_ids=("mail.send",),
        authorized_tool_ids=("core", "topics", "memory"),
    )
    result = format_step_result(
        "Nothing new.", authorized_tool_ids=["core", "topics"], invoked_tools=[], skipped=(), toolset=choice
    )
    trace, output = runtime_task_service.parse_step_result(result)
    assert trace is not None
    assert trace.authorized_tools == ["core", "topics"]
    assert trace.tool_selection == "selected, 2 of 3 offered (core, topics); trimmed: mail.send. Why: Topics only."
    assert output == "Nothing new."


def test_parse_step_result_reads_none_as_an_empty_tool_list():
    """Parse step result reads none as an empty tool list."""
    trace, output = runtime_task_service.parse_step_result(
        format_step_result("Nothing to do.", authorized_tool_ids=[], invoked_tools=[], skipped=())
    )
    assert trace is not None
    assert trace.authorized_tools == [] and trace.invoked_tools == []
    assert trace.model is None
    assert output == "Nothing to do."


@pytest.mark.parametrize(
    "result",
    [
        "A plain answer without a header.",
        "Authorized tools: mail.read\nSomething unexpected\n\nbody",
        "Authorized tools: mail.read\nUnknown label: x\n\nbody",
    ],
)
def test_parse_step_result_keeps_unrecognised_results_whole(result):
    """Anything that is not exactly the audit header is shown as the answer, untouched."""
    assert runtime_task_service.parse_step_result(result) == (None, result)


def test_parse_step_result_handles_empty_results():
    """Parse step result handles empty results."""
    assert runtime_task_service.parse_step_result(None) == (None, None)
    assert runtime_task_service.parse_step_result("") == (None, "")


def test_parse_step_result_without_answer_body():
    """Parse step result without answer body."""
    trace, output = runtime_task_service.parse_step_result("Authorized tools: none\nTools invoked: none")
    assert trace is not None and output is None


# --- list_tasks_for_user -----------------------------------------------------


def test_list_merges_trigger_runs_definition_runs_and_delegations_newest_first(db):
    """List merges trigger runs definition runs and delegations newest first."""
    alice = _user(db, "alice")
    definition = add_definition(db, alice.id, "Weekly review")
    add_trigger(db, alice.id, "adhoc", minutes=1)
    save_plan(db, alice.id, "task-adhoc", "Summarize the inbox\nwith extra context")
    add_trigger(db, alice.id, "defrun", minutes=3, definition_id=definition.id)
    add_delegation(db, alice.id, "del-1", parent_task_id="task-adhoc", minutes=2)
    save_plan(db, alice.id, "child-del-1", "Look up del-1", task_mode="delegated")

    page = runtime_task_service.list_tasks_for_user(db, alice.id)

    assert page.total == 3
    assert [(item.source, item.ref_id) for item in page.items] == [
        ("definition", "defrun"),
        ("delegated", "del-1"),
        ("trigger", "adhoc"),
    ]
    definition_run, delegated, adhoc = page.items
    assert definition_run.title == "Weekly review"
    assert definition_run.definition_name == "Weekly review"
    assert definition_run.step_count == 0 and definition_run.plan_status is None
    assert adhoc.title == "Summarize the inbox"
    assert adhoc.plan_status == "completed"
    assert (adhoc.step_count, adhoc.completed_step_count, adhoc.failed_step_count) == (1, 1, 0)
    assert adhoc.child_count == 1
    assert delegated.task_id == "child-del-1"
    assert delegated.step_count == 1


def test_list_filters_by_source_and_status_and_paginates(db):
    """List filters by source and status and paginates."""
    alice = _user(db, "alice")
    definition = add_definition(db, alice.id)
    for minute in range(5):
        add_trigger(db, alice.id, f"run-{minute}", minutes=minute, status="failed" if minute % 2 else "completed")
    add_trigger(db, alice.id, "defrun", minutes=10, definition_id=definition.id)
    add_delegation(db, alice.id, "del-1", parent_task_id="task-run-0", minutes=11)

    adhoc = runtime_task_service.list_tasks_for_user(db, alice.id, source="trigger")
    assert adhoc.total == 5 and {item.source for item in adhoc.items} == {"trigger"}
    assert runtime_task_service.list_tasks_for_user(db, alice.id, source="definition").total == 1
    assert [
        item.ref_id for item in runtime_task_service.list_tasks_for_user(db, alice.id, source="delegated").items
    ] == ["del-1"]
    failed = runtime_task_service.list_tasks_for_user(db, alice.id, status="failed")
    assert [item.ref_id for item in failed.items] == ["run-3", "run-1"]

    first = runtime_task_service.list_tasks_for_user(db, alice.id, limit=3)
    second = runtime_task_service.list_tasks_for_user(db, alice.id, limit=3, offset=3)
    third = runtime_task_service.list_tasks_for_user(db, alice.id, limit=3, offset=6)
    assert first.total == second.total == 7
    seen = [item.ref_id for item in first.items + second.items + third.items]
    assert seen == ["del-1", "defrun", "run-4", "run-3", "run-2", "run-1", "run-0"]


@pytest.mark.parametrize("fk_enforced", [False, True], ids=["dangling-id", "set-null"])
def test_a_deleted_definitions_runs_keep_their_provenance(db, fk_enforced):
    """A deleted definition's runs stay definition runs under their original name, without a link.

    SQLite (no FK enforcement) leaves ``definition_id`` dangling; a database
    that enforces ``ON DELETE SET NULL`` nulls it. Both must read the same.
    """
    from janus.trigger_definition_service import delete_definition, enqueue_definition_run
    from janus.trigger_service import TriggerKind

    alice = _user(db, "alice")
    definition = add_definition(db, alice.id, "Weekly review")
    record, duplicate = enqueue_definition_run(
        db, definition, occurrence_key="manual:1", kind=TriggerKind.MANUAL, source="definition.manual"
    )
    assert not duplicate
    assert record.definition_name == "Weekly review"
    add_trigger(db, alice.id, "adhoc", minutes=1)

    definition.name = "Weekly review (renamed)"
    db.commit()
    linked = runtime_task_service.get_trigger_task_for_user(db, alice.id, record.trigger_id)
    assert linked is not None
    assert (linked.source, linked.definition_id) == ("definition", definition.id)
    assert linked.definition_name == linked.title == "Weekly review (renamed)"

    delete_definition(db, definition)
    if fk_enforced:
        db.get(models.RuntimeTrigger, record.id).definition_id = None
        db.commit()
    db.expire_all()

    detail = runtime_task_service.get_trigger_task_for_user(db, alice.id, record.trigger_id)
    assert detail is not None
    assert detail.source == "definition"
    assert detail.definition_id is None
    assert detail.definition_name == detail.title == "Weekly review"

    definitions = runtime_task_service.list_tasks_for_user(db, alice.id, source="definition")
    assert [item.ref_id for item in definitions.items] == [record.trigger_id]
    adhoc = runtime_task_service.list_tasks_for_user(db, alice.id, source="trigger")
    assert [item.ref_id for item in adhoc.items] == ["adhoc"]


def test_tasks_api_keeps_a_deleted_definitions_run_in_the_definition_filter(client, db):
    """Deleting a definition through the API does not reclassify its run as ad hoc."""
    _login(client, db, "alice")
    created = client.post(
        "/api/runtime/definitions/", json={"name": "Inbox sweep", "objective": "Sweep the inbox.", "tool_ids": []}
    )
    assert created.status_code in (200, 201), created.text
    definition_id = created.json()["id"]
    run = client.post(f"/api/runtime/definitions/{definition_id}/run")
    assert run.status_code in (200, 202), run.text
    trigger_id = run.json()["trigger_id"]
    assert client.delete(f"/api/runtime/definitions/{definition_id}").status_code in (200, 204)

    listed = client.get("/api/runtime/tasks/", params={"source": "definition"}).json()
    assert [(item["ref_id"], item["definition_name"], item["definition_id"]) for item in listed["items"]] == [
        (trigger_id, "Inbox sweep", None)
    ]
    assert client.get("/api/runtime/tasks/", params={"source": "trigger"}).json()["total"] == 0
    detail = client.get(f"/api/runtime/tasks/triggers/{trigger_id}").json()
    assert (detail["source"], detail["title"], detail["definition_id"]) == ("definition", "Inbox sweep", None)


def test_list_rejects_invalid_arguments(db):
    """List rejects invalid arguments."""
    alice = _user(db, "alice")
    with pytest.raises(ValueError):
        runtime_task_service.list_tasks_for_user(db, alice.id, source="chat")
    with pytest.raises(ValueError):
        runtime_task_service.list_tasks_for_user(db, alice.id, limit=0)


def test_list_skips_an_unreadable_plan_snapshot(db):
    """List skips an unreadable plan snapshot."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "broken")
    db.add(models.PlanRuntimeState(user_id=alice.id, task_id="task-broken", node="complete", state={"nope": 1}))
    db.commit()
    item = runtime_task_service.list_tasks_for_user(db, alice.id).items[0]
    assert item.ref_id == "broken" and item.plan_status is None and item.title == "settings"


def test_running_task_reports_its_current_step(db):
    """Running task reports its current step."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "live", status="running")
    save_plan(db, alice.id, "task-live", "Draft the status mail", step_status=PlanStepStatus.RUNNING)
    item = runtime_task_service.list_tasks_for_user(db, alice.id).items[0]
    assert item.current_step == "Draft the status mail"
    assert item.plan_status == "active"


def test_list_expires_a_delegation_that_outlived_its_lease(db):
    """List applies the JAN-207 lease rule: requeue an interrupted delegation once, then fail it."""
    alice = _user(db, "alice")
    stale = add_delegation(db, alice.id, "stale", parent_task_id="x", status="completed")
    stale.status = "running"
    db.commit()
    assert runtime_task_service.list_tasks_for_user(db, alice.id).items[0].status == "queued"

    db.refresh(stale)
    assert stale.requeue_count == 1
    stale.status = "running"
    stale.requeued_at = stale.created_at
    db.commit()
    assert runtime_task_service.list_tasks_for_user(db, alice.id).items[0].status == "failed"


# --- detail ------------------------------------------------------------------


def test_trigger_detail_has_steps_trace_children_and_scheduler(db):
    """Trigger detail has steps trace children and scheduler."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "scheduler", minutes=0)
    add_trigger(db, alice.id, "follow-up", minutes=5, parent_trigger_id="scheduler")
    save_plan(db, alice.id, "task-follow-up", "Check the reply")
    add_delegation(db, alice.id, "child-a", parent_task_id="task-follow-up", minutes=6)

    detail = runtime_task_service.get_trigger_task_for_user(db, alice.id, "follow-up")

    assert detail is not None
    assert detail.objective == "Check the reply"
    assert detail.task_mode == "trigger"
    assert detail.node == "complete"
    assert detail.trigger_source == "settings" and detail.attempt_count == 1
    assert detail.parent is not None and detail.parent.ref_id == "scheduler"
    [step] = detail.steps
    assert step.trace is not None and step.trace.invoked_tools == ["search_emails"]
    assert step.output == "Inbox reviewed."
    assert step.required_tool_ids == ["mail.read"]
    assert [child.ref_id for child in detail.children] == ["child-a"]
    assert "secret payload" not in detail.model_dump_json()


def test_trigger_detail_records_a_failed_step(db):
    """Trigger detail records a failed step."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "bad", status="exhausted")
    save_plan(db, alice.id, "task-bad", "Send it", step_status=PlanStepStatus.FAILED, error="provider down")
    detail = runtime_task_service.get_trigger_task_for_user(db, alice.id, "bad")
    assert detail is not None
    assert detail.reason == "step failed"
    assert detail.steps[0].error == "provider down"
    assert detail.steps[0].trace is None
    assert detail.failed_step_count == 1


def test_trigger_detail_before_any_plan_exists(db):
    """Trigger detail before any plan exists."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "queued", status="queued")
    detail = runtime_task_service.get_trigger_task_for_user(db, alice.id, "queued")
    assert detail is not None and detail.steps == [] and detail.objective is None and detail.parent is None


def test_delegation_detail_links_its_parent_trigger_and_chat(db):
    """Delegation detail links its parent trigger and chat."""
    alice = _user(db, "alice")
    add_trigger(db, alice.id, "parent")
    conversation = models.Conversation(user_id=alice.id, title="chat")
    db.add(conversation)
    db.commit()
    add_delegation(db, alice.id, "from-trigger", parent_task_id="task-parent")
    add_delegation(db, alice.id, "from-chat", parent_task_id="chat-task", conversation_id=conversation.id)
    save_plan(
        db,
        alice.id,
        "child-from-trigger",
        "Look up from-trigger",
        task_mode="delegated",
        result=step_result("Found it."),
    )

    from_trigger = runtime_task_service.get_delegated_task_for_user(db, alice.id, "from-trigger")
    from_chat = runtime_task_service.get_delegated_task_for_user(db, alice.id, "from-chat")

    assert from_trigger is not None and from_chat is not None
    assert from_trigger.parent is not None and from_trigger.parent.ref_id == "parent"
    assert from_trigger.task_mode == "delegated"
    assert from_trigger.steps[0].output == "Found it."
    assert from_trigger.allowed_tool_ids == ["mail.read"]
    assert from_trigger.delegation_result == "Child summary."
    assert from_chat.parent is None and from_chat.conversation_id == conversation.id
    assert from_chat.steps == [] and from_chat.objective == "Look up from-chat"


# --- API ---------------------------------------------------------------------


def test_tasks_api_lists_pages_and_returns_detail(client, db):
    """Tasks api lists pages and returns detail."""
    alice = _login(client, db, "alice")
    for minute in range(3):
        add_trigger(db, alice.id, f"run-{minute}", minutes=minute)
    save_plan(db, alice.id, "task-run-2", "Summarize the inbox")
    add_delegation(db, alice.id, "del-1", parent_task_id="task-run-2", minutes=4)

    first = client.get("/api/runtime/tasks/", params={"limit": 2})
    assert first.status_code == 200
    body = first.json()
    assert (body["total"], body["limit"], body["offset"], body["has_more"]) == (4, 2, 0, True)
    assert [item["ref_id"] for item in body["items"]] == ["del-1", "run-2"]
    last = client.get("/api/runtime/tasks/", params={"limit": 2, "offset": 2}).json()
    assert last["has_more"] is False and [item["ref_id"] for item in last["items"]] == ["run-1", "run-0"]
    assert client.get("/api/runtime/tasks/", params={"source": "delegated"}).json()["total"] == 1

    detail = client.get("/api/runtime/tasks/triggers/run-2").json()
    assert detail["steps"][0]["trace"]["authorized_tools"] == ["mail.read", "calendar.read"]
    assert [child["ref_id"] for child in detail["children"]] == ["del-1"]
    assert "payload" not in detail
    child = client.get("/api/runtime/tasks/delegations/del-1").json()
    assert child["parent"] == {"kind": "trigger", "ref_id": "run-2"}


def test_tasks_api_lists_and_filters_a_run_waiting_on_its_children(client, db):
    """A JAN-207 run parked on its delegated children reads as ``waiting``, not a 500."""
    alice = _login(client, db, "alice")
    add_trigger(db, alice.id, "parked", status=models.RuntimeTriggerStatus.WAITING.value)
    add_trigger(db, alice.id, "done", minutes=1)
    add_delegation(db, alice.id, "awaited", parent_task_id="task-parked", status="running", minutes=2)

    waiting = client.get("/api/runtime/tasks/", params={"status": "waiting"})
    assert waiting.status_code == 200, waiting.text
    assert [(item["ref_id"], item["status"]) for item in waiting.json()["items"]] == [("parked", "waiting")]
    detail = client.get("/api/runtime/tasks/triggers/parked")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "waiting"
    assert [child["ref_id"] for child in detail.json()["children"]] == ["awaited"]


@pytest.mark.parametrize(
    "params",
    [{"source": "chat"}, {"status": "nope"}, {"limit": 0}, {"limit": 101}, {"offset": -1}],
)
def test_tasks_api_rejects_invalid_query(client, db, params):
    """Tasks api rejects invalid query."""
    _login(client, db, "alice")
    assert client.get("/api/runtime/tasks/", params=params).status_code == 422


def test_tasks_api_requires_authentication(client):
    """Tasks api requires authentication."""
    assert client.get("/api/runtime/tasks/").status_code == 401
    assert client.get("/api/runtime/tasks/triggers/x").status_code == 401


def test_tasks_api_returns_404_for_unknown_ids(client, db):
    """Tasks api returns 404 for unknown ids."""
    _login(client, db, "alice")
    assert client.get("/api/runtime/tasks/triggers/missing").status_code == 404
    assert client.get("/api/runtime/tasks/delegations/missing").status_code == 404
