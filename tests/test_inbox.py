"""Action inbox and device tokens: the aggregate list, the low-risk actions, and the token fences."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from janus import models, security
from janus import trigger_definition_service as definitions
from janus.services import inbox_service
from janus.services.device_token_service import (
    LAST_USED_RESOLUTION,
    MAX_ACTIVE_TOKENS,
    TOKEN_PREFIX,
    DeviceTokenError,
    DeviceTokenService,
    hash_token,
)

PASSWORD = "inbox-pass-1234"


def _user(db, username="owner") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash(PASSWORD))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, username: str):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -- seeding one item of every kind ----------------------------------------------------------------


def _conversation(db, user, title="Your flight moved", unread=True, origin="janus") -> models.Conversation:
    conversation = models.Conversation(user_id=user.id, title=title, origin=origin, unread=unread)
    conversation.messages.append(
        models.ConversationMessage(role=models.MessageRole.ASSISTANT, content="Should I update?")
    )
    db.add(conversation)
    db.commit()
    return conversation


def _planner_suggestion(db, user, title="Dentist on Friday") -> models.PlannerSuggestion:
    suggestion = models.PlannerSuggestion(
        user_id=user.id,
        suggestion_type=models.PlannerSuggestionType.EVENT,
        title=title,
        reason="Mentioned in an email",
    )
    db.add(suggestion)
    db.commit()
    return suggestion


def _topic_suggestion(db, user) -> models.TopicSuggestion:
    suggestion = models.TopicSuggestion(
        user_id=user.id,
        suggestion_type=models.TopicSuggestionType.RENAME,
        payload_json='{"title": "Tax return"}',
        reason="Seen in four emails",
        confidence=models.ConfidenceLevel.MEDIUM,
    )
    db.add(suggestion)
    db.commit()
    return suggestion


def _clarification(db, user) -> models.EmailClarificationRequest:
    account = models.EmailAccount(user_id=user.id, name=f"mail-{user.username}", email=f"{user.username}@example.com")
    db.add(account)
    db.flush()
    email = models.EmailRecord(account_id=account.id, message_id=f"m-{user.username}", subject="Invoice")
    db.add(email)
    db.flush()
    row = models.EmailClarificationRequest(
        user_id=user.id,
        email_id=email.id,
        previous_priority=models.Priority.DIGEST,
        requested_priority=models.Priority.IMPORTANT,
        suggested_rule_name="Invoices matter",
        suggested_rule_pattern="invoice",
        suggested_rule_field="subject",
        suggested_rule_priority=models.Priority.IMPORTANT,
        suggestion_confidence=models.ConfidenceLevel.HIGH,
        suggestion_reason="You raised two invoices",
    )
    db.add(row)
    db.commit()
    return row


def _trigger_proposal(db, user, name="Weekly review") -> models.TriggerDefinition:
    definition = definitions.create_definition(
        db, user.id, origin="agent", enabled=False, name=name, objective="Review my week.", tool_ids=["core"]
    )
    definition.proposal_status = models.TriggerProposalStatus.PROPOSED.value
    db.commit()
    return definition


def _seed_everything(db, user):
    return {
        "conversation": _conversation(db, user),
        "planner_suggestion": _planner_suggestion(db, user),
        "topic_suggestion": _topic_suggestion(db, user),
        "trigger_proposal": _trigger_proposal(db, user),
    }


# -- device tokens ---------------------------------------------------------------------------------


def test_a_token_is_shown_once_and_only_its_hash_is_stored(db):
    """The plaintext comes back from creation; the table holds a hash and a short prefix."""
    user = _user(db)

    record, raw = DeviceTokenService(db).create_token(user.id, "  Laptop   eww ")

    assert raw.startswith(TOKEN_PREFIX) and len(raw) > 30
    assert record.name == "Laptop eww" and record.token_hash == hash_token(raw) and raw not in record.token_hash
    assert raw.startswith(record.token_prefix) and len(record.token_prefix) < len(raw)
    assert db.query(models.DeviceToken).one().token_hash != raw


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_a_token_needs_a_reasonable_name(db, name):
    """Blank or overlong names are refused."""
    with pytest.raises(DeviceTokenError):
        DeviceTokenService(db).create_token(_user(db).id, name)


def test_an_owner_holds_at_most_ten_active_tokens(db):
    """Revoking frees a slot."""
    user = _user(db)
    service = DeviceTokenService(db)
    records = [service.create_token(user.id, f"device {n}")[0] for n in range(MAX_ACTIVE_TOKENS)]

    with pytest.raises(DeviceTokenError, match="at most"):
        service.create_token(user.id, "one too many")
    assert service.revoke_token(user.id, records[0].id) is True
    service.create_token(user.id, "fits again")


def test_authenticate_accepts_only_an_active_token_of_the_right_shape(db):
    """Unknown, revoked, and non-prefixed strings never authenticate."""
    user = _user(db)
    service = DeviceTokenService(db)
    record, raw = service.create_token(user.id, "phone")

    assert service.authenticate(raw).id == user.id
    assert service.authenticate(raw + "x") is None
    assert service.authenticate(None) is None
    assert service.authenticate(hash_token(raw)) is None  # a stored hash is not a credential
    service.revoke_token(user.id, record.id)
    assert service.authenticate(raw) is None


def test_revoking_is_owner_scoped(db):
    """Another owner's token id looks like a missing one."""
    owner, other = _user(db), _user(db, "other")
    record, _ = DeviceTokenService(db).create_token(owner.id, "mine")

    assert DeviceTokenService(db).revoke_token(other.id, record.id) is False
    assert [token.id for token in DeviceTokenService(db).list_tokens(owner.id)] == [record.id]


def test_last_use_is_written_at_most_once_a_minute(db):
    """A polling popup does not write on every request."""
    user = _user(db)
    service = DeviceTokenService(db)
    record, raw = service.create_token(user.id, "poller")

    service.authenticate(raw)
    first = db.get(models.DeviceToken, record.id).last_used_at
    service.authenticate(raw)
    assert db.get(models.DeviceToken, record.id).last_used_at == first

    stale = datetime.now(UTC).replace(tzinfo=None) - LAST_USED_RESOLUTION - timedelta(seconds=5)
    db.get(models.DeviceToken, record.id).last_used_at = stale
    db.commit()
    service.authenticate(raw)
    assert db.get(models.DeviceToken, record.id).last_used_at > stale


# -- the inbox list --------------------------------------------------------------------------------


def test_the_inbox_lists_every_source_with_its_actions_and_risk(db):
    """One item per source; only the trigger proposal needs the web."""
    user = _user(db)
    seeded = _seed_everything(db, user)

    items = {item.kind: item for item in inbox_service.InboxService(db).list_items(user)}

    assert set(items) == set(seeded)
    assert {kind: item.id for kind, item in items.items()} == {kind: row.id for kind, row in seeded.items()}
    assert items["conversation"].actions == ["mark_read"] and items["conversation"].open_url.startswith("/chat?")
    assert all(
        items[kind].actions == ["dismiss"] and items[kind].risk == "low"
        for kind in ("planner_suggestion", "topic_suggestion")
    )
    assert items["trigger_proposal"].actions == [] and items["trigger_proposal"].risk == "needs_web"
    assert items["topic_suggestion"].title == "Topic: Tax return"
    assert all(item.open_url.startswith("/") and "://" not in item.open_url for item in items.values())


def test_a_proposed_automation_opens_the_triggers_settings_tab(db):
    """The Triggers tab in Settings has the id ``runtime``; ``tab=triggers`` would open the default tab."""
    user = _user(db)
    _trigger_proposal(db, user)

    (item,) = [i for i in inbox_service.InboxService(db).list_items(user) if i.kind == "trigger_proposal"]

    assert item.open_url == "/settings?tab=runtime"


def test_the_inbox_is_owner_scoped_and_hides_what_is_done(db):
    """Another owner's items, read conversations, user-started chats, and handled suggestions never show."""
    owner, other = _user(db), _user(db, "other")
    _seed_everything(db, other)
    _conversation(db, owner, title="Already read", unread=False)
    _conversation(db, owner, title="Started by me", origin="user")
    dismissed = _planner_suggestion(db, owner)
    dismissed.status = models.PlannerSuggestionStatus.DISMISSED
    db.commit()

    assert inbox_service.InboxService(db).list_items(owner) == []


def test_titles_and_summaries_are_plain_cut_text(db):
    """Control and bidi characters go, whitespace collapses, long text is cut: clients render untrusted text."""
    user = _user(db)
    _planner_suggestion(db, user, title="Pay\x1b[31m rent‮\n\nnow " + "x" * 300)

    (item,) = inbox_service.InboxService(db).list_items(user)

    assert "\x1b" not in item.title and "‮" not in item.title and "\n" not in item.title
    assert len(item.title) <= inbox_service.TITLE_CHARS and item.title.endswith("…")


def test_the_newest_item_comes_first(db):
    """Newest first across sources."""
    user = _user(db)
    old = _planner_suggestion(db, user, "Old")
    old.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    db.commit()
    _conversation(db, user, "New")

    assert [item.kind for item in inbox_service.InboxService(db).list_items(user)] == [
        "conversation",
        "planner_suggestion",
    ]


# -- the API: tokens, auth fences, ETag, actions ---------------------------------------------------


def test_token_management_needs_the_web_session_and_never_returns_a_secret_twice(client, db):
    """Create shows the secret once; the list shows prefix and last use; revoke removes it."""
    _login(client, _user(db).username)

    created = client.post("/api/inbox/tokens", json={"name": "Desktop"})
    assert created.status_code == 201
    secret = created.json()["token"]
    listed = client.get("/api/inbox/tokens").json()
    assert [entry["name"] for entry in listed] == ["Desktop"] and "token" not in listed[0]
    assert secret not in client.get("/api/inbox/tokens").text

    assert client.delete(f"/api/inbox/tokens/{created.json()['id']}").status_code == 204
    assert client.get("/api/inbox/tokens").json() == []
    assert client.delete(f"/api/inbox/tokens/{created.json()['id']}").status_code == 404


def test_a_device_token_cannot_manage_tokens_or_reach_anything_but_the_inbox(client, db):
    """The token is refused on token management and on every other endpoint."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "script")

    assert client.get("/api/inbox", headers=_bearer(raw)).status_code == 200
    for method, path in (
        ("post", "/api/inbox/tokens"),
        ("get", "/api/inbox/tokens"),
        ("get", "/api/emails/notifications/preferences"),
        ("get", "/api/conversations/"),
        ("get", "/api/memory/"),
        ("get", "/api/desktop/summary"),
        ("get", "/api/auth/me"),
    ):
        response = getattr(client, method)(
            path, headers=_bearer(raw), **({"json": {"name": "x"}} if method == "post" else {})
        )
        assert response.status_code == 401, (method, path, response.status_code)


def test_a_wrong_or_revoked_token_gets_401(client, db):
    """Garbage, a JWT-looking value, and a revoked token are all refused."""
    user = _user(db)
    record, raw = DeviceTokenService(db).create_token(user.id, "script")

    assert client.get("/api/inbox", headers=_bearer("jdt_nope")).status_code == 401
    assert client.get("/api/inbox").status_code == 401
    DeviceTokenService(db).revoke_token(user.id, record.id)
    assert client.get("/api/inbox", headers=_bearer(raw)).status_code == 401


def test_the_inbox_api_returns_items_and_supports_etag_polling(client, db):
    """A second poll with the ETag is a 304 until something changes."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    suggestion = _planner_suggestion(db, user)

    first = client.get("/api/inbox", headers=_bearer(raw))
    assert first.status_code == 200 and [item["kind"] for item in first.json()["items"]] == ["planner_suggestion"]
    etag = first.headers["etag"]
    assert client.get("/api/inbox", headers={**_bearer(raw), "If-None-Match": etag}).status_code == 304

    done = client.post(f"/api/inbox/planner_suggestion/{suggestion.id}/dismiss", headers=_bearer(raw))
    assert done.status_code == 204
    after = client.get("/api/inbox", headers={**_bearer(raw), "If-None-Match": etag})
    assert after.status_code == 200 and after.json()["items"] == []


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("conversation", "mark_read"),
        ("planner_suggestion", "dismiss"),
        ("topic_suggestion", "dismiss"),
    ],
)
def test_a_device_token_can_take_each_low_risk_action_without_csrf(client, db, kind, action):
    """Each safe action works with a device token (no cookie, so no CSRF), and the item leaves the inbox."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    row = _seed_everything(db, user)[kind]

    assert client.post(f"/api/inbox/{kind}/{row.id}/{action}", headers=_bearer(raw)).status_code == 204

    kinds = {item["kind"] for item in client.get("/api/inbox", headers=_bearer(raw)).json()["items"]}
    assert kind not in kinds and "trigger_proposal" in kinds
    db.expire_all()
    if kind == "conversation":
        assert db.get(models.Conversation, row.id).unread is False


@pytest.mark.parametrize(
    ("kind", "action"),
    [
        ("trigger_proposal", "approve"),
        ("trigger_proposal", "reject"),
        ("trigger_proposal", "dismiss"),
        ("planner_suggestion", "approve"),
        ("topic_suggestion", "approve"),
        ("conversation", "dismiss"),
        ("character_proposal", "approve"),
        ("planner_suggestion", "mark_read"),
    ],
)
def test_approvals_and_unlisted_actions_do_not_exist_for_a_device(client, db, kind, action):
    """Nothing that grants, applies, or changes behavior is offered; the call is a 404 and changes nothing."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    seeded = _seed_everything(db, user)
    item_id = seeded[kind].id if kind in seeded else 1

    assert client.post(f"/api/inbox/{kind}/{item_id}/{action}", headers=_bearer(raw)).status_code == 404

    assert {item["kind"] for item in client.get("/api/inbox", headers=_bearer(raw)).json()["items"]} == set(seeded)
    db.expire_all()
    assert db.get(models.TriggerDefinition, seeded["trigger_proposal"].id).proposal_status == "proposed"


def test_another_owners_item_looks_missing(client, db):
    """Acting on someone else's id is a 404 and leaves their item alone."""
    owner, other = _user(db), _user(db, "other")
    _, raw = DeviceTokenService(db).create_token(owner.id, "popup")
    theirs = _seed_everything(db, other)

    for kind, action in (
        ("conversation", "mark_read"),
        ("planner_suggestion", "dismiss"),
        ("topic_suggestion", "dismiss"),
    ):
        assert client.post(f"/api/inbox/{kind}/{theirs[kind].id}/{action}", headers=_bearer(raw)).status_code == 404

    db.expire_all()
    assert db.get(models.Conversation, theirs["conversation"].id).unread is True
    assert (
        db.get(models.PlannerSuggestion, theirs["planner_suggestion"].id).status
        == models.PlannerSuggestionStatus.PENDING
    )


def test_a_user_started_conversation_cannot_be_marked_read_through_the_inbox(client, db):
    """Only Janus-started conversations are inbox items, so the action refuses others."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    mine = _conversation(db, user, origin="user")

    assert client.post(f"/api/inbox/conversation/{mine.id}/mark_read", headers=_bearer(raw)).status_code == 404
    db.expire_all()
    assert db.get(models.Conversation, mine.id).unread is True


def test_the_web_session_needs_csrf_for_inbox_actions(client, db):
    """A cookie session keeps CSRF protection on the inbox actions."""
    user = _user(db)
    suggestion = _planner_suggestion(db, user)
    response = client.post("/api/auth/login", json={"username": user.username, "password": PASSWORD})
    assert response.status_code == 200

    assert client.post(f"/api/inbox/planner_suggestion/{suggestion.id}/dismiss").status_code == 403
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    assert client.post(f"/api/inbox/planner_suggestion/{suggestion.id}/dismiss").status_code == 204


# -- review fixes: token cleanup, open-only actions, timestamps, cookie fallback ------------------------------


def test_deleting_a_user_deletes_their_tokens_so_a_reused_id_cannot_inherit_one(client, db):
    """SQLite may reuse a deleted user's id; a leftover token row would then authenticate as the next user."""
    admin = _user(db, "admin-owner")
    admin.is_admin = True
    victim = _user(db, "victim")
    _, raw = DeviceTokenService(db).create_token(victim.id, "old laptop")
    _login(client, admin.username)

    assert client.delete(f"/api/auth/users/{victim.username}").status_code == 200

    assert db.query(models.DeviceToken).count() == 0
    assert DeviceTokenService(db).authenticate(raw) is None


def test_a_password_change_and_revoke_sessions_revoke_device_tokens(db):
    """Account recovery also closes the devices; another owner's tokens are untouched."""
    from janus.services.auth_service import AuthService

    owner, other = _user(db), _user(db, "other")
    service = DeviceTokenService(db)
    _, first = service.create_token(owner.id, "laptop")
    _, second = service.create_token(owner.id, "phone")
    _, theirs = service.create_token(other.id, "theirs")

    AuthService(db).change_password(owner, PASSWORD, "a-new-password-1234")
    assert service.authenticate(first) is None and service.authenticate(second) is None
    assert service.authenticate(theirs).id == other.id

    _, again = service.create_token(owner.id, "new laptop")
    AuthService(db).revoke_user_sessions(owner, reason="test")
    assert service.authenticate(again) is None
    assert service.authenticate(theirs).id == other.id


@pytest.mark.parametrize(
    ("kind", "model", "closed"),
    [
        ("planner_suggestion", models.PlannerSuggestion, models.PlannerSuggestionStatus.APPLIED),
        ("topic_suggestion", models.TopicSuggestion, models.TopicSuggestionStatus.APPROVED),
    ],
)
def test_dismiss_refuses_an_item_that_is_no_longer_open(client, db, kind, model, closed):
    """A device cannot rewrite the history of a suggestion that was already approved or applied."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    row = _seed_everything(db, user)[kind]
    row.status = closed
    db.commit()

    assert client.post(f"/api/inbox/{kind}/{row.id}/dismiss", headers=_bearer(raw)).status_code == 404

    db.expire_all()
    assert db.get(model, row.id).status == closed


def test_timestamps_carry_a_utc_offset(client, db):
    """Naive stored times are serialized with an explicit offset, so a client in another zone cannot misread them."""
    user = _user(db)
    _planner_suggestion(db, user)
    _login(client, user.username)
    client.post("/api/inbox/tokens", json={"name": "Desktop"})

    created = client.get("/api/inbox").json()["items"][0]["created_at"]
    token = client.get("/api/inbox/tokens").json()[0]["created_at"]

    for value in (created, token):
        assert value.endswith("Z") or value.endswith("+00:00"), value


def test_a_valid_device_token_works_next_to_a_stale_cookie(client, db):
    """An expired or invalid session cookie must not hide a valid bearer device token."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "script")

    response = client.get("/api/inbox", headers={**_bearer(raw), "Cookie": "access_token=expired-or-garbage"})

    assert response.status_code == 200
    assert client.get("/api/inbox", headers={"Cookie": "access_token=expired-or-garbage"}).status_code == 401


def test_suggestions_link_to_the_tabs_that_show_their_review_ui(db):
    """The popup's Open button must land on the panel where the item can be approved."""
    user = _user(db)
    _seed_everything(db, user)

    items = {item.kind: item for item in inbox_service.InboxService(db).list_items(user)}

    assert items["topic_suggestion"].open_url == "/memory?tab=topics"


def test_a_stored_rule_suggestion_is_not_an_inbox_item(client, db):
    """Rule suggestions are no longer produced or reviewed; a row left from an older version stays out of the inbox."""
    user = _user(db)
    _, raw = DeviceTokenService(db).create_token(user.id, "popup")
    row = _clarification(db, user)

    assert all(item.kind != "email_clarification" for item in inbox_service.InboxService(db).list_items(user))
    assert client.post(f"/api/inbox/email_clarification/{row.id}/dismiss", headers=_bearer(raw)).status_code == 404
    db.expire_all()
    assert db.get(models.EmailClarificationRequest, row.id).status == models.ClarificationStatus.PENDING
