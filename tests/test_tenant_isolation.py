"""Regression tests for tenant isolation.

Verifying that API endpoints scope their data access to the requesting user
and reject cross-user operations with 404 (not 403, to avoid leaking existence
information).
"""

import json
from datetime import timedelta

import pytest

from janus import config, models, security, time_utils, tool_policy
from janus.services import mailbox_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_and_login(client, db, username: str, password: str = "pw", is_admin: bool = False):
    """Creates a user and returns an authenticated client + user object."""
    hashed = security.security_service.get_password_hash(password)
    user = models.User(username=username, hashed_password=hashed, is_admin=is_admin)
    db.add(user)
    db.commit()

    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers[security.CSRF_HEADER_NAME] = resp.json()["csrf_token"]
    return user


def _add_email_account(db, user: models.User) -> models.EmailAccount:
    account = models.EmailAccount(
        name=f"account-{user.username}",
        email=f"{user.username}@example.com",
        imap_server="imap.example.com",
        imap_port=993,
        username=user.username,
        password="encrypted",
        user_id=user.id,
    )
    db.add(account)
    db.commit()
    return account


def _add_email_record(db, account: models.EmailAccount) -> models.EmailRecord:
    record = models.EmailRecord(
        message_id=f"<msg-{account.id}@test>",
        sender="sender@example.com",
        subject="Hello",
        snippet="Hello world",
        priority=models.Priority.DIGEST,
        account_id=account.id,
    )
    db.add(record)
    db.commit()
    return record


def _add_google_planner_account(db, user: models.User) -> models.PlannerAccount:
    acct = models.PlannerAccount(
        name="Google Calendar",
        provider=models.Provider.GOOGLE_CALENDAR,
        user_id=user.id,
        token="{}",
    )
    db.add(acct)
    db.commit()
    return acct


# ---------------------------------------------------------------------------
# JAN-129: test_account_connection must check user ownership
# ---------------------------------------------------------------------------


def test_test_account_connection_rejects_cross_user(client, db, monkeypatch):
    """User B cannot test IMAP connection for an account owned by user A."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)

    # Now log in as user B
    user_b = _create_and_login(client, db, "bob")
    _ = user_b  # noqa: F841

    response = client.post(f"/api/emails/accounts/{account_a.id}/test")
    assert response.status_code == 404


def test_test_account_connection_allows_owner(client, db, monkeypatch):
    """The account owner can test their own IMAP connection."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)

    # Mock the IMAP monitor so we don't make real network calls
    from unittest.mock import MagicMock, patch

    mock_monitor = MagicMock()
    mock_monitor.test_connection.return_value = (True, "OK")
    with patch("janus.api.emails.IMAPMonitor", return_value=mock_monitor):
        response = client.post(f"/api/emails/accounts/{account_a.id}/test")

    assert response.status_code == 200
    assert response.json()["status"] == "success"


# ---------------------------------------------------------------------------
# JAN-128: google_disconnect must be scoped to requesting user
# ---------------------------------------------------------------------------


def test_google_disconnect_only_removes_own_account(client, db):
    """User B calling DELETE /google/disconnect does not remove user A's Google account."""
    user_a = _create_and_login(client, db, "alice")
    _add_google_planner_account(db, user_a)

    # Log in as user B who has no Google account connected
    _create_and_login(client, db, "bob")

    response = client.delete("/api/connections/google")
    # User B has no Google account, so they get 404
    assert response.status_code == 404

    # User A's account must still be in the DB
    db.expire_all()
    remaining = db.query(models.PlannerAccount).filter(models.PlannerAccount.user_id == user_a.id).count()
    assert remaining == 1


def test_google_disconnect_removes_own_account(client, db):
    """User A can disconnect their own Google Calendar account."""
    user_a = _create_and_login(client, db, "alice")
    _add_google_planner_account(db, user_a)

    response = client.delete("/api/connections/google")
    assert response.status_code == 204

    db.expire_all()
    remaining = db.query(models.PlannerAccount).filter(models.PlannerAccount.user_id == user_a.id).count()
    assert remaining == 0


# ---------------------------------------------------------------------------
# JAN-131: update_email_priority must check user ownership
# ---------------------------------------------------------------------------


def test_update_email_priority_rejects_cross_user(client, db):
    """User B cannot change the priority of user A's email."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)
    record_a = _add_email_record(db, account_a)

    # Log in as user B
    _create_and_login(client, db, "bob")

    response = client.patch(f"/api/emails/{record_a.id}/priority", params={"priority": "important"})
    assert response.status_code == 404


def test_update_email_priority_allows_owner(client, db, monkeypatch):
    """The email owner can change their own email's priority."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)
    record_a = _add_email_record(db, account_a)

    # Suppress LLM calls that fire when marking as IMPORTANT
    from unittest.mock import patch

    with patch("janus.services.email_service.llm_service.LLMService") as mock_llm:
        mock_llm.return_value.classify_email.return_value = (
            models.Priority.IMPORTANT,
            "Test reasoning.",
            models.ConfidenceLevel.HIGH,
        )
        with patch("janus.services.email_service.planner_service.PlannerService") as mock_planner:
            mock_planner.return_value.get_context_data.return_value = {}
            response = client.patch(f"/api/emails/{record_a.id}/priority", params={"priority": "important"})

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# JAN-130: update_settings must require admin_required
# ---------------------------------------------------------------------------


def test_update_settings_requires_admin(client, db):
    """A non-admin user gets 403 when trying to update global settings."""
    _create_and_login(client, db, "regular_user", is_admin=False)

    response = client.patch("/api/planner/settings", json={"ollama_model": "llama3"})
    assert response.status_code == 403


def test_update_settings_allowed_for_admin(client, db):
    """An admin user can update global settings."""
    _create_and_login(client, db, "admin_user", is_admin=True)

    response = client.patch("/api/planner/settings", json={"ollama_model": "llama3"})
    assert response.status_code == 200
    assert response.json()["ollama_model"] == "llama3"


def test_get_settings_available_to_all_authenticated_users(client, db):
    """Any authenticated user (non-admin) can read settings."""
    _create_and_login(client, db, "regular_user", is_admin=False)

    response = client.get("/api/planner/settings")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# JAN-137: ownership lives in user-scoped service methods
# ---------------------------------------------------------------------------


def _add_rule(db, user: models.User, *, name: str = "rule", order: int = 0) -> models.Rule:
    rule = models.Rule(
        name=name,
        pattern="^boss@example\\.com$",
        field="sender",
        priority=models.Priority.IMPORTANT,
        order=order,
        user_id=user.id,
    )
    db.add(rule)
    db.commit()
    return rule


def _add_conversation(db, user: models.User, title: str = "chat") -> models.Conversation:
    conversation = models.Conversation(user_id=user.id, title=title)
    db.add(conversation)
    db.commit()
    return conversation


def test_update_account_rejects_cross_user(client, db):
    """User B cannot rename user A's email account; the account is untouched."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.patch(f"/api/emails/accounts/{account_a.id}", json={"name": "hijacked"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"
    db.expire_all()
    assert db.get(models.EmailAccount, account_a.id).name == "account-alice"


def test_update_account_allows_owner(client, db):
    """The owner can still update their own email account."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)

    response = client.patch(f"/api/emails/accounts/{account_a.id}", json={"name": "renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "renamed"


def test_delete_account_rejects_cross_user(client, db):
    """User B cannot delete user A's email account."""
    user_a = _create_and_login(client, db, "alice")
    account_a = _add_email_account(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.delete(f"/api/emails/accounts/{account_a.id}")

    assert response.status_code == 404
    db.expire_all()
    assert db.get(models.EmailAccount, account_a.id) is not None


def test_delete_account_allows_owner(client, db):
    """The owner can delete their own email account."""
    user_a = _create_and_login(client, db, "alice")
    account_id = _add_email_account(db, user_a).id

    response = client.delete(f"/api/emails/accounts/{account_id}")

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    db.expire_all()
    assert db.get(models.EmailAccount, account_id) is None


def test_update_rule_rejects_cross_user(client, db):
    """User B cannot toggle or reorder user A's rule."""
    user_a = _create_and_login(client, db, "alice")
    rule_a = _add_rule(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.patch(f"/api/emails/rules/{rule_a.id}", json={"is_active": False, "order": 9})

    assert response.status_code == 404
    assert response.json()["detail"] == "Rule not found"
    db.expire_all()
    stored = db.get(models.Rule, rule_a.id)
    assert stored.is_active is True
    assert stored.order == 0


def test_update_rule_allows_owner(client, db):
    """The owner can toggle and reorder their own rule."""
    user_a = _create_and_login(client, db, "alice")
    rule_a = _add_rule(db, user_a)

    response = client.patch(f"/api/emails/rules/{rule_a.id}", json={"is_active": False, "order": 4})

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["order"] == 4


def test_delete_rule_rejects_cross_user(client, db):
    """User B cannot delete user A's rule."""
    user_a = _create_and_login(client, db, "alice")
    rule_a = _add_rule(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.delete(f"/api/emails/rules/{rule_a.id}")

    assert response.status_code == 404
    db.expire_all()
    assert db.get(models.Rule, rule_a.id) is not None


def test_delete_rule_allows_owner(client, db):
    """The owner can delete their own rule."""
    user_a = _create_and_login(client, db, "alice")
    rule_id = _add_rule(db, user_a).id

    response = client.delete(f"/api/emails/rules/{rule_id}")

    assert response.status_code == 200
    db.expire_all()
    assert db.get(models.Rule, rule_id) is None


def test_list_emails_scoped_to_user(client, db):
    """GET /api/emails/ only returns mail from the caller's own accounts and skips SPAM."""
    user_a = _create_and_login(client, db, "alice")
    record_a = _add_email_record(db, _add_email_account(db, user_a))
    user_b = _create_and_login(client, db, "bob")
    account_b = _add_email_account(db, user_b)
    record_b = _add_email_record(db, account_b)
    spam_b = models.EmailRecord(
        message_id="<spam-b@test>",
        sender="spam@example.com",
        subject="Win",
        snippet="Win",
        priority=models.Priority.SPAM,
        account_id=account_b.id,
    )
    db.add(spam_b)
    db.commit()

    response = client.get("/api/emails/")

    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]
    assert ids == [record_b.id]
    assert record_a.id not in ids


def test_update_email_priority_rejects_ownerless_email(client, db):
    """An email with no account has no owner, so no user may relabel it."""
    _create_and_login(client, db, "alice")
    orphan = models.EmailRecord(
        message_id="<orphan@test>",
        sender="sender@example.com",
        subject="Orphan",
        snippet="Orphan",
        priority=models.Priority.DIGEST,
        account_id=None,
    )
    db.add(orphan)
    db.commit()

    response = client.patch(f"/api/emails/{orphan.id}/priority", params={"priority": "ignore"})

    assert response.status_code == 404
    db.expire_all()
    assert db.get(models.EmailRecord, orphan.id).priority == models.Priority.DIGEST


def test_get_conversation_rejects_cross_user(client, db):
    """User B cannot read user A's conversation."""
    user_a = _create_and_login(client, db, "alice")
    conversation_a = _add_conversation(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.get(f"/api/conversations/{conversation_a.id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found."


def test_delete_conversation_rejects_cross_user(client, db):
    """User B cannot delete user A's conversation."""
    user_a = _create_and_login(client, db, "alice")
    conversation_a = _add_conversation(db, user_a)
    _create_and_login(client, db, "bob")

    response = client.delete(f"/api/conversations/{conversation_a.id}")

    assert response.status_code == 404
    db.expire_all()
    assert db.get(models.Conversation, conversation_a.id) is not None


def test_user_scoped_service_methods_treat_foreign_rows_as_missing(db):
    """The service layer itself refuses foreign rows, independent of any router."""
    from janus.services.conversation_service import ConversationService
    from janus.services.email_service import EmailService

    pw = security.security_service.get_password_hash("pw")
    alice = models.User(username="alice", hashed_password=pw)
    bob = models.User(username="bob", hashed_password=pw)
    db.add_all([alice, bob])
    db.commit()
    account = _add_email_account(db, alice)
    record = _add_email_record(db, account)
    rule = _add_rule(db, alice)
    conversation = _add_conversation(db, alice)

    emails = EmailService(db)
    conversations = ConversationService(db)

    assert mailbox_service.update_account_for_user(db, account.id, bob.id, {"name": "x"}) is None
    assert mailbox_service.delete_account_for_user(db, account.id, bob.id) is False
    assert emails.update_rule_for_user(rule.id, bob.id, is_active=False) is None
    assert emails.delete_rule_for_user(rule.id, bob.id) is False
    assert emails.get_email_for_user(record.id, bob.id) is None
    assert emails.update_email_priority_for_user(record.id, models.Priority.IGNORE, bob) is None
    assert emails.list_emails_for_user(bob.id) == []
    assert conversations.get_conversation_for_user(conversation.id, bob.id) is None
    assert conversations.delete_conversation_for_user(conversation.id, bob.id) is False
    assert conversations.list_conversations_for_user(bob.id) == []

    db.expire_all()
    assert db.get(models.EmailAccount, account.id).name == "account-alice"
    assert db.get(models.Rule, rule.id).is_active is True
    assert db.get(models.EmailRecord, record.id).priority == models.Priority.DIGEST
    assert db.get(models.Conversation, conversation.id) is not None

    assert emails.get_email_for_user(record.id, alice.id).id == record.id
    assert [row.id for row in emails.list_emails_for_user(alice.id)] == [record.id]
    assert conversations.get_conversation_for_user(conversation.id, alice.id).id == conversation.id


# ---------------------------------------------------------------------------
# JAN-286: dashboard widgets, briefing context, and mail tools read and send
# only through the authenticated / run owner's own accounts
# ---------------------------------------------------------------------------

_SHARED_SENDER = "news@shared.example"


def _add_recent_email(
    db, account: models.EmailAccount, subject: str, *, priority: models.Priority = models.Priority.IMPORTANT
) -> models.EmailRecord:
    record = models.EmailRecord(
        message_id=f"<{subject.replace(' ', '-')}-{account.id}@test>",
        sender=_SHARED_SENDER,
        subject=subject,
        snippet=f"{subject} snippet",
        body=f"{subject} body",
        priority=priority,
        received_at=time_utils.utcnow() - timedelta(minutes=5),
        account_id=account.id,
    )
    db.add(record)
    db.commit()
    return record


def _seed_two_mailboxes(db, alice: models.User, bob: models.User):
    """Give alice and bob one active account and one recent IMPORTANT email each."""
    account_a = _add_email_account(db, alice)
    account_b = _add_email_account(db, bob)
    mail_a = _add_recent_email(db, account_a, "Quarterly report alice")
    mail_b = _add_recent_email(db, account_b, "Quarterly report bob")
    return account_a, account_b, mail_a, mail_b


def _make_user(db, username: str) -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    return user


def test_dashboard_email_widgets_only_show_the_callers_mail(client, db):
    """GET /api/dashboard/ and /important-emails never include another user's mail."""
    alice = _create_and_login(client, db, "alice")
    bob = _create_and_login(client, db, "bob")
    _, _, mail_a, mail_b = _seed_two_mailboxes(db, alice, bob)

    widget = client.get("/api/dashboard/important-emails")
    assert widget.status_code == 200, widget.text
    assert [row["id"] for row in widget.json()] == [mail_b.id]

    aggregate = client.get("/api/dashboard/")
    assert aggregate.status_code == 200, aggregate.text
    payload = aggregate.json()
    assert [row["id"] for row in payload["important_emails"]] == [mail_b.id]
    assert [row["id"] for row in payload["all_emails"]] == [mail_b.id]
    assert mail_a.id not in {row["id"] for row in payload["all_emails"]}


def test_briefing_email_section_only_lists_the_owners_mail(db):
    """The Morning briefing's email context (an unattended run) is owner-scoped."""
    from janus.services.dashboard_service import DashboardService

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    _seed_two_mailboxes(db, alice, bob)

    section = DashboardService(db)._build_briefing_email_section(bob, models.BriefingEmailWindow.LAST_24_HOURS)

    assert "Quarterly report bob" in section
    assert "Quarterly report alice" not in section


def _unwrap(output):
    """Unwrap a plan-step tool's spotlighted output (JAN-127); errors stay plain strings."""
    if isinstance(output, str) and output.startswith("<untrusted_tool_output"):
        return json.loads(output.split("\n", 1)[1].rsplit("\n", 1)[0])
    return output


def _mail_step_tools(db, user: models.User, task_mode: str, tool_ids: tuple[str, ...]) -> dict:
    """Prepare a plan step in ``task_mode`` the way chat / unattended runs do and index its tools."""
    from janus.agent_tools import DEFAULT_TOOL_REGISTRY
    from janus.capability_profile import user_connected_capabilities
    from janus.runtime_adapter import RuntimeAdapter

    capabilities = user_connected_capabilities(db, user)
    if task_mode == "chat":
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id="chat-mail",
            objective="Check my mail",
            requested_tool_ids=tool_ids,
            task_mode="chat",
            granted_capabilities=capabilities,
            confirmed_tool_ids=frozenset(tool_ids),
            guard_tool_errors=True,
        )
    else:
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, DEFAULT_TOOL_REGISTRY.entries(), task_mode=task_mode
        )
        prepared = RuntimeAdapter(db, user).prepare_step(
            task_id=f"{task_mode}-mail",
            objective="Check my mail",
            requested_tool_ids=tool_ids,
            task_mode=task_mode,
            allowed_tool_ids=offered,
            granted_capabilities=capabilities,
            confirmed_tool_ids=confirmed,
            blocked_tool_ids=offered - confirmed,
            refusal_stubs=True,
            guard_tool_errors=True,
        )
    assert prepared.denials == ()
    return {tool.name: tool for tool in prepared.tools}


@pytest.mark.parametrize("task_mode", ["chat", *sorted(tool_policy.UNATTENDED_TASK_MODES)])
def test_mail_read_tools_never_return_another_users_mail(db, task_mode):
    """search_emails / get_email_detail / get_email_details_batch are owner-scoped in chat and unattended runs."""
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    _, _, mail_a, mail_b = _seed_two_mailboxes(db, alice, bob)
    tools = _mail_step_tools(db, bob, task_mode, ("mail",))

    by_query = _unwrap(tools["search_emails"].invoke({"query": "Quarterly"}))
    assert [row["id"] for row in by_query] == [mail_b.id]
    by_body = _unwrap(tools["search_emails"].invoke({"query": "report alice body"}))
    assert by_body == []
    by_window = _unwrap(tools["search_emails"].invoke({"since_hours": 24}))
    assert [row["id"] for row in by_window] == [mail_b.id]

    foreign_detail = tools["get_email_detail"].invoke({"email_id": mail_a.id})
    assert foreign_detail == f"ERROR: get_email_detail failed: email {mail_a.id} not found"
    own_detail = _unwrap(tools["get_email_detail"].invoke({"email_id": mail_b.id}))
    assert own_detail["id"] == mail_b.id

    batch = _unwrap(tools["get_email_details_batch"].invoke({"email_ids": [mail_a.id, mail_b.id]}))
    assert [row["id"] for row in batch["emails"]] == [mail_b.id]
    assert batch["missing_ids"] == [mail_a.id]


class _RecordingSMTP:
    """Fake SMTP transport recording which credentials and sender were used."""

    logins: list[str] = []
    senders: list[str] = []

    def __init__(self, host, port, timeout):
        del host, port, timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        del password
        self.logins.append(username)

    def send_message(self, message, from_addr, to_addrs):
        del message, to_addrs
        self.senders.append(from_addr)


@pytest.fixture
def recording_smtp(monkeypatch):
    """Replace SMTP with a recorder whose logins/senders start empty."""
    _RecordingSMTP.logins = []
    _RecordingSMTP.senders = []
    monkeypatch.setattr("janus.services.email_service.smtplib.SMTP", _RecordingSMTP)
    return _RecordingSMTP


def _add_sending_account(db, user: models.User) -> models.EmailAccount:
    account = _add_email_account(db, user)
    account.password = security.security_service.encrypt("smtp-secret")
    db.commit()
    return account


@pytest.mark.parametrize("task_mode", ["chat", "trigger"])
def test_send_email_tool_never_uses_another_users_account(db, recording_smtp, task_mode):
    """send_email only ever picks the run owner's own active account, explicit or implicit."""
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    account_a = _add_sending_account(db, alice)
    account_b = _add_sending_account(db, bob)
    tool_policy.set_policy(db, bob.id, "mail.send", True)
    send = _mail_step_tools(db, bob, task_mode, ("mail.send",))["send_email"]
    message = {"to": ["someone@example.com"], "subject": "Hi", "body": "Hello"}

    foreign = send.invoke({**message, "account_id": account_a.id})
    assert foreign == f"ERROR: send_email failed: email account {account_a.id} not found"
    assert recording_smtp.logins == []

    # Before JAN-286 the implicit choice spanned every user's active accounts,
    # so two users with one account each made this fail (or pick alice's).
    implicit = _unwrap(send.invoke(message))
    assert implicit["account_id"] == account_b.id
    assert recording_smtp.logins == ["bob"]
    assert recording_smtp.senders == ["bob@example.com"]


def test_send_email_without_an_own_account_never_falls_back_to_another_users(db, recording_smtp):
    """A user with no active account of their own cannot send, even when another user has exactly one."""
    from janus.services.email_service import EmailService

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    account_a = _add_sending_account(db, alice)
    message = {"to": ["someone@example.com"], "subject": "Hi", "body": "Hello"}

    with pytest.raises(ValueError, match="no active email accounts are configured"):
        EmailService(db).send_email(user_id=bob.id, **message)
    with pytest.raises(ValueError, match=f"email account {account_a.id} not found"):
        EmailService(db).send_email(user_id=bob.id, account_id=account_a.id, **message)
    assert recording_smtp.logins == []


# ---------------------------------------------------------------------------
# JAN-287: correction-derived rules only ever match and reorder the acting
# user's own rules (every rule has an owner, JAN-338)
# ---------------------------------------------------------------------------

_SHARED_SENDER_PATTERN = "^news@shared\\.example$"


def _add_sender_rule(db, user_id: int | None, *, name: str, order: int) -> models.Rule:
    rule = models.Rule(
        name=name,
        pattern=_SHARED_SENDER_PATTERN,
        field="sender",
        priority=models.Priority.IMPORTANT,
        is_active=True,
        order=order,
        user_id=user_id,
    )
    db.add(rule)
    db.commit()
    return rule


def _rule_state(db, rule_id: int) -> tuple:
    db.expire_all()
    rule = db.get(models.Rule, rule_id)
    return (rule.user_id, rule.name, rule.priority, rule.is_active, rule.order)


def test_relabel_by_b_never_touches_a_rules(client, db):
    """B's correction creates B's own rule; A's matching rules are not renamed, reordered, or retired."""
    alice = _create_and_login(client, db, "alice")
    first_a = _add_sender_rule(db, alice.id, name="Alice news", order=7)
    duplicate_a = _add_sender_rule(db, alice.id, name="Alice news again", order=3)
    before = {rule.id: _rule_state(db, rule.id) for rule in (first_a, duplicate_a)}
    bob = _create_and_login(client, db, "bob")
    mail_b = _add_recent_email(db, _add_email_account(db, bob), "Newsletter", priority=models.Priority.DIGEST)

    response = client.patch(f"/api/emails/{mail_b.id}/priority", params={"priority": "ignore"})

    assert response.status_code == 200, response.text
    assert {rule_id: _rule_state(db, rule_id) for rule_id in before} == before
    bob_rules = db.query(models.Rule).filter(models.Rule.user_id == bob.id).all()
    assert [(rule.pattern, rule.priority, rule.is_active) for rule in bob_rules] == [
        (_SHARED_SENDER_PATTERN, models.Priority.IGNORE, True)
    ]
    # The next order is computed among bob's own rules, not alice's.
    assert bob_rules[0].order == 0
    assert [rule["id"] for rule in client.get("/api/emails/rules").json()] == [bob_rules[0].id]


def test_relabel_tool_by_b_never_touches_a_rules(db):
    """The relabel_email tool goes through the same owner-scoped rule learning."""
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    rule_a = _add_sender_rule(db, alice.id, name="Alice news", order=7)
    before = _rule_state(db, rule_a.id)
    mail_b = _add_recent_email(db, _add_email_account(db, bob), "Newsletter", priority=models.Priority.DIGEST)
    relabel = _mail_step_tools(db, bob, "chat", ("mail.label",))["relabel_email"]

    assert _unwrap(relabel.invoke({"email_id": mail_b.id, "priority": "ignore"}))["priority"] == "ignore"

    assert _rule_state(db, rule_a.id) == before
    assert db.query(models.Rule).filter(models.Rule.user_id == bob.id).count() == 1


@pytest.mark.parametrize("model", [models.Rule, models.EmailAccount, models.PlannerAccount])
def test_rules_and_accounts_cannot_be_stored_without_an_owner(db, model):
    """``user_id`` is ``NOT NULL`` in the models and the migrations alike, so no row is ownerless (JAN-338)."""
    from sqlalchemy.exc import IntegrityError

    db.add(model(name="Ownerless"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# JAN-295: IMAP rule evaluation applies only the mail owner's rules
# JAN-296: reminder memory and planner outlook in email extraction are owner-scoped
# ---------------------------------------------------------------------------


class _RecordingLLM:
    """Offline stand-in for ``LLMService`` that records every extraction context it is given."""

    calls: list[tuple[str, object]] = []

    def __init__(self, *args, **kwargs):
        pass

    def classify_email(self, email_data, planner_context=None, **kwargs):
        type(self).calls.append(("classify_email", planner_context))
        return models.Priority.DIGEST, "recorded", models.ConfidenceLevel.LOW

    def extract_email_reminders(self, email_data, reminder_context):
        type(self).calls.append(("extract_email_reminders", reminder_context))
        return []

    def extract_email_topics(self, email_data, context):
        type(self).calls.append(("extract_email_topics", context["reminder_context"]))
        return []

    def extract_planner_suggestions(self, email_data, planner_context=None):
        return []


@pytest.fixture
def recording_llm(monkeypatch):
    """Replace the LLM with :class:`_RecordingLLM` and keep mail on the synchronous classifier path."""
    from janus import llm_service

    _RecordingLLM.calls = []
    monkeypatch.setattr(llm_service, "LLMService", _RecordingLLM)
    monkeypatch.setattr(config.settings, "runtime_trigger_worker_enabled", False)
    return _RecordingLLM


def _incoming(account: models.EmailAccount, subject: str) -> dict:
    return {
        "message_id": f"<incoming-{subject.replace(' ', '-')}-{account.id}@test>",
        "account_id": account.id,
        "uid": 1,
        "subject": subject,
        "sender": _SHARED_SENDER,
        "snippet": f"{subject} snippet",
        "received_at": time_utils.utcnow(),
    }


def _add_priority_rule(db, user_id: int | None, priority: models.Priority, *, order: int) -> models.Rule:
    rule = _add_sender_rule(db, user_id, name=f"rule-{user_id}-{priority.value}", order=order)
    rule.priority = priority
    db.commit()
    return rule


def test_a_rule_never_classifies_bs_incoming_mail(db, recording_llm):
    """A's earlier-ordered SPAM rule never files B's mail; B's own rule decides it (JAN-295)."""
    from janus import processor

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    account_a, account_b = _add_email_account(db, alice), _add_email_account(db, bob)
    _add_priority_rule(db, alice.id, models.Priority.SPAM, order=0)
    _add_priority_rule(db, bob.id, models.Priority.IMPORTANT, order=5)

    mail_b = processor.EmailProcessor(db).process_email(_incoming(account_b, "Bob newsletter"))
    mail_a = processor.EmailProcessor(db).process_email(_incoming(account_a, "Alice newsletter"))

    assert mail_b.priority == models.Priority.IMPORTANT
    assert mail_a.priority == models.Priority.SPAM


def test_a_rule_alone_never_matches_bs_mail(db):
    """Without a rule of B's own no rule matches B's mail (JAN-295); mail without an owner matches none."""
    from janus import processor
    from janus.services.email_service import EmailService

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    account_a, account_b = _add_email_account(db, alice), _add_email_account(db, bob)
    _add_priority_rule(db, alice.id, models.Priority.SPAM, order=0)
    proc = processor.EmailProcessor(db)

    assert proc._evaluate_rules(_incoming(account_b, "Bob newsletter")) is None
    assert proc._evaluate_rules(_incoming(account_a, "Alice newsletter")) == models.Priority.SPAM
    assert EmailService(db).get_active_rules_for_mail_owner(None) == []


def _seed_reminder_and_outlook(db, user: models.User, account: models.EmailAccount) -> None:
    """Give ``user`` one active reminder (from their own mail) and one upcoming planner event."""
    source = _add_recent_email(db, account, f"Source {user.username}")
    now = time_utils.utcnow()
    planner_account = _add_google_planner_account(db, user)
    planner_account.name = f"Google Calendar {user.username}"
    db.add_all(
        [
            models.ReminderMemory(
                source_email_id=source.id,
                kind=models.ReminderKind.EVENT,
                status=models.ReminderStatus.ACTIVE,
                title=f"Reminder of {user.username}",
                notes=f"Private note of {user.username}",
                due_at=now + timedelta(days=3),
                dedupe_key=f"{account.id}:event:reminder-of-{user.username}",
            ),
            models.CalendarEvent(
                planner_account_id=planner_account.id,
                external_id=f"evt-{user.username}",
                summary=f"Offsite of {user.username}",
                start_time=now + timedelta(days=10),
                end_time=now + timedelta(days=10, hours=1),
                source_type=models.CalendarEventSource.PLANNER,
            ),
        ]
    )
    db.commit()


def _seed_two_reminder_owners(db):
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    account_a, account_b = _add_email_account(db, alice), _add_email_account(db, bob)
    _seed_reminder_and_outlook(db, alice, account_a)
    _seed_reminder_and_outlook(db, bob, account_b)
    return alice, bob, account_a, account_b


def test_bs_email_extraction_context_never_contains_a_reminders_or_planner_events(db, recording_llm):
    """Processing B's mail feeds only B's reminders and planner outlook to every extractor (JAN-296)."""
    from janus import processor

    _, bob, _, account_b = _seed_two_reminder_owners(db)
    _add_priority_rule(db, bob.id, models.Priority.IMPORTANT, order=0)

    processor.EmailProcessor(db).process_email(_incoming(account_b, "Bob update"))

    contexts = {name: json.dumps(context, default=str) for name, context in recording_llm.calls}
    # Reminder capture, plus topic extraction with reminder linking, which now
    # runs for B (the mail's owner) rather than the first user.
    assert set(contexts) == {"extract_email_reminders", "extract_email_topics"}
    for context in contexts.values():
        assert "Reminder of bob" in context
        assert "Offsite of bob" in context
        assert "alice" not in context


def test_briefing_reminders_and_outlook_only_list_the_owners(db):
    """The briefing summary and planner context list only the owner's reminders and planner events (JAN-296)."""
    from janus import planner_service

    _, bob, _, _ = _seed_two_reminder_owners(db)
    planner = planner_service.PlannerService(db)

    summary = planner.get_todays_summary(user_id=bob.id)
    context = json.dumps(planner.get_context_data(user_id=bob.id), default=str)

    for text in (summary, context):
        assert "Reminder of bob" in text
        assert "Offsite of bob" in text
        assert "alice" not in text


def test_correction_reasoning_context_is_the_correcting_users(db, recording_llm):
    """B marking mail IMPORTANT never sends A's (the first user's) planner data to the LLM (JAN-296)."""
    from janus.services.email_service import EmailService

    _, bob, _, account_b = _seed_two_reminder_owners(db)
    mail_b = _add_recent_email(db, account_b, "Bob digest", priority=models.Priority.DIGEST)

    EmailService(db).update_email_priority_for_user(mail_b.id, models.Priority.IMPORTANT, bob)

    [(name, context)] = recording_llm.calls
    assert name == "classify_email"
    assert "Reminder of bob" in json.dumps(context, default=str)
    assert "alice" not in json.dumps(context, default=str)


def test_reminder_dedupe_never_rewrites_another_users_reminder(db):
    """A candidate whose dedupe key matches A's reminder never re-points or rewrites it (JAN-296)."""
    from janus.reminder_service import ReminderService

    alice, bob, account_a, account_b = _seed_two_reminder_owners(db)
    mail_a = _add_recent_email(db, account_a, "Alice invite")
    mail_b = _add_recent_email(db, account_b, "Bob invite")
    # A colliding key (for example from before an account changed owner):
    # A's reminder carries the key B's candidate derives.
    colliding = models.ReminderMemory(
        source_email_id=mail_a.id,
        kind=models.ReminderKind.EVENT,
        status=models.ReminderStatus.ACTIVE,
        title="Alice dinner",
        due_at=time_utils.utcnow() + timedelta(days=5),
        dedupe_key=f"{account_b.id}:event:dinner",
    )
    db.add(colliding)
    db.commit()

    created = ReminderService(db).persist_email_reminders(
        mail_b,
        [{"kind": "event", "title": "Bob dinner", "due_at": colliding.due_at.isoformat(), "dedupe_hint": "dinner"}],
    )
    db.commit()
    db.expire_all()

    assert created == []
    row = db.get(models.ReminderMemory, colliding.id)
    assert (row.source_email_id, row.title) == (mail_a.id, "Alice dinner")
    assert "Alice dinner" in [r.title for r in ReminderService(db).get_active_reminders(alice.id)]
    assert "Alice dinner" not in [r.title for r in ReminderService(db).get_active_reminders(bob.id)]


# ---------------------------------------------------------------------------
# Calendar and task agent tools are owner-scoped (Semgrep JAN-267 finding)
# ---------------------------------------------------------------------------


def _seed_planner_rows(db, user: models.User) -> tuple[models.CalendarEvent, models.CalendarEvent, models.TaskRecord]:
    """Give ``user`` a synced event, a synthetic reminder event from their own mail, and a Jira task."""
    now = time_utils.utcnow()
    calendar = _add_google_planner_account(db, user)
    calendar.name = f"Google Calendar {user.username}"
    jira = models.PlannerAccount(
        name=f"Jira {user.username}", provider=models.Provider.JIRA, user_id=user.id, token="{}"
    )
    db.add(jira)
    db.flush()
    synced = models.CalendarEvent(
        planner_account_id=calendar.id,
        external_id=f"evt-{user.username}",
        summary=f"Offsite of {user.username}",
        start_time=now + timedelta(hours=2),
        end_time=now + timedelta(hours=3),
        source_type=models.CalendarEventSource.PLANNER,
    )
    reminder_event = models.CalendarEvent(
        planner_account_id=None,
        external_id=f"janus-reminder:{user.username}",
        summary=f"[Prep] Review of {user.username}",
        start_time=now + timedelta(hours=4),
        end_time=now + timedelta(hours=5),
        source_type=models.CalendarEventSource.JANUS_REMINDER,
    )
    task = models.TaskRecord(planner_account_id=jira.id, external_id=f"JAN-{user.id}", title=f"Task of {user.username}")
    db.add_all([synced, reminder_event, task])
    db.flush()
    source = _add_recent_email(db, _add_email_account(db, user), f"Source {user.username}")
    db.add(
        models.ReminderMemory(
            source_email_id=source.id,
            kind=models.ReminderKind.PREPARATION,
            status=models.ReminderStatus.ACTIVE,
            title=f"Review of {user.username}",
            due_at=now + timedelta(hours=5),
            dedupe_key=f"{user.username}:prep",
            synthetic_calendar_event_id=reminder_event.id,
        )
    )
    db.commit()
    return synced, reminder_event, task


@pytest.mark.parametrize("task_mode", ["chat", *sorted(tool_policy.UNATTENDED_TASK_MODES)])
def test_calendar_and_task_tools_never_return_another_users_rows(db, task_mode):
    """list/get calendar events and tasks only see the run owner's planner accounts and reminders."""
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    alice_synced, alice_reminder, alice_task = _seed_planner_rows(db, alice)
    bob_synced, bob_reminder, bob_task = _seed_planner_rows(db, bob)
    tools = _mail_step_tools(db, bob, task_mode, ("calendar", "jira"))

    events = _unwrap(tools["list_calendar_events"].invoke({"lookahead_days": 1}))
    assert sorted(row["id"] for row in events) == sorted([bob_synced.id, bob_reminder.id])
    for foreign in (alice_synced, alice_reminder):
        assert tools["get_event_detail"].invoke({"event_id": foreign.id}) == (
            f"ERROR: get_event_detail failed: event {foreign.id} not found"
        )
    assert _unwrap(tools["get_event_detail"].invoke({"event_id": bob_reminder.id}))["id"] == bob_reminder.id

    tasks = _unwrap(tools["list_tasks"].invoke({}))
    assert [row["id"] for row in tasks] == [bob_task.id]
    assert tools["get_task_detail"].invoke({"task_id": alice_task.id}) == (
        f"ERROR: get_task_detail failed: task {alice_task.id} not found"
    )
    assert _unwrap(tools["get_task_detail"].invoke({"task_id": bob_task.id}))["id"] == bob_task.id


# ---------------------------------------------------------------------------
# JAN-288: knowledge sources, planner accounts, and WhatsApp accounts are read
# and mutated through owner-scoped service methods; foreign ids answer 404
# ---------------------------------------------------------------------------


def _login(client, username: str, password: str = "pw") -> None:
    """Logs an existing user in on ``client``."""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers[security.CSRF_HEADER_NAME] = resp.json()["csrf_token"]


def _add_knowledge_source(db, user: models.User, title: str = "notes.txt") -> models.KnowledgeSource:
    source = models.KnowledgeSource(
        user_id=user.id,
        source_type=models.KnowledgeSourceType.UPLOAD.value,
        title=title,
        status=models.KnowledgeSourceStatus.READY.value,
    )
    db.add(source)
    db.commit()
    return source


def _add_whatsapp_account(db, user: models.User) -> models.WhatsAppAccount:
    account = models.WhatsAppAccount(user_id=user.id, display_name=f"wa-{user.username}", is_enabled=False)
    db.add(account)
    db.commit()
    return account


def test_knowledge_sources_list_and_delete_reject_cross_user(client, db):
    """Bob neither sees nor deletes Alice's knowledge source; Alice can delete her own."""
    alice = _create_and_login(client, db, "alice")
    source_id = _add_knowledge_source(db, alice).id
    _create_and_login(client, db, "bob")

    assert client.get("/api/knowledge/sources").json() == []
    assert client.delete(f"/api/knowledge/sources/{source_id}").status_code == 404
    db.expire_all()
    assert db.get(models.KnowledgeSource, source_id) is not None

    _login(client, "alice")
    assert [row["id"] for row in client.get("/api/knowledge/sources").json()] == [source_id]
    assert client.delete(f"/api/knowledge/sources/{source_id}").status_code == 204
    db.expire_all()
    assert db.get(models.KnowledgeSource, source_id) is None


def test_planner_accounts_and_google_calendars_reject_cross_user(client, db):
    """Bob's planner account list excludes Alice's, and her Google Calendar account is not his."""
    alice = _create_and_login(client, db, "alice")
    account_id = _add_google_planner_account(db, alice).id
    bob = _create_and_login(client, db, "bob")

    assert client.get("/api/planner/accounts").json() == []
    assert client.get("/api/planner/google/calendars").status_code == 404
    assert client.put("/api/planner/google/calendars", json={"calendar_ids": ["primary"]}).status_code == 404
    db.expire_all()
    assert not db.get(models.PlannerAccount, account_id).selected_calendar_ids

    own = models.PlannerAccount(name="Jira bob", provider=models.Provider.JIRA, user_id=bob.id, token="{}")
    db.add(own)
    db.commit()
    own_id = own.id
    assert [row["id"] for row in client.get("/api/planner/accounts").json()] == [own_id]


def test_whatsapp_account_update_and_delete_reject_cross_user(client, db, monkeypatch):
    """Bob cannot rename or delete Alice's WhatsApp account."""
    stopped: list[int] = []
    monkeypatch.setattr("janus.workers.whatsapp.stop_account_connector", lambda account_id: stopped.append(account_id))
    alice = _create_and_login(client, db, "alice")
    account_id = _add_whatsapp_account(db, alice).id
    _create_and_login(client, db, "bob")

    assert client.patch(f"/api/whatsapp/accounts/{account_id}", json={"display_name": "x"}).status_code == 404
    assert client.delete(f"/api/whatsapp/accounts/{account_id}").status_code == 404
    assert stopped == []
    db.expire_all()
    assert db.get(models.WhatsAppAccount, account_id).display_name == "wa-alice"


def test_jan288_service_methods_treat_foreign_rows_as_missing(db, monkeypatch):
    """The JAN-288 owner-scoped service methods refuse foreign rows, independent of any router."""
    from janus.services import knowledge_sources, oauth_connection_service, planner_account_service
    from janus.services.whatsapp_service import WhatsAppService

    monkeypatch.setattr("janus.workers.whatsapp.stop_account_connector", lambda account_id: None)
    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    source = _add_knowledge_source(db, alice)
    planner = _add_google_planner_account(db, alice)
    whatsapp = _add_whatsapp_account(db, alice)
    whatsapp_service = WhatsAppService(db)

    assert knowledge_sources.list_sources_for_user(db, bob.id) == []
    assert knowledge_sources.get_source_for_user(db, source.id, bob.id) is None
    assert knowledge_sources.delete_source_for_user(db, source.id, bob.id) is False
    assert planner_account_service.list_accounts_for_user(db, bob.id) == []
    assert planner_account_service.get_google_calendar_account_for_user(db, bob.id) is None
    assert oauth_connection_service.delete_connection(db, bob.id, "google") is False
    assert whatsapp_service.update_account_for_user(whatsapp.id, bob, {"display_name": "x"}) is None
    assert whatsapp_service.delete_account_for_user(whatsapp.id, bob) is False

    db.expire_all()
    assert db.get(models.KnowledgeSource, source.id) is not None
    assert db.get(models.PlannerAccount, planner.id) is not None
    assert db.get(models.WhatsAppAccount, whatsapp.id).display_name == "wa-alice"

    assert [row.id for row, _count in knowledge_sources.list_sources_for_user(db, alice.id)] == [source.id]
    assert planner_account_service.get_google_calendar_account_for_user(db, alice.id).id == planner.id
    assert whatsapp_service.update_account_for_user(whatsapp.id, alice, {"display_name": "y"}).display_name == "y"
    assert knowledge_sources.delete_source_for_user(db, source.id, alice.id) is True
    assert oauth_connection_service.delete_connection(db, alice.id, "google") is True
    assert whatsapp_service.delete_account_for_user(whatsapp.id, alice) is True


# JAN-298: background email notifications follow the mail owner's preference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("muted", ["alice", "bob"])
def test_one_users_notification_preference_never_decides_anothers_mail(
    db, testing_session_local, recording_llm, monkeypatch, muted
):
    """Muting IMPORTANT notifications only silences the muting user's own mail (JAN-298)."""
    from unittest.mock import MagicMock

    from janus.workers import email as email_worker

    users = {"alice": _make_user(db, "alice"), "bob": _make_user(db, "bob")}
    accounts = {name: _add_email_account(db, user) for name, user in users.items()}
    for order, user in enumerate(users.values()):
        _add_priority_rule(db, user.id, models.Priority.IMPORTANT, order=order)
    db.add(models.NotificationPreference(user_id=users[muted].id, notify_important=False))
    db.commit()

    notify = MagicMock()
    monkeypatch.setattr(email_worker.notifications.NotificationService, "notify", notify)
    monkeypatch.setattr(email_worker, "database", MagicMock(SessionLocal=testing_session_local))

    for name, account in accounts.items():
        email_worker.email_callback(_incoming(account, f"{name} urgent"))

    notified = [call.args[0].subject for call in notify.call_args_list]
    unmuted = "bob" if muted == "alice" else "alice"
    assert notified == [f"{unmuted} urgent"]


# ---------------------------------------------------------------------------
# JAN-204: the Tasks page only shows the caller's trigger runs and delegated tasks
# ---------------------------------------------------------------------------


def _seed_alices_tasks(db, alice: models.User) -> None:
    from runtime_task_fixtures import add_definition, add_delegation, add_trigger, save_plan

    definition = add_definition(db, alice.id, "Alice's review")
    add_trigger(db, alice.id, "alice-run", definition_id=definition.id)
    save_plan(db, alice.id, "task-alice-run", "Alice's private objective")
    add_delegation(db, alice.id, "alice-child", parent_task_id="task-alice-run", minutes=1)


def test_runtime_tasks_api_rejects_cross_user(client, db):
    """Bob's Tasks list is empty and Alice's run and delegation read as 404, never 403."""
    alice = _create_and_login(client, db, "alice")
    _seed_alices_tasks(db, alice)
    assert client.get("/api/runtime/tasks/").json()["total"] == 2
    assert client.get("/api/runtime/tasks/triggers/alice-run").status_code == 200
    assert client.get("/api/runtime/tasks/delegations/alice-child").status_code == 200

    _create_and_login(client, db, "bob")
    page = client.get("/api/runtime/tasks/").json()
    assert page["items"] == [] and page["total"] == 0
    for source in ("trigger", "definition", "delegated"):
        assert client.get("/api/runtime/tasks/", params={"source": source}).json()["total"] == 0
    assert client.get("/api/runtime/tasks/triggers/alice-run").status_code == 404
    assert client.get("/api/runtime/tasks/delegations/alice-child").status_code == 404


def test_runtime_task_service_methods_treat_foreign_rows_as_missing(db):
    """The JAN-204 *_for_user methods filter by owner themselves, independent of any router."""
    from runtime_task_fixtures import add_delegation, add_trigger

    from janus.services import runtime_task_service

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    _seed_alices_tasks(db, alice)
    # Bob's own run shares Alice's task id; it must not pick up her plan state or child.
    add_trigger(db, bob.id, "bob-run", task_id="task-alice-run", minutes=2)
    add_delegation(db, bob.id, "bob-child", parent_task_id="task-alice-run", minutes=3)

    bobs = runtime_task_service.list_tasks_for_user(db, bob.id)
    assert {item.ref_id for item in bobs.items} == {"bob-run", "bob-child"}
    bob_run = next(item for item in bobs.items if item.ref_id == "bob-run")
    assert bob_run.plan_status is None and bob_run.child_count == 1
    assert runtime_task_service.get_trigger_task_for_user(db, bob.id, "alice-run") is None
    assert runtime_task_service.get_delegated_task_for_user(db, bob.id, "alice-child") is None

    bob_detail = runtime_task_service.get_trigger_task_for_user(db, bob.id, "bob-run")
    assert bob_detail is not None and bob_detail.steps == []
    assert [child.ref_id for child in bob_detail.children] == ["bob-child"]
    bob_child = runtime_task_service.get_delegated_task_for_user(db, bob.id, "bob-child")
    assert bob_child is not None and bob_child.parent is not None and bob_child.parent.ref_id == "bob-run"
    assert "Alice" not in bob_detail.model_dump_json() + bob_child.model_dump_json()

    alice_detail = runtime_task_service.get_trigger_task_for_user(db, alice.id, "alice-run")
    assert alice_detail is not None and alice_detail.definition_name == "Alice's review"
    assert [child.ref_id for child in alice_detail.children] == ["alice-child"]


# ---------------------------------------------------------------------------
# JAN-334: wiki page provenance and the knowledge graph are owner-scoped
# ---------------------------------------------------------------------------


def _seed_knowledge(db, user: models.User, label: str):
    """A topic with a cited, entity-mentioning passage and a cited memory fact, and its wiki page."""
    source = models.KnowledgeSource(user_id=user.id, source_type="email", title=f"{label} mail", status="ready")
    db.add(source)
    db.flush()
    chunk = models.KnowledgeChunk(
        source_id=source.id,
        user_id=user.id,
        ordinal=0,
        text=f"{label} secret passage",
        content_hash=f"{label}-hash",
        char_count=20,
        token_estimate=5,
    )
    fact = models.AgentMemoryFact(user_id=user.id, summary=f"{label} secret fact")
    topic = models.TrackedTopic(user_id=user.id, slug=f"{label}-topic", title=f"{label} topic")
    entity = models.KnowledgeEntity(
        user_id=user.id, entity_type="person", name=f"{label} person", normalized_name=f"{label} person", aliases=[]
    )
    db.add_all([chunk, fact, topic, entity])
    db.flush()
    db.add(models.KnowledgeMention(user_id=user.id, chunk_id=chunk.id, entity_id=entity.id))
    page = models.WikiPage(
        user_id=user.id,
        slug=f"{label}-topic",
        title=f"{label} topic",
        subject_type="topic",
        subject_id=topic.id,
        body="Text [1] [2].",
        citations=[{"n": 1, "chunk_id": chunk.id}, {"n": 2, "fact_id": fact.id}],
    )
    db.add(page)
    db.commit()
    return page


def test_wiki_provenance_rejects_cross_user(client, db):
    """User B gets a 404 for user A's page provenance, and only sees their own passages and facts."""
    user_a = _create_and_login(client, db, "prov_a")
    page_a = _seed_knowledge(db, user_a, "alpha")
    assert client.get(f"/api/wiki/pages/{page_a.id}/provenance").status_code == 200

    user_b = _create_and_login(client, db, "prov_b")
    page_b = _seed_knowledge(db, user_b, "beta")
    assert client.get(f"/api/wiki/pages/{page_a.id}/provenance").status_code == 404
    own = client.get(f"/api/wiki/pages/{page_b.id}/provenance").json()
    assert "alpha" not in json.dumps(own)
    assert own["citations"][0]["excerpt"] == "beta secret passage"
    assert own["citations"][1]["fact"]["summary"] == "beta secret fact"


def test_knowledge_graph_only_shows_own_topics_and_entities(client, db):
    """The graph never contains another user's topics or entities."""
    user_a = _create_and_login(client, db, "graph_a")
    _seed_knowledge(db, user_a, "alpha")
    _create_and_login(client, db, "graph_b")
    user_b = db.query(models.User).filter_by(username="graph_b").one()
    _seed_knowledge(db, user_b, "beta")
    graph = client.get("/api/knowledge/graph").json()
    assert {node["label"] for node in graph["nodes"]} == {"beta topic", "beta person"}
    assert "alpha" not in json.dumps(graph)


def test_jan334_service_methods_treat_foreign_pages_as_missing(db):
    """``page_provenance_for_user`` refuses another user's page and ``graph_for_user`` reads only the caller's rows."""
    from janus.services import knowledge_provenance, wiki_service

    alice, bob = _make_user(db, "alice"), _make_user(db, "bob")
    page_a = _seed_knowledge(db, alice, "alpha")
    with pytest.raises(wiki_service.WikiError):
        knowledge_provenance.page_provenance_for_user(db, bob.id, page_a.id)
    assert knowledge_provenance.graph_for_user(db, bob.id) == {"nodes": [], "edges": []}
    own = knowledge_provenance.page_provenance_for_user(db, alice.id, page_a.id)
    assert own["citations"][0]["excerpt"] == "alpha secret passage"
