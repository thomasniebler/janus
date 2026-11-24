"""REST surface for a conversation's delegated tasks (JAN-208)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from janus import delegation_service, models, security
from janus.services.conversation_service import ConversationService

# db and client fixtures are provided by conftest.py


def _make_user(db, username: str) -> models.User:
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("pw"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str) -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert response.status_code == 200
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]


def _conversation(db, user: models.User) -> models.Conversation:
    conversation = models.Conversation(user_id=user.id, title="chat")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def _delegation(
    db,
    user: models.User,
    conversation: models.Conversation | None,
    *,
    objective: str = "Summarize the inbox",
    status: str = models.RuntimeDelegationStatus.COMPLETED.value,
    created_at: datetime | None = None,
    result: str | None = None,
    error: str | None = None,
) -> models.RuntimeDelegation:
    now = datetime.now(UTC).replace(tzinfo=None)
    row = models.RuntimeDelegation(
        delegation_id=f"del_{uuid.uuid4().hex}",
        user_id=user.id,
        parent_task_id=f"task_{uuid.uuid4().hex}",
        child_task_id=f"task_{uuid.uuid4().hex}",
        conversation_id=conversation.id if conversation else None,
        objective=objective,
        requested_tool_ids=["emails", "calendar"],
        allowed_tool_ids=["emails"],
        status=status,
        claim_token="secret-claim-token" if status == models.RuntimeDelegationStatus.RUNNING.value else None,
        claimed_at=now if status == models.RuntimeDelegationStatus.RUNNING.value else None,
        result=result,
        error=error,
        created_at=created_at or now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def alice(db):
    """A regular user who owns the conversations under test."""
    return _make_user(db, "alice")


@pytest.fixture
def bob(db):
    """A second user who must never see alice's delegations."""
    return _make_user(db, "bob")


def test_lists_conversation_delegations_newest_first_with_serialized_fields(client, db, alice):
    """The page is newest first and serializes exactly the public delegation fields."""
    conversation = _conversation(db, alice)
    base = datetime.now(UTC).replace(tzinfo=None)
    older = _delegation(
        db,
        alice,
        conversation,
        objective="Older task",
        created_at=base - timedelta(minutes=5),
        result="Three unread messages.",
    )
    newer = _delegation(
        db,
        alice,
        conversation,
        objective="Newer task",
        status=models.RuntimeDelegationStatus.FAILED.value,
        created_at=base - timedelta(minutes=1),
        error="tool emails failed",
    )
    _login(client, "alice")

    response = client.get(f"/api/conversations/{conversation.id}/delegations")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["has_more"] is False
    assert [item["delegation_id"] for item in body["items"]] == [newer.delegation_id, older.delegation_id]
    first, second = body["items"]
    assert first["status"] == "failed"
    assert first["objective"] == "Newer task"
    assert first["error"] == "tool emails failed"
    assert first["result"] is None
    assert second["status"] == "completed"
    assert second["result"] == "Three unread messages."
    assert second["requested_tool_ids"] == ["emails", "calendar"]
    assert second["allowed_tool_ids"] == ["emails"]
    assert second["depth"] == 1
    assert set(second) == {
        "delegation_id",
        "status",
        "objective",
        "requested_tool_ids",
        "allowed_tool_ids",
        "result",
        "error",
        "depth",
        "created_at",
        "claimed_at",
        "completed_at",
    }


def test_never_exposes_claim_token_or_internal_task_ids(client, db, alice):
    """The private claim token and the parent/child task ids never leave the server."""
    conversation = _conversation(db, alice)
    row = _delegation(db, alice, conversation, status=models.RuntimeDelegationStatus.RUNNING.value)
    _login(client, "alice")

    response = client.get(f"/api/conversations/{conversation.id}/delegations")

    assert response.status_code == 200
    text = response.text
    assert "secret-claim-token" not in text
    assert row.parent_task_id not in text
    assert row.child_task_id not in text
    assert response.json()["items"][0]["status"] == "running"


def test_paginates_with_limit_and_offset(client, db, alice):
    """limit/offset walk the list without gaps or overlap and report has_more."""
    conversation = _conversation(db, alice)
    base = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        _delegation(db, alice, conversation, objective=f"task {i}", created_at=base - timedelta(minutes=10 - i))
        for i in range(5)
    ]
    newest_first = [row.delegation_id for row in reversed(rows)]
    _login(client, "alice")

    first = client.get(f"/api/conversations/{conversation.id}/delegations", params={"limit": 2}).json()
    second = client.get(f"/api/conversations/{conversation.id}/delegations", params={"limit": 2, "offset": 2}).json()
    last = client.get(f"/api/conversations/{conversation.id}/delegations", params={"limit": 2, "offset": 4}).json()

    assert [item["delegation_id"] for item in first["items"]] == newest_first[:2]
    assert first["has_more"] is True and first["total"] == 5
    assert [item["delegation_id"] for item in second["items"]] == newest_first[2:4]
    assert second["has_more"] is True
    assert [item["delegation_id"] for item in last["items"]] == newest_first[4:]
    assert last["has_more"] is False


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_rejects_out_of_range_pagination(client, db, alice, params):
    """Out-of-range paging parameters answer 422."""
    conversation = _conversation(db, alice)
    _login(client, "alice")

    response = client.get(f"/api/conversations/{conversation.id}/delegations", params=params)

    assert response.status_code == 422


def test_only_lists_delegations_of_that_conversation(client, db, alice):
    """Delegations of other conversations, or of none, are not listed."""
    conversation = _conversation(db, alice)
    other_conversation = _conversation(db, alice)
    mine = _delegation(db, alice, conversation)
    _delegation(db, alice, other_conversation)
    _delegation(db, alice, None)
    _login(client, "alice")

    body = client.get(f"/api/conversations/{conversation.id}/delegations").json()

    assert [item["delegation_id"] for item in body["items"]] == [mine.delegation_id]
    assert body["total"] == 1


def test_another_users_conversation_answers_404_not_403(client, db, alice, bob):
    """A foreign conversation looks exactly like a missing one and leaks nothing."""
    conversation = _conversation(db, alice)
    _delegation(db, alice, conversation, objective="Alice's private task")
    _login(client, "bob")

    response = client.get(f"/api/conversations/{conversation.id}/delegations")
    missing = client.get("/api/conversations/999999/delegations")

    assert response.status_code == 404
    assert response.json() == missing.json()
    assert "Alice's private task" not in response.text


def test_foreign_delegation_rows_on_own_conversation_are_not_listed(db, alice, bob):
    """Defense in depth: rows are filtered on the owner too, not only on the conversation."""
    conversation = _conversation(db, alice)
    _delegation(db, bob, conversation, objective="Bob's row")

    page = ConversationService(db).list_delegations_for_user(conversation.id, alice.id)

    assert page == ([], 0)


def test_requires_authentication(client, db, alice):
    """An anonymous request is refused."""
    conversation = _conversation(db, alice)

    response = client.get(f"/api/conversations/{conversation.id}/delegations")

    assert response.status_code == 401


def test_stale_running_delegation_is_requeued_once_then_shown_as_failed(client, db, alice):
    """A run orphaned past its lease is re-run once (JAN-207), then reported failed (interrupted)."""
    conversation = _conversation(db, alice)
    long_ago = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=delegation_service.DELEGATION_LEASE_SECONDS + 60
    )
    row = _delegation(db, alice, conversation, status=models.RuntimeDelegationStatus.RUNNING.value)
    row.created_at = long_ago
    row.claimed_at = long_ago
    db.commit()
    _login(client, "alice")

    item = client.get(f"/api/conversations/{conversation.id}/delegations").json()["items"][0]

    assert item["status"] == "queued"
    assert item["error"] is None
    assert item["claimed_at"] is None

    db.refresh(row)
    assert row.requeue_count == 1
    row.status = models.RuntimeDelegationStatus.RUNNING.value
    row.claimed_at = long_ago
    db.commit()

    item = client.get(f"/api/conversations/{conversation.id}/delegations").json()["items"][0]

    assert item["status"] == "failed"
    assert "interrupted" in item["error"]


def test_service_rejects_invalid_pagination(db, alice):
    """The service refuses a non-positive limit or a negative offset."""
    conversation = _conversation(db, alice)
    service = ConversationService(db)

    with pytest.raises(ValueError):
        service.list_delegations_for_user(conversation.id, alice.id, limit=0)
    with pytest.raises(ValueError):
        service.list_delegations_for_user(conversation.id, alice.id, offset=-1)


def test_huge_offset_answers_422_not_500(client, db, alice):
    """An offset past SQLite's integer range is refused up front instead of crashing the query."""
    conversation = _conversation(db, alice)
    _login(client, "alice")

    response = client.get(f"/api/conversations/{conversation.id}/delegations", params={"offset": 2**63})

    assert response.status_code == 422
