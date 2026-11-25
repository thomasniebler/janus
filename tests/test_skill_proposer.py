"""Tests for skill suggestions from repeated tool sequences (JAN-407)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from janus import models, security, time_utils
from janus.services import skill_proposer


def _user(db, name="skill-user") -> models.User:
    user = models.User(username=name, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _turn(db, user, tools, *, title="Weekly report", days_ago=1):
    """One chat turn whose assistant message called ``tools`` in order."""
    when = time_utils.utcnow() - timedelta(days=days_ago)
    conversation = models.Conversation(user_id=user.id, title=title)
    db.add(conversation)
    db.flush()
    message = models.ConversationMessage(
        conversation_id=conversation.id, role=models.MessageRole.ASSISTANT, content="ok"
    )
    db.add(message)
    db.flush()
    for position, name in enumerate(tools):
        db.add(
            models.ConversationToolCall(
                message_id=message.id,
                position=position,
                call_id=f"c{position}",
                tool_name=name,
                phase="done",
                created_at=when,
            )
        )
    db.commit()


def test_a_sequence_in_three_turns_is_suggested_with_examples_and_a_brief(db):
    """Three turns with the same chain make one suggestion; its brief names the chain and the chats."""
    user = _user(db)
    for title in ("Report A", "Report B", "Report C"):
        _turn(db, user, ["search_mail", "read_email", "create_task"], title=title)
    (suggestion,) = skill_proposer.suggestions(db, user.id)
    assert suggestion.tools == ("search_mail", "read_email", "create_task")
    assert suggestion.turns == 3
    assert suggestion.examples == ["Report A", "Report B", "Report C"]
    assert "search_mail -> read_email -> create_task" in suggestion.brief


def test_two_turns_are_not_enough_and_old_turns_are_ignored(db):
    """Below the threshold, or outside the window, nothing is suggested."""
    user = _user(db)
    _turn(db, user, ["a", "b"])
    _turn(db, user, ["a", "b"])
    _turn(db, user, ["a", "b"], days_ago=skill_proposer.WINDOW_DAYS + 5)
    assert skill_proposer.suggestions(db, user.id) == []


def test_contained_patterns_collapse_into_the_longest(db):
    """A sub-chain that occurs exactly as often as its longer chain is not offered separately."""
    user = _user(db)
    for _ in range(3):
        _turn(db, user, ["x", "y", "z"])
    _turn(db, user, ["x", "y"])
    tools = [s.tools for s in skill_proposer.suggestions(db, user.id)]
    # x->y occurs in 4 turns (more than x->y->z), so both are offered; y->z occurs exactly as often, so it is folded.
    assert ("x", "y", "z") in tools and ("x", "y") in tools and ("y", "z") not in tools


def test_repeating_one_tool_is_not_a_routine(db):
    """The same tool twice in a row is not offered as a routine."""
    user = _user(db)
    for _ in range(3):
        _turn(db, user, ["search", "search"])
    assert skill_proposer.suggestions(db, user.id) == []


def test_a_call_recorded_in_several_phases_counts_once(db):
    """Rows sharing a call id (start, then done) are one call, so a -> a is never formed from them."""
    user = _user(db)
    for _ in range(3):
        _turn(db, user, ["a", "b"])
        message_id = db.query(models.ConversationMessage).order_by(models.ConversationMessage.id.desc()).first().id
        db.add(
            models.ConversationToolCall(message_id=message_id, position=5, call_id="c0", tool_name="a", phase="start")
        )
        db.commit()
    (suggestion,) = skill_proposer.suggestions(db, user.id)
    assert suggestion.tools == ("a", "b")


def test_other_users_turns_never_count(db):
    """Suggestions come from the user's own conversations only."""
    owner, other = _user(db, "owner"), _user(db, "other")
    for _ in range(3):
        _turn(db, other, ["a", "b"])
    assert skill_proposer.suggestions(db, owner.id) == []
    assert len(skill_proposer.suggestions(db, other.id)) == 1


def test_dismissed_suggestions_stay_hidden_for_the_cooldown(db):
    """A dismissed pattern is hidden, and comes back once the cooldown has passed."""
    user = _user(db)
    for _ in range(3):
        _turn(db, user, ["a", "b"])
    (suggestion,) = skill_proposer.suggestions(db, user.id)
    skill_proposer.dismiss(db, user.id, suggestion.key)
    db.commit()
    assert skill_proposer.suggestions(db, user.id) == []
    row = db.query(models.SkillSuggestionDismissal).one()
    row.dismissed_at = (time_utils.utcnow() - timedelta(days=skill_proposer.DISMISS_DAYS + 1)).replace(tzinfo=None)
    db.commit()
    assert [s.key for s in skill_proposer.suggestions(db, user.id)] == [suggestion.key]


# --- API -------------------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """A cookie-authenticated client with CSRF configured."""
    _user(db, "skill-api-user")
    response = client.post("/api/auth/login", json={"username": "skill-api-user", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_api_lists_and_dismisses_suggestions(auth_client, db):
    """GET lists the user's suggestions (not shadowed by /api/skills/{id}); POST dismiss hides one."""
    user = db.query(models.User).filter_by(username="skill-api-user").one()
    for _ in range(3):
        _turn(db, user, ["list_events", "draft_reply"])
    listed = auth_client.get("/api/skills/suggestions")
    assert listed.status_code == 200, listed.text
    (row,) = listed.json()
    assert row["tools"] == ["list_events", "draft_reply"] and row["turns"] == 3 and "list_events" in row["brief"]
    assert auth_client.post("/api/skills/suggestions/dismiss", json={"key": row["key"]}).status_code == 204
    assert auth_client.get("/api/skills/suggestions").json() == []
