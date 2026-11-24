"""Tests for the agent-facing mail rule and rule-suggestion toolbox."""

import pytest

from janus import models, security
from janus.agent_tools import build_tool_set


@pytest.fixture
def auth_client(client, db):
    """Return a client authenticated as an admin test user."""
    db.add(
        models.User(
            username="admin",
            hashed_password=security.security_service.get_password_hash("admin"),
            is_admin=True,
        )
    )
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


@pytest.fixture
def user(db, auth_client):
    """Return the authenticated admin user row."""
    return db.query(models.User).filter(models.User.username == "admin").one()


def _make_account(db, user, *, is_active: bool = True) -> models.EmailAccount:
    """Return the user's email account, persisting one on first use."""
    account = db.query(models.EmailAccount).filter(models.EmailAccount.user_id == user.id).first()
    if account is None:
        account = models.EmailAccount(
            name=f"Test {user.id}", email=f"test-{user.id}@test.com", password="hash", user_id=user.id
        )
        db.add(account)
    account.is_active = is_active
    db.commit()
    db.refresh(account)
    return account


def _rule_tools(db, user):
    """Build the tool set for a user with a connected mailbox, indexed by tool name."""
    _make_account(db, user)
    return {tool.name: tool for tool in build_tool_set(db, user)}


def _make_clarification(db, user, account, *, sender: str, message_id: str) -> models.EmailClarificationRequest:
    """Persist a pending learned rule suggestion for the given sender."""
    email = models.EmailRecord(
        account_id=account.id,
        message_id=message_id,
        subject="Subject",
        sender=sender,
        snippet="Snippet",
        priority=models.Priority.DIGEST,
    )
    db.add(email)
    db.flush()
    clarification = models.EmailClarificationRequest(
        user_id=user.id,
        email_id=email.id,
        previous_priority=models.Priority.DIGEST,
        requested_priority=models.Priority.IMPORTANT,
        suggested_rule_name=f"Messages from {sender}",
        suggested_rule_pattern=f"^{sender.replace('.', chr(92) + '.')}$",
        suggested_rule_field="sender",
        suggested_rule_priority=models.Priority.IMPORTANT,
        suggestion_confidence=models.ConfidenceLevel.HIGH,
        suggestion_reason="High confidence sender match.",
    )
    db.add(clarification)
    db.commit()
    db.refresh(clarification)
    return clarification


def _rule_shape(rule: models.Rule) -> dict:
    """Return the identity-independent shape of a rule row."""
    return {
        "name": rule.name,
        "field": rule.field,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }


def test_rule_tools_require_a_connected_mailbox(db, user):
    """The rules toolbox is only built once the user has an active email account."""
    assert "create_mail_rule" not in {tool.name for tool in build_tool_set(db, user)}

    assert "create_mail_rule" in _rule_tools(db, user)

    _make_account(db, user, is_active=False)
    assert "create_mail_rule" not in {tool.name for tool in build_tool_set(db, user)}


def test_create_mail_rule_persists_a_rule_the_rest_api_also_returns(db, user, auth_client):
    """An agent-authored rule is an ordinary rule row visible through the REST API."""
    tools = _rule_tools(db, user)
    created = tools["create_mail_rule"].invoke(
        {"name": "Spam from noreply", "pattern": r"^noreply@spam\.test$", "field": "sender", "priority": "spam"}
    )

    assert created["priority"] == "spam"
    assert created["field"] == "sender"

    listed = auth_client.get("/api/emails/rules")
    assert listed.status_code == 200
    assert [rule["pattern"] for rule in listed.json()] == [r"^noreply@spam\.test$"]
    assert [rule["id"] for rule in listed.json()] == [created["id"]]
    assert tools["list_mail_rules"].invoke({}) == [created]


def test_create_mail_rule_validates_patterns_like_the_rest_endpoint(db, user, auth_client):
    """Pattern validation must match POST /api/emails/rules exactly (ReDoS parity)."""
    tools = _rule_tools(db, user)
    payload = {"name": "Probe", "field": "sender", "priority": "important"}

    # Patterns re2 can compile are accepted by both paths, including patterns
    # that would backtrack catastrophically in a backtracking engine.
    for safe_pattern in (r"hello", r"^[a-zA-Z0-9]+$", r"(a+)+$", r"((a|b)+)*$"):
        assert tools["create_mail_rule"].invoke({**payload, "pattern": safe_pattern})["pattern"] == safe_pattern
        assert auth_client.post("/api/emails/rules", json={**payload, "pattern": safe_pattern}).status_code == 200

    # Patterns re2 rejects are refused by both paths.
    invalid_pattern = r"[unterminated"
    assert auth_client.post("/api/emails/rules", json={**payload, "pattern": invalid_pattern}).status_code == 400
    with pytest.raises(ValueError, match="Invalid regex pattern"):
        tools["create_mail_rule"].invoke({**payload, "pattern": invalid_pattern})

    assert db.query(models.Rule).filter(models.Rule.pattern == invalid_pattern).count() == 0


def test_create_mail_rule_rejects_unknown_fields_and_blank_names(db, user):
    """Rule authoring refuses inputs the classifier could never evaluate."""
    tools = _rule_tools(db, user)
    with pytest.raises(ValueError, match="field must be one of"):
        tools["create_mail_rule"].invoke({"name": "Probe", "pattern": "hello", "field": "attachment"})
    with pytest.raises(ValueError, match="name must not be blank"):
        tools["create_mail_rule"].invoke({"name": "  ", "pattern": "hello"})
    assert db.query(models.Rule).count() == 0


def test_created_rules_are_appended_after_existing_rules(db, user):
    """New agent rules must not silently preempt rules the user already reviewed."""
    tools = _rule_tools(db, user)
    first = tools["create_mail_rule"].invoke({"name": "First", "pattern": "first"})
    second = tools["create_mail_rule"].invoke({"name": "Second", "pattern": "second"})

    assert second["order"] > first["order"]
    assert [rule["name"] for rule in tools["list_mail_rules"].invoke({})] == ["First", "Second"]


def test_update_and_delete_are_scoped_to_the_owning_user(db, user, auth_client):
    """Rule writes refuse ids that belong to another user."""
    other = models.User(username="other", hashed_password=security.security_service.get_password_hash("other"))
    db.add(other)
    db.commit()
    foreign_rule = models.Rule(name="Theirs", pattern="theirs", field="sender", user_id=other.id)
    db.add(foreign_rule)
    db.commit()

    tools = _rule_tools(db, user)
    own = tools["create_mail_rule"].invoke({"name": "Mine", "pattern": "mine"})

    with pytest.raises(ValueError, match="not found"):
        tools["update_mail_rule"].invoke({"rule_id": foreign_rule.id, "is_active": False})
    with pytest.raises(ValueError, match="not found"):
        tools["delete_mail_rule"].invoke({"rule_id": foreign_rule.id})
    assert db.get(models.Rule, foreign_rule.id) is not None

    assert tools["update_mail_rule"].invoke({"rule_id": own["id"], "is_active": False})["is_active"] is False
    assert tools["delete_mail_rule"].invoke({"rule_id": own["id"]})["status"] == "success"
    assert db.get(models.Rule, own["id"]) is None


def test_update_mail_rule_requires_a_changed_field(db, user):
    """A no-op update is an error rather than a silent success."""
    tools = _rule_tools(db, user)
    rule = tools["create_mail_rule"].invoke({"name": "Mine", "pattern": "mine"})
    with pytest.raises(ValueError, match="provide is_active or order"):
        tools["update_mail_rule"].invoke({"rule_id": rule["id"]})


def test_list_pending_rule_suggestions_returns_only_pending_by_default(db, user, auth_client):
    """The suggestion queue read is scoped to items still awaiting review."""
    account = _make_account(db, user)
    pending = _make_clarification(db, user, account, sender="pending@test.com", message_id="pending-1")
    dismissed = _make_clarification(db, user, account, sender="dismissed@test.com", message_id="dismissed-1")
    assert auth_client.post(f"/api/emails/clarifications/{dismissed.id}/dismiss").status_code == 200
    db.expire_all()  # The REST write committed in the API's own session.

    tools = _rule_tools(db, user)
    listed = tools["list_pending_rule_suggestions"].invoke({})
    assert [item["id"] for item in listed] == [pending.id]
    assert listed[0]["suggestion_confidence"] == "high"
    assert listed[0]["suggestion_reason"] == "High confidence sender match."

    all_ids = {item["id"] for item in tools["list_pending_rule_suggestions"].invoke({"include_resolved": True})}
    assert all_ids == {pending.id, dismissed.id}


def test_approve_rule_suggestion_matches_the_rest_endpoint_side_effects(db, user, auth_client):
    """Approving through the tool and through the API produce the same side effects."""
    account = _make_account(db, user)
    via_api = _make_clarification(db, user, account, sender="api@test.com", message_id="api-1")
    via_tool = _make_clarification(db, user, account, sender="tool@test.com", message_id="tool-1")

    assert auth_client.post(f"/api/emails/clarifications/{via_api.id}/approve").status_code == 200
    approved = _rule_tools(db, user)["approve_rule_suggestion"].invoke({"suggestion_id": via_tool.id})

    db.expire_all()
    for clarification in (via_api, via_tool):
        db.refresh(clarification)
        assert clarification.status == models.ClarificationStatus.APPROVED
        assert clarification.applied_rule_id is not None

    assert approved["status"] == "approved"
    assert approved["applied_rule_id"] == via_tool.applied_rule_id

    api_rule = db.get(models.Rule, via_api.applied_rule_id)
    tool_rule = db.get(models.Rule, via_tool.applied_rule_id)
    assert _rule_shape(api_rule) == {
        "name": "Messages from api@test.com",
        "field": "sender",
        "priority": models.Priority.IMPORTANT,
        "is_active": True,
    }
    assert _rule_shape(tool_rule) == {**_rule_shape(api_rule), "name": "Messages from tool@test.com"}
    assert tool_rule.pattern == via_tool.suggested_rule_pattern
    assert tool_rule.user_id == user.id


def test_dismiss_rule_suggestion_matches_the_rest_endpoint_side_effects(db, user, auth_client):
    """Dismissing through the tool and through the API produce the same side effects."""
    account = _make_account(db, user)
    via_api = _make_clarification(db, user, account, sender="api@test.com", message_id="api-1")
    via_tool = _make_clarification(db, user, account, sender="tool@test.com", message_id="tool-1")

    assert auth_client.post(f"/api/emails/clarifications/{via_api.id}/dismiss").status_code == 200
    dismissed = _rule_tools(db, user)["dismiss_rule_suggestion"].invoke({"suggestion_id": via_tool.id})

    db.expire_all()
    for clarification in (via_api, via_tool):
        db.refresh(clarification)
        assert clarification.status == models.ClarificationStatus.DISMISSED
        assert clarification.applied_rule_id is None

    assert dismissed["status"] == "dismissed"
    assert db.query(models.Rule).count() == 0


def test_suggestion_actions_are_scoped_to_the_owning_user(db, user, auth_client):
    """Another user's suggestion is invisible and unactionable."""
    account = _make_account(db, user)
    other = models.User(username="other", hashed_password=security.security_service.get_password_hash("other"))
    db.add(other)
    db.commit()
    foreign = _make_clarification(db, other, account, sender="foreign@test.com", message_id="foreign-1")

    tools = _rule_tools(db, user)
    assert tools["list_pending_rule_suggestions"].invoke({}) == []
    with pytest.raises(ValueError, match="not found"):
        tools["approve_rule_suggestion"].invoke({"suggestion_id": foreign.id})
    with pytest.raises(ValueError, match="not found"):
        tools["dismiss_rule_suggestion"].invoke({"suggestion_id": foreign.id})

    db.refresh(foreign)
    assert foreign.status == models.ClarificationStatus.PENDING
    assert db.query(models.Rule).count() == 0
