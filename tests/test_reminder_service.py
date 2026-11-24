"""Tests for reminder extraction, persistence, and briefing context."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, llm_service, models, planner_service, reminder_service
from janus.time_utils import normalize_timestamp


def _make_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    return testing_session_local


def _owned_source_email(db, user: models.User) -> models.EmailRecord:
    """Return an email on one of ``user``'s accounts; reminder memory is owned through its source email."""
    account = models.EmailAccount(name=f"Mail {user.username}", email=f"{user.username}@example.com", user_id=user.id)
    db.add(account)
    db.commit()
    email = models.EmailRecord(
        account_id=account.id, message_id=f"src-{user.username}", priority=models.Priority.DIGEST
    )
    db.add(email)
    db.commit()
    return email


def test_capture_email_reminders_upserts_active_memory(monkeypatch):
    """Repeated reminder extraction should refresh one active reminder instead of duplicating it."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    owner = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=owner.id)
    db.add(account)
    db.commit()
    db.refresh(account)

    first_email = models.EmailRecord(
        account_id=account.id,
        message_id="msg-1",
        sender="boss@example.com",
        subject="Prep for quarterly review",
        snippet="Please prepare slides for the review on May 1 at 10:00 UTC.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 4, 5, 8, 0, tzinfo=UTC),
    )
    second_email = models.EmailRecord(
        account_id=account.id,
        message_id="msg-2",
        sender="boss@example.com",
        subject="Prep for quarterly review",
        snippet="Reminder to finish the slides before May 1 at 10:00 UTC.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
    )
    db.add_all([first_email, second_email])
    db.commit()
    db.refresh(first_email)
    db.refresh(second_email)

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_reminders(self, email_data, reminder_context):
            return [
                {
                    "title": "Prepare quarterly review slides",
                    "kind": "preparation",
                    "notes": "Slides should be ready before the review meeting.",
                    "due_at": "2026-04-30T10:00:00+00:00",
                    "prepare_for_at": "2026-05-01T10:00:00+00:00",
                    "confidence": "high",
                    "dedupe_hint": "quarterly-review-slides",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    service = reminder_service.ReminderService(db)
    service.capture_email_reminders(first_email)
    service.capture_email_reminders(second_email)

    reminders = db.query(models.ReminderMemory).all()
    calendar_entries = (
        db.query(models.CalendarEvent)
        .filter(models.CalendarEvent.source_type == models.CalendarEventSource.JANUS_REMINDER)
        .all()
    )

    assert len(reminders) == 1
    assert reminders[0].title == "Prepare quarterly review slides"
    assert normalize_timestamp(reminders[0].last_seen_at) == datetime(2026, 4, 6, 8, 0, tzinfo=UTC)
    assert len(calendar_entries) == 1
    assert calendar_entries[0].summary == "[Prep] Prepare quarterly review slides"
    db.close()


def test_planner_summary_includes_active_reminders_and_outlook():
    """Morning summary should include active reminders and a 6-week preparation outlook."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.PlannerAccount(
        name="Work Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="token",
        user_id=user.id,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    upcoming_event = models.CalendarEvent(
        planner_account_id=account.id,
        external_id="evt-1",
        summary="Annual planning offsite",
        start_time=datetime.now(UTC) + timedelta(days=21),
        end_time=datetime.now(UTC) + timedelta(days=21, hours=1),
        source_type=models.CalendarEventSource.PLANNER,
    )
    # "Today" is the UTC day, so place today's events in the time left before
    # UTC midnight; a fixed "+1h" falls on tomorrow late in the evening.
    now = datetime.now(UTC)
    left_today = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)) - now
    todays_planner_event = models.CalendarEvent(
        planner_account_id=account.id,
        external_id="evt-2",
        summary="Team standup",
        start_time=now + left_today / 4,
        end_time=now + left_today / 2,
        source_type=models.CalendarEventSource.PLANNER,
    )
    synthetic_today = models.CalendarEvent(
        planner_account_id=None,
        external_id="janus-reminder:test",
        summary="[Prep] Book travel for offsite",
        start_time=now + left_today / 2,
        end_time=now + left_today * 3 / 4,
        source_type=models.CalendarEventSource.JANUS_REMINDER,
    )
    reminder = models.ReminderMemory(
        kind=models.ReminderKind.PREPARATION,
        status=models.ReminderStatus.ACTIVE,
        title="Book travel for offsite",
        notes="Reserve train and hotel.",
        due_at=datetime.now(UTC) + timedelta(days=14),
        prepare_for_at=datetime.now(UTC) + timedelta(days=21),
        dedupe_key="prep-offsite-travel",
        source_email_id=_owned_source_email(db, user).id,
    )
    db.add_all([upcoming_event, todays_planner_event, synthetic_today, reminder])
    db.commit()

    summary = planner_service.PlannerService(db=db).get_todays_summary(user_id=user.id)
    context = planner_service.PlannerService(db=db).get_context_data(user_id=user.id)

    assert "Team standup" in summary
    assert "[Prep] Book travel for offsite" not in summary.split("\n📝 Active Tasks:")[0]
    assert "⏰ Active Reminders:" in summary
    assert "Book travel for offsite" in summary
    assert "🔭 Preparation Outlook (Next 6 Weeks):" in summary
    assert "Annual planning offsite" in summary
    assert context["calendar"] == context["calendar_today"]
    assert [event["summary"] for event in context["calendar_today"]] == ["Team standup"]
    assert context["active_reminders"][0]["title"] == "Book travel for offsite"
    assert [event["summary"] for event in context["calendar_upcoming"]] == [
        "Team standup",
        "Annual planning offsite",
    ]
    db.close()


def test_planner_context_without_resolved_user_hides_imported_planner_data(monkeypatch):
    """Anonymous planner context reads must not expose imported planner data."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    account = models.PlannerAccount(
        name="Private Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        token="token",
        user_id=user.id,
        is_active=True,
    )
    db.add(account)
    db.flush()

    start_time = datetime.now(UTC) + timedelta(hours=1)
    db.add_all(
        [
            models.TaskRecord(
                planner_account_id=account.id,
                external_id="private-task",
                title="Private imported task",
                status=models.TaskStatus.TODO,
            ),
            models.CalendarEvent(
                planner_account_id=account.id,
                external_id="private-event",
                summary="Private imported event",
                start_time=start_time,
                end_time=start_time + timedelta(hours=1),
                source_type=models.CalendarEventSource.PLANNER,
            ),
        ]
    )
    db.commit()

    monkeypatch.setattr(planner_service.topic_service.TopicService, "get_default_user", lambda self: None)

    payload = planner_service.PlannerService(db=db).get_briefing_payload()
    context = planner_service.PlannerService(db=db).get_context_data()

    assert "Private imported event" not in payload["summary"]
    assert "Private imported task" not in payload["summary"]
    assert context["calendar"] == []
    assert context["calendar_today"] == []
    assert context["tasks"] == []
    db.close()


def test_briefing_payload_prefers_unbriefed_topics_for_context():
    """Briefing payloads should foreground durable topics that have not been briefed recently."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    now = datetime.now(UTC)
    db.add_all(
        [
            user,
            models.Settings(topic_tracking_available=True, topic_tracking_max_context_topics=2),
        ]
    )
    db.commit()
    db.refresh(user)
    db.add(
        models.TopicTrackingPreference(
            user_id=user.id,
            enabled=True,
            chat_context_enabled=True,
            max_active_topics=5,
        )
    )
    db.add_all(
        [
            models.TrackedTopic(
                user_id=user.id,
                slug="hiring-pipeline",
                title="Hiring pipeline",
                summary="Interview coordination and offer planning.",
                status=models.TopicStatus.ACTIVE,
                pinned=True,
                origin=models.TopicOrigin.AUTO,
                confidence=models.ConfidenceLevel.HIGH,
                first_seen_at=now - timedelta(days=3),
                last_seen_at=now - timedelta(hours=1),
                last_briefed_at=None,
            ),
            models.TrackedTopic(
                user_id=user.id,
                slug="incident-review",
                title="Incident review",
                summary="Postmortem follow-up.",
                status=models.TopicStatus.ACTIVE,
                pinned=False,
                origin=models.TopicOrigin.AUTO,
                confidence=models.ConfidenceLevel.MEDIUM,
                first_seen_at=now - timedelta(days=4),
                last_seen_at=now - timedelta(hours=2),
                last_briefed_at=now - timedelta(minutes=30),
            ),
            models.TrackedTopic(
                user_id=user.id,
                slug="travel-plan",
                title="Travel plan",
                summary="Offsite booking tasks.",
                status=models.TopicStatus.ACTIVE,
                pinned=False,
                origin=models.TopicOrigin.AUTO,
                confidence=models.ConfidenceLevel.MEDIUM,
                first_seen_at=now - timedelta(days=2),
                last_seen_at=now - timedelta(hours=3),
                last_briefed_at=now - timedelta(days=2),
            ),
        ]
    )
    db.commit()

    payload = planner_service.PlannerService(db=db).get_briefing_payload(user_id=user.id)
    context = planner_service.PlannerService(db=db).get_context_data(user_id=user.id)

    assert "🧭 Topic Threads:" in payload["summary"]
    assert "Hiring pipeline" in payload["summary"]
    assert payload["topic_ids"][:2] == [1, 3]
    assert [topic["title"] for topic in context["active_topics"]] == ["Hiring pipeline", "Travel plan"]
    db.close()


def test_briefing_payload_formats_topic_months_with_stable_english_abbreviations(monkeypatch):
    """Topic summary metadata should use locale-independent English month abbreviations."""
    fixed_now = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    monkeypatch.setattr(planner_service, "datetime", FrozenDateTime)

    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all(
        [
            user,
            models.Settings(topic_tracking_available=True, topic_tracking_max_context_topics=2),
        ]
    )
    db.commit()
    db.refresh(user)
    db.add(
        models.TopicTrackingPreference(
            user_id=user.id,
            enabled=True,
            chat_context_enabled=True,
            max_active_topics=5,
        )
    )
    db.add(
        models.TrackedTopic(
            user_id=user.id,
            slug="release-prep",
            title="Release prep",
            summary="Coordinate the August release.",
            status=models.TopicStatus.ACTIVE,
            pinned=False,
            origin=models.TopicOrigin.AUTO,
            confidence=models.ConfidenceLevel.HIGH,
            first_seen_at=fixed_now - timedelta(days=5),
            last_seen_at=datetime(2026, 8, 5, 11, 0, tzinfo=UTC),
            last_briefed_at=None,
        )
    )
    db.commit()

    payload = planner_service.PlannerService(db=db).get_briefing_payload(user_id=user.id)

    assert "updated Aug 05" in payload["summary"]
    db.close()


def test_get_active_reminders_filters_stale_entries_without_mutating_status():
    """Read paths should hide stale reminders without mutating reminder state."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash")
    db.add(user)
    db.commit()
    stale_due_at = datetime(2026, 4, 1, 9, 0, tzinfo=UTC)
    reminder = models.ReminderMemory(
        source_email_id=_owned_source_email(db, user).id,
        kind=models.ReminderKind.EVENT,
        status=models.ReminderStatus.ACTIVE,
        title="Old reminder",
        due_at=stale_due_at,
        dedupe_key="stale-reminder",
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    service = reminder_service.ReminderService(db)
    assert [row.id for row in service.get_active_reminders(user.id, now=stale_due_at)] == [reminder.id]
    active = service.get_active_reminders(user.id, now=stale_due_at + timedelta(days=3))

    db.refresh(reminder)
    assert active == []
    assert reminder.status == models.ReminderStatus.ACTIVE

    service.expire_stale_reminders(now=stale_due_at + timedelta(days=3))
    db.commit()
    db.refresh(reminder)
    assert reminder.status == models.ReminderStatus.EXPIRED
    db.close()
