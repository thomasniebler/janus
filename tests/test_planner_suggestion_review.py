"""Planner suggestion dedupe and bulk review: service, API, Janus tools, and the mail run's context."""

from datetime import UTC, datetime, timedelta

import pytest

from janus import builtin_triggers, models, security
from janus.agent_toolboxes.planner_suggestions import (
    build_planner_suggestion_read_tools,
    build_planner_suggestion_review_tools,
)
from janus.agent_toolboxes.shared import ToolContext
from janus.services import planner_suggestion_review as review
from janus.services.local_planner_service import LocalPlannerService


def _seed(db):
    user = models.User(username="owner", hashed_password="hash", is_admin=True)
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Work", email="owner@example.com", password="secret", user_id=user.id)
    db.add(account)
    db.flush()
    emails = []
    for index in range(3):
        email = models.EmailRecord(
            account_id=account.id,
            message_id=f"m-{index}",
            sender="boss@example.com",
            subject=f"Mail {index}",
            priority=models.Priority.IMPORTANT,
            received_at=datetime(2026, 11, 20, 9, index, tzinfo=UTC),
        )
        db.add(email)
        emails.append(email)
    db.commit()
    return user, emails


def _suggest(service, email, user, **fields):
    payload = {"suggestion_type": "task", "title": "Finish board deck", **fields}
    return service.record_email_suggestions(email, [payload], user_id=user.id)[0]


def _pending(db):
    return db.query(models.PlannerSuggestion).filter_by(status=models.PlannerSuggestionStatus.PENDING).all()


def test_reworded_titles_and_keys_merge_into_one_pending_suggestion(db):
    """The model words a suggestion anew per email; case, punctuation and spacing must not split it."""
    user, emails = _seed(db)
    service = LocalPlannerService(db)

    _suggest(service, emails[0], user, title="Finish board deck", dedupe_hint="board-deck")
    _suggest(service, emails[1], user, title="finish  the board deck!", dedupe_hint="Board Deck")
    _suggest(service, emails[2], user, title="Finish board-deck.", dedupe_hint="another-key")

    rows = _pending(db)
    assert len(rows) == 1
    assert rows[0].source_email_id == emails[2].id


def test_a_dismissed_suggestion_is_not_proposed_again_for_the_same_day(db):
    """Dismissing must stick: re-processing mail (or a retried run) cannot bring it back."""
    user, emails = _seed(db)
    service = LocalPlannerService(db)
    due = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    first = _suggest(service, emails[0], user, due_at=due.isoformat())
    service.dismiss_suggestion(user.id, first["id"])

    again = _suggest(service, emails[1], user, title="FINISH BOARD DECK", due_at=due.isoformat())
    assert again["id"] == first["id"] and again["status"] == "dismissed"
    assert _pending(db) == []

    next_month = _suggest(service, emails[2], user, due_at=(due + timedelta(days=31)).isoformat())
    assert next_month["status"] == "pending" and next_month["id"] != first["id"]


def test_bulk_dismiss_needs_a_filter_or_all_pending(db):
    """An empty call never clears the whole list by accident."""
    user, emails = _seed(db)
    service = LocalPlannerService(db)
    _suggest(service, emails[0], user, title="Book flights")
    _suggest(service, emails[1], user, title="Renew passport")
    _suggest(service, emails[2], user, suggestion_type="event", title="Team offsite", start_time="2026-12-03T09:00:00")

    with pytest.raises(ValueError):
        review.dismiss_suggestions(db, user.id)
    assert len(review.dismiss_suggestions(db, user.id, text="passport")) == 1
    assert len(review.dismiss_suggestions(db, user.id, suggestion_type=models.PlannerSuggestionType.EVENT)) == 1
    assert len(review.dismiss_suggestions(db, user.id, all_pending=True)) == 1
    assert _pending(db) == []


def test_duplicate_cleanup_keeps_the_newest_of_each_group_and_restore_undoes_it(db):
    """Legacy duplicates (from before the stronger dedupe) collapse to their newest copy."""
    user, emails = _seed(db)
    base = datetime(2026, 11, 20, 9, 0)
    for index, title in enumerate(["Pay invoice", "pay invoice.", "Pay  Invoice", "Call plumber"]):
        db.add(
            models.PlannerSuggestion(
                user_id=user.id,
                suggestion_type=models.PlannerSuggestionType.TASK,
                title=title,
                created_at=base + timedelta(minutes=index),
            )
        )
    db.commit()

    groups = review.duplicate_suggestion_groups(db, user.id)
    assert [len(group) for group in groups] == [3]
    result = review.dismiss_duplicate_suggestions(db, user.id)
    assert result["groups"] == 1 and len(result["dismissed_ids"]) == 2
    assert sorted(row.title for row in _pending(db)) == ["Call plumber", "Pay  Invoice"]

    assert sorted(review.restore_suggestions(db, user.id, result["dismissed_ids"])) == sorted(result["dismissed_ids"])
    assert len(_pending(db)) == 4


def test_janus_tools_list_and_dismiss_only_the_owners_suggestions(db):
    """The chat tools are owner-scoped and report what they changed."""
    user, emails = _seed(db)
    other = models.User(username="other", hashed_password="hash")
    db.add(other)
    db.commit()
    db.add(models.PlannerSuggestion(user_id=other.id, suggestion_type=models.PlannerSuggestionType.TASK, title="X"))
    db.commit()
    service = LocalPlannerService(db)
    _suggest(service, emails[0], user, title="Book flights")
    _suggest(service, emails[1], user, title="Book flights!")
    context = ToolContext(db=db, user=user)
    read = {tool.name: tool for tool in build_planner_suggestion_read_tools(context)}
    review = {tool.name: tool for tool in build_planner_suggestion_review_tools(context)}

    listed = read["list_planner_suggestions"].invoke({})
    assert listed["total"] == 1
    dismissed = review["dismiss_planner_suggestions"].invoke({"all_pending": True})
    assert dismissed["dismissed"] == 1
    assert db.query(models.PlannerSuggestion).filter_by(user_id=other.id).one().status.value == "pending"


def test_mail_run_context_lists_pending_suggestions(db):
    """The mail run sees what already awaits review, so it updates instead of re-proposing."""
    user, emails = _seed(db)
    assert builtin_triggers._prepare_mail(db, user, None).context is None
    _suggest(LocalPlannerService(db), emails[0], user, title="Book flights", dedupe_hint="flights-dec")

    context = builtin_triggers._prepare_mail(db, user, None).context
    assert "Book flights" in context and "dedupe_hint=flights-dec" in context
    assert "<prepared_context" in context


@pytest.fixture
def auth_client(client, db):
    """A logged-in client."""
    db.add(models.User(username="admin", hashed_password=security.security_service.get_password_hash("admin")))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_bulk_api_dismisses_selected_duplicates_and_all(auth_client, db):
    """The dashboard's bulk buttons: selected ids, duplicates, everything."""
    user = db.query(models.User).filter_by(username="admin").one()
    for title in ["A", "a.", "B", "C"]:
        db.add(
            models.PlannerSuggestion(user_id=user.id, suggestion_type=models.PlannerSuggestionType.TASK, title=title)
        )
    db.commit()

    assert auth_client.post("/api/planner/suggestions/dismiss", json={}).status_code == 422
    assert auth_client.get("/api/planner/suggestions/duplicates").json() == {"groups": 1, "duplicates": 1}
    assert len(auth_client.post("/api/planner/suggestions/dismiss-duplicates").json()["dismissed_ids"]) == 1
    ids = [row["id"] for row in auth_client.get("/api/planner/suggestions").json()]
    assert (
        auth_client.post("/api/planner/suggestions/dismiss", json={"suggestion_ids": ids[:1]}).json()["dismissed_ids"]
        == ids[:1]
    )
    assert (
        len(auth_client.post("/api/planner/suggestions/dismiss", json={"all_pending": True}).json()["dismissed_ids"])
        == 2
    )
    assert auth_client.get("/api/planner/suggestions").json() == []
