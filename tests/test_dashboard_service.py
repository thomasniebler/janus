"""Tests for DashboardService briefing email context."""

from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, models
from janus.services.dashboard_service import DashboardService


def test_build_briefing_email_section_excludes_candidate_topics(monkeypatch):
    """The briefing email section lists emails as delimited, untrusted content, without candidate topics."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        user = models.User(username="owner", hashed_password="hash", is_admin=True)
        db.add_all([user, models.Settings()])
        db.commit()
        db.refresh(user)

        account = models.EmailAccount(name="Work", email="work@example.com", password="secret", user_id=user.id)
        db.add(account)
        db.commit()
        db.refresh(account)

        email = models.EmailRecord(
            account_id=account.id,
            message_id="briefing-1",
            sender="sender@example.com",
            subject="Briefing subject",
            snippet="Briefing body",
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

        section = DashboardService(db)._build_briefing_email_section(
            user,
            models.BriefingEmailWindow.LAST_24_HOURS,
        )

        assert section is not None
        assert 'subject="Briefing subject"' in section
        assert "<email_content " in section
        assert "Candidate topic" not in section
    finally:
        db.close()


def test_tasks_widget_nests_subtasks_under_their_parent(db):
    """JAN-317: the tasks widget returns the parent/subtask tree, not a flat list of siblings.

    Before the fix, a parent task used as a category ("Work") and each of its
    subtasks came back as independent rows, so the dashboard rendered the
    category as one more task and lost the subtask grouping.
    """
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    imported_account = models.PlannerAccount(
        name="Jira", provider=models.Provider.JIRA, token="encrypted-token", user_id=user.id
    )
    db.add(imported_account)
    db.flush()
    db.add(
        models.TaskRecord(
            planner_account_id=imported_account.id,
            external_id="JAN-1",
            title="Imported issue",
            status=models.TaskStatus.TODO,
        )
    )
    category = models.PlannerTask(user_id=user.id, title="Work", status=models.TaskStatus.TODO)
    done_category = models.PlannerTask(user_id=user.id, title="Archive", status=models.TaskStatus.DONE)
    db.add_all([category, done_category])
    db.flush()
    db.add_all(
        [
            models.PlannerTask(user_id=user.id, parent_task_id=category.id, title="Write report", sort_order=0),
            models.PlannerTask(
                user_id=user.id,
                parent_task_id=category.id,
                title="Finished subtask",
                status=models.TaskStatus.DONE,
                sort_order=1,
            ),
            models.PlannerTask(user_id=user.id, parent_task_id=done_category.id, title="Leftover", sort_order=0),
            models.PlannerTask(user_id=user.id, title="Standalone task"),
        ]
    )
    db.commit()

    tasks = DashboardService(db).get_tasks_widget_data(user)
    by_title = {task["title"]: task for task in tasks}

    # Subtasks are nested, never returned as top-level rows.
    assert set(by_title) == {"Work", "Archive", "Standalone task", "Imported issue"}
    work = by_title["Work"]
    assert [child["title"] for child in work["children"]] == ["Write report"]
    assert work["child_count"] == 1
    assert work["open_child_count"] == 1
    # A completed parent is kept only as the grouping row of its open subtasks.
    assert [child["title"] for child in by_title["Archive"]["children"]] == ["Leftover"]
    assert by_title["Standalone task"]["children"] == []
    assert by_title["Standalone task"]["child_count"] == 0
    # Imported tasks stay flat, read-only roots after the local tree.
    assert tasks[-1]["title"] == "Imported issue"
    assert tasks[-1]["read_only"] is True
    assert tasks[-1].get("children", []) == []
