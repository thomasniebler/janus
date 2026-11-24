"""Tests for the Janus-owned local planner service."""

from datetime import UTC, datetime, timedelta

from janus import llm_service, models
from janus.services.local_planner_service import _TASK_CONTEXT_LIMIT, LocalPlannerService
from janus.services.planner_item_deletion import delete_local_event, delete_local_task


def _seed_user(db):
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    account = models.EmailAccount(name="Work", email="owner@example.com", password="secret", user_id=1)
    db.add(user)
    db.flush()
    account.user_id = user.id
    db.add(account)
    db.commit()
    db.refresh(user)
    db.refresh(account)
    return user, account


def test_local_planner_service_unifies_local_and_imported_items(db):
    """Unified planner reads should include writable local items plus read-only imported context."""
    user, _account = _seed_user(db)
    imported_account = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="encrypted-token",
        user_id=user.id,
    )
    db.add(imported_account)
    db.flush()

    start = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    db.add_all(
        [
            models.TaskRecord(
                id=501,
                planner_account_id=imported_account.id,
                external_id="task-imported",
                title="Imported task",
                status=models.TaskStatus.TODO,
            ),
            models.CalendarEvent(
                id=601,
                planner_account_id=imported_account.id,
                external_id="event-imported",
                summary="Imported event",
                start_time=start,
                end_time=start + timedelta(hours=1),
            ),
        ]
    )
    db.commit()

    service = LocalPlannerService(db)
    created_task = service.create_task(user.id, {"title": "Local task", "due_at": start.isoformat()})
    created_event = service.create_event(
        user.id,
        {
            "title": "Local event",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=2)).isoformat(),
        },
    )

    tasks = service.list_tasks(user.id, include_imported=True)
    events = service.list_events(user.id, start=start, end=start + timedelta(days=1), include_imported=True)

    assert {(task["title"], task["read_only"], task["source"]) for task in tasks} == {
        ("Local task", False, "manual"),
        ("Imported task", True, "imported"),
    }
    assert {(event["title"], event["read_only"], event["source"]) for event in events} == {
        ("Local event", False, "manual"),
        ("Imported event", True, "imported"),
    }

    updated_task = service.update_task(user.id, created_task["id"], {"status": models.TaskStatus.DONE})
    assert updated_task is not None
    assert updated_task["completed_at"] is not None
    assert [task["title"] for task in service.list_tasks(user.id, include_imported=True)] == ["Imported task"]
    assert created_event["title"] == "Local event"


def test_local_planner_service_list_tasks_applies_limits_to_local_and_imported_items(db):
    """Task limits should cap local and imported planner queries independently."""
    user, _account = _seed_user(db)
    imported_account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token="encrypted-token",
        user_id=user.id,
    )
    db.add(imported_account)
    db.flush()

    now = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    for index in range(3):
        db.add(
            models.PlannerTask(
                user_id=user.id,
                title=f"Local task {index}",
                status=models.TaskStatus.TODO,
                due_at=now + timedelta(hours=index),
                source=models.PlannerItemSource.MANUAL,
            )
        )
        db.add(
            models.TaskRecord(
                planner_account_id=imported_account.id,
                external_id=f"imported-task-{index}",
                title=f"Imported task {index}",
                status=models.TaskStatus.TODO,
                due_date=now + timedelta(hours=index),
            )
        )
    db.commit()

    tasks = LocalPlannerService(db).list_tasks(user.id, include_imported=True, limit=2)

    assert [task["title"] for task in tasks] == [
        "Local task 0",
        "Local task 1",
        "Imported task 0",
        "Imported task 1",
    ]


def test_local_planner_service_build_context_data_caps_task_context(db):
    """Planner context assembly should cap both local and imported tasks."""
    user, _account = _seed_user(db)
    imported_account = models.PlannerAccount(
        name="Google Tasks",
        provider=models.Provider.GOOGLE_TASKS,
        token="encrypted-token",
        user_id=user.id,
    )
    db.add(imported_account)
    db.flush()

    now = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    for index in range(_TASK_CONTEXT_LIMIT + 5):
        db.add(
            models.PlannerTask(
                user_id=user.id,
                title=f"Local context task {index}",
                status=models.TaskStatus.TODO,
                due_at=now + timedelta(hours=index),
                source=models.PlannerItemSource.MANUAL,
            )
        )
        db.add(
            models.TaskRecord(
                planner_account_id=imported_account.id,
                external_id=f"context-imported-task-{index}",
                title=f"Imported context task {index}",
                status=models.TaskStatus.TODO,
                due_date=now + timedelta(hours=index),
            )
        )
    db.commit()

    context = LocalPlannerService(db).build_context_data(user.id)

    assert len(context["local_tasks"]) == _TASK_CONTEXT_LIMIT
    assert len(context["imported_tasks"]) == _TASK_CONTEXT_LIMIT
    assert context["local_tasks"][0]["title"] == "Local context task 0"
    assert context["local_tasks"][-1]["title"] == f"Local context task {_TASK_CONTEXT_LIMIT - 1}"
    assert context["imported_tasks"][0]["title"] == "Imported context task 0"
    assert context["imported_tasks"][-1]["title"] == f"Imported context task {_TASK_CONTEXT_LIMIT - 1}"


def test_local_planner_service_build_context_data_excludes_candidate_topics(db):
    """Candidate topics awaiting corroboration must never reach LLM-facing suggestion extraction."""
    user, account = _seed_user(db)
    email = models.EmailRecord(
        account_id=account.id,
        message_id="context-1",
        sender="sender@example.com",
        subject="Context subject",
        snippet="Context body",
        priority=models.Priority.IMPORTANT,
        received_at=datetime.now(UTC),
    )
    active_topic = models.TrackedTopic(
        user_id=user.id,
        slug="active-topic",
        title="Active topic",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
    )
    candidate_topic = models.TrackedTopic(
        user_id=user.id,
        slug="candidate-topic",
        title="Candidate topic",
        status=models.TopicStatus.CANDIDATE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add_all([email, active_topic, candidate_topic])
    db.commit()
    db.add_all(
        [
            models.TopicEvidence(
                topic_id=topic.id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_id=email.id,
                evidence_role=models.TopicEvidenceRole.TRIGGER,
            )
            for topic in (active_topic, candidate_topic)
        ]
    )
    db.commit()

    context = LocalPlannerService(db).build_context_data(user.id)

    assert len(context["recent_emails"]) == 1
    assert [topic["title"] for topic in context["recent_emails"][0]["topics"]] == ["Active topic"]


def test_local_planner_service_applies_and_dismisses_suggestions(db):
    """Approving suggestions should create or update local planner items; dismissing should not."""
    user, account = _seed_user(db)
    source_email = models.EmailRecord(
        account_id=account.id,
        message_id="planner-suggestion-email",
        sender="manager@example.com",
        subject="Trip prep",
        snippet="Please book travel and block time for the kickoff.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 6, 28, 8, 0, tzinfo=UTC),
    )
    existing_task = models.PlannerTask(
        user_id=user.id,
        title="Book venue",
        status=models.TaskStatus.TODO,
        source=models.PlannerItemSource.MANUAL,
    )
    db.add_all([source_email, existing_task])
    db.flush()

    new_task = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.TASK,
        source=models.PlannerItemSource.EMAIL_AGENT,
        title="Book travel",
        description="Travel needs to be booked for the kickoff.",
        task_status=models.TaskStatus.TODO,
        source_email_id=source_email.id,
    )
    new_event = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.EVENT,
        source=models.PlannerItemSource.EMAIL_AGENT,
        title="Kickoff",
        start_time=datetime(2026, 7, 1, 13, 0, tzinfo=UTC),
        end_time=datetime(2026, 7, 1, 14, 0, tzinfo=UTC),
        source_email_id=source_email.id,
    )
    update_task = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.TASK_UPDATE,
        source=models.PlannerItemSource.EMAIL_AGENT,
        title="Book venue and catering",
        task_status=models.TaskStatus.IN_PROGRESS,
        target_task_id=existing_task.id,
        source_email_id=source_email.id,
    )
    dismissed = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.TASK,
        source=models.PlannerItemSource.EMAIL_AGENT,
        title="Ignore me",
    )
    db.add_all([new_task, new_event, update_task, dismissed])
    db.commit()

    service = LocalPlannerService(db)
    applied_task = service.approve_suggestion(user.id, new_task.id)
    applied_event = service.approve_suggestion(user.id, new_event.id)
    applied_update = service.approve_suggestion(user.id, update_task.id)
    dismissed_payload = service.dismiss_suggestion(user.id, dismissed.id)

    assert applied_task is not None
    assert applied_task["status"] == "applied"
    assert applied_task["target_task_id"] is not None
    assert applied_event is not None
    assert applied_event["target_event_id"] is not None
    assert applied_update is not None
    assert dismissed_payload is not None
    assert dismissed_payload["status"] == "dismissed"

    db.refresh(existing_task)
    assert existing_task.title == "Book venue and catering"
    assert existing_task.status == models.TaskStatus.IN_PROGRESS
    assert db.query(models.PlannerTask).filter(models.PlannerTask.title == "Book travel").count() == 1
    assert db.query(models.PlannerEvent).filter(models.PlannerEvent.title == "Kickoff").count() == 1
    assert (
        db.query(models.PlannerLink).filter(models.PlannerLink.source_type == models.PlannerLinkSource.EMAIL).count()
        >= 2
    )


def test_capture_email_suggestions_dedupes_repeat_emails(db, monkeypatch):
    """Repeated emails about the same obligation should refresh one pending suggestion instead of duplicating it."""
    user, account = _seed_user(db)
    first_email = models.EmailRecord(
        account_id=account.id,
        message_id="repeat-1",
        sender="manager@example.com",
        subject="Finish board deck",
        snippet="Need the board deck by Friday.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 6, 28, 9, 0, tzinfo=UTC),
    )
    second_email = models.EmailRecord(
        account_id=account.id,
        message_id="repeat-2",
        sender="manager@example.com",
        subject="Finish board deck",
        snippet="Following up on the same board deck deadline.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 6, 28, 10, 0, tzinfo=UTC),
    )
    db.add_all([first_email, second_email])
    db.commit()
    db.refresh(first_email)
    db.refresh(second_email)

    class FakeLLM:
        def __init__(self, db=None):
            self.db = db

        def extract_planner_suggestions(self, email_data, planner_context=None):
            return [
                {
                    "suggestion_type": "task",
                    "title": "Finish board deck",
                    "description": "Prepare the board deck before Friday.",
                    "task_status": "Todo",
                    "confidence": "high",
                    "dedupe_hint": "finish-board-deck",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLM)

    service = LocalPlannerService(db)
    first_payloads = service.capture_email_suggestions(first_email)
    second_payloads = service.capture_email_suggestions(second_email)

    suggestions = db.query(models.PlannerSuggestion).all()
    assert len(first_payloads) == 1
    assert len(second_payloads) == 1
    assert len(suggestions) == 1
    assert suggestions[0].dedupe_key == "finish-board-deck"
    assert suggestions[0].source_email_id == second_email.id


def test_delete_task_promotes_subtasks_and_detaches_links_and_suggestions(db):
    """Deleting a local task keeps its subtasks (moved up), drops its links, and dismisses pending updates (JAN-221)."""
    user, _account = _seed_user(db)
    service = LocalPlannerService(db)
    root = service.create_task(user.id, {"title": "Home"})
    middle = service.create_task(user.id, {"title": "Kitchen", "parent_task_id": root["id"]})
    leaf = service.create_task(user.id, {"title": "Fix sink", "parent_task_id": middle["id"]})
    db.add_all(
        [
            models.PlannerLink(
                user_id=user.id,
                planner_task_id=middle["id"],
                source_type=models.PlannerLinkSource.EMAIL,
                source_id=1,
            ),
            models.PlannerSuggestion(
                user_id=user.id,
                suggestion_type=models.PlannerSuggestionType.TASK_UPDATE,
                status=models.PlannerSuggestionStatus.PENDING,
                title="Kitchen (renamed)",
                target_task_id=middle["id"],
            ),
            models.PlannerSuggestion(
                user_id=user.id,
                suggestion_type=models.PlannerSuggestionType.TASK,
                status=models.PlannerSuggestionStatus.APPLIED,
                title="Kitchen",
                target_task_id=middle["id"],
            ),
        ]
    )
    db.commit()

    assert delete_local_task(db, user.id, middle["id"]) is True

    db.expire_all()
    assert service.get_local_task(user.id, middle["id"]) is None
    assert service.get_local_task(user.id, leaf["id"]).parent_task_id == root["id"]
    assert db.query(models.PlannerLink).count() == 0
    suggestions = {s.status: s for s in db.query(models.PlannerSuggestion).all()}
    assert suggestions[models.PlannerSuggestionStatus.DISMISSED].target_task_id is None
    assert suggestions[models.PlannerSuggestionStatus.APPLIED].target_task_id is None
    assert delete_local_task(db, user.id, middle["id"]) is False


def test_delete_event_and_owner_scoping(db):
    """Only the owner can delete a local event or task; a foreign or missing id deletes nothing (JAN-221)."""
    user, _account = _seed_user(db)
    other = models.User(username="other", hashed_password="hash")
    db.add(other)
    db.commit()
    service = LocalPlannerService(db)
    start = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)
    event = service.create_event(
        user.id, {"title": "Standup", "start_time": start, "end_time": start + timedelta(minutes=15)}
    )
    task = service.create_task(user.id, {"title": "Mine"})
    db.add(
        models.PlannerLink(
            user_id=user.id,
            planner_event_id=event["id"],
            source_type=models.PlannerLinkSource.EMAIL,
            source_id=1,
        )
    )
    db.commit()

    assert delete_local_event(db, other.id, event["id"]) is False
    assert delete_local_task(db, other.id, task["id"]) is False
    assert service.get_local_event(user.id, event["id"]) is not None
    assert service.get_local_task(user.id, task["id"]) is not None

    assert delete_local_event(db, user.id, event["id"]) is True
    db.expire_all()
    assert service.get_local_event(user.id, event["id"]) is None
    assert db.query(models.PlannerLink).count() == 0
    assert delete_local_event(db, user.id, event["id"]) is False
