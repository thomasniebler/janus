"""Tests for email processing edge cases."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, llm_service, models, processor, reminder_service
from janus.schemas.character import CharacterProfilePayload
from janus.services import topic_service
from janus.services.character_service import save_character_profile


def _make_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    return testing_session_local


def test_process_email_rolls_back_failed_reminder_capture(monkeypatch):
    """Reminder capture failures should not leave the shared session unusable."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    owner = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=owner.id)
    db.add(account)
    db.commit()
    db.refresh(account)

    monkeypatch.setattr(processor.EmailProcessor, "_evaluate_rules", lambda self, email_data: models.Priority.IMPORTANT)

    class FailingReminderService:
        def __init__(self, db):
            self.db = db

        def capture_email_reminders(self, email_record):
            self.db.add(
                models.ReminderMemory(
                    source_email_id=email_record.id,
                    kind=models.ReminderKind.EVENT,
                    status=models.ReminderStatus.ACTIVE,
                    title="One",
                    due_at=datetime(2026, 4, 10, tzinfo=UTC),
                    dedupe_key="duplicate-reminder",
                )
            )
            self.db.add(
                models.ReminderMemory(
                    source_email_id=email_record.id,
                    kind=models.ReminderKind.EVENT,
                    status=models.ReminderStatus.ACTIVE,
                    title="Two",
                    due_at=datetime(2026, 4, 11, tzinfo=UTC),
                    dedupe_key="duplicate-reminder",
                )
            )
            self.db.flush()

    monkeypatch.setattr(reminder_service, "ReminderService", FailingReminderService)

    email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-1",
            "account_id": account.id,
            "subject": "Plan the trip",
            "sender": "boss@example.com",
            "snippet": "Please arrange travel.",
            "received_at": datetime(2026, 4, 6, 8, 0, tzinfo=UTC),
        }
    )

    assert email.message_id == "msg-1"
    assert db.query(models.EmailRecord).count() == 1
    assert db.query(models.ReminderMemory).count() == 0

    second_email = models.EmailRecord(
        account_id=account.id,
        message_id="msg-2",
        subject="Follow-up",
        sender="boss@example.com",
        snippet="Still usable.",
        priority=models.Priority.DIGEST,
    )
    db.add(second_email)
    db.commit()

    assert db.query(models.EmailRecord).count() == 2
    db.close()


def test_process_email_persists_received_at_as_naive_utc(monkeypatch):
    """Processor should convert aware timestamps to naive UTC before persistence."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    owner = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=owner.id)
    db.add(account)
    db.commit()
    db.refresh(account)

    monkeypatch.setattr(processor.EmailProcessor, "_evaluate_rules", lambda self, email_data: models.Priority.DIGEST)

    email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-aware-ts",
            "account_id": account.id,
            "subject": "Timezone test",
            "sender": "ops@example.com",
            "snippet": "Timestamp includes offset.",
            "received_at": datetime(2026, 4, 6, 8, 0, tzinfo=timezone(timedelta(hours=-5))),
        }
    )

    assert email.received_at == datetime(2026, 4, 6, 13, 0)
    assert email.received_at.tzinfo is None
    db.close()


def test_process_email_falls_back_to_utc_now_for_missing_or_invalid_received_at(monkeypatch):
    """Processor should apply an explicit UTC fallback when source timestamps are unusable."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    owner = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(owner)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=owner.id)
    db.add(account)
    db.commit()
    db.refresh(account)

    monkeypatch.setattr(processor.EmailProcessor, "_evaluate_rules", lambda self, email_data: models.Priority.DIGEST)
    fallback_now = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(processor.time_utils, "utcnow", lambda: fallback_now)

    invalid_timestamp_email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-invalid-ts",
            "account_id": account.id,
            "subject": "Invalid time",
            "sender": "ops@example.com",
            "snippet": "Timestamp is malformed.",
            "received_at": "not-a-timestamp",
        }
    )
    missing_timestamp_email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-missing-ts",
            "account_id": account.id,
            "subject": "Missing time",
            "sender": "ops@example.com",
            "snippet": "Timestamp is absent.",
        }
    )

    expected = fallback_now.replace(tzinfo=None)
    assert invalid_timestamp_email.received_at == expected
    assert missing_timestamp_email.received_at == expected
    assert invalid_timestamp_email.received_at.tzinfo is None
    assert missing_timestamp_email.received_at.tzinfo is None
    db.close()


def test_process_email_rolls_back_failed_topic_capture(monkeypatch):
    """Topic capture failures should not undo the stored email record."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    db.add_all([user, account])
    db.commit()
    db.refresh(account)

    monkeypatch.setattr(
        processor.EmailProcessor,
        "_evaluate_rules",
        lambda self, email_data: models.Priority.DIGEST,
    )

    def raise_topic_failure(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(topic_service.TopicService, "capture_email_topics", raise_topic_failure)

    email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-topic-failure",
            "account_id": account.id,
            "subject": "Hiring update",
            "sender": "recruiter@example.com",
            "snippet": "Interview loop is moving ahead.",
            "received_at": datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
        }
    )

    assert email.message_id == "msg-topic-failure"
    assert db.query(models.EmailRecord).count() == 1
    assert db.query(models.TrackedTopic).count() == 0
    db.close()


def test_process_email_passes_character_guidance_to_classifier(monkeypatch):
    """LLM classification is guided by the owner's character constitution plus the flat skill library (JAN-201)."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="triage-owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    skill = models.AgentSkill(
        name="Release radar",
        description="Highlights release-sensitive work.",
        content="Treat deployment regressions and launch blockers as urgent.",
    )
    db.add_all([account, skill, models.Settings()])
    db.commit()
    db.refresh(account)
    save_character_profile(
        db,
        user.id,
        CharacterProfilePayload(
            name="Triage Janus",
            identity="Prioritizes founder and launch-critical mail.",
            core_values=["Protect focus"],
            non_negotiables=["Never hide urgent mail"],
        ),
    )

    captured: dict[str, object] = {}

    class FakeLLM:
        def classify_email(self, email_data, planner_context=None, *, system_prompt=None):
            captured["system_prompt"] = system_prompt
            return models.Priority.IMPORTANT, "Launch blocker", models.ConfidenceLevel.HIGH

    monkeypatch.setattr(processor.EmailProcessor, "_evaluate_rules", lambda self, email_data: None)
    monkeypatch.setattr(processor.EmailProcessor, "_get_llm", lambda self: FakeLLM())

    email = processor.EmailProcessor(db).process_email(
        {
            "message_id": "msg-agent-guidance",
            "account_id": account.id,
            "subject": "Production launch issue",
            "sender": "ceo@example.com",
            "snippet": "The release is blocked until we fix checkout.",
            "received_at": datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
        }
    )

    assert email.priority == models.Priority.IMPORTANT
    guidance = str(captured["system_prompt"])
    assert "# Character constitution: Triage Janus" in guidance
    assert "Prioritizes founder and launch-critical mail." in guidance
    assert "Never hide urgent mail" in guidance
    assert "Release radar" in guidance
    assert "launch blockers as urgent" in guidance
    db.close()


def test_process_email_routes_llm_classification_through_runtime_adapter(monkeypatch):
    """Ingestion's LLM fallback authorizes a runtime step via the shared adapter (JAN-164)."""
    testing_session_local = _make_test_session()
    # event_logger.log() always opens its own session via database.SessionLocal,
    # independent of the db session this test uses directly; point it at the
    # same in-memory engine so the denial event this test asserts on actually
    # persists instead of silently failing against a schema-less default engine.
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    db = testing_session_local()
    user = models.User(username="ingest-owner", hashed_password="not-a-password", is_admin=False)
    db.add(user)
    db.flush()
    account = models.EmailAccount(
        name="Work",
        email="work@example.com",
        password="secret",
        user_id=user.id,
    )
    db.add(account)
    db.commit()
    db.refresh(user)
    db.refresh(account)
    db.add(models.Settings())
    db.commit()

    class FakeLLM:
        def classify_email(self, email_data, planner_context=None, *, system_prompt=None):
            return models.Priority.IMPORTANT, "Launch blocker", models.ConfidenceLevel.HIGH

    class FakePlanner:
        def get_context_data(self):
            return {"calendar": [], "tasks": []}

    monkeypatch.setattr(processor.EmailProcessor, "_evaluate_rules", lambda self, email_data: None)
    monkeypatch.setattr(processor.EmailProcessor, "_get_llm", lambda self: FakeLLM())
    monkeypatch.setattr(processor.EmailProcessor, "_get_planner", lambda self: FakePlanner())

    proc = processor.EmailProcessor(db)
    email = proc.process_email(
        {
            "message_id": "msg-runtime-adapter",
            "account_id": account.id,
            "subject": "Production launch issue",
            "sender": "ceo@example.com",
            "snippet": "The release is blocked until we fix checkout.",
            "received_at": datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
        }
    )

    assert email.priority == models.Priority.IMPORTANT

    events = db.query(models.Event).filter(models.Event.source == "EmailProcessor").all()
    denial_events = [e for e in events if "denied" in (e.message or "").lower()]
    assert denial_events, "expected a logged event about denied ingestion tools"
    details = json.loads(denial_events[0].details)
    denied_ids = {entry["tool_id"] for entry in details["denied"]}
    assert "calendar" in denied_ids
    assert "jira" in denied_ids
    assert details["task_id"] == "ingestion-msg-runtime-adapter"
    db.close()


def test_prepare_ingestion_runtime_step_returns_none_without_a_resolvable_user():
    """No owning user means no runtime step is authorized (and nothing raises)."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    proc = processor.EmailProcessor(db)

    prepared = proc._prepare_ingestion_runtime_step({"message_id": "msg-anon", "subject": "Hi"}, None)

    assert prepared is None
    db.close()


def test_mail_ingestion_guidance_query_is_cached_per_processor():
    """Character-sourced guidance is loaded once per user per processor instance."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="cache-owner", hashed_password="hash", is_admin=True)
    skill = models.AgentSkill(
        name="Release radar",
        description="Highlights release-sensitive work.",
        content="Treat deployment regressions and launch blockers as urgent.",
    )
    db.add_all([user, skill])
    db.commit()
    db.refresh(user)

    proc = processor.EmailProcessor(db)
    matching_statements: list[str] = []

    def _capture_sql(conn, cursor, statement, parameters, context, executemany):
        del conn, cursor, parameters, context, executemany
        if "FROM character_profiles" in statement:
            matching_statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", _capture_sql)
    try:
        first_guidance = proc._get_mail_ingestion_guidance(user.id)
        first_call_queries = len(matching_statements)
        second_guidance = proc._get_mail_ingestion_guidance(user.id)
    finally:
        event.remove(db.bind, "before_cursor_execute", _capture_sql)

    assert first_guidance == second_guidance
    assert first_guidance is not None
    # No persisted profile yet: the safe default character is rendered.
    assert "# Character constitution: Janus" in first_guidance
    assert "launch blockers as urgent" in first_guidance
    # The first call reads the profile (constitution, and the state envelope, JAN-301); the second reads nothing.
    assert first_call_queries >= 1
    assert len(matching_statements) == first_call_queries
    assert proc._get_mail_ingestion_guidance(None) is None
    db.close()


def test_capture_email_topics_upserts_existing_topic(monkeypatch):
    """Repeated related emails should refresh one tracked topic instead of duplicating it."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    db.add_all([user, account, models.Settings()])
    db.commit()
    db.refresh(user)
    db.refresh(account)

    first_email = models.EmailRecord(
        account_id=account.id,
        message_id="topic-1",
        sender="manager@example.com",
        subject="Hiring pipeline",
        snippet="We need to schedule the next round of interviews.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
    )
    second_email = models.EmailRecord(
        account_id=account.id,
        message_id="topic-2",
        sender="manager@example.com",
        subject="Hiring pipeline",
        snippet="Candidate feedback and offer planning are next.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 4, 9, 9, 0, tzinfo=UTC),
    )
    db.add_all([first_email, second_email])
    db.commit()
    db.refresh(first_email)
    db.refresh(second_email)

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            return [
                {
                    "title": "Hiring pipeline",
                    "summary": "Interview coordination and offer planning.",
                    "confidence": "high",
                    "status_hint": "active",
                    "dedupe_hint": "hiring-pipeline",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    service = topic_service.TopicService(db)
    service.capture_email_topics(first_email, user_id=user.id, planner_context={})
    service.capture_email_topics(second_email, user_id=user.id, planner_context={})
    db.commit()

    topics = db.query(models.TrackedTopic).all()
    evidence = db.query(models.TopicEvidence).order_by(models.TopicEvidence.source_id.asc()).all()

    assert len(topics) == 1
    assert topics[0].title == "Hiring pipeline"
    assert topics[0].slug == "hiring-pipeline"
    assert topics[0].last_seen_at == datetime(2026, 4, 9, 9, 0)
    assert len(evidence) == 2
    assert [row.source_id for row in evidence] == [first_email.id, second_email.id]
    db.close()


def test_capture_email_topics_matches_manual_topic_marker_keywords(monkeypatch):
    """Manual topics with marker keywords should attach matching incoming mail without LLM extraction."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    db.add_all([user, account, models.Settings()])
    db.commit()
    db.refresh(user)
    db.refresh(account)

    manual_topic = models.TrackedTopic(
        user_id=user.id,
        slug="home-blog",
        title="Home blog",
        summary="Posts, deployments, and workflow updates for the home blog.",
        marker_keywords=json.dumps(["example.com", "deploy preview"]),
        status=models.TopicStatus.ACTIVE,
        user_confirmed=True,
        origin=models.TopicOrigin.MANUAL,
        first_seen_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
    )
    email = models.EmailRecord(
        account_id=account.id,
        message_id="manual-marker-1",
        sender="actions@github.com",
        subject="Deploy preview for example.com",
        snippet="The deploy preview succeeded for the home blog site.",
        priority=models.Priority.DIGEST,
        received_at=datetime(2026, 4, 9, 9, 0, tzinfo=UTC),
    )
    db.add_all([manual_topic, email])
    db.commit()
    db.refresh(manual_topic)
    db.refresh(email)

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            return []

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    matched_topics = topic_service.TopicService(db).capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()
    db.refresh(manual_topic)

    evidence = (
        db.query(models.TopicEvidence)
        .filter(
            models.TopicEvidence.topic_id == manual_topic.id,
            models.TopicEvidence.source_type == models.TopicEvidenceSource.EMAIL,
            models.TopicEvidence.source_id == email.id,
        )
        .one()
    )

    assert [topic.id for topic in matched_topics] == [manual_topic.id]
    assert manual_topic.last_seen_at == datetime(2026, 4, 9, 9, 0)
    assert evidence.excerpt == "The deploy preview succeeded for the home blog site."
    db.close()


def test_get_or_create_preference_uses_configured_max_active_topics():
    """New topic-tracking preferences should inherit the configured active-topic limit."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all([user, models.Settings(topic_tracking_max_active_topics=25)])
    db.commit()
    db.refresh(user)

    preference = topic_service.TopicService(db).get_or_create_preference(user.id)

    assert preference.max_active_topics == 25
    db.close()


def test_capture_email_topics_keeps_last_seen_at_while_topic_is_snoozed(monkeypatch):
    """Refreshing a still-snoozed topic should not advance its last-seen timestamp."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    original_last_seen = datetime(2026, 4, 8, 9, 0, tzinfo=UTC).replace(tzinfo=None)
    db.add_all([user, account, models.Settings()])
    db.commit()
    db.refresh(user)
    db.refresh(account)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        summary="Interview coordination and offer planning.",
        status=models.TopicStatus.SNOOZED,
        user_confirmed=True,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
        first_seen_at=original_last_seen,
        last_seen_at=original_last_seen,
        snoozed_until=datetime(2099, 4, 12, 9, 0, tzinfo=UTC),
    )
    email = models.EmailRecord(
        account_id=account.id,
        message_id="topic-snoozed",
        sender="manager@example.com",
        subject="Hiring pipeline",
        snippet="Another update while this topic is snoozed.",
        priority=models.Priority.IMPORTANT,
        received_at=datetime(2026, 4, 9, 9, 0, tzinfo=UTC),
    )
    db.add_all([topic, email])
    db.commit()
    db.refresh(topic)
    db.refresh(email)

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            return [
                {
                    "title": "Hiring pipeline",
                    "summary": "Interview coordination and offer planning.",
                    "confidence": "high",
                    "status_hint": "active",
                    "dedupe_hint": "hiring-pipeline",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    service = topic_service.TopicService(db)
    service.capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()
    db.refresh(topic)

    assert topic.last_seen_at == original_last_seen
    db.close()


def test_update_topic_rejects_titles_without_alphanumeric_characters():
    """Renaming a topic to only separators should raise a normal validation error."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    with pytest.raises(ValueError, match="alphanumeric"):
        topic_service.TopicService(db).update_topic(user_id=user.id, topic_id=topic.id, title="!!! --- ???")

    db.close()


def test_update_topic_normalizes_repeated_separator_runs_in_slugs():
    """Slug generation should collapse repeated separator runs to a single hyphen."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    updated = topic_service.TopicService(db).update_topic(
        user_id=user.id,
        topic_id=topic.id,
        title="Hiring / Pipeline --- 2026",
    )

    assert updated.slug == "hiring-pipeline-2026"
    db.close()


def _make_topic_capture_fixture(db, *, settings=None):
    """Create a user and account for topic-capture tests."""
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
    db.add_all([user, account, settings or models.Settings()])
    db.commit()
    db.refresh(user)
    db.refresh(account)
    return user, account


def _add_topic_email(db, account, *, message_id, snippet, received_at):
    """Persist one important email that mentions the shared test topic."""
    email = models.EmailRecord(
        account_id=account.id,
        message_id=message_id,
        sender="manager@example.com",
        subject="Hiring pipeline",
        snippet=snippet,
        priority=models.Priority.IMPORTANT,
        received_at=received_at,
    )
    db.add(email)
    db.commit()
    db.refresh(email)
    return email


def _patch_hiring_pipeline_llm(monkeypatch):
    """Make topic extraction always propose the same hiring-pipeline candidate."""

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            return [
                {
                    "title": "Hiring pipeline",
                    "summary": "Interview coordination and offer planning.",
                    "confidence": "high",
                    "status_hint": "active",
                    "dedupe_hint": "hiring-pipeline",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)


def test_capture_email_topics_creates_candidate_from_a_single_email(monkeypatch):
    """One email is weak evidence, so a new auto topic must start as an unsurfaced candidate."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    email = _add_topic_email(
        db,
        account,
        message_id="candidate-1",
        snippet="We need to schedule the next round of interviews.",
        received_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
    )
    _patch_hiring_pipeline_llm(monkeypatch)

    service = topic_service.TopicService(db)
    service.capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()

    topic = db.query(models.TrackedTopic).one()

    assert topic.status == models.TopicStatus.CANDIDATE
    assert topic.origin == models.TopicOrigin.AUTO
    assert db.query(models.TopicEvidence).count() == 1
    assert service.build_active_topic_summary(user.id) == []
    assert service.build_context_topic_summary(user.id) == []
    assert [row["id"] for row in service.list_topics(user.id)] == [topic.id]
    db.close()


def test_capture_email_topics_promotes_candidate_after_enough_mentions(monkeypatch):
    """A candidate should graduate to active once distinct emails corroborate it."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(
        db, settings=models.Settings(topic_tracking_default_min_confirmation_mentions=3)
    )
    _patch_hiring_pipeline_llm(monkeypatch)
    service = topic_service.TopicService(db)

    statuses = []
    for index in range(3):
        email = _add_topic_email(
            db,
            account,
            message_id=f"promote-{index}",
            snippet=f"Interview loop update {index}.",
            received_at=datetime(2026, 4, 8 + index, 9, 0, tzinfo=UTC),
        )
        service.capture_email_topics(email, user_id=user.id, planner_context={})
        db.commit()
        statuses.append(db.query(models.TrackedTopic).one().status)

    topic = db.query(models.TrackedTopic).one()

    assert statuses == [
        models.TopicStatus.CANDIDATE,
        models.TopicStatus.CANDIDATE,
        models.TopicStatus.ACTIVE,
    ]
    assert [row["id"] for row in service.build_active_topic_summary(user.id)] == [topic.id]
    assert [row["id"] for row in service.build_context_topic_summary(user.id)] == [topic.id]
    db.close()


def test_capture_email_topics_does_not_promote_on_repeated_evidence_from_one_email(monkeypatch):
    """Re-processing the same email must not count as a second corroborating mention."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    email = _add_topic_email(
        db,
        account,
        message_id="repeat-1",
        snippet="We need to schedule the next round of interviews.",
        received_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
    )
    _patch_hiring_pipeline_llm(monkeypatch)

    service = topic_service.TopicService(db)
    service.capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()
    service.capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()

    topic = db.query(models.TrackedTopic).one()

    assert topic.status == models.TopicStatus.CANDIDATE
    assert db.query(models.TopicEvidence).count() == 1
    db.close()


def test_capture_email_topics_leaves_promoted_topics_active(monkeypatch):
    """Later mail on an already promoted auto topic must not demote it back to candidate."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    _patch_hiring_pipeline_llm(monkeypatch)
    service = topic_service.TopicService(db)

    for index in range(3):
        email = _add_topic_email(
            db,
            account,
            message_id=f"stable-{index}",
            snippet=f"Interview loop update {index}.",
            received_at=datetime(2026, 4, 8 + index, 9, 0, tzinfo=UTC),
        )
        service.capture_email_topics(email, user_id=user.id, planner_context={})
        db.commit()

    topic = db.query(models.TrackedTopic).one()

    assert topic.status == models.TopicStatus.ACTIVE
    assert db.query(models.TopicEvidence).count() == 3
    db.close()


def test_capture_email_topics_respects_user_confirmed_candidate_status(monkeypatch):
    """A user who deliberately parked a topic as a candidate should not see it auto-promoted."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.CANDIDATE,
        user_confirmed=True,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(topic)
    db.commit()
    _patch_hiring_pipeline_llm(monkeypatch)
    service = topic_service.TopicService(db)

    for index in range(3):
        email = _add_topic_email(
            db,
            account,
            message_id=f"confirmed-candidate-{index}",
            snippet=f"Interview loop update {index}.",
            received_at=datetime(2026, 4, 8 + index, 9, 0, tzinfo=UTC),
        )
        service.capture_email_topics(email, user_id=user.id, planner_context={})
        db.commit()

    db.refresh(topic)

    assert topic.status == models.TopicStatus.CANDIDATE
    db.close()


def test_capture_email_topics_keeps_manual_topics_active(monkeypatch):
    """Manual topics stay active regardless of how many emails have corroborated them."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    manual_topic = models.TrackedTopic(
        user_id=user.id,
        slug="home-blog",
        title="Home blog",
        summary="Posts and deployments for the home blog.",
        marker_keywords=json.dumps(["deploy preview"]),
        status=models.TopicStatus.ACTIVE,
        user_confirmed=True,
        origin=models.TopicOrigin.MANUAL,
    )
    db.add(manual_topic)
    db.commit()
    email = _add_topic_email(
        db,
        account,
        message_id="manual-1",
        snippet="The deploy preview succeeded.",
        received_at=datetime(2026, 4, 8, 9, 0, tzinfo=UTC),
    )

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            return []

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)

    topic_service.TopicService(db).capture_email_topics(email, user_id=user.id, planner_context={})
    db.commit()
    db.refresh(manual_topic)

    assert manual_topic.status == models.TopicStatus.ACTIVE
    db.close()


def test_create_topic_yields_an_active_topic_immediately():
    """Manual topic creation must not wait for corroborating mail."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all([user, models.Settings()])
    db.commit()
    db.refresh(user)

    topic = topic_service.TopicService(db).create_topic(
        user_id=user.id,
        title="Hiring pipeline",
        summary="Interview coordination.",
        marker_keywords=["interview"],
    )

    assert topic.status == models.TopicStatus.ACTIVE
    assert topic.user_confirmed is True
    db.close()


def test_create_topic_confirms_an_existing_candidate_topic():
    """Confirming an auto candidate through create_topic should promote it right away."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all([user, models.Settings()])
    db.commit()
    db.refresh(user)
    candidate = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.CANDIDATE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(candidate)
    db.commit()

    topic = topic_service.TopicService(db).create_topic(user_id=user.id, title="Hiring pipeline")

    assert topic.id == candidate.id
    assert topic.status == models.TopicStatus.ACTIVE
    db.close()


@pytest.mark.parametrize(
    "update_kwargs",
    [
        {"title": "Hiring loop"},
        {"summary": "Interview coordination."},
        {"pinned": True},
    ],
)
def test_update_topic_promotes_curated_candidates(update_kwargs):
    """Editing a candidate is itself confirmation that the topic is worth tracking."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.CANDIDATE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    updated = topic_service.TopicService(db).update_topic(user_id=user.id, topic_id=topic.id, **update_kwargs)

    assert updated.status == models.TopicStatus.ACTIVE
    assert updated.user_confirmed is True
    db.close()


def test_update_topic_honors_an_explicit_status_on_a_candidate():
    """An explicit status wins over the implicit candidate promotion."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="hiring-pipeline",
        title="Hiring pipeline",
        status=models.TopicStatus.CANDIDATE,
        origin=models.TopicOrigin.AUTO,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    updated = topic_service.TopicService(db).update_topic(
        user_id=user.id,
        topic_id=topic.id,
        status=models.TopicStatus.IGNORED,
    )

    assert updated.status == models.TopicStatus.IGNORED
    db.close()


def test_get_or_create_preference_uses_configured_min_confirmation_mentions():
    """New topic-tracking preferences should inherit the configured confirmation threshold."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all([user, models.Settings(topic_tracking_default_min_confirmation_mentions=4)])
    db.commit()
    db.refresh(user)

    preference = topic_service.TopicService(db).get_or_create_preference(user.id)

    assert preference.min_confirmation_mentions == 4
    db.close()


def test_default_min_confirmation_mentions_requires_a_second_email():
    """The shipped default should require corroboration from a second email."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add_all([user, models.Settings()])
    db.commit()
    db.refresh(user)

    preference = topic_service.TopicService(db).get_or_create_preference(user.id)

    assert preference.min_confirmation_mentions == 2
    db.close()


def test_capture_email_topics_feeds_candidates_back_into_extraction_context(monkeypatch):
    """Later mail must see watched topics and their dedupe hints, or they could never be corroborated."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    user, account = _make_topic_capture_fixture(db)
    seen_contexts = []

    class FakeLLMService:
        def __init__(self, db=None):
            self.db = db

        def extract_email_topics(self, email_data, topic_context):
            seen_contexts.append(topic_context)
            return [
                {
                    "title": "Hiring pipeline",
                    "summary": "Interview coordination and offer planning.",
                    "confidence": "high",
                    "dedupe_hint": "hiring-pipeline",
                }
            ]

    monkeypatch.setattr(llm_service, "LLMService", FakeLLMService)
    service = topic_service.TopicService(db)

    for index in range(2):
        email = _add_topic_email(
            db,
            account,
            message_id=f"context-{index}",
            snippet=f"Interview loop update {index}.",
            received_at=datetime(2026, 4, 8 + index, 9, 0, tzinfo=UTC),
        )
        service.capture_email_topics(email, user_id=user.id, planner_context={})
        db.commit()

    assert seen_contexts[0]["active_topics"] == []
    assert seen_contexts[0]["watched_topics"] == []
    assert [topic["dedupe_hint"] for topic in seen_contexts[1]["watched_topics"]] == ["hiring-pipeline"]
    assert seen_contexts[1]["active_topics"] == []
    db.close()
