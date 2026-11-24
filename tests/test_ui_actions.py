"""Unit tests for chat page actions (JAN-251): allowlists, the broker, the toolbox, and its chat-only fences."""

import asyncio
import json
import threading

import pytest

from janus import models, tool_policy
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_toolboxes.ui_actions import UI_ACTIONS_TOOL_ID, build_ui_action_tools
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.runtime_adapter import INJECTION_ESCALATED_TOOL_NAMES, TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import ui_actions
from janus.services.ui_actions import (
    UI_ACTION_PREFIX,
    UI_ACTION_TOOL_NAMES,
    PageActionTurn,
    UIActionBroker,
    UIActionError,
    UIActionOutcome,
    validate_action,
)

SNAPSHOT = {
    "page": "Emails",
    "route": "/emails",
    "selection": {"type": "email", "id": 11, "label": "Invoice overdue — from billing@example.com"},
    "visible_items": [
        {"type": "email", "id": 11, "label": "Invoice overdue — from billing@example.com"},
        {"type": "email", "id": 12, "label": "Lunch? — from anna@example.com"},
        {"type": "topic", "id": "t-4", "label": "Kitchen renovation"},
    ],
}


def _user(db, name: str = "ui-user") -> models.User:
    user = models.User(username=name, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    return user


# -- allowlists -----------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "normalized"),
    [
        ("/", "/"),
        ("/emails", "/emails"),
        ("/emails/", "/emails"),
        ("/memory?tab=topics", "/memory?tab=topics"),
        ("/settings?tab=runtime", "/settings?tab=runtime"),
        ("/memory", "/memory"),
        # Admin-only tab: the server allows it, Settings.tsx keeps enforcing admin visibility.
        ("/settings?tab=errors", "/settings?tab=errors"),
    ],
)
def test_navigate_accepts_known_routes(route, normalized):
    """Known app routes (and the Memory/Settings tabs) are accepted and normalized."""
    validated = validate_action("navigate", None, {"route": route})
    assert (validated.action, validated.params, validated.label) == ("navigate", {"route": normalized}, normalized)


@pytest.mark.parametrize(
    "route",
    [
        "https://evil.example.com/emails",
        "//evil.example.com/",
        "/admin",
        "/emails?email=3",
        "/settings?tab=nope",
        "/settings?tab=runtime&x=1",
        "/memory?tab=topics#frag",
        "javascript:alert(1)",
        "emails",
        "",
        None,
        42,
    ],
)
def test_navigate_rejects_unknown_routes(route):
    """Hosts, schemes, fragments, unknown paths, extra queries, and unknown tabs are refused."""
    with pytest.raises(UIActionError):
        validate_action("navigate", None, {"route": route})


def test_set_filter_accepts_only_allowlisted_pairs_and_values():
    """Each page's filters accept their own values; free text is length-capped."""
    assert validate_action("set_filter", None, {"page": "Emails", "key": "priority", "value": "IMPORTANT"}).params == {
        "page": "emails",
        "key": "priority",
        "value": "important",
    }
    assert validate_action("set_filter", None, {"page": "triggers", "key": "run_status", "value": "failed"}).label == (
        "run_status = failed"
    )
    # A run parked on its delegated tasks (JAN-207) can be filtered for too.
    assert validate_action("set_filter", None, {"page": "triggers", "key": "run_status", "value": "waiting"}).label == (
        "run_status = waiting"
    )
    assert validate_action("set_filter", None, {"page": "triggers", "key": "show_finished", "value": True}).params[
        "value"
    ] == ("true")
    assert validate_action("set_filter", None, {"page": "topics", "key": "search", "value": " kitchen "}).params[
        "value"
    ] == ("kitchen")
    for bad in (
        {"page": "emails", "key": "priority", "value": "urgent"},
        {"page": "emails", "key": "sender", "value": "anna"},
        {"page": "settings", "key": "tab", "value": "runtime"},
        {"page": "topics", "key": "search", "value": "x" * 81},
        {"page": "topics", "key": "status", "value": None},
    ):
        with pytest.raises(UIActionError):
            validate_action("set_filter", None, bad)


def test_item_actions_need_an_id_from_the_snapshot():
    """open_item/highlight accept only (type, id) pairs the turn's snapshot lists."""
    opened = validate_action("open_item", SNAPSHOT, {"type": "email", "id": "12"})
    assert opened.params == {"type": "email", "id": 12}
    assert opened.label == "Lunch? — from anna@example.com"
    assert validate_action("highlight", SNAPSHOT, {"type": "topic", "id": "t-4"}).params == {
        "type": "topic",
        "id": "t-4",
    }
    for args in (
        {"type": "email", "id": 99},  # not on screen
        {"type": "topic", "id": 11},  # right id, wrong type
        {"type": "memory_fact", "id": 1},  # type not allowlisted
        {"type": "email", "id": ""},
    ):
        with pytest.raises(UIActionError):
            validate_action("open_item", SNAPSHOT, args)
    with pytest.raises(UIActionError, match="not on the user's screen"):
        validate_action("highlight", None, {"type": "email", "id": 11})


def test_scroll_and_prefill_accept_only_registered_ids_and_fields():
    """Sections and forms are allowlisted; form fields are known names with short text values."""
    assert validate_action("scroll_to", None, {"section": "schedule"}).params == {"section": "schedule"}
    with pytest.raises(UIActionError):
        validate_action("scroll_to", None, {"section": "#app > div"})
    filled = validate_action("prefill_form", None, {"form_id": "topic.create", "fields": {"title": "Garden"}})
    assert filled.params == {"form_id": "topic.create", "fields": {"title": "Garden"}}
    for args in (
        {"form_id": "login", "fields": {"password": "x"}},
        {"form_id": "topic.create", "fields": {"submit": "yes"}},
        {"form_id": "topic.create", "fields": {}},
        {"form_id": "topic.create", "fields": {"title": "x" * 1001}},
        {"form_id": "topic.create", "fields": {"title": 3}},
    ):
        with pytest.raises(UIActionError):
            validate_action("prefill_form", None, args)
    with pytest.raises(UIActionError, match="unknown page action"):
        validate_action("submit_form", None, {})


# -- broker -----------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def test_broker_sends_a_frame_and_returns_the_client_result():
    """The frame carries the claimed call_id; the matching result resolves the request."""

    async def scenario():
        sent: list[str] = []

        async def send(frame: str) -> None:
            sent.append(frame)

        broker = UIActionBroker(send, timeout_seconds=2)
        broker.turn(None)
        broker.note_started("call-x", "ui_navigate", {"route": "/emails"})
        broker.note_started("ignored", "search_emails", {})
        validated = validate_action("navigate", None, {"route": "/emails"})
        task = asyncio.create_task(broker.request("ui_navigate", validated, {"route": "/emails"}))
        while not sent:
            await asyncio.sleep(0)
        frame = json.loads(sent[0][len(UI_ACTION_PREFIX) :])
        assert frame["call_id"] == "call-x"
        assert (frame["action"], frame["params"], frame["label"]) == ("navigate", {"route": "/emails"}, "/emails")
        assert frame["timeout_seconds"] == 2
        # Unknown ids and malformed results are consumed but ignored.
        assert broker.handle_frame(json.dumps({"type": "ui_action_result", "action_id": "other", "ok": True}))
        assert broker.handle_frame(json.dumps({"type": "ui_action_result", "action_id": frame["action_id"], "ok": "y"}))
        assert not broker.handle_frame("hello")
        assert not broker.handle_frame(json.dumps({"type": "message", "text": "hi"}))
        assert not task.done()
        broker.handle_frame(
            json.dumps({"type": "ui_action_result", "action_id": frame["action_id"], "ok": False, "error": "e" * 500})
        )
        outcome = await task
        assert outcome.status == ui_actions.FAILED
        assert outcome.error == "e" * ui_actions.MAX_CLIENT_ERROR_CHARS

    _run(scenario())


def test_broker_prefers_the_start_with_equal_arguments():
    """Parallel calls of one tool pair with their own start events."""

    async def scenario():
        broker = UIActionBroker(lambda frame: asyncio.sleep(0), timeout_seconds=1)
        broker.note_started("first", "ui_highlight", {"type": "email", "id": 1})
        broker.note_started("second", "ui_highlight", {"type": "email", "id": 2})
        assert await broker._call_id("ui_highlight", {"type": "email", "id": 2}) == "second"
        assert await broker._call_id("ui_highlight", {"type": "email", "id": 9}) == "first"
        # A new turn forgets unclaimed starts of the last one.
        broker.note_started("stale", "ui_highlight", {})
        broker.turn(None)
        assert broker._claim("ui_highlight", {}) is None

    _run(scenario())


def test_broker_timeout_unavailable_and_disconnect_never_succeed(monkeypatch):
    """No answer, no capable client, or a closed socket all answer a non-ok outcome."""
    monkeypatch.setattr(ui_actions, "START_WAIT_SECONDS", 0.05)
    validated = validate_action("scroll_to", None, {"section": "tasks"})

    async def scenario():
        async def send(frame: str) -> None:
            return None

        broker = UIActionBroker(send, timeout_seconds=0.1)
        timed_out = await broker.request("ui_scroll_to", validated, {})
        assert timed_out == UIActionOutcome(ui_actions.TIMEOUT)

        unavailable = UIActionBroker(None, timeout_seconds=1)
        assert not unavailable.available
        assert await unavailable.request("ui_scroll_to", validated, {}) == UIActionOutcome(ui_actions.UNAVAILABLE)

        closing = UIActionBroker(send, timeout_seconds=5)
        task = asyncio.create_task(closing.request("ui_scroll_to", validated, {}))
        await asyncio.sleep(0.1)
        closing.close()
        assert (await task).status == ui_actions.DISCONNECTED
        assert (await closing.request("ui_scroll_to", validated, {})).status == ui_actions.DISCONNECTED

    _run(scenario())


def test_perform_from_a_worker_thread_waits_for_the_loop():
    """The tool's worker thread blocks on the connection's loop until the client answers."""

    async def scenario():
        sent: list[str] = []

        async def send(frame: str) -> None:
            sent.append(frame)

        broker = UIActionBroker(send, timeout_seconds=2)
        broker.note_started("call-t", "ui_scroll_to", {"section": "tasks"})
        validated = validate_action("scroll_to", None, {"section": "tasks"})
        result: dict[str, UIActionOutcome] = {}
        worker = threading.Thread(
            target=lambda: result.setdefault("outcome", broker.perform("ui_scroll_to", validated, {"section": "tasks"}))
        )
        worker.start()
        while not sent:
            await asyncio.sleep(0.01)
        action_id = json.loads(sent[0][len(UI_ACTION_PREFIX) :])["action_id"]
        broker.handle_frame(json.dumps({"type": "ui_action_result", "action_id": action_id, "ok": True}))
        await asyncio.to_thread(worker.join)
        assert result["outcome"].ok
        # On the loop's own thread it refuses instead of deadlocking.
        assert broker.perform("ui_scroll_to", validated, {}).status == ui_actions.UNAVAILABLE

    _run(scenario())


# -- toolbox --------------------------------------------------------------


class _FakeBroker:
    def __init__(self, outcome: UIActionOutcome, available: bool = True):
        self.outcome = outcome
        self.available = available
        self.calls: list[tuple[str, str, dict]] = []

    def perform(self, tool_name, validated, args):
        self.calls.append((tool_name, validated.action, validated.params))
        return self.outcome


def _tools(db, broker=None, snapshot=SNAPSHOT, trace=None):
    turn = PageActionTurn(broker, snapshot) if broker is not None else None
    context = ToolContext(db=db, user=_user(db, f"tb-{id(broker)}"), turn_trace=trace or TurnTrace(), page_actions=turn)
    return {tool.name: tool for tool in build_ui_action_tools(context)}


def test_toolbox_answers_the_clients_result(db):
    """A confirmed action answers Done; a rejected one raises with the client's reason."""
    broker = _FakeBroker(UIActionOutcome(ui_actions.OK))
    tools = _tools(db, broker)
    assert set(tools) == set(UI_ACTION_TOOL_NAMES)
    answer = tools["ui_open_item"].invoke({"type": "email", "id": 12})
    assert answer.startswith("Done:") and "Lunch?" in answer
    assert broker.calls == [("ui_open_item", "open_item", {"type": "email", "id": 12})]

    broker.outcome = UIActionOutcome(ui_actions.FAILED, "no such filter here")
    with pytest.raises(RuntimeError, match="the page could not do it: no such filter here"):
        tools["ui_set_filter"].invoke({"page": "emails", "key": "priority", "value": "spam"})
    broker.outcome = UIActionOutcome(ui_actions.TIMEOUT)
    with pytest.raises(RuntimeError, match="did not confirm it in time"):
        tools["ui_navigate"].invoke({"route": "/emails"})


def test_toolbox_refuses_before_asking_the_client(db):
    """Disallowed requests and clients that cannot act never reach the broker."""
    broker = _FakeBroker(UIActionOutcome(ui_actions.OK))
    tools = _tools(db, broker)
    with pytest.raises(RuntimeError, match="not allowed: .*not on the user's screen"):
        tools["ui_highlight"].invoke({"type": "email", "id": 999})
    with pytest.raises(RuntimeError, match="not allowed: unknown route"):
        tools["ui_navigate"].invoke({"route": "/admin"})
    assert broker.calls == []

    for unavailable in (_tools(db, None), _tools(db, _FakeBroker(UIActionOutcome(ui_actions.OK), available=False))):
        with pytest.raises(RuntimeError, match="this client cannot perform page actions"):
            unavailable["ui_navigate"].invoke({"route": "/emails"})


def test_toolbox_refuses_every_action_after_a_suspected_injection(db):
    """Even navigate and highlight answer the injection refusal once the turn is flagged."""
    trace = TurnTrace()
    trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))
    broker = _FakeBroker(UIActionOutcome(ui_actions.OK))
    tools = _tools(db, broker, trace=trace)
    for name, args in (
        ("ui_navigate", {"route": "/settings?tab=tools"}),
        ("ui_highlight", {"type": "email", "id": 11}),
    ):
        assert tools[name].invoke(args).startswith(f"BLOCKED: {name} was not run because content read earlier")
    assert broker.calls == []


def _prepare(db, user, mode, **kwargs):
    return RuntimeAdapter(db, user).prepare_step(
        task_id=f"ui-{mode}",
        objective="open the mail from Anna",
        requested_tool_ids=("ui_actions", "delegation", "core"),
        task_mode=mode,
        guard_tool_errors=True,
        **kwargs,
    )


def test_chat_step_offers_page_actions_but_never_grants_them_onward(db):
    """Chat gets the tools (escalated on injection); delegated children cannot inherit them."""
    user = _user(db)
    prepared = _prepare(
        db,
        user,
        "chat",
        confirmed_tool_ids=frozenset({"ui_actions", "delegation", "core"}),
        page_actions=PageActionTurn(_FakeBroker(UIActionOutcome(ui_actions.OK)), SNAPSHOT),
    )
    assert "ui_actions" in {entry.id for entry in prepared.selection.entries}
    assert set(prepared.tool_ids_by_name) >= UI_ACTION_TOOL_NAMES
    assert UI_ACTION_TOOL_NAMES <= INJECTION_ESCALATED_TOOL_NAMES
    assert "ui_actions" in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    assert UI_ACTIONS_TOOL_ID not in tool_policy.delegable_tool_ids(
        frozenset({UI_ACTIONS_TOOL_ID}), DEFAULT_TOOL_REGISTRY.entries()
    )


def test_authorized_tool_ids_leave_out_chat_only_entries(db):
    """The grant builders see never includes a locked entry, so nothing spawned inherits it."""
    user = _user(db)
    seen: dict[str, frozenset[str]] = {}
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == "core")
    original_builder = entry.builder

    def recording_builder(context):
        seen["authorized"] = context.authorized_tool_ids
        return original_builder(context)

    object.__setattr__(entry, "builder", recording_builder)
    try:
        _prepare(db, user, "chat", confirmed_tool_ids=frozenset({"ui_actions", "delegation", "core"}))
    finally:
        object.__setattr__(entry, "builder", original_builder)
    assert "core" in seen["authorized"]
    assert "ui_actions" not in seen["authorized"]


@pytest.mark.parametrize("mode", ["trigger", "automation", "delegated", "briefing", "digest", "ingestion"])
def test_page_actions_are_never_offered_outside_chat(db, mode):
    """No unattended or pipeline mode is offered the toolbox, whatever is requested or narrowed to."""
    user = _user(db, f"mode-{mode}")
    entries = DEFAULT_TOOL_REGISTRY.entries()
    if mode in tool_policy.UNATTENDED_TASK_MODES:
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, entries, task_mode=mode, narrowed_to=frozenset({"ui_actions", "core"})
        )
        assert "ui_actions" not in offered | confirmed
        prepared = _prepare(
            db, user, mode, allowed_tool_ids=frozenset({"ui_actions", "core"}), confirmed_tool_ids=offered
        )
    else:
        assert "ui_actions" not in TASK_MODE_ALLOWED_TOOL_IDS[mode]
        prepared = _prepare(db, user, mode)
    assert "ui_actions" not in {entry.id for entry in prepared.selection.entries}
    assert not UI_ACTION_TOOL_NAMES & set(prepared.tool_ids_by_name)


def test_toolbox_is_disabled_outside_chat_even_when_allowlisted(db):
    """Defense in depth: the entry's enabled_when is chat-only, so an explicit allowlist cannot add it."""
    prepared = _prepare(
        db,
        _user(db),
        "trigger",
        allowed_tool_ids=frozenset({"ui_actions"}),
        confirmed_tool_ids=frozenset({"ui_actions"}),
    )
    assert ("ui_actions", "disabled_by_context") in [(denial.tool_id, denial.reason) for denial in prepared.denials]


def test_locked_switch_cannot_be_turned_on(db):
    """The unattended switch of ui_actions is locked, like triggers.propose."""
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.set_policy(db, _user(db).id, "ui_actions", True)
