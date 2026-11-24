"""Proactive secretary: the built-in trigger's gates and context, and the WhatsApp alert to the owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from janus import builtin_triggers, database, models, security, trigger_service
from janus import trigger_definition_service as definitions
from janus.agent_task_runner import AgentTaskRun
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_toolboxes.whatsapp import (
    NOTIFY_ME_TOOL_NAME,
    WHATSAPP_NOTIFY_ENTRY,
    build_whatsapp_notify_tools,
)
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.runtime_adapter import INJECTION_ESCALATED_TOOL_NAMES
from janus.services import secretary_service
from janus.tool_registry import ToolBuildContext
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_runtime import run_definition_inline
from janus.workers import whatsapp as whatsapp_worker

NUMBER = "+4915112345678"
SENT_AT = datetime(2026, 11, 14, 10, 0, 0)
NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def _user(db, username="boss") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("pw"))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _secretary(db, user) -> models.TriggerDefinition:
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    return definitions.get_builtin_definition(db, user.id, builtin_triggers.BUILTIN_SECRETARY)


def _run(db, user, name, *, definition=None) -> models.RuntimeTrigger:
    spec = ScheduledTriggerSpec(
        schedule_id=f"run-{name}",
        owner_id=user.id,
        task_id=f"task-{name}",
        objective=f"run {name}",
        due_at=datetime.now(UTC) + timedelta(hours=1),
        source="api",
    )
    record, _ = trigger_service.enqueue_trigger(db, spec.as_envelope())
    if definition is not None:
        record.definition_id = definition.id
        db.commit()
    return record


def _complete(db, run, *, output, at) -> None:
    run.status = models.RuntimeTriggerStatus.COMPLETED.value
    run.output = output
    run.completed_at = at.replace(tzinfo=None)
    db.commit()


def _remember_planner(db, user) -> str:
    """Record the planners' current content the way a finished real run does."""
    fingerprint = secretary_service.planner_fingerprint(db, user.id, now=NOON)
    secretary_service.remember_planner_fingerprint(db, user.id, fingerprint)
    return fingerprint


def _planner_account(db, user) -> models.PlannerAccount:
    account = models.PlannerAccount(
        user_id=user.id, name="Google Calendar", provider=models.Provider.GOOGLE_CALENDAR, token="x"
    )
    db.add(account)
    db.commit()
    return account


def _preference(db, user, **fields) -> models.NotificationPreference:
    preference = models.NotificationPreference(user_id=user.id, **fields)
    db.add(preference)
    db.commit()
    return preference


# -- the built-in definition ---------------------------------------------------------------


def test_secretary_is_seeded_enabled_hourly_with_its_delivery_tools(db):
    """Seeded active (the owner's choice), hourly, with the quietest delivery tools and no way to mail others."""
    secretary = _secretary(db, _user(db))

    assert secretary.enabled is True
    assert secretary.kind == "schedule" and secretary.schedule == {"type": "interval", "minutes": 60}
    assert secretary.origin == "system"
    assert {"conversations.start", "memory", "topics", "web_search", "triggers"} <= set(secretary.tool_ids)
    assert not {"mail.send", "whatsapp.send", "shell", "self_evolution"} & set(secretary.tool_ids)
    assert "NOTHING_TO_REPORT" in secretary.objective


def test_the_browser_timezone_is_adopted_by_an_untouched_secretary(db):
    """The waking hours are meaningless in UTC, so an unedited secretary follows the first browser timezone."""
    user = _user(db)
    secretary = _secretary(db, user)

    builtin_triggers.adopt_client_timezone(db, user.id, "Europe/Berlin")

    db.refresh(secretary)
    assert secretary.timezone == "Europe/Berlin"


# -- gates and context -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tz", "hour_utc", "expected"),
    [
        ("UTC", 12, True),
        ("UTC", 7, False),
        ("UTC", 21, False),
        ("Europe/Berlin", 20, False),
        ("Europe/Berlin", 7, True),
    ],
)
def test_waking_hours_follow_the_definitions_clock(tz, hour_utc, expected):
    """08:00 up to, not including, 21:00 on the clock of the definition's timezone."""
    now = datetime(2026, 11, 14, hour_utc, 30, tzinfo=UTC)
    assert secretary_service.within_active_hours(now, tz, 8, 21) is expected


def test_a_run_outside_waking_hours_costs_no_model_call(db):
    """Skipped with a note instead of a model call."""
    user = _user(db)
    secretary = _secretary(db, user)

    plan = secretary_service.prepare(db, user, secretary, now=datetime(2026, 11, 14, 3, 0, tzinfo=UTC))

    assert plan.context is None and "active hours" in plan.skip_output


def test_the_owners_waking_hours_setting_is_honoured(db):
    """A narrower window from the notification settings applies."""
    user = _user(db)
    secretary = _secretary(db, user)
    _preference(db, user, secretary_active_start_hour=13, secretary_active_end_hour=15)

    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is not None
    assert secretary_service.prepare(db, user, secretary, now=NOON.replace(hour=14)).skip_output is None


def test_the_first_run_always_thinks(db):
    """With no earlier run there is nothing to compare, so the model is called."""
    user = _user(db)
    secretary = _secretary(db, user)

    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert plan.skip_output is None and "waking hours" in plan.context


def test_nothing_changed_since_a_recent_run_skips_the_model_call(db):
    """A quiet inbox costs nothing: no change and a run under six hours old skips."""
    user = _user(db)
    secretary = _secretary(db, user)
    _complete(
        db,
        _run(db, user, "prev", definition=secretary),
        output=secretary_service.QUIET_OUTPUT,
        at=NOON - timedelta(hours=1),
    )

    _remember_planner(db, user)
    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert plan.context is None and plan.skip_output == "Nothing new since the last secretary run."


def test_a_new_reminder_or_old_run_lets_the_model_think_again(db):
    """A change since the last run, or six quiet hours, re-opens the gate."""
    user = _user(db)
    secretary = _secretary(db, user)
    _complete(
        db,
        _run(db, user, "prev", definition=secretary),
        output=secretary_service.QUIET_OUTPUT,
        at=NOON - timedelta(hours=7),
    )
    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is None

    _complete(
        db,
        _run(db, user, "prev2", definition=secretary),
        output=secretary_service.QUIET_OUTPUT,
        at=NOON - timedelta(hours=1),
    )
    _remember_planner(db, user)
    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is not None
    account = models.EmailAccount(user_id=user.id, name="work", email="boss@example.com")
    db.add(account)
    db.flush()
    source = models.EmailRecord(
        account_id=account.id,
        message_id="m1",
        subject="Passport",
        received_at=NOON.replace(tzinfo=None) - timedelta(days=2),
    )
    db.add(source)
    db.flush()
    db.add(
        models.ReminderMemory(
            source_email_id=source.id,
            kind=models.ReminderKind.PREPARATION,
            title="Renew passport",
            due_at=NOON.replace(tzinfo=None) + timedelta(days=3),
            dedupe_key="passport-1",
            updated_at=NOON.replace(tzinfo=None) - timedelta(minutes=10),
        )
    )
    db.commit()

    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert plan.skip_output is None and "1 reminders" in plan.context


def test_the_run_is_told_its_own_findings_and_what_the_owner_did_with_its_conversations(db):
    """History and engagement outcomes (replied / opened / ignored) reach the next run, quiet runs do not."""
    user = _user(db)
    secretary = _secretary(db, user)
    earlier = _run(db, user, "earlier", definition=secretary)
    _complete(db, earlier, output="Passport expires in 5 weeks.", at=NOON - timedelta(hours=9))
    _complete(
        db, _run(db, user, "quiet", definition=secretary), output="NOTHING_TO_REPORT", at=NOON - timedelta(hours=8)
    )
    for title, unread, replied in (("Passport", False, True), ("Gym plan", True, False), ("Tax letter", False, False)):
        conversation = models.Conversation(
            user_id=user.id,
            title=title,
            origin=models.ConversationOrigin.JANUS.value,
            origin_trigger_id=earlier.trigger_id,
            unread=unread,
        )
        conversation.messages.append(models.ConversationMessage(role=models.MessageRole.ASSISTANT, content="Hi"))
        if replied:
            conversation.messages.append(models.ConversationMessage(role=models.MessageRole.USER, content="Thanks"))
        db.add(conversation)
    db.commit()

    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert "Passport expires in 5 weeks." in plan.context and "NOTHING_TO_REPORT" not in plan.context
    assert '"Passport": replied' in plan.context
    assert '"Gym plan": ignored (still unread)' in plan.context
    assert '"Tax letter": opened, no reply' in plan.context
    assert plan.context.count("untrusted data") == 2


# -- the WhatsApp alert ------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    """Capture sends instead of reaching a live Neonize connection."""
    calls: list[tuple[int, str, str]] = []

    def fake_send(account_id, chat_jid, text):
        calls.append((account_id, chat_jid, text))
        return f"MSG{len(calls)}", SENT_AT

    monkeypatch.setattr(whatsapp_worker, "send_text_message", fake_send)
    return calls


def _alert_setup(db, *, enabled=True, number=NUMBER):
    user = _user(db, "alerted")
    account = models.WhatsAppAccount(user_id=user.id, display_name="Phone", is_enabled=True)
    db.add(account)
    db.commit()
    _preference(db, user, secretary_whatsapp_enabled=enabled, secretary_whatsapp_number=number)
    return user, account


def _notify(db, user, run_trigger_id, message="Your flight leaves at 06:10 tomorrow.", *, trace=None):
    (tool,) = build_whatsapp_notify_tools(
        ToolContext(db=db, user=user, turn_trace=trace), run_trigger_id=run_trigger_id
    )
    return tool.invoke({"message": message})


def test_the_alert_goes_to_the_owners_own_number_and_is_marked_with_its_run(db, sent):
    """Recipient is the configured number, never an argument; the stored message names the run."""
    user, account = _alert_setup(db)
    run = _run(db, user, "alert")

    result = _notify(db, user, run.trigger_id)

    assert result["sent"] is True
    assert sent == [(account.id, "4915112345678@s.whatsapp.net", "Your flight leaves at 06:10 tomorrow.")]
    message = db.query(models.WhatsAppMessage).one()
    assert message.origin_trigger_id == run.trigger_id
    assert message.direction == models.WhatsAppMessageDirection.OUTGOING


def test_the_tool_takes_only_a_message(db):
    """No recipient, conversation, or account argument exists to point it at someone else."""
    user, _ = _alert_setup(db)
    (tool,) = build_whatsapp_notify_tools(ToolContext(db=db, user=user), run_trigger_id="t")
    assert tool.name == NOTIFY_ME_TOOL_NAME and set(tool.args) == {"message"}


def test_the_alert_needs_the_owners_opt_in_and_a_number(db, sent):
    """Off by default: no switch or no number means nothing is sent."""
    user, _ = _alert_setup(db, enabled=False)
    with pytest.raises(PermissionError, match="alerts are off"):
        _notify(db, user, _run(db, user, "a").trigger_id)
    preference = secretary_service.get_preference(db, user.id)
    preference.secretary_whatsapp_enabled, preference.secretary_whatsapp_number = True, None
    db.commit()
    with pytest.raises(PermissionError, match="alerts are off"):
        _notify(db, user, _run(db, user, "b").trigger_id)
    assert sent == []


def test_a_step_without_a_run_sends_nothing(db, sent):
    """Fail closed without a durable run identity."""
    user, _ = _alert_setup(db)
    with pytest.raises(PermissionError, match="no trigger run"):
        _notify(db, user, None)
    assert sent == []


def test_one_alert_per_run_and_three_per_day(db, sent):
    """The per-run and rolling-day caps both hold."""
    user, _ = _alert_setup(db)
    first = _run(db, user, "r1")
    _notify(db, user, first.trigger_id)
    with pytest.raises(ValueError, match="already sent its WhatsApp alert"):
        _notify(db, user, first.trigger_id, "Again")
    _notify(db, user, _run(db, user, "r2").trigger_id, "Second")
    _notify(db, user, _run(db, user, "r3").trigger_id, "Third")

    with pytest.raises(ValueError, match="daily limit of 3"):
        _notify(db, user, _run(db, user, "r4").trigger_id, "Fourth")
    assert len(sent) == 3


@pytest.mark.parametrize(
    "message",
    ["See https://evil.example/x?d=secret", "Look ![x](http://a.b/c.png)", "Open www.evil.example", "x" * 601, "   "],
)
def test_the_alert_refuses_links_images_empty_and_overlong_text(db, sent, message):
    """Same no-exfiltration rule as Janus-started conversations, plus a short length cap."""
    user, _ = _alert_setup(db)
    with pytest.raises(ValueError):
        _notify(db, user, _run(db, user, "bad").trigger_id, message)
    assert sent == []


def test_the_alert_is_refused_after_a_suspected_injection(db, sent):
    """Content that looked like an injection earlier in the run blocks the alert."""
    user, _ = _alert_setup(db)
    trace = TurnTrace()
    trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))

    answer = _notify(db, user, _run(db, user, "inj").trigger_id, trace=trace)

    assert answer.startswith("BLOCKED: notify_me_on_whatsapp was not run")
    assert sent == [] and NOTIFY_ME_TOOL_NAME in INJECTION_ESCALATED_TOOL_NAMES


def test_a_missing_connection_tells_the_run_to_fall_back_to_a_conversation(db, monkeypatch):
    """A send failure is reported, not swallowed, and nothing is stored."""
    user, _ = _alert_setup(db)

    def offline(*_args):
        raise whatsapp_worker.WhatsAppNotConnectedError("offline")

    monkeypatch.setattr(whatsapp_worker, "send_text_message", offline)
    with pytest.raises(ValueError, match="use start_conversation instead"):
        _notify(db, user, _run(db, user, "off").trigger_id)
    assert db.query(models.WhatsAppMessage).count() == 0


def _build_context(db, user, task_mode, *, connected=frozenset({"whatsapp"})) -> ToolBuildContext:
    return ToolBuildContext(
        tool_context=ToolContext(db=db, user=user, connected_capabilities=connected), task_mode=task_mode
    )


@pytest.mark.parametrize(
    ("task_mode", "connected", "expected"),
    [
        ("trigger", frozenset({"whatsapp"}), True),
        ("automation", frozenset({"whatsapp"}), True),
        ("chat", frozenset({"whatsapp"}), False),
        ("delegated", frozenset({"whatsapp"}), False),
        ("trigger", frozenset(), False),
    ],
)
def test_the_entry_is_offered_only_to_unattended_runs_with_a_connection(db, task_mode, connected, expected):
    """Chat, delegated children, and runs without a WhatsApp connection never see it."""
    user, _ = _alert_setup(db)
    assert WHATSAPP_NOTIFY_ENTRY.enabled_when(_build_context(db, user, task_mode, connected=connected)) is expected


def test_the_entry_is_hidden_until_the_owner_switches_alerts_on(db):
    """The setting gates the offer itself, so a run cannot even see a disabled channel."""
    user, _ = _alert_setup(db, enabled=False)
    assert WHATSAPP_NOTIFY_ENTRY.enabled_when(_build_context(db, user, "trigger")) is False


def test_the_entry_is_registered_with_the_conversation_style_defaults():
    """Low risk, recommended, default Allowed unattended (no per-call approval), unlike ``whatsapp.send``."""
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == "whatsapp.notify_me")
    assert (entry.confirmation, entry.operation, entry.risk_level) == ("recommended", "write", "low")


# -- settings API --------------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """A logged-in client for the settings endpoint."""
    db.add(
        models.User(
            username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
        )
    )
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_preferences_default_and_store_a_normalized_number(auth_client):
    """Defaults are 08-21 and alerts off; a number is stored as +digits."""
    body = auth_client.get("/api/emails/notifications/preferences").json()
    assert (body["secretary_active_start_hour"], body["secretary_active_end_hour"]) == (8, 21)
    assert body["secretary_whatsapp_enabled"] is False and body["secretary_whatsapp_number"] is None

    response = auth_client.patch(
        "/api/emails/notifications/preferences",
        json={"secretary_whatsapp_enabled": True, "secretary_whatsapp_number": "0049 151 1234-5678"},
    )

    assert response.status_code == 200
    assert response.json()["secretary_whatsapp_number"] == NUMBER


def test_preferences_reject_a_national_number_and_an_inverted_window(auth_client):
    """A national number and an end before the start are 422s that change nothing."""
    assert (
        auth_client.patch(
            "/api/emails/notifications/preferences", json={"secretary_whatsapp_number": "015112345678"}
        ).status_code
        == 422
    )
    assert (
        auth_client.patch(
            "/api/emails/notifications/preferences",
            json={"secretary_active_start_hour": 20, "secretary_active_end_hour": 9},
        ).status_code
        == 422
    )
    body = auth_client.get("/api/emails/notifications/preferences").json()
    assert body["secretary_whatsapp_number"] is None and body["secretary_active_start_hour"] == 8


# -- skipped runs complete like any other and must not count as thinking ------------------------------


def test_skipped_runs_do_not_reset_the_gate_or_show_up_as_findings(db):
    """Hourly skip notes are completed runs; the gate and the history must look past them."""
    user = _user(db)
    secretary = _secretary(db, user)
    thought = _run(db, user, "thought", definition=secretary)
    _complete(db, thought, output="Passport expires in 5 weeks.", at=NOON - timedelta(hours=7))
    for hour in range(1, 7):
        skipped = _run(db, user, f"skip{hour}", definition=secretary)
        _complete(db, skipped, output=secretary_service.SKIP_NOTHING_NEW, at=NOON - timedelta(hours=7 - hour))

    assert secretary_service.last_completed_run(db, secretary).id == thought.id
    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    # Seven hours after the last real run, six skips later, the refresh still fires.
    assert plan.skip_output is None
    assert "Passport expires in 5 weeks." in plan.context
    assert secretary_service.SKIP_NOTHING_NEW not in plan.context


def test_changes_during_skipped_hours_are_seen_when_the_window_opens(db):
    """Mail that arrived overnight counts against the last real run, not the last skip note."""
    user = _user(db)
    secretary = _secretary(db, user)
    _complete(
        db, _run(db, user, "evening", definition=secretary), output="NOTHING_TO_REPORT", at=NOON - timedelta(hours=14)
    )
    _complete(
        db,
        _run(db, user, "dawn", definition=secretary),
        output=f"{secretary_service.SKIP_OUTSIDE_HOURS} (08:00-21:00).",
        at=NOON - timedelta(minutes=30),
    )
    account = models.EmailAccount(user_id=user.id, name="work", email="boss@example.com")
    db.add(account)
    db.flush()
    db.add(
        models.EmailRecord(
            account_id=account.id,
            message_id="night",
            subject="Urgent",
            received_at=(NOON - timedelta(hours=9)).replace(tzinfo=None),
        )
    )
    db.commit()

    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert plan.skip_output is None and "1 mail" in plan.context


# -- through the real trigger runtime ---------------------------------------------------------------


class _Executor:
    def __init__(self, output):
        self.output, self.calls, self.notify_answer = output, [], None

    def __call__(self, db, system_prompt, tools, objective):
        self.calls.append({"objective": objective, "tools": [tool.name for tool in tools]})
        notify = next((tool for tool in tools if tool.name == NOTIFY_ME_TOOL_NAME), None)
        if notify is not None:
            self.notify_answer = notify.invoke({"message": "Your flight leaves at 06:10 tomorrow."})
        return AgentTaskRun(output=self.output, tool_calls=())


def test_the_secretary_runs_once_then_skips_the_model_until_something_changes(db, testing_session_local, monkeypatch):
    """End to end: a run thinks with the delivery tools it may use; an immediate second run costs no model call."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    secretary = _secretary(db, user)
    _preference(db, user, secretary_active_start_hour=0, secretary_active_end_hour=24)  # always awake: no clock flake
    executor = _Executor("Passport expires in 5 weeks; I started a conversation about it.")

    first = run_definition_inline(testing_session_local, secretary.id, task_executor=executor)
    second = run_definition_inline(testing_session_local, secretary.id, task_executor=executor)

    assert first.status == "completed" and first.output.startswith("Passport expires")
    assert len(executor.calls) == 1
    objective, tools = executor.calls[0]["objective"], executor.calls[0]["tools"]
    assert objective.startswith("You are my secretary") and "waking hours" in objective
    assert "start_conversation" in tools
    # Alerts are off: the model only gets a refusal stub for the channel, and calling it sends nothing.
    assert NOTIFY_ME_TOOL_NAME in tools and executor.notify_answer.startswith("BLOCKED: notify_me_on_whatsapp")
    assert db.query(models.WhatsAppMessage).count() == 0
    assert "send_email" not in tools and "send_whatsapp_message" not in tools
    assert second.status == "completed" and second.output == secretary_service.SKIP_NOTHING_NEW


# -- synced calendar and task changes re-open the gate (review: the planner is the point of a secretary) -------


def _thought_an_hour_ago(db, user, secretary):
    _complete(
        db,
        _run(db, user, "prev", definition=secretary),
        output=secretary_service.QUIET_OUTPUT,
        at=NOON - timedelta(hours=1),
    )
    _remember_planner(db, user)


def _synced_event(db, account, external_id="evt-1", summary="Board meeting", start=None):
    event = models.CalendarEvent(
        planner_account_id=account.id,
        external_id=external_id,
        summary=summary,
        start_time=(start or NOON.replace(tzinfo=None) + timedelta(days=2)),
        end_time=(start or NOON.replace(tzinfo=None) + timedelta(days=2)) + timedelta(hours=1),
    )
    db.add(event)
    db.commit()
    return event


def test_an_unchanged_planner_keeps_the_gate_closed(db):
    """With synced events and nothing new, the hour is still skipped."""
    user = _user(db)
    secretary = _secretary(db, user)
    _synced_event(db, _planner_account(db, user))
    _thought_an_hour_ago(db, user, secretary)

    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output == secretary_service.SKIP_NOTHING_NEW


def test_a_newly_synced_calendar_event_reopens_the_gate(db):
    """A meeting that appears through the planner sync needs preparation, not a six-hour wait."""
    user = _user(db)
    secretary = _secretary(db, user)
    account = _planner_account(db, user)
    _thought_an_hour_ago(db, user, secretary)

    _synced_event(db, account, summary="Board meeting")
    plan = secretary_service.prepare(db, user, secretary, now=NOON)

    assert plan.skip_output is None and "calendar or tasks changed" in plan.context


def test_a_moved_event_a_synced_task_and_a_local_task_each_reopen_the_gate(db):
    """Edits in place (no timestamp on synced events), synced tasks, and Janus's own tasks all count."""
    user = _user(db)
    secretary = _secretary(db, user)
    account = _planner_account(db, user)
    event = _synced_event(db, account)
    _thought_an_hour_ago(db, user, secretary)

    event.start_time = event.start_time + timedelta(hours=3)
    db.commit()
    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is None

    _remember_planner(db, user)
    db.add(models.TaskRecord(planner_account_id=account.id, external_id="t-1", title="File taxes"))
    db.commit()
    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is None

    _remember_planner(db, user)
    db.add(models.PlannerTask(user_id=user.id, title="Call the bank"))
    db.commit()
    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output is None


def test_other_owners_planners_and_past_events_do_not_reopen_the_gate(db):
    """Another user's calendar, and an event that already ended yesterday, are not this owner's change."""
    user, other = _user(db), _user(db, "someone-else")
    secretary = _secretary(db, user)
    _thought_an_hour_ago(db, user, secretary)

    _synced_event(db, _planner_account(db, other))
    _synced_event(
        db, _planner_account(db, user), external_id="old", start=NOON.replace(tzinfo=None) - timedelta(days=2)
    )

    assert secretary_service.prepare(db, user, secretary, now=NOON).skip_output == secretary_service.SKIP_NOTHING_NEW


def test_a_run_that_thought_remembers_the_planner_but_a_skipped_one_does_not(db, testing_session_local, monkeypatch):
    """Finalize stores the fingerprint only after a real run, so a skip cannot hide a change."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    secretary = _secretary(db, user)
    _preference(db, user, secretary_active_start_hour=0, secretary_active_end_hour=24)
    account = _planner_account(db, user)
    executor = _Executor("Nothing urgent.")

    run_definition_inline(testing_session_local, secretary.id, task_executor=executor)
    db.expire_all()
    stored = secretary_service.get_preference_fingerprint(db, user.id)
    assert stored == secretary_service.planner_fingerprint(db, user.id)
    skipped = run_definition_inline(testing_session_local, secretary.id, task_executor=executor)
    assert skipped.output == secretary_service.SKIP_NOTHING_NEW and len(executor.calls) == 1

    # This path runs on the real clock, so the event is relative to it, not to the fixed NOON.
    _synced_event(db, account, start=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2))
    third = run_definition_inline(testing_session_local, secretary.id, task_executor=executor)
    assert third.output == "Nothing urgent." and len(executor.calls) == 2
