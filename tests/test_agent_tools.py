"""Unit tests for LangChain-wrapped Janus agent tools."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from janus import models, security, time_utils
from janus.agent_toolboxes import shared as toolbox_shared
from janus.agent_tools import build_tool_set


def _make_user(db, username: str = "agent-user") -> models.User:
    """Create and persist a test user."""
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_admin_user(db, username: str = "agent-admin") -> models.User:
    """Create and persist an admin test user."""
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _tool_map(db, user: models.User, **kwargs):
    """Build the tool set and return it indexed by tool name."""
    return {tool.name: tool for tool in build_tool_set(db, user, **kwargs)}


def _grant_calendar_connection(db, user: models.User) -> None:
    """Give a user a real, active calendar-capable PlannerAccount (the "dashboard" gate)."""
    db.add(
        models.PlannerAccount(
            user_id=user.id,
            name=f"calendar-{user.id}",
            provider=models.Provider.GOOGLE_CALENDAR,
            token="token",
            is_active=True,
        )
    )
    db.commit()


def test_build_tool_set_gates_on_real_connections(db):
    """Mail/rules/calendar/jira/dashboard need a real connection; the rest is always visible.

    GitHub/Linear (JAN-171), memory, topics, and the local planner (JAN-201), and the
    event log and trigger tools are ungated.
    ``web_search`` (JAN-211) is chat-only and never appears via this
    build path, which has no task identity to gate on.
    """
    user = _make_user(db)
    tools = _tool_map(db, user)

    assert set(tools) == {
        "get_current_datetime",
        "list_recent_events",
        "list_my_triggers",
        "get_trigger",
        "list_my_trigger_history",
        "create_scheduled_trigger",
        "cancel_trigger",
        "propose_trigger_definition",
        "revise_proposal",
        "list_my_proposals",
        "withdraw_proposal",
        # Read-only character state (JAN-301); propose_character_state is chat-only.
        "get_character_state",
        "list_prs",
        "merge_pr",
        "list_linear_teams",
        "list_linear_tasks",
        "add_linear_task",
        "add_linear_subtask",
        "update_linear_task",
        "comment_on_linear_task",
        "start_linear_task",
        "start_linear_subtask",
        "list_memory_facts",
        "search_facts",
        "add_memory_fact",
        "update_fact",
        "delete_fact",
        "group_facts",
        "list_topics",
        "create_topic",
        "search_topics",
        "get_topic_detail",
        "update_topic_state",
        "search_knowledge",
        "get_entity",
        "list_wiki",
        "read_wiki",
        "propose_wiki_edit",
        "list_docs",
        "read_doc",
        "search_docs",
        "list_local_tasks",
        "get_local_task",
        "create_local_task",
        "update_local_task",
        "complete_local_task",
        "move_local_task",
        "reorder_local_task",
    }


def test_dashboard_tools_list_update_and_clear_widget_preferences(db):
    """Planner-enabled agents should be able to inspect and update dashboard widget settings."""
    user = _make_user(db)
    _grant_calendar_connection(db, user)
    tools = _tool_map(db, user)

    listed = tools["list_dashboard_widgets"].invoke({})
    assert listed[0] == {
        "widget_key": "briefing",
        "is_visible": True,
        "is_collapsed": False,
        "prompt_override": None,
        "briefing_email_window": None,
    }

    updated = tools["update_dashboard_widget"].invoke(
        {"widget_key": "briefing", "is_visible": False, "is_collapsed": True}
    )
    assert updated == {
        "widget_key": "briefing",
        "is_visible": False,
        "is_collapsed": True,
        "prompt_override": None,
        "briefing_email_window": None,
    }

    prompted = tools["set_dashboard_widget_prompt"].invoke(
        {"widget_key": "briefing", "prompt_override": "Focus on blockers first:\n{planner_summary}"}
    )
    assert prompted["prompt_override"] == "Focus on blockers first:\n{planner_summary}"

    cleared = tools["clear_dashboard_widget_prompt"].invoke({"widget_key": "briefing"})
    assert cleared["prompt_override"] is None


def test_dashboard_tool_prompt_override_accepts_free_form_instructions(db):
    """Briefing prompt overrides should accept free-form user instructions."""
    user = _make_user(db, username="briefing-template-user")
    _grant_calendar_connection(db, user)
    tools = _tool_map(db, user)

    plain = tools["set_dashboard_widget_prompt"].invoke(
        {"widget_key": "briefing", "prompt_override": "Highlight priorities only."}
    )
    assert plain["prompt_override"] == "Highlight priorities only."

    braced = tools["set_dashboard_widget_prompt"].invoke(
        {"widget_key": "briefing", "prompt_override": "Use {planner_summary} and {today}"}
    )
    assert braced["prompt_override"] == "Use {planner_summary} and {today}"


def test_dashboard_tool_prompt_override_rejects_unsupported_widgets(db):
    """Prompt override tools should reject widgets that do not consume prompt overrides."""
    user = _make_user(db, username="unsupported-widget-user")
    _grant_calendar_connection(db, user)
    tools = _tool_map(db, user)

    with pytest.raises(ValueError, match=r"only supported for: briefing"):
        tools["set_dashboard_widget_prompt"].invoke({"widget_key": "tasks", "prompt_override": "Use {planner_summary}"})

    with pytest.raises(ValueError, match=r"only supported for: briefing"):
        tools["clear_dashboard_widget_prompt"].invoke({"widget_key": "schedule"})


def _stub_briefing_context(monkeypatch, prompt_override: str | None = None) -> dict[str, object]:
    """Replace the dashboard briefing context with a canned one; returns the captured kwargs."""
    captured: dict[str, object] = {}

    def fake_context(self, user, include_calendar=True, include_tasks=True):
        captured["called"] = True
        return {
            "summary": "Today's plan",
            "topic_ids": [],
            "active_topics": [],
            "prompt_override": prompt_override,
            "briefing_email_window": None,
        }

    monkeypatch.setattr(
        "janus.agent_toolboxes.dashboard.DashboardService.get_briefing_generation_context",
        fake_context,
    )
    return captured


def _briefing_definition(db, user: models.User) -> models.TriggerDefinition:
    from janus import builtin_triggers
    from janus import trigger_definition_service as definitions

    builtin_triggers.ensure_builtin_definitions(db, user.id)
    definition = definitions.get_builtin_definition(db, user.id, builtin_triggers.BUILTIN_BRIEFING)
    assert definition is not None
    return definition


def _completed_briefing_run(db, definition, output: str, completed_at: datetime) -> None:
    db.add(
        models.RuntimeTrigger(
            user_id=definition.user_id,
            trigger_id=f"briefing-run-{output}",
            idempotency_key=f"briefing-run-{output}",
            task_id=f"briefing-run-{output}",
            kind="schedule",
            source="test",
            payload={},
            status=models.RuntimeTriggerStatus.COMPLETED.value,
            completed_at=completed_at,
            definition_id=definition.id,
            output=output,
        )
    )
    db.commit()


def test_get_daily_briefing_returns_todays_briefing_run_and_saved_prompt_override(db, monkeypatch):
    """Without a refresh, the tool returns today's Morning briefing run output and never runs it again."""
    user = _make_user(db, username="briefing-tool-user")
    _grant_calendar_connection(db, user)
    captured = _stub_briefing_context(monkeypatch, prompt_override="Focus on blockers first:\n{planner_summary}")
    definition = _briefing_definition(db, user)
    now = datetime.now(UTC).replace(tzinfo=None)
    _completed_briefing_run(db, definition, "Yesterday's briefing", now - timedelta(days=2))
    _completed_briefing_run(db, definition, "Today's briefing", now)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("get_daily_briefing must not run the trigger without force_refresh")

    monkeypatch.setattr("janus.trigger_runtime.run_definition_inline", fail_run)
    tools = _tool_map(db, user)

    briefing = tools["get_daily_briefing"].invoke({})

    assert briefing == {
        "summary": "Today's plan",
        "briefing": "Today's briefing",
        "prompt_override": "Focus on blockers first:\n{planner_summary}",
    }
    assert captured == {"called": True}


def test_get_daily_briefing_is_none_before_todays_run(db, monkeypatch):
    """With no completed briefing run today, the tool reports no briefing instead of generating one."""
    user = _make_user(db, username="briefing-tool-none-user")
    _grant_calendar_connection(db, user)
    _stub_briefing_context(monkeypatch)
    definition = _briefing_definition(db, user)
    _completed_briefing_run(db, definition, "Old briefing", datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2))
    tools = _tool_map(db, user)

    assert tools["get_daily_briefing"].invoke({})["briefing"] is None


def test_get_daily_briefing_force_refresh_runs_the_briefing_trigger_inline(db, monkeypatch):
    """force_refresh runs the user's Morning briefing definition now and returns that run's output."""
    user = _make_user(db, username="briefing-refresh-user")
    _grant_calendar_connection(db, user)
    _stub_briefing_context(monkeypatch)
    definition = _briefing_definition(db, user)
    _completed_briefing_run(db, definition, "Stale briefing", datetime.now(UTC).replace(tzinfo=None))
    ran: list[int] = []

    def fake_run_definition_inline(session_factory, definition_id, **_kwargs):
        ran.append(definition_id)
        return SimpleNamespace(status=models.RuntimeTriggerStatus.COMPLETED.value, output="Fresh briefing")

    monkeypatch.setattr("janus.trigger_runtime.run_definition_inline", fake_run_definition_inline)
    tools = _tool_map(db, user)

    briefing = tools["get_daily_briefing"].invoke({"force_refresh": True})

    assert briefing["briefing"] == "Fresh briefing"
    assert ran == [definition.id]


def test_get_daily_briefing_force_refresh_reports_no_briefing_when_the_run_fails(db, monkeypatch):
    """A failed refresh run must not surface a stale or partial briefing."""
    user = _make_user(db, username="briefing-refresh-fail-user")
    _grant_calendar_connection(db, user)
    _stub_briefing_context(monkeypatch)
    monkeypatch.setattr(
        "janus.trigger_runtime.run_definition_inline",
        lambda *_args, **_kwargs: SimpleNamespace(status=models.RuntimeTriggerStatus.FAILED.value, output=None),
    )
    tools = _tool_map(db, user)

    assert tools["get_daily_briefing"].invoke({"force_refresh": True})["briefing"] is None


def test_dashboard_widget_key_error_lists_current_enum_values():
    """Invalid widget-key errors should be generated from the current enum values."""
    expected = ", ".join(widget_key.value for widget_key in models.DashboardWidgetKey)

    with pytest.raises(ValueError, match=expected):
        toolbox_shared.parse_dashboard_widget_key("unknown-widget")


def test_topic_tools_are_available_without_topic_access_flag(db):
    """Topic tools are ungated for the single Janus agent (JAN-201), even with topic_access off."""
    user = _make_user(db, username="topic-gating-user")
    tools = _tool_map(db, user)

    assert {"list_topics", "search_topics", "get_topic_detail", "create_topic", "update_topic_state"} <= set(tools)


def test_topic_read_tools_use_isolated_sessions(db, testing_session_local):
    """Topic read tools should not commit unrelated pending rows from the chat session."""
    user = _make_user(db, username="topic-read-isolation-user")
    expired_snoozed_topic = models.TrackedTopic(
        user_id=user.id,
        slug="incident-response",
        title="Incident response",
        summary="Production issue investigation and follow-up.",
        status=models.TopicStatus.SNOOZED,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
        snoozed_until=time_utils.utcnow() - timedelta(minutes=5),
    )
    conversation = models.Conversation(user_id=user.id, title="Topic read isolation")
    db.add_all([expired_snoozed_topic, conversation])
    db.commit()
    db.refresh(expired_snoozed_topic)
    db.refresh(conversation)

    tools = _tool_map(db, user)
    db.add(
        models.ConversationMessage(
            conversation_id=conversation.id,
            role=models.MessageRole.USER,
            content="Pending topic read note",
        )
    )

    listed = tools["list_topics"].invoke({})
    assert listed[0]["id"] == expired_snoozed_topic.id
    assert listed[0]["status"] == "active"

    searched = tools["search_topics"].invoke({"query": "incident"})
    assert searched[0]["id"] == expired_snoozed_topic.id
    assert searched[0]["status"] == "active"

    verification_db = testing_session_local()
    try:
        refreshed_topic = verification_db.get(models.TrackedTopic, expired_snoozed_topic.id)
        assert refreshed_topic is not None
        assert refreshed_topic.status == models.TopicStatus.ACTIVE
        assert refreshed_topic.snoozed_until is None
        assert (
            verification_db.query(models.ConversationMessage)
            .filter(models.ConversationMessage.conversation_id == conversation.id)
            .count()
            == 0
        )
    finally:
        verification_db.close()


def test_topic_tools_list_create_and_update_topics(db, testing_session_local):
    """Topic-enabled agents can inspect and update the current user's tracked topics."""
    user = _make_user(db, username="topic-tool-user")
    topic = models.TrackedTopic(
        user_id=user.id,
        slug="incident-response",
        title="Incident response",
        summary="Production issue investigation and follow-up.",
        status=models.TopicStatus.ACTIVE,
        origin=models.TopicOrigin.AUTO,
        confidence=models.ConfidenceLevel.HIGH,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)

    db.add(
        models.TopicEvidence(
            topic_id=topic.id,
            source_type=models.TopicEvidenceSource.EMAIL,
            source_id=101,
            excerpt="Production incident follow-up",
            evidence_role=models.TopicEvidenceRole.TRIGGER,
        )
    )
    db.commit()

    conversation = models.Conversation(user_id=user.id, title="Topic tool isolation")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    tools = _tool_map(db, user)
    db.add(
        models.ConversationMessage(
            conversation_id=conversation.id,
            role=models.MessageRole.USER,
            content="Pending topic note",
        )
    )

    listed = tools["list_topics"].invoke({})
    assert listed[0]["title"] == "Incident response"

    created = tools["create_topic"].invoke(
        {
            "title": "Home blog",
            "summary": "GitHub workflows and publishing updates for the home blog.",
            "pinned": True,
        }
    )
    assert created["title"] == "Home blog"
    assert created["origin"] == "manual"
    assert created["status"] == "active"
    assert created["pinned"] is True
    assert created["user_confirmed"] is True

    verification_db = testing_session_local()
    try:
        assert (
            verification_db.query(models.ConversationMessage)
            .filter(models.ConversationMessage.conversation_id == conversation.id)
            .count()
            == 0
        )
    finally:
        verification_db.close()

    searched = tools["search_topics"].invoke({"query": "incident"})
    assert searched[0]["id"] == topic.id

    detail = tools["get_topic_detail"].invoke({"topic_id": topic.id})
    assert detail["evidence"][0]["source_type"] == "email"

    updated = tools["update_topic_state"].invoke({"topic_id": topic.id, "status": "resolved", "pinned": True})
    assert updated["status"] == "resolved"
    assert updated["pinned"] is True


def test_memory_tool_mutations_use_isolated_sessions(db, testing_session_local):
    """Memory tool writes should not commit unrelated pending rows from the chat session."""
    user = _make_user(db, username="memory-isolation-user")
    conversation = models.Conversation(user_id=user.id, title="Memory tool isolation")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    tools = _tool_map(db, user)
    db.add(
        models.ConversationMessage(
            conversation_id=conversation.id,
            role=models.MessageRole.USER,
            content="Pending memory note",
        )
    )

    created = tools["add_memory_fact"].invoke({"summary": "Home blog", "detail": "Tracked as a durable fact."})
    assert created["summary"] == "Home blog"

    verification_db = testing_session_local()
    try:
        assert verification_db.get(models.AgentMemoryFact, created["id"]) is not None
        assert (
            verification_db.query(models.ConversationMessage)
            .filter(models.ConversationMessage.conversation_id == conversation.id)
            .count()
            == 0
        )
    finally:
        verification_db.close()


def test_update_dashboard_widget_raises_clear_error_when_updated_preference_is_missing(db, monkeypatch):
    """Missing updated preferences should surface a tool-friendly ValueError instead of StopIteration."""
    user = _make_user(db, username="missing-widget-user")
    _grant_calendar_connection(db, user)
    tools = _tool_map(db, user)

    monkeypatch.setattr(
        "janus.agent_toolboxes.dashboard.DashboardService.update_widget_preferences",
        lambda self, current_user, updates: [],
    )

    with pytest.raises(ValueError, match=r"updated widget preference not found for: briefing"):
        tools["update_dashboard_widget"].invoke({"widget_key": "briefing", "is_visible": False})


def test_get_current_datetime_returns_iso_timestamp(db):
    """Datetime tool should return a parseable ISO-8601 timestamp."""
    user = _make_user(db)
    tools = _tool_map(db, user)

    value = tools["get_current_datetime"].invoke({})
    parsed = time_utils.normalize_timestamp(value)
    assert parsed is not None


def test_mail_tools_search_detail_and_relabel(db, monkeypatch):
    """Mail tools should search, fetch detail, and persist relabeling."""
    user = _make_user(db)
    fixed_now = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("janus.agent_toolboxes.mail.time_utils.utcnow", lambda: fixed_now)
    account = models.EmailAccount(name="Test Mail", email="test@example.com", password="secret", user_id=user.id)
    db.add(account)
    db.flush()

    older = models.EmailRecord(
        account_id=account.id,
        message_id="old-1",
        subject="Weekly digest",
        sender="digest@example.com",
        snippet="Digest email",
        priority=models.Priority.DIGEST,
        received_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    )
    recent = models.EmailRecord(
        account_id=account.id,
        message_id="new-1",
        subject="Urgent: deployment",
        sender="ops@example.com",
        snippet="Please check production.",
        body="Deployment runbook says the prod migration is urgent.",
        priority=models.Priority.DIGEST,
        received_at=datetime(2026, 1, 16, 11, 0, tzinfo=UTC),
    )
    db.add_all([older, recent])
    db.commit()

    tools = _tool_map(db, user)
    assert "get_email_details_batch" in tools
    assert "relabel_emails_batch" in tools
    assert "send_email" in tools

    results = tools["search_emails"].invoke({"query": "urgent", "limit": 5, "since_hours": 2})
    assert [row["id"] for row in results] == [recent.id]
    body_results = tools["search_emails"].invoke({"query": "runbook", "limit": 5})
    assert [row["id"] for row in body_results] == [recent.id]
    yesterday_results = tools["search_emails"].invoke({"date_preset": "yesterday", "limit": 5})
    assert [row["id"] for row in yesterday_results] == [older.id]
    interval_results = tools["search_emails"].invoke(
        {
            "start_at": "2026-01-16T05:30:00-05:00",
            "end_at": "2026-01-16T06:30:00-05:00",
            "limit": 5,
        }
    )
    assert [row["id"] for row in interval_results] == [recent.id]
    assert tools["search_emails"].invoke({"query": "urgent", "limit": 0}) == []

    with pytest.raises(ValueError, match="provide query or a bounded lower time filter"):
        tools["search_emails"].invoke({"query": "   ", "limit": 5})

    detail = tools["get_email_detail"].invoke({"email_id": recent.id})
    assert detail["sender"] == "ops@example.com"
    assert detail["priority"] == "digest"

    relabeled = tools["relabel_email"].invoke({"email_id": recent.id, "priority": "important"})
    assert relabeled["priority"] == "important"
    db.refresh(recent)
    assert recent.priority == models.Priority.IMPORTANT


def test_mail_tools_bound_body_only_search_to_recent_candidates(db, monkeypatch):
    """Body-only mail searches should avoid scanning every stored email body."""
    user = _make_user(db, username="mail-body-bound-user")
    monkeypatch.setattr("janus.agent_toolboxes.mail._BODY_SEARCH_RECENT_CANDIDATE_LIMIT", 1)

    account = models.EmailAccount(name="Bounded Mail", email="bounded@example.com", password="secret", user_id=user.id)
    db.add(account)
    db.flush()

    db.add_all(
        [
            models.EmailRecord(
                account_id=account.id,
                message_id="recent-non-match",
                subject="Most recent note",
                sender="ops@example.com",
                snippet="No body hit here",
                body="Nothing relevant.",
                priority=models.Priority.DIGEST,
                received_at=datetime(2026, 1, 16, 11, 0, tzinfo=UTC),
            ),
            models.EmailRecord(
                account_id=account.id,
                message_id="older-body-match",
                subject="Older note",
                sender="ops@example.com",
                snippet="Still no metadata hit",
                body="Legacy runbook details only appear in the body.",
                priority=models.Priority.DIGEST,
                received_at=datetime(2026, 1, 15, 11, 0, tzinfo=UTC),
            ),
        ]
    )
    db.commit()

    tools = _tool_map(db, user)

    assert tools["search_emails"].invoke({"query": "runbook", "limit": 1}) == []


def test_mail_tools_support_batch_detail_relabel_and_sending(db, monkeypatch):
    """Mail tools should support batch operations and SMTP-backed sending."""
    user = _make_user(db, username="mail-batch-user")
    account = models.EmailAccount(
        name="Primary Mail",
        email="sender@example.com",
        username="sender-user",
        imap_server="imap.example.com",
        imap_port=993,
        password=security.security_service.encrypt("smtp-secret"),
        is_active=True,
        user_id=user.id,
    )
    db.add(account)
    db.flush()

    first = models.EmailRecord(
        account_id=account.id,
        message_id="batch-1",
        subject="First message",
        sender="first@example.com",
        snippet="First",
        priority=models.Priority.DIGEST,
        received_at=time_utils.utcnow() - timedelta(hours=4),
    )
    second = models.EmailRecord(
        account_id=account.id,
        message_id="batch-2",
        subject="Second message",
        sender="second@example.com",
        snippet="Second",
        priority=models.Priority.IMPORTANT,
        received_at=time_utils.utcnow() - timedelta(hours=2),
    )
    db.add_all([first, second])
    db.commit()

    tools = _tool_map(db, user)

    batch_detail = tools["get_email_details_batch"].invoke({"email_ids": [first.id, 9999, second.id]})
    assert [row["id"] for row in batch_detail["emails"]] == [first.id, second.id]
    assert batch_detail["missing_ids"] == [9999]

    relabeled = tools["relabel_emails_batch"].invoke({"email_ids": [first.id, second.id, 7777], "priority": "ignore"})
    assert [row["id"] for row in relabeled["updated"]] == [first.id, second.id]
    assert relabeled["missing_ids"] == [7777]
    db.refresh(first)
    db.refresh(second)
    assert first.priority == models.Priority.IGNORE
    assert second.priority == models.Priority.IGNORE

    sent: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            sent["ehlo_called"] = sent.get("ehlo_called", 0) + 1

        def starttls(self):
            sent["starttls_called"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message, from_addr, to_addrs):
            sent["message_to"] = message["To"]
            sent["message_cc"] = message["Cc"]
            sent["from_addr"] = from_addr
            sent["to_addrs"] = to_addrs
            sent["subject"] = message["Subject"]
            sent["body"] = message.get_content().strip()

    monkeypatch.setattr("janus.services.email_service.smtplib.SMTP", FakeSMTP)

    result = tools["send_email"].invoke(
        {
            "to": ["recipient@example.com"],
            "cc": ["copy@example.com"],
            "bcc": ["blind@example.com"],
            "subject": "Hello from Janus",
            "body": "This is a test email.",
        }
    )

    assert result["status"] == "sent"
    assert result["account_id"] == account.id
    assert result["smtp_server"] == "smtp.example.com"
    assert result["smtp_port"] == 587
    assert sent["login"] == ("sender-user", "smtp-secret")
    assert sent["message_to"] == "recipient@example.com"
    assert sent["message_cc"] == "copy@example.com"
    assert sent["from_addr"] == "sender@example.com"
    assert sent["to_addrs"] == ["recipient@example.com", "copy@example.com", "blind@example.com"]
    assert sent["subject"] == "Hello from Janus"
    assert sent["body"] == "This is a test email."
    assert sent["starttls_called"] is True


def test_calendar_tools_list_and_detail(db):
    """Calendar tools should filter by date range and expose event detail."""
    user = _make_user(db)
    planner = models.PlannerAccount(
        name="Calendar", provider=models.Provider.GOOGLE_CALENDAR, token="token", user_id=user.id
    )
    db.add(planner)
    db.flush()

    start = time_utils.utcnow().replace(hour=9, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    today_event = models.CalendarEvent(
        planner_account_id=planner.id,
        external_id="ev-1",
        summary="Standup",
        start_time=start,
        end_time=start + timedelta(minutes=30),
        location="Room A",
        description="Daily standup",
    )
    future_event = models.CalendarEvent(
        planner_account_id=planner.id,
        external_id="ev-2",
        summary="Planning",
        start_time=start + timedelta(days=3),
        end_time=start + timedelta(days=3, hours=1),
    )
    db.add_all([today_event, future_event])
    db.commit()

    tools = _tool_map(db, user)

    listed = tools["list_calendar_events"].invoke({"date": start.date().isoformat(), "lookahead_days": 1})
    assert [row["id"] for row in listed] == [today_event.id]

    detail = tools["get_event_detail"].invoke({"event_id": today_event.id})
    assert detail["summary"] == "Standup"
    assert detail["source_type"] == models.CalendarEventSource.PLANNER.value


def test_task_tools_list_and_detail(db):
    """Task tools should support status filtering and detail lookup."""
    user = _make_user(db)
    planner = models.PlannerAccount(name="Jira", provider=models.Provider.JIRA, token="token", user_id=user.id)
    db.add(planner)
    db.flush()

    todo = models.TaskRecord(
        planner_account_id=planner.id,
        external_id="TASK-1",
        title="Open item",
        status=models.TaskStatus.TODO,
    )
    done = models.TaskRecord(
        planner_account_id=planner.id,
        external_id="TASK-2",
        title="Closed item",
        status=models.TaskStatus.DONE,
    )
    db.add_all([todo, done])
    db.commit()

    tools = _tool_map(db, user)

    listed = tools["list_tasks"].invoke({"status": "Todo", "limit": 10})
    assert [row["id"] for row in listed] == [todo.id]
    assert tools["list_tasks"].invoke({"status": "Todo", "limit": 0}) == []
    assert tools["list_tasks"].invoke({"limit": 0}) == []

    detail = tools["get_task_detail"].invoke({"task_id": done.id})
    assert detail["title"] == "Closed item"
    assert detail["status"] == models.TaskStatus.DONE.value


def test_memory_tools_list_and_add_fact_for_current_user(db):
    """Memory tools should support listing, searching, updating, grouping, and deleting."""
    user = _make_user(db, username="alice")
    other_user = _make_user(db, username="bob")
    first_fact = models.AgentMemoryFact(
        user_id=user.id,
        summary="Alice fact",
        detail="Visible",
        status=models.AgentMemoryStatus.ACTIVE,
    )
    second_fact = models.AgentMemoryFact(
        user_id=user.id,
        summary="Project memory",
        detail="Discuss deployment",
        status=models.AgentMemoryStatus.ACTIVE,
    )
    archived_fact = models.AgentMemoryFact(
        user_id=user.id,
        summary="Old memory",
        detail="Archived detail",
        status=models.AgentMemoryStatus.ARCHIVED,
    )
    db.add_all(
        [
            first_fact,
            second_fact,
            archived_fact,
            models.AgentMemoryFact(
                user_id=other_user.id,
                summary="Bob fact",
                detail="Hidden",
                status=models.AgentMemoryStatus.ACTIVE,
            ),
        ]
    )
    db.commit()
    db.refresh(first_fact)
    db.refresh(second_fact)
    db.refresh(archived_fact)

    tools = _tool_map(db, user)

    listed = tools["list_memory_facts"].invoke({})
    assert [row["summary"] for row in listed] == ["Project memory", "Alice fact"]

    searched = tools["search_facts"].invoke({"query": "deploy", "limit": 10})
    assert [row["id"] for row in searched] == [second_fact.id]

    searched_archived = tools["search_facts"].invoke({"query": "archived", "include_archived": True, "limit": 10})
    assert [row["id"] for row in searched_archived] == [archived_fact.id]

    created = tools["add_memory_fact"].invoke({"summary": "Prefers concise responses", "detail": "No fluff"})
    assert created["status"] == models.AgentMemoryStatus.ACTIVE.value

    updated = tools["update_fact"].invoke(
        {"fact_id": first_fact.id, "summary": "Alice fact updated", "detail": "Fresh context"}
    )
    assert updated["summary"] == "Alice fact updated"

    grouped = tools["group_facts"].invoke(
        {
            "fact_ids": [first_fact.id, second_fact.id],
            "summary": "Deployment memory",
            "detail": "Keep deployment context in one place.",
        }
    )
    assert grouped["grouped_fact_ids"] == [first_fact.id, second_fact.id]
    assert grouped["merged_fact"]["summary"] == "Deployment memory"

    db.refresh(first_fact)
    db.refresh(second_fact)
    assert first_fact.status == models.AgentMemoryStatus.ARCHIVED
    assert second_fact.status == models.AgentMemoryStatus.ARCHIVED
    assert first_fact.compacted_into_id == grouped["merged_fact"]["id"]
    assert second_fact.compacted_into_id == grouped["merged_fact"]["id"]

    deleted = tools["delete_fact"].invoke({"fact_id": grouped["merged_fact"]["id"]})
    assert deleted == {"fact_id": grouped["merged_fact"]["id"], "deleted": True}
    assert tools["delete_fact"].invoke({"fact_id": 999999}) == {"fact_id": 999999, "deleted": False}

    stored = db.get(models.AgentMemoryFact, created["id"])
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.summary == "Prefers concise responses"


def test_non_admin_tool_set_excludes_skill_tools(db):
    """Non-admin users should not receive skill-library mutation tools."""
    user = _make_user(db)
    tools = _tool_map(db, user)

    assert "create_skill" not in tools
    assert "list_skills" not in tools
    assert "update_skill" not in tools
    assert "search_remote_skills" not in tools
    assert "import_remote_skill" not in tools


def test_admin_tool_set_includes_skill_tools(db):
    """Admin status alone gates the skill-library toolbox."""
    admin = _make_admin_user(db, "flag-admin")
    tools = _tool_map(db, admin)

    assert {"create_skill", "list_skills", "update_skill", "search_remote_skills", "import_remote_skill"} <= set(tools)


def test_admin_skill_tools_create_update_and_list(db):
    """Admin skill tools should persist changes and return structured metadata."""
    admin = _make_admin_user(db)
    tools = _tool_map(db, admin)

    created_skill = tools["create_skill"].invoke(
        {
            "name": "authoring-skill",
            "description": "Can update definitions.",
            "content": "Use these steps to update configs.",
        }
    )
    assert created_skill["id"] > 0
    assert created_skill["name"] == "authoring-skill"
    assert "assigned_agent_count" not in created_skill
    assert tools["list_skills"].invoke({}) == [
        {
            "id": created_skill["id"],
            "name": "authoring-skill",
            "description": "Can update definitions.",
            "license": None,
            "created_at": created_skill["created_at"],
            "updated_at": created_skill["updated_at"],
        }
    ]

    updated_skill = tools["update_skill"].invoke(
        {
            "skill_id": created_skill["id"],
            "description": "Can update and validate definitions.",
            "content": "Validate and then persist config updates.",
        }
    )
    assert updated_skill["id"] == created_skill["id"]
    assert updated_skill["description"] == "Can update and validate definitions."
    assert db.get(models.AgentSkill, created_skill["id"]) is not None


def test_admin_skill_tools_log_success_and_failure_events(db):
    """Skill tools should write SUCCESS/WARNING audit events for outcomes."""
    admin = _make_admin_user(db)
    tools = _tool_map(db, admin)

    tools["create_skill"].invoke(
        {
            "name": "evented-skill",
            "description": "Skill to verify events.",
            "content": "Used by tests.",
        }
    )

    with pytest.raises(ValueError, match="already exists"):
        tools["create_skill"].invoke(
            {
                "name": "evented-skill",
                "description": "duplicate",
                "content": "duplicate",
            }
        )

    events = (
        db.query(models.Event).filter(models.Event.source == "SkillManagementTools").order_by(models.Event.id).all()
    )
    assert len(events) == 2
    assert [event.event_type for event in events] == [models.EventType.SUCCESS, models.EventType.WARNING]
    assert [event.message for event in events] == [
        "create_skill: skill created",
        "create_skill: failed",
    ]


def test_admin_skill_tools_can_search_and_import_remote_skills(db, monkeypatch):
    """Admin tools should support community catalog search and import flows."""
    admin = _make_admin_user(db)
    tools = _tool_map(db, admin)

    monkeypatch.setattr(
        "janus.agent_tools.skill_catalog_service.search_remote_skills",
        lambda query, limit=10: [
            {
                "remote_id": "incident-commander",
                "name": "incident-commander",
                "description": "Coordinate incidents.",
                "license": "MIT",
                "content": "Run incident triage and status reporting.",
                "source_url": "https://github.com/anthropics/skills/tree/main/skills/incident-commander",
            }
        ],
    )
    monkeypatch.setattr(
        "janus.agent_tools.skill_catalog_service.get_remote_skill",
        lambda remote_id: {
            "remote_id": remote_id,
            "name": "Incident Commander",
            "description": "Coordinate incidents.",
            "license": "MIT",
            "content": "Run incident triage and status reporting.",
            "source_url": "https://github.com/anthropics/skills/tree/main/skills/incident-commander",
        },
    )

    discovered = tools["search_remote_skills"].invoke({"query": "incident"})
    assert discovered[0]["remote_id"] == "incident-commander"

    imported = tools["import_remote_skill"].invoke({"remote_id": "incident-commander"})
    assert imported["name"] == "incident-commander"
    assert imported["license"] == "MIT"


# ---------------------------------------------------------------------------
# Skill name normalization tests
# ---------------------------------------------------------------------------


def test_create_skill_normalizes_human_readable_name(db):
    """create_skill converts a human-readable name to a slug before persisting."""
    admin = _make_admin_user(db, "norm-admin")
    tools = _tool_map(db, admin)

    result = tools["create_skill"].invoke({"name": "My Cool Skill", "description": "Test skill", "content": "# Skill"})

    assert result["name"] == "my-cool-skill"
    skill = db.query(models.AgentSkill).filter(models.AgentSkill.name == "my-cool-skill").first()
    assert skill is not None


def test_create_skill_raises_on_underivable_name(db):
    """create_skill raises ValueError when no valid slug can be derived from the name."""
    admin = _make_admin_user(db, "bad-name-admin")
    tools = _tool_map(db, admin)

    with pytest.raises(ValueError, match="unable to derive a valid skill name"):
        tools["create_skill"].invoke({"name": "---", "description": "Test skill", "content": "# Skill"})


def test_update_skill_normalizes_name(db):
    """update_skill converts a human-readable new name to a slug."""
    admin = _make_admin_user(db, "upd-admin")
    tools = _tool_map(db, admin)

    created = tools["create_skill"].invoke({"name": "original-skill", "description": "Desc", "content": "# Content"})
    skill_id = created["id"]

    result = tools["update_skill"].invoke({"skill_id": skill_id, "name": "Updated Skill Name"})

    assert result["name"] == "updated-skill-name"


def test_update_skill_raises_on_underivable_name(db):
    """update_skill raises ValueError when the new name produces an empty slug."""
    admin = _make_admin_user(db, "upd-bad-admin")
    tools = _tool_map(db, admin)

    created = tools["create_skill"].invoke({"name": "base-skill", "description": "Desc", "content": "# Content"})
    skill_id = created["id"]

    with pytest.raises(ValueError, match="unable to derive a valid skill name"):
        tools["update_skill"].invoke({"skill_id": skill_id, "name": "!!!"})


def test_github_and_linear_tools_are_always_built(db):
    """GitHub and Linear toolboxes are instance-wide integrations and are always built."""
    user = _make_user(db, "integration-user")
    tools = _tool_map(db, user)

    assert {"list_prs", "merge_pr", "list_linear_tasks", "add_linear_task", "add_linear_subtask"} <= set(tools)
    assert {"start_linear_task", "start_linear_subtask", "list_linear_teams"} <= set(tools)
    assert {"update_linear_task", "comment_on_linear_task"} <= set(tools)


def test_github_tools_list_and_merge_prs(db, monkeypatch):
    """GitHub tools should use the configured repository token and default repo."""
    user = _make_user(db, "github-tool-user")
    db.add(
        models.Settings(
            github_token=security.security_service.encrypt("ghs_test"),
            github_default_repo="thomasniebler/janus",
        )
    )
    db.commit()

    captured: dict[str, object] = {}

    class FakeGitHubService:
        def __init__(self, token: str):
            captured["token"] = token

        def close(self):
            return None

        def list_pull_requests(self, *, repo: str, state: str = "open", limit: int = 20):
            captured["list_repo"] = repo
            captured["state"] = state
            captured["limit"] = limit
            return [{"number": 184, "title": "Tighten layout", "url": "https://example/pr/184"}]

        def merge_pull_request(self, *, repo: str, pull_number: int, merge_method: str = "squash"):
            captured["merge_repo"] = repo
            captured["pull_number"] = pull_number
            captured["merge_method"] = merge_method
            return {"merged": True, "pull_number": pull_number, "repo": repo}

    monkeypatch.setattr("janus.agent_toolboxes.github.GitHubService", FakeGitHubService)

    tools = _tool_map(db, user)

    assert tools["list_prs"].invoke({}) == [{"number": 184, "title": "Tighten layout", "url": "https://example/pr/184"}]
    assert tools["merge_pr"].invoke({"pull_number": 184}) == {
        "merged": True,
        "pull_number": 184,
        "repo": "thomasniebler/janus",
    }
    assert captured == {
        "token": "ghs_test",
        "list_repo": "thomasniebler/janus",
        "state": "open",
        "limit": 20,
        "merge_repo": "thomasniebler/janus",
        "pull_number": 184,
        "merge_method": "squash",
    }


def test_github_tools_missing_token_uses_settings_copy(db):
    """GitHub tool errors should point users to the Settings UI with consistent copy."""
    user = _make_user(db, "github-tool-missing-token")
    tools = _tool_map(db, user)

    with pytest.raises(ValueError, match="Settings → GitHub"):
        tools["list_prs"].invoke({})


def test_linear_tools_list_create_and_start_tasks(db, monkeypatch):
    """Linear tools should list tasks, create tasks/subtasks, and assign Copilot."""
    user = _make_user(db, "linear-tool-user")
    db.add(
        models.Settings(
            linear_api_key=security.security_service.encrypt("lin_api_test"),
            linear_default_team_id="team_123",
        )
    )
    db.commit()

    captured: dict[str, object] = {"created": [], "assigned": []}

    class FakeLinearService:
        def __init__(self, api_key: str):
            captured["api_key"] = api_key

        def close(self):
            return None

        def choose_team(self, team_ref, default_ref):
            return team_ref or default_ref, None

        def list_team_issues(self, team_id: str, *, limit: int = 20, include_completed: bool = False):
            captured["listed"] = {"team_id": team_id, "limit": limit, "include_completed": include_completed}
            return [{"id": "lin-1", "identifier": "JAN-1", "title": "Inbox triage", "url": "https://linear/JAN-1"}]

        def create_issue(
            self,
            team_id: str,
            title: str,
            description: str,
            *,
            parent_id: str | None = None,
            assignee_id: str | None = None,
        ):
            captured["created"].append(
                {
                    "team_id": team_id,
                    "title": title,
                    "description": description,
                    "parent_id": parent_id,
                    "assignee_id": assignee_id,
                }
            )
            return {
                "id": f"issue-{len(captured['created'])}",
                "identifier": f"JAN-{len(captured['created'])}",
                "title": title,
                "url": f"https://linear/{title.replace(' ', '-')}",
            }

        def find_user(self, *candidate_names: str):
            captured["candidate_names"] = candidate_names
            return {"id": "copilot-user"}

        def assign_issue(self, issue_id: str, assignee_id: str):
            captured["assigned"].append({"issue_id": issue_id, "assignee_id": assignee_id})
            return {"id": issue_id, "identifier": "JAN-99", "title": "Assigned", "url": "https://linear/JAN-99"}

    monkeypatch.setattr("janus.agent_toolboxes.linear.LinearService", FakeLinearService)

    tools = _tool_map(db, user)

    assert tools["list_linear_tasks"].invoke({}) == [
        {"id": "lin-1", "identifier": "JAN-1", "title": "Inbox triage", "url": "https://linear/JAN-1"}
    ]
    open_pr = "Open the PR for the toolbox change so review can start this week."
    fix_comment = "Fix the last review comment about error copy, then re-request review."
    created_task = tools["add_linear_task"].invoke({"title": "Ship toolbox PR", "description": open_pr})
    assert created_task["identifier"] == "JAN-1"
    assert (
        tools["add_linear_subtask"].invoke(
            {"parent_issue_id": "lin-1", "title": "Address review", "description": fix_comment}
        )["identifier"]
        == "JAN-2"
    )
    assert tools["start_linear_task"].invoke({"issue_id": "lin-1"})["id"] == "lin-1"
    assert tools["start_linear_subtask"].invoke({"issue_id": "lin-2"})["id"] == "lin-2"
    assert captured == {
        "created": [
            {
                "team_id": "team_123",
                "title": "Ship toolbox PR",
                "description": open_pr,
                "parent_id": None,
                "assignee_id": None,
            },
            {
                "team_id": "team_123",
                "title": "Address review",
                "description": fix_comment,
                "parent_id": "lin-1",
                "assignee_id": None,
            },
        ],
        "assigned": [
            {"issue_id": "lin-1", "assignee_id": "copilot-user"},
            {"issue_id": "lin-2", "assignee_id": "copilot-user"},
        ],
        "api_key": "lin_api_test",
        "listed": {"team_id": "team_123", "limit": 20, "include_completed": False},
        "candidate_names": ("GitHub Copilot", "Github Copilot", "Copilot"),
    }


def test_linear_tools_missing_api_key_uses_settings_copy(db):
    """Linear tool errors should point users to the Settings UI with consistent copy."""
    user = _make_user(db, "linear-tool-missing-api-key")
    tools = _tool_map(db, user)

    with pytest.raises(ValueError, match="Settings → Linear"):
        tools["list_linear_tasks"].invoke({})
