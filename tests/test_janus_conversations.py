"""Janus-started conversations (JAN-319).

An unattended run may start a new chat conversation for its owner with the
``start_conversation`` tool. These tests pin the owner scoping, the per-run and
per-day limits, the no-links rule, the tool-policy gating (unattended only,
the Unattended switch, never delegated), the injection refusal, the domain
change, and that the conversation shows up -- marked and unread -- through
the REST API.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from janus import domain_changes, models, security, tool_policy, trigger_service
from janus import trigger_definition_service as definitions
from janus.agent_task_runner import AgentTaskRun
from janus.agent_toolboxes import conversations as conversation_tools
from janus.agent_toolboxes.conversations import (
    CONVERSATIONS_START_ENTRY,
    CONVERSATIONS_START_TOOL_ID,
    START_CONVERSATION_TOOL_NAME,
    build_conversation_tools,
)
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.notifications import NotificationService
from janus.runtime_adapter import INJECTION_ESCALATED_TOOL_NAMES, TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import conversation_service
from janus.services.conversation_service import ConversationService, JanusConversationError
from janus.trigger_adapters import ScheduledTriggerSpec
from janus.trigger_runtime import build_runtime_trigger_handler
from janus.trigger_service import DurableTriggerDispatcher

PASSWORD = "conversation-pass-1234"


@pytest.fixture(autouse=True)
def notifications(monkeypatch, testing_session_local):
    """Record desktop notifications instead of calling ``notify-send``; log events into the test database."""
    from janus import database

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    sent: list[str] = []
    monkeypatch.setattr(NotificationService, "notify_conversation", staticmethod(sent.append))
    return sent


def _user(db, username: str) -> models.User:
    user = models.User(
        username=username, hashed_password=security.security_service.get_password_hash(PASSWORD), is_admin=False
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _run(db, user: models.User, name: str) -> models.RuntimeTrigger:
    """Persist one durable run of ``user`` that an unattended step can belong to."""
    spec = ScheduledTriggerSpec(
        schedule_id=f"run-{name}",
        owner_id=user.id,
        task_id=f"task-{name}",
        objective=f"run {name}",
        due_at=datetime.now(UTC) + timedelta(hours=1),
        source="api",
    )
    record, _ = trigger_service.enqueue_trigger(db, spec.as_envelope())
    return record


def _tool(db, user: models.User, run_trigger_id: str | None, *, trace: TurnTrace | None = None):
    (tool,) = build_conversation_tools(ToolContext(db=db, user=user, turn_trace=trace), run_trigger_id=run_trigger_id)
    return tool


def _start(db, user, run_trigger_id, title="Your flight moved", message="Should I update the calendar?"):
    return _tool(db, user, run_trigger_id).invoke({"title": title, "message": message})


def _login(client, username: str):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


# -- the tool and the service ------------------------------------------------------


def test_the_tool_starts_an_unread_janus_conversation_for_the_runs_owner(db, notifications):
    """The conversation belongs to the owner, opens with Janus's message, and is linked to the run."""
    user = _user(db, "starter")
    run = _run(db, user, "root")

    created = _start(db, user, run.trigger_id)

    conversation = db.get(models.Conversation, created["conversation_id"])
    assert conversation.user_id == user.id
    assert (conversation.origin, conversation.origin_trigger_id, conversation.unread) == (
        "janus",
        run.trigger_id,
        True,
    )
    assert [(message.role, message.content) for message in conversation.messages] == [
        (models.MessageRole.ASSISTANT, "Should I update the calendar?")
    ]
    assert created["title"] == "Your flight moved"
    assert notifications == ["Your flight moved"]
    assert db.query(models.Event).filter_by(source="JanusConversations", user_id=user.id).count() == 1


def test_user_created_conversations_are_neither_janus_nor_unread(db):
    """The defaults keep every existing path unchanged."""
    user = _user(db, "plain")
    conversation = ConversationService(db).create_conversation_for_user(user.id, title="Mine")
    assert (conversation.origin, conversation.origin_trigger_id, conversation.unread) == ("user", None, False)


def test_a_run_of_another_owner_starts_nothing(db):
    """The run identity must be the owner's own: another user's run id reads as missing."""
    owner = _user(db, "owner")
    other = _user(db, "other")
    others_run = _run(db, other, "theirs")

    with pytest.raises(PermissionError, match="could not be found"):
        _start(db, owner, others_run.trigger_id)
    assert db.query(models.Conversation).count() == 0


def test_a_step_without_a_run_starts_nothing(db):
    """Fail closed: no durable run identity, no conversation."""
    user = _user(db, "no-run")
    with pytest.raises(PermissionError, match="no trigger run"):
        _start(db, user, None)
    assert db.query(models.Conversation).count() == 0


def test_the_tool_cannot_target_an_existing_or_foreign_conversation(db):
    """The tool's only arguments are the title and message: there is no conversation to aim at."""
    user = _user(db, "args")
    tool = _tool(db, user, _run(db, user, "a").trigger_id)
    assert set(tool.args) == {"title", "message"}


def test_one_run_starts_at_most_one_conversation(db):
    """A second call from the same run is refused."""
    user = _user(db, "per-run")
    run = _run(db, user, "root")
    _start(db, user, run.trigger_id)

    with pytest.raises(ValueError, match="already started 1 conversation"):
        _start(db, user, run.trigger_id, title="Another one")
    assert db.query(models.Conversation).count() == 1


def test_the_owner_gets_at_most_a_few_janus_conversations_per_day(db, monkeypatch):
    """Across runs (a follow-up chain included), one owner's budget is shared; another owner's is not."""
    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 2)
    user = _user(db, "daily")
    other = _user(db, "daily-other")
    for name in ("a", "b"):
        _start(db, user, _run(db, user, name).trigger_id, title=f"From {name}")

    with pytest.raises(ValueError, match="already started 2 conversations in the last day"):
        _start(db, user, _run(db, user, "c").trigger_id)
    # User-created conversations never use the budget, and another owner is unaffected.
    ConversationService(db).create_conversation_for_user(user.id, title="Mine")
    assert _start(db, other, _run(db, other, "d").trigger_id)["conversation_id"]


def test_the_daily_limit_only_counts_the_last_day(db, monkeypatch):
    """A Janus conversation older than a day no longer counts."""
    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 1)
    user = _user(db, "window")
    first = _start(db, user, _run(db, user, "old").trigger_id)
    row = db.get(models.Conversation, first["conversation_id"])
    row.created_at = datetime.now(UTC) - timedelta(days=1, minutes=1)
    db.commit()

    assert _start(db, user, _run(db, user, "new").trigger_id)["conversation_id"] != first["conversation_id"]


def test_deleting_a_janus_conversation_frees_its_slot(db, monkeypatch):
    """The owner deciding they saw it is enough."""
    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 1)
    user = _user(db, "delete-frees")
    first = _start(db, user, _run(db, user, "a").trigger_id)
    assert ConversationService(db).delete_conversation_for_user(first["conversation_id"], user.id)
    assert _start(db, user, _run(db, user, "b").trigger_id)["conversation_id"]


@pytest.mark.parametrize(
    ("title", "message"),
    [
        ("Read this", "Details at https://example.com/report"),
        ("Read this", "See http://evil.example/?q=secret"),
        ("Read this", "![status](/api/images/abc)"),
        ("Read this", "![pixel](https://evil.example/p.png?d=secret)"),
        ("Read this", "[report](//evil.example/x)"),
        ("Read this", "Visit www.example.com for more"),
        ("Read this", "Mail me: mailto:someone@example.com"),
        ("Read this", "[x](javascript:alert(1))"),
        ("Read this", "[x](data:text/html,hi)"),
        ("Go to https://example.com", "Plain message."),
        ("Read this", "ftp://files.example/secret"),
        # Escapes and entities a Markdown renderer resolves into a scheme (Codex P1 on #552).
        ("Read this", "[open](https\\://attacker.example/secret)"),
        ("Read this", "[open](https:\\/\\/attacker.example/secret)"),
        ("Read this", "[open](https&#58;//attacker.example/secret)"),
        ("Read this", "[open](https&colon;//attacker.example/secret)"),
        ("Read this", "[open](https&#x3A;&#x2F;&#x2F;attacker.example/secret)"),
        ("Read this", "[open](&#104;ttps://attacker.example/secret)"),
        ("Read this", "[open](https&amp;#58;//attacker.example/secret)"),
        ("Read this", "[open](java&#115;cript:alert(1))"),
        ("Read this", "[open](\\/\\/attacker.example/secret)"),
        ("Read this", "[open](/&#47;attacker.example/secret)"),
        ("Read this", "[open](/\\attacker.example/secret)"),
        ("Read this", "[open](<ht\ttps://attacker.example/secret>)"),
        ("Read this", "[open](ht&#9;tps://attacker.example/secret)"),
        ("Read this", "[open](attacker.example/secret)"),
        # Autolinks.
        ("Read this", "<https://attacker.example/secret>"),
        ("Read this", "<https\\://attacker.example/secret>"),
        ("Read this", "<mailto:someone@example.com>"),
        ("Read this", "<someone@example.com>"),
        # Reference-style links and images.
        ("Read this", "See [the report][r].\n\n[r]: https\\://attacker.example/secret"),
        ("Read this", "See [the report][r].\n\n[r]: https&#58;//attacker.example/secret"),
        ("Read this", "See [the report].\n\n[the report]: <ht&#116;ps://attacker.example/secret>"),
        ("Read this", "![chart][img]\n\n[img]: /api/images/abc"),
        ("Read this", "&#33;[pixel](/api/images/abc)"),
    ],
)
def test_links_out_urls_and_images_are_refused(db, title, message):
    """Nothing in a Janus-started conversation can reach another site, on open or on a click."""
    user = _user(db, "no-links")
    with pytest.raises(ValueError, match="may not contain links to other sites"):
        _start(db, user, _run(db, user, "x").trigger_id, title=title, message=message)
    assert db.query(models.Conversation).count() == 0


def test_app_links_and_ordinary_text_are_allowed(db):
    """Links inside Janus, times, ratios, and addresses in prose stay fine."""
    user = _user(db, "app-links")
    message = (
        "Your meeting moved to 10:30 (ratio 3:1). Review it on [Triggers](/settings?tab=triggers); "
        "it came from alice@example.com."
    )
    created = _start(db, user, _run(db, user, "x").trigger_id, message=message)
    assert db.get(models.Conversation, created["conversation_id"]).messages[0].content == message


@pytest.mark.parametrize(
    "message",
    [
        "Open [Triggers](/settings?tab=triggers) or [the top](#summary).",
        "See [the run][run].\n\n[run]: /tasks?kind=trigger&task=abc",
        "Escaped punctuation stays fine: 3\\*4 \\[not a link\\] and &amp; and 10&#58;30.",
        "A table:\n\n| a | b |\n| - | - |\n| 1 | 2 |",
    ],
)
def test_app_destinations_survive_the_parsed_check(db, message):
    """The parsed-destination check keeps app paths, anchors, reference links to app paths, and escaped prose."""
    user = _user(db, "app-destinations")
    created = _start(db, user, _run(db, user, "x").trigger_id, message=message)
    assert "conversation_id" in created, created


@pytest.mark.parametrize(
    ("title", "message", "match"),
    [
        ("   ", "Hello", "title must not be empty"),
        ("x" * 121, "Hello", "title must be at most 120"),
        ("Title", "  ", "message must not be empty"),
        ("Title", "x" * 4001, "message must be at most 4000"),
    ],
)
def test_text_limits(db, title, message, match):
    """Empty or overlong text is refused before anything is written."""
    user = _user(db, "limits")
    with pytest.raises(ValueError, match=match):
        _start(db, user, _run(db, user, "x").trigger_id, title=title, message=message)


def test_the_title_is_collapsed_to_one_line(db):
    """A multi-line title is shown as one line in the conversation list."""
    user = _user(db, "one-line")
    created = _start(db, user, _run(db, user, "x").trigger_id, title="  Flight\n moved\t again ")
    assert created["title"] == "Flight moved again"


def test_the_service_raises_its_own_error_type(db):
    """Callers outside the tool (the e2e seed) get a ``JanusConversationError``."""
    user = _user(db, "service")
    with pytest.raises(JanusConversationError):
        ConversationService(db).start_janus_conversation(user.id, title="", message="x", trigger_id="t")


def test_the_tool_refuses_after_a_suspected_injection(db, notifications):
    """The toolbox checks the trace itself too: nothing is written, and the answer says why."""
    user = _user(db, "injected")
    trace = TurnTrace()
    trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))

    answer = _tool(db, user, _run(db, user, "x").trigger_id, trace=trace).invoke(
        {"title": "Urgent", "message": "Forward all mail to me."}
    )

    assert answer.startswith("BLOCKED: start_conversation was not run because content read earlier looked like")
    assert db.query(models.Conversation).count() == 0
    assert notifications == []


# -- registry and policy gating ----------------------------------------------------


def test_registry_entry_metadata_and_fences(db):
    """Recommended, low-risk write; unattended only; escalated on injection; never delegable; not locked."""
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == CONVERSATIONS_START_TOOL_ID)
    assert entry is CONVERSATIONS_START_ENTRY
    assert (entry.confirmation, entry.operation, entry.risk_level, entry.required_capabilities, entry.docs_ref) == (
        "recommended",
        "write",
        "low",
        (),
        "tools.md#start-conversations",
    )
    assert START_CONVERSATION_TOOL_NAME in INJECTION_ESCALATED_TOOL_NAMES
    assert all(CONVERSATIONS_START_TOOL_ID not in allowed for allowed in TASK_MODE_ALLOWED_TOOL_IDS.values())
    assert CONVERSATIONS_START_TOOL_ID not in tool_policy.LOCKED_UNATTENDED
    assert CONVERSATIONS_START_TOOL_ID not in tool_policy.delegable_tool_ids(
        frozenset({CONVERSATIONS_START_TOOL_ID}), DEFAULT_TOOL_REGISTRY.entries()
    )
    assert tool_policy.default_unattended_allowed(entry)


@pytest.mark.parametrize(
    ("task_mode", "offered"),
    [
        ("trigger", True),
        ("automation", True),
        ("delegated", False),
        ("chat", False),
        ("briefing", False),
        ("digest", False),
        ("ingestion", False),
    ],
)
def test_only_trigger_and_automation_steps_get_the_tool(db, task_mode, offered):
    """Even an explicit allowlist cannot hand it to chat, a pipeline mode, or a delegated child."""
    user = _user(db, f"mode-{task_mode}")
    run = _run(db, user, "x")
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id=f"conv-{task_mode}",
        objective="tell the user",
        requested_tool_ids=(CONVERSATIONS_START_TOOL_ID,),
        task_mode=task_mode,
        allowed_tool_ids=frozenset({CONVERSATIONS_START_TOOL_ID}),
        confirmed_tool_ids=frozenset({CONVERSATIONS_START_TOOL_ID}),
        trigger_id=run.trigger_id,
    )
    names = {tool.name for tool in prepared.tools}
    assert (START_CONVERSATION_TOOL_NAME in names) is offered
    if not offered:
        assert [(denial.tool_id, denial.reason) for denial in prepared.denials] == [
            (CONVERSATIONS_START_TOOL_ID, "disabled_by_context")
        ]


def test_chat_default_allowlist_does_not_offer_it(db):
    """Chat's own allowlist never contains it: Janus is already talking to the user there."""
    user = _user(db, "chat-default")
    prepared = RuntimeAdapter(db, user).prepare_step(
        task_id="chat-1", objective="hi", requested_tool_ids=(CONVERSATIONS_START_TOOL_ID,), task_mode="chat"
    )
    assert not prepared.tools
    assert prepared.denials[0].reason == "not_allowlisted"


def test_the_unattended_switch_governs_it(db):
    """Allowed by default; switched off it is offered but not confirmed (a BLOCKED stub)."""
    user = _user(db, "switch")
    entries = DEFAULT_TOOL_REGISTRY.entries()
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")
    assert CONVERSATIONS_START_TOOL_ID in offered and CONVERSATIONS_START_TOOL_ID in confirmed

    tool_policy.set_policy(db, user.id, CONVERSATIONS_START_TOOL_ID, False)
    offered, confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="trigger")
    assert CONVERSATIONS_START_TOOL_ID in offered and CONVERSATIONS_START_TOOL_ID not in confirmed
    _, delegated_confirmed = tool_policy.unattended_tool_sets(db, user.id, entries, task_mode="delegated")
    assert CONVERSATIONS_START_TOOL_ID not in delegated_confirmed


class _StartingExecutor:
    """Stands in for the agent loop and calls start_conversation once, like a model would."""

    def __init__(self, title: str = "Your flight moved"):
        self.title = title
        self.tool_names: list[str] = []
        self.answer = None

    def __call__(self, db, system_prompt, tools, objective):
        self.tool_names = [tool.name for tool in tools]
        tool = next((tool for tool in tools if tool.name == START_CONVERSATION_TOOL_NAME), None)
        if tool is not None:
            self.answer = tool.invoke({"title": self.title, "message": "Should I update the calendar?"})
        return AgentTaskRun(output="Done.", tool_calls=())


def _dispatch(testing_session_local, executor, envelope, definition_id=None):
    dispatcher = DurableTriggerDispatcher(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )
    dispatcher.dispatch(envelope, definition_id=definition_id)


def _ad_hoc_envelope(user: models.User, name: str):
    return ScheduledTriggerSpec(
        schedule_id=f"adhoc-{name}",
        owner_id=user.id,
        task_id=f"adhoc-{name}",
        objective="Check the flight and tell the user if it moved.",
        due_at=datetime.now(UTC) - timedelta(minutes=1),
        source="agent-tool",
    ).as_envelope()


def test_an_ad_hoc_trigger_run_starts_a_conversation_the_owner_sees_via_the_api(client, db, testing_session_local):
    """End to end: a durable run calls the tool, and the owner's API lists it, marked and unread, until opened."""
    owner = _user(db, "api-owner")
    _user(db, "api-other")
    executor = _StartingExecutor()

    _dispatch(testing_session_local, executor, _ad_hoc_envelope(owner, "flight"))

    assert isinstance(executor.answer, dict), executor.answer
    _login(client, "api-owner")
    listed = client.get("/api/conversations/").json()
    assert [(item["title"], item["origin"], item["unread"]) for item in listed] == [
        ("Your flight moved", "janus", True)
    ]
    detail = client.get(f"/api/conversations/{listed[0]['id']}").json()
    assert detail["messages"][0]["role"] == "assistant"
    assert detail["messages"][0]["content"] == "Should I update the calendar?"
    assert detail["origin"] == "janus" and detail["unread"] is False
    assert client.get("/api/conversations/").json()[0]["unread"] is False

    # Another user neither lists nor opens it.
    client.post("/api/auth/logout")
    _login(client, "api-other")
    assert client.get("/api/conversations/").json() == []
    assert client.get(f"/api/conversations/{listed[0]['id']}").status_code == 404
    assert db.get(models.Conversation, listed[0]["id"]).unread is False


def test_an_ad_hoc_run_does_not_get_the_tool_when_the_switch_is_off(db, testing_session_local):
    """With no tool list, an Off tool is not offered at all."""
    owner = _user(db, "off-adhoc")
    tool_policy.set_policy(db, owner.id, CONVERSATIONS_START_TOOL_ID, False)
    executor = _StartingExecutor()

    _dispatch(testing_session_local, executor, _ad_hoc_envelope(owner, "off"))

    assert START_CONVERSATION_TOOL_NAME not in executor.tool_names
    assert db.query(models.Conversation).count() == 0


def _definition(db, user, *, origin: str):
    extra = {"builtin_key": "builtin.test"} if origin == "system" else {}
    return definitions.create_definition(
        db,
        user.id,
        origin=origin,
        name="Flight watch",
        objective="Tell the user when the flight moves.",
        tool_ids=["core", CONVERSATIONS_START_TOOL_ID],
        kind="manual",
        **extra,
    )


def _dispatch_definition(testing_session_local, executor, definition):
    envelope = definitions.build_definition_envelope(
        definition, occurrence_key="test-1", kind=definitions.TriggerKind.MANUAL, source="test"
    )
    _dispatch(testing_session_local, executor, envelope, definition.id)


def test_a_saved_trigger_listing_the_switched_off_tool_gets_blocked(db, testing_session_local):
    """A system definition cannot override the switch: the call answers BLOCKED and writes nothing."""
    owner = _user(db, "off-system")
    tool_policy.set_policy(db, owner.id, CONVERSATIONS_START_TOOL_ID, False)
    executor = _StartingExecutor()

    _dispatch_definition(testing_session_local, executor, _definition(db, owner, origin="system"))

    assert str(executor.answer).startswith("BLOCKED: start_conversation was not run")
    assert db.query(models.Conversation).count() == 0


def test_a_user_authored_trigger_that_picked_the_tool_may_use_it_even_when_off(db, testing_session_local):
    """The user's own selection on their definition is the standing approval, as for any tool."""
    owner = _user(db, "off-user")
    tool_policy.set_policy(db, owner.id, CONVERSATIONS_START_TOOL_ID, False)
    executor = _StartingExecutor()

    _dispatch_definition(testing_session_local, executor, _definition(db, owner, origin="user"))

    assert isinstance(executor.answer, dict), executor.answer
    conversation = db.query(models.Conversation).one()
    assert (conversation.user_id, conversation.origin) == (owner.id, "janus")


def test_a_janus_follow_up_run_can_start_a_conversation(db, testing_session_local):
    """A follow-up Janus scheduled for itself (JAN-257) runs in trigger mode and gets the tool."""
    owner = _user(db, "follow-up")
    from janus.agent_toolboxes.triggers import build_trigger_tools

    schedule = {tool.name: tool for tool in build_trigger_tools(ToolContext(db=db, user=owner), task_mode="chat")}
    created = schedule["create_scheduled_trigger"].invoke(
        {
            "due_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "objective": "Check the flight again",
            "task_id": "flight",
        }
    )
    record = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
    record.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    executor = _StartingExecutor()
    worker = trigger_service.DurableTriggerWorker(
        testing_session_local, build_runtime_trigger_handler(testing_session_local, task_executor=executor)
    )

    worker.run_once()

    assert isinstance(executor.answer, dict), executor.answer
    assert db.query(models.Conversation).one().origin_trigger_id == record.trigger_id


# -- safety review gaps (JAN-319 recovery) -------------------------------------------


def test_extra_owner_or_conversation_arguments_are_ignored(db):
    """A model passing ``user_id``/``conversation_id`` still only creates a new conversation for the run's owner."""
    owner = _user(db, "extra-owner")
    victim = _user(db, "extra-victim")
    victims_chat = ConversationService(db).create_conversation_for_user(victim.id, title="Private")
    tool = _tool(db, owner, _run(db, owner, "extra").trigger_id)

    created = tool.invoke(
        {
            "title": "Hello",
            "message": "A question for you.",
            "user_id": victim.id,
            "conversation_id": victims_chat.id,
        }
    )

    assert created["conversation_id"] != victims_chat.id
    assert db.get(models.Conversation, created["conversation_id"]).user_id == owner.id
    db.expire_all()
    assert db.get(models.Conversation, victims_chat.id).messages == []
    assert db.query(models.Conversation).filter_by(user_id=victim.id).count() == 1


def test_another_user_opening_it_neither_reads_nor_clears_it(client, db):
    """A foreign GET answers 404 and leaves the owner's conversation unread."""
    owner = _user(db, "unread-owner")
    _user(db, "unread-other")
    created = _start(db, owner, _run(db, owner, "unread").trigger_id)

    _login(client, "unread-other")
    assert client.get(f"/api/conversations/{created['conversation_id']}").status_code == 404
    assert client.get("/api/conversations/").json() == []
    db.expire_all()
    assert db.get(models.Conversation, created["conversation_id"]).unread is True


def test_the_tool_cannot_be_proposed_by_janus(db):
    """Janus cannot draft a definition that grants itself the tool (JAN-235): the user must add it."""
    from janus import trigger_proposal_service

    assert CONVERSATIONS_START_TOOL_ID not in trigger_proposal_service.PROPOSABLE_TOOL_IDS


def test_a_follow_up_chain_shares_the_owners_daily_budget_and_stays_bounded(db, monkeypatch):
    """Each run of a chain (JAN-274) may start one conversation, all from one budget; the chain guard still holds."""
    from janus.agent_toolboxes.triggers import MAX_UNATTENDED_CHAIN_DEPTH, build_trigger_tools

    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 2)
    owner = _user(db, "chain")
    run = _run(db, owner, "chain-root")
    due = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    runs = [run]
    for depth in range(1, MAX_UNATTENDED_CHAIN_DEPTH + 1):
        schedule = {
            tool.name: tool
            for tool in build_trigger_tools(
                ToolContext(db=db, user=owner), task_mode="trigger", run_trigger_id=runs[-1].trigger_id
            )
        }
        created = schedule["create_scheduled_trigger"].invoke(
            {"due_at": due, "objective": f"Check again ({depth})", "task_id": f"chain-{depth}"}
        )
        child = db.query(models.RuntimeTrigger).filter_by(trigger_id=created["trigger_id"]).one()
        assert (child.parent_trigger_id, child.chain_depth) == (runs[-1].trigger_id, depth)
        runs.append(child)

    # The chain guard refuses a follow-up past the maximum depth, conversation tool or not.
    deepest = {
        tool.name: tool
        for tool in build_trigger_tools(
            ToolContext(db=db, user=owner), task_mode="trigger", run_trigger_id=runs[-1].trigger_id
        )
    }
    with pytest.raises(ValueError, match="cannot schedule another one"):
        deepest["create_scheduled_trigger"].invoke({"due_at": due, "objective": "Again", "task_id": "chain-x"})

    _start(db, owner, runs[0].trigger_id, title="From the root")
    with pytest.raises(ValueError, match="already started 1 conversation"):
        _start(db, owner, runs[0].trigger_id, title="Root again")
    _start(db, owner, runs[1].trigger_id, title="From the follow-up")
    with pytest.raises(ValueError, match="already started 2 conversations in the last day"):
        _start(db, owner, runs[2].trigger_id, title="From the last follow-up")
    assert db.query(models.Conversation).filter_by(user_id=owner.id).count() == 2


def test_a_concurrent_start_over_the_limit_rolls_back(db, monkeypatch):
    """Two runs that both pass the first count: the recount after the insert refuses the second, writing nothing."""
    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 1)
    owner = _user(db, "race")
    first_run, second_run = _run(db, owner, "race-a"), _run(db, owner, "race-b")
    service = ConversationService(db)
    real_count = service.count_janus_conversations_since
    calls = {"n": 0}

    def count_racing(user_id, since):
        calls["n"] += 1
        if calls["n"] == 1:
            # The first check runs before the other run's conversation lands.
            return 0
        return real_count(user_id, since)

    service.start_janus_conversation(owner.id, title="First", message="One.", trigger_id=first_run.trigger_id)
    monkeypatch.setattr(service, "count_janus_conversations_since", count_racing)
    with pytest.raises(JanusConversationError, match="in the last day"):
        service.start_janus_conversation(owner.id, title="Second", message="Two.", trigger_id=second_run.trigger_id)

    db.expire_all()
    assert [c.title for c in db.query(models.Conversation).filter_by(user_id=owner.id).all()] == ["First"]
    assert db.query(models.ConversationMessage).count() == 1


def test_the_owner_lock_is_a_row_lock_on_postgresql_and_a_plain_read_on_sqlite():
    """PostgreSQL serializes on ``FOR UPDATE`` on the owner's row; SQLite has no row locks and omits it."""
    from sqlalchemy.dialects import postgresql, sqlite

    statement = conversation_service.owner_lock_statement(7)
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" not in str(statement.compile(dialect=sqlite.dialect()))


def test_concurrent_starts_in_separate_transactions_cannot_exceed_the_daily_cap(tmp_path, monkeypatch):
    """Two real transactions on one SQLite file: the second waits on the first's write lock, recounts, and refuses.

    Thread A has inserted and recounted its conversation but not committed yet when
    thread B starts; B's first count misses A's row, so only the write lock and the
    recount after B's own insert keep the owner at the cap (Codex P2 on #552).
    """
    import threading

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from janus.database import Base

    monkeypatch.setattr(conversation_service, "MAX_JANUS_CONVERSATIONS_PER_DAY", 1)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False, "timeout": 10}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    try:
        with sessions() as setup:
            owner_id = _user(setup, "race-owner").id

        a_inserted, b_counted = threading.Event(), threading.Event()
        outcomes: dict[str, str] = {}
        real_check = ConversationService._check_janus_limits

        def check(self, user_id, trigger_id, since, *, already_added):
            real_check(self, user_id, trigger_id, since, already_added=already_added)
            if trigger_id == "run-a" and already_added == 1:
                a_inserted.set()  # A holds the write lock until it commits.
                b_counted.wait(timeout=5)
            if trigger_id == "run-b" and already_added == 0:
                b_counted.set()  # B's first count ran without seeing A's uncommitted row.

        monkeypatch.setattr(ConversationService, "_check_janus_limits", check)

        def start(name: str) -> None:
            with sessions() as session:
                try:
                    ConversationService(session).start_janus_conversation(
                        owner_id, title=f"From {name}", message="Hello.", trigger_id=f"run-{name}"
                    )
                    outcomes[name] = "started"
                except JanusConversationError as error:
                    outcomes[name] = str(error)

        thread_a = threading.Thread(target=start, args=("a",))
        thread_a.start()
        assert a_inserted.wait(timeout=5)
        thread_b = threading.Thread(target=start, args=("b",))
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert outcomes["a"] == "started"
        assert "in the last day" in outcomes["b"]
        with sessions() as check_session:
            titles = [c.title for c in check_session.query(models.Conversation).all()]
            assert titles == ["From a"]
            assert check_session.query(models.ConversationMessage).count() == 1
    finally:
        engine.dispose()


# -- live refresh --------------------------------------------------------------------


@contextmanager
def _subscribed(user_id: int):
    """Subscribe ``user_id`` to domain changes on a private loop; yields a reader (as in test_domain_changes)."""
    domain_changes.install()
    loop = asyncio.new_event_loop()

    async def _subscribe():
        return domain_changes.bus.subscribe(user_id)

    async def _read(subscription):
        await asyncio.sleep(0)
        return subscription.take()

    subscription = loop.run_until_complete(_subscribe())
    try:
        yield lambda: loop.run_until_complete(_read(subscription))
    finally:
        domain_changes.bus.unsubscribe(subscription)
        loop.close()


def test_starting_and_opening_a_conversation_publish_the_owners_conversations_domain(db):
    """The owner's open tabs refresh their conversation list and Chat badge; another user hears nothing."""
    user = _user(db, "live")
    other = _user(db, "live-other")
    run = _run(db, user, "x")
    with _subscribed(user.id) as changes, _subscribed(other.id) as other_changes:
        created = _start(db, user, run.trigger_id)
        assert "conversations" in changes()
        assert other_changes() == frozenset()

        service = ConversationService(db)
        service.mark_read_for_user(service.get_conversation_for_user(created["conversation_id"], user.id))
        assert changes() == {"conversations"}


def test_module_exports(db):
    """The toolbox module keeps its public names."""
    assert conversation_tools.__all__ == [
        "CONVERSATIONS_START_ENTRY",
        "CONVERSATIONS_START_TOOL_ID",
        "START_CONVERSATION_TOOL_NAME",
        "build_conversation_tools",
    ]
