"""Tests for user-owned runtime-trigger ingress and observability."""

from types import SimpleNamespace

from janus import models, security, startup
from janus.config import settings
from janus.main import api
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


def test_runtime_trigger_api_scopes_rows_to_authenticated_user(client, db):
    """Authenticated users can enqueue and list only their own trigger rows."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-1",
                "idempotency_key": "manual-1",
                "kind": "manual",
                "source": "settings",
                "objective": "Review the inbox",
                "task_mode": "focused",
                "max_steps": 4,
                "tool_ids": ["send_email"],
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["task_id"] == "task-1"
        assert body["status"] == "queued"
        assert body["source"] == "settings"
        assert "payload" not in body

        listed = client.get("/api/runtime/triggers/")
        assert listed.status_code == 200
        assert [row["trigger_id"] for row in listed.json()["items"]] == [body["trigger_id"]]
        assert db.query(models.RuntimeTrigger).one().user_id == 1

        api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=2)
        assert client.get("/api/runtime/triggers/").json()["items"] == []
        collision = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-1",
                "idempotency_key": "manual-1",
                "kind": "manual",
                "objective": "Try to reuse another user's key",
            },
        )
        assert collision.status_code == 409
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_api_rejects_unbounded_context(client):
    """The API rejects invalid graph limits before creating a durable row."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-2",
                "idempotency_key": "manual-2",
                "kind": "scheduled",
                "objective": "Run work",
                "max_steps": 101,
            },
        )
        assert response.status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_api_rejects_coerced_step_limits(client):
    """The API requires a real integer rather than accepting coercible strings."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-3",
                "idempotency_key": "manual-3",
                "kind": "event",
                "objective": "Run work",
                "max_steps": "4",
            },
        )
        assert response.status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_api_builds_canonical_scheduled_occurrence(client, db):
    """Scheduled ingress derives identity and availability from schedule fields."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-scheduled",
                "kind": "scheduled",
                "source": "settings",
                "schedule_id": "morning-review",
                "scheduled_for": "2030-09-21T08:30:00+02:00",
                "objective": "Review the inbox",
            },
        )

        assert response.status_code == 202
        record = db.query(models.RuntimeTrigger).filter_by(task_id="task-scheduled").one()
        assert record.idempotency_key == "schedule:morning-review:2030-09-21T06:30:00+00:00"
        assert record.created_at.isoformat() == "2030-09-21T06:30:00"
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_api_builds_canonical_external_event(client, db):
    """Event ingress namespaces provider identity and preserves occurrence time."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-event",
                "kind": "event",
                "source": "calendar",
                "event_id": "evt-42",
                "occurred_at": "2026-09-21T08:30:00Z",
                "objective": "Review the changed event",
            },
        )

        assert response.status_code == 202
        record = db.query(models.RuntimeTrigger).filter_by(task_id="task-event").one()
        assert record.idempotency_key == "event:calendar:evt-42"
        assert record.created_at.isoformat() == "2026-09-21T08:30:00"
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_api_rejects_missing_or_incompatible_kind_fields(client):
    """Kind labels cannot be submitted without their canonical identity fields."""
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        missing_schedule = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-scheduled",
                "kind": "scheduled",
                "objective": "Run later",
            },
        )
        incompatible_event = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-event",
                "kind": "event",
                "idempotency_key": "manual-only",
                "event_id": "evt-42",
                "occurred_at": "2026-09-21T08:30:00Z",
                "objective": "Review event",
            },
        )

        assert missing_schedule.status_code == 422
        assert "scheduled triggers require" in missing_schedule.text
        assert incompatible_event.status_code == 422
        assert "event triggers do not accept idempotency_key" in incompatible_event.text
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def _completed_plan_state(task_id: str, result: str) -> PlanGraphState:
    """Build the plan state a successfully executed trigger leaves behind."""
    return PlanGraphState(
        task=TaskContext(task_id=task_id, objective="Summarize today", task_mode="trigger"),
        node=PlanGraphNode.COMPLETE,
        plan=Plan(
            plan_id=f"plan-{task_id}",
            task_id=task_id,
            objective="Summarize today",
            status=PlanStatus.COMPLETED,
            steps=(
                PlanStep(
                    step_id=f"{task_id}::step-1",
                    objective="Summarize today",
                    status=PlanStepStatus.COMPLETED,
                    result=result,
                ),
            ),
        ),
    )


def test_runtime_trigger_listing_surfaces_the_executed_outcome(client, db):
    """The Settings UI must be able to show what a run produced (JAN-192).

    ``RuntimeTrigger`` itself only holds lifecycle columns; what the automation
    actually did lives in the durable plan state keyed by the same owner and
    task_id. The listing joins the two rather than storing the result twice.
    """
    user = models.User(username="trigger-history-api", hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)

    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=user.id)
    try:
        created = client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "task-history",
                "idempotency_key": "manual-history",
                "kind": "manual",
                "objective": "Summarize today",
            },
        )
        assert created.status_code == 202
        assert created.json()["result"] is None

        PlanStateStore(lambda: db).save(
            db,
            user.id,
            _completed_plan_state(
                "task-history",
                "Authorized tools: core\nTools invoked: get_current_time\n\nAll quiet.",
            ),
        )

        row = client.get("/api/runtime/triggers/").json()["items"][0]
        assert "Tools invoked: get_current_time" in row["result"]
        assert "All quiet." in row["result"]
        assert row["step_error"] is None
        # The raw ingress payload stays hidden, exactly as before.
        assert "payload" not in row
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_listing_never_borrows_another_owners_plan_state(client, db):
    """Plan state is owner-scoped, so a shared task_id must not leak a result."""
    mine = models.User(username="history-mine", hashed_password="x", is_admin=False)
    theirs = models.User(username="history-theirs", hashed_password="x", is_admin=False)
    db.add_all([mine, theirs])
    db.commit()
    db.refresh(mine)
    db.refresh(theirs)

    PlanStateStore(lambda: db).save(
        db, theirs.id, _completed_plan_state("shared-task", "the other user's private summary")
    )

    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=mine.id)
    try:
        client.post(
            "/api/runtime/triggers/",
            json={
                "task_id": "shared-task",
                "idempotency_key": "manual-shared",
                "kind": "manual",
                "objective": "Summarize today",
            },
        )
        assert client.get("/api/runtime/triggers/").json()["items"][0]["result"] is None
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_worker_status_endpoint_reports_real_state(client, monkeypatch):
    """The Settings UI reads worker state from the backend, not hardcoded copy (JAN-199)."""
    monkeypatch.setattr(settings, "runtime_trigger_worker_enabled", False)
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        response = client.get("/api/runtime/triggers/worker")
        assert response.status_code == 200
        assert response.json() == {"enabled": False, "running": False}

        monkeypatch.setattr(startup, "runtime_trigger_worker_status", lambda: {"enabled": True, "running": True})
        assert client.get("/api/runtime/triggers/worker").json() == {"enabled": True, "running": True}
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_runtime_trigger_worker_status_endpoint_requires_authentication(client):
    """Worker status is only visible to authenticated users."""
    assert client.get("/api/runtime/triggers/worker").status_code == 401


def _seed_runs(db, user_id: int) -> models.TriggerDefinition:
    """Seed one definition with three runs plus two ad-hoc runs for ``user_id``."""
    from datetime import datetime

    definition = models.TriggerDefinition(
        user_id=user_id, name="Weekly review", objective="Review", tool_ids=[], kind="manual", timezone="UTC"
    )
    db.add(definition)
    db.commit()
    rows = [
        ("run-a", "completed", "manual", definition.id, datetime(2026, 9, 1, 8)),
        ("run-b", "failed", "scheduled", definition.id, datetime(2026, 9, 2, 8)),
        ("run-c", "completed", "scheduled", definition.id, datetime(2026, 9, 3, 8)),
        ("run-d", "queued", "manual", None, datetime(2026, 9, 4, 8)),
        ("run-e", "completed", "event", None, datetime(2026, 9, 5, 8)),
    ]
    for trigger_id, status, kind, definition_id, created_at in rows:
        db.add(
            models.RuntimeTrigger(
                user_id=user_id,
                trigger_id=trigger_id if user_id == 1 else f"{trigger_id}-u{user_id}",
                idempotency_key=trigger_id,
                task_id=f"task-{trigger_id}-u{user_id}",
                kind=kind,
                source="test",
                payload={},
                status=status,
                definition_id=definition_id,
                created_at=created_at,
            )
        )
    db.commit()
    return definition


def test_runtime_trigger_list_filters_paginates_and_labels_runs(client, db):
    """Runs can be filtered by status/kind/definition/date and paged with a total (JAN-256)."""
    definition = _seed_runs(db, 1)
    _seed_runs(db, 2)  # Another owner's runs must never leak into counts.
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:

        def ids(**params):
            body = client.get("/api/runtime/triggers/", params=params).json()
            return [row["trigger_id"] for row in body["items"]], body["total"], body["has_more"]

        assert ids() == (["run-e", "run-d", "run-c", "run-b", "run-a"], 5, False)
        assert ids(limit=2, offset=2) == (["run-c", "run-b"], 5, True)
        assert ids(status="completed") == (["run-e", "run-c", "run-a"], 3, False)
        assert ids(kind="scheduled") == (["run-c", "run-b"], 2, False)
        assert ids(definition_id=str(definition.id)) == (["run-c", "run-b", "run-a"], 3, False)
        assert ids(definition_id="none") == (["run-e", "run-d"], 2, False)
        assert ids(start="2026-09-02T00:00:00Z", end="2026-09-04T00:00:00Z") == (["run-c", "run-b"], 2, False)

        row = client.get("/api/runtime/triggers/", params={"definition_id": str(definition.id), "limit": 1}).json()
        assert row["items"][0]["definition_name"] == "Weekly review"
        assert row["items"][0]["definition_origin"] == "user"
        ad_hoc = client.get("/api/runtime/triggers/", params={"definition_id": "none", "limit": 1}).json()
        assert ad_hoc["items"][0]["definition_name"] is None

        assert client.get("/api/runtime/triggers/", params={"definition_id": "abc"}).status_code == 422
        assert client.get("/api/runtime/triggers/", params={"status": "bogus"}).status_code == 422
        inverted = client.get(
            "/api/runtime/triggers/", params={"start": "2026-09-04T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
        )
        assert inverted.status_code == 422
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)


def test_run_responses_carry_the_definition_origin_as_the_enum(db):
    """The linked definition's origin is the schema's enum, so serializing never warns (JAN-294).

    ``with_outcomes`` adds the label through ``model_copy``, which skips validation; the raw
    ``origin`` string used to trigger ``PydanticSerializationUnexpectedValue`` on every
    run-history response.
    """
    import warnings

    from janus.api.trigger_definitions import with_outcomes

    definition = _seed_runs(db, 1)
    definition.origin = models.TriggerDefinitionOrigin.SYSTEM.value
    db.commit()
    records = db.query(models.RuntimeTrigger).filter_by(user_id=1, definition_id=definition.id).all()

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        responses = with_outcomes(db, 1, records)
        payloads = [response.model_dump(mode="json") for response in responses]
        for response in responses:
            response.model_dump_json()

    assert len(responses) == 3
    assert {response.definition_origin for response in responses} == {models.TriggerDefinitionOrigin.SYSTEM}
    assert {payload["definition_origin"] for payload in payloads} == {"system"}


def test_a_waiting_run_is_listed_filterable_and_cancellable(client, db):
    """JAN-207: a run parked on its delegated children reads ``waiting`` and can be filtered by it."""
    from janus import trigger_service

    _seed_runs(db, 1)
    db.query(models.RuntimeTrigger).filter_by(trigger_id="run-d").update({"status": "waiting"})
    db.commit()
    api.dependency_overrides[security.get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        body = client.get("/api/runtime/triggers/", params={"status": "waiting"}).json()
        assert [(row["trigger_id"], row["status"]) for row in body["items"]] == [("run-d", "waiting")]
        assert body["total"] == 1
    finally:
        api.dependency_overrides.pop(security.get_current_user, None)
    assert trigger_service.cancel_owned_trigger(db, 1, "run-d") is True
