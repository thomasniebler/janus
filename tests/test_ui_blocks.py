"""Unit tests for chat UI blocks (JAN-252): the catalog, its budgets, the toolbox, and its chat-only fences."""

import json

import pytest

from janus import models, tool_policy
from janus.agent_toolboxes.shared import ToolContext, TurnToolCall, TurnTrace
from janus.agent_toolboxes.ui_blocks import UI_BLOCKS_TOOL_ID, build_ui_block_tools
from janus.agent_tools import DEFAULT_TOOL_REGISTRY
from janus.runtime_adapter import INJECTION_ESCALATED_TOOL_NAMES, TASK_MODE_ALLOWED_TOOL_IDS, RuntimeAdapter
from janus.services import ui_blocks
from janus.services.ui_blocks import (
    MAX_UI_BLOCKS_PER_MESSAGE,
    RENDER_UI_TOOL_NAME,
    UI_BLOCK_MAX_BYTES,
    UI_BLOCK_PREFIX,
    UI_BLOCK_SCHEMA_VERSION,
    UIBlockError,
    UIBlockTurn,
    validate_block,
)

CARD = {
    "type": "card",
    "title": "Invoice overdue",
    "subtitle": "billing@example.com",
    "body": "Payment of 120 EUR was due on Friday.",
    "fields": [{"label": "Amount", "value": "120 EUR"}],
    "links": [{"label": "Open", "href": "/emails"}],
}
TABLE = {"type": "table", "title": "Tasks", "columns": ["Task", "Due", "Done"], "rows": [["Pay", "Fri", False]]}
CHART = {
    "type": "chart",
    "kind": "bar",
    "labels": ["Mon", "Tue"],
    "series": [{"name": "Mails", "values": [3, 5]}],
    "unit": "mails",
}
CHECKLIST = {"type": "checklist", "items": [{"text": "Pay invoice", "checked": True}, {"text": "Reply to Anna"}]}
KEY_VALUE = {"type": "key_value", "items": [{"key": "Unread", "value": 4}, {"key": "Status", "value": "ok"}]}
ACTIONS = {
    "type": "actions",
    "buttons": [{"label": "Snooze", "tool": "snooze_topic", "args": {"topic_id": 4}, "style": "primary"}],
}
IMAGE = {"type": "image", "alt": "A generated sketch", "url": "/api/images/abc_123", "caption": "Sketch"}
VALID = [CARD, TABLE, CHART, CHECKLIST, KEY_VALUE, ACTIONS, IMAGE]


def _user(db, name: str = "block-user") -> models.User:
    user = models.User(username=name, hashed_password="x", is_admin=False)
    db.add(user)
    db.commit()
    return user


def _invalid(block, match: str, **kwargs) -> None:
    with pytest.raises(UIBlockError, match=match):
        validate_block(block, **kwargs)


# -- catalog ----------------------------------------------------------------


@pytest.mark.parametrize("block", VALID, ids=lambda block: block["type"])
def test_every_catalog_type_validates_and_gets_an_id_and_version(block):
    """Each valid block keeps its fields and gains a fresh id and the catalog version."""
    validated = validate_block(block, offered_tool_names={"snooze_topic"})
    assert validated["version"] == UI_BLOCK_SCHEMA_VERSION == 1
    assert validated["id"].startswith("blk_") and len(validated["id"]) == 16
    assert validated["type"] == block["type"]
    for key in block:
        assert key in validated
    assert validate_block(block, offered_tool_names={"snooze_topic"})["id"] != validated["id"]


def test_the_model_cannot_choose_the_id_or_another_version():
    """An id from the model is replaced; another catalog version is refused."""
    assert validate_block({**CARD, "id": "mine"})["id"] != "mine"
    assert validate_block({**CARD, "version": 1})["version"] == 1
    _invalid({**CARD, "version": 2}, "version must be 1")


def test_a_json_string_block_is_accepted():
    """Some models send the object as JSON text."""
    assert validate_block(json.dumps(TABLE))["type"] == "table"
    _invalid("not json", "JSON object")
    _invalid(["card"], "JSON object")


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"type": "html", "html": "<b>x</b>"}, "type must be one of"),
        ({"title": "no type"}, "type must be one of"),
        ({**CARD, "onclick": "alert(1)"}, "Extra inputs are not permitted"),
        ({**CARD, "fields": [{"label": "a", "value": "b", "html": "<i>"}]}, "Extra inputs"),
        ({"type": "card"}, "title"),
        ({**CARD, "title": "x" * 121}, "title"),
        ({**CARD, "body": "x" * 2001}, "body"),
        ({**CARD, "fields": [{"label": "a", "value": "b"}] * 13}, "fields"),
        ({**CARD, "links": [{"label": "a", "href": "/a"}] * 5}, "links"),
    ],
)
def test_card_schema(block, match):
    """Card schema."""
    _invalid(block, match)


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>",
        "http://example.com",
        "//evil.example.com/x",
        "/\\evil.example.com",
        "https://",
        "ftp://example.com",
        "/emails\nx",
        " https://example.com",
    ],
)
def test_links_are_same_origin_paths_or_https(href):
    """Only /... (not //) and https:// with a host are links."""
    _invalid({**CARD, "links": [{"label": "x", "href": href}]}, "href must be")


def test_allowed_links():
    """Allowed links."""
    for href in ("/", "/emails?priority=important", "https://example.com/a?b=c#d"):
        assert validate_block({**CARD, "links": [{"label": "x", "href": href}]})["links"][0]["href"] == href


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({**TABLE, "columns": []}, "columns"),
        ({**TABLE, "columns": [f"c{i}" for i in range(9)], "rows": []}, "columns"),
        ({**TABLE, "rows": [["a", "b"]]}, "row 0 has 2 cells but there are 3 columns"),
        ({**TABLE, "rows": [["a", "b", "c"]] * 51}, "rows"),
        ({**TABLE, "rows": [["a", "b", {"x": 1}]]}, "rows"),
        ({**TABLE, "rows": [["a", "b", "x" * 301]]}, "rows"),
        ({**TABLE, "rows": [["a", "b", float("nan")]]}, "rows"),
    ],
)
def test_table_schema_and_budget(block, match):
    """Table schema and budget."""
    _invalid(block, match)


def test_table_budget_edges_are_accepted():
    """Table budget edges are accepted."""
    columns = [f"c{i}" for i in range(8)]
    validated = validate_block({"type": "table", "columns": columns, "rows": [[1] * 8] * 50})
    assert len(validated["rows"]) == 50 and len(validated["columns"]) == 8
    assert validate_block({"type": "table", "columns": ["a"], "rows": [[None]]})["rows"] == [[None]]


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({**CHART, "kind": "scatter"}, "kind"),
        ({**CHART, "labels": []}, "labels"),
        ({**CHART, "labels": [str(i) for i in range(51)], "series": [{"name": "s", "values": [1] * 51}]}, "labels"),
        ({**CHART, "series": [{"name": f"s{i}", "values": [1, 2]} for i in range(6)]}, "series"),
        ({**CHART, "series": [{"name": "s", "values": [1]}]}, "has 1 values but there are 2 labels"),
        ({**CHART, "series": [{"name": "s", "values": [1, float("inf")]}]}, "finite"),
        ({**CHART, "series": [{"name": "s", "values": [1, "many"]}]}, "values"),
        ({**CHART, "kind": "pie", "series": [{"name": "a", "values": [1, 2]}, {"name": "b", "values": [1, 2]}]}, "one"),
        ({**CHART, "kind": "pie", "series": [{"name": "a", "values": [1, -2]}]}, "non-negative"),
        ({**CHART, "kind": "pie", "series": [{"name": "a", "values": [0, 0]}]}, "positive sum"),
    ],
)
def test_chart_schema_and_budget(block, match):
    """Chart schema and budget."""
    _invalid(block, match)


def test_chart_budget_edges_are_accepted():
    """Chart budget edges are accepted."""
    labels = [str(i) for i in range(50)]
    series = [{"name": f"s{i}", "values": list(range(50))} for i in range(5)]
    assert len(validate_block({"type": "chart", "kind": "line", "labels": labels, "series": series})["series"]) == 5
    assert validate_block({**CHART, "kind": "pie"})["kind"] == "pie"


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"type": "checklist", "items": []}, "items"),
        ({"type": "checklist", "items": [{"text": "x"}] * 51}, "items"),
        ({"type": "checklist", "items": [{"text": ""}]}, "text"),
        ({"type": "checklist", "items": [{"text": "x", "checked": "yes"}]}, "checked"),
        ({"type": "key_value", "items": []}, "items"),
        ({"type": "key_value", "items": [{"key": "k", "value": "v"}] * 31}, "items"),
        ({"type": "key_value", "items": [{"key": "k", "value": None}]}, "value"),
        ({"type": "key_value", "items": [{"key": "k", "value": ["list"]}]}, "value"),
    ],
)
def test_checklist_and_key_value_schema_and_budget(block, match):
    """Checklist and key value schema and budget."""
    _invalid(block, match)


def test_checklist_items_default_to_unchecked():
    """Checklist items default to unchecked."""
    assert validate_block(CHECKLIST)["items"][1] == {"text": "Reply to Anna", "checked": False}


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"type": "actions", "buttons": []}, "buttons"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "snooze_topic"}] * 7}, "buttons"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "snooze topic; rm"}]}, "tool"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "snooze_topic", "args": "x"}]}, "args"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "snooze_topic", "style": "evil"}]}, "style"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "snooze_topic", "onclick": "x"}]}, "Extra"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": "delete_everything"}]}, "not available"),
        ({"type": "actions", "buttons": [{"label": "a", "tool": RENDER_UI_TOOL_NAME}]}, "not available"),
    ],
)
def test_actions_schema_and_offered_tools(block, match):
    """Actions schema and offered tools."""
    _invalid(block, match, offered_tool_names={"snooze_topic", RENDER_UI_TOOL_NAME})


def test_action_buttons_keep_their_arguments_and_default_style():
    """Action buttons keep their arguments and default style."""
    button = validate_block(
        {"type": "actions", "buttons": [{"label": "Go", "tool": "snooze_topic"}]}, offered_tool_names={"snooze_topic"}
    )["buttons"][0]
    assert button == {"label": "Go", "tool": "snooze_topic", "args": {}, "style": "secondary"}


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/cat.png",
        "http://localhost/api/images/1",
        "//example.com/api/images/1",
        "/api/images/../admin",
        "/api/images/",
        "/static/cat.png",
        "data:image/png;base64,AAAA",
    ],
)
def test_images_are_same_origin_janus_images_only(url):
    """Images are same origin janus images only."""
    _invalid({**IMAGE, "url": url}, "no external images")


def test_an_image_without_url_is_alt_text_only():
    """An image without url is alt text only."""
    assert "url" not in validate_block({"type": "image", "alt": "Not generated yet"})
    _invalid({"type": "image", "url": "/api/images/1"}, "alt")


def test_the_whole_block_has_an_8_kb_budget():
    """Each field can be within its own limit while the block is too big."""
    rows = [["x" * 300] * 8 for _ in range(4)]
    _invalid({"type": "table", "columns": [f"c{i}" for i in range(8)], "rows": rows}, f"limit is {UI_BLOCK_MAX_BYTES}")
    # Multi-byte text counts in UTF-8 bytes, not characters.
    _invalid({**CARD, "body": "€" * 2000, "fields": [{"label": "a", "value": "€" * 300}] * 12}, "bytes of JSON")


def test_validation_errors_are_short_and_name_the_field():
    """Validation errors are short and name the field."""
    with pytest.raises(UIBlockError) as error:
        validate_block({"type": "table", "columns": ["a"], "rows": [[{"x": 1}]] * 40})
    message = str(error.value)
    assert message.startswith("table.rows.0.0") and "more" in message and len(message) < 1500


def test_needs_trust_marks_buttons_and_external_links():
    """Needs trust marks buttons and external links."""
    assert ui_blocks.needs_trust(validate_block(ACTIONS))
    assert ui_blocks.needs_trust(validate_block({**CARD, "links": [{"label": "x", "href": "https://example.com"}]}))
    assert not ui_blocks.needs_trust(validate_block(CARD))
    assert not ui_blocks.needs_trust(validate_block(TABLE))


# -- per-turn collector -------------------------------------------------------


def test_turn_hands_out_each_frame_once_and_attaches_every_block(db):
    """Turn hands out each frame once and attaches every block."""
    turn = UIBlockTurn(available=True)
    first, second = validate_block(CARD), validate_block(TABLE)
    turn.add(first)
    assert turn.drain_frames() == [UI_BLOCK_PREFIX + json.dumps(first, ensure_ascii=False)]
    assert turn.drain_frames() == []
    turn.add(second)
    assert [json.loads(frame[len(UI_BLOCK_PREFIX) :])["id"] for frame in turn.drain_frames()] == [second["id"]]

    message = models.ConversationMessage(role=models.MessageRole.ASSISTANT, content="")
    turn.attach_to(message)
    rows = [(row.position, row.block_id, row.block_type, row.schema_version, row.payload) for row in message.ui_blocks]
    assert rows == [(0, first["id"], "card", 1, first), (1, second["id"], "table", 1, second)]


def test_turn_caps_blocks_per_reply():
    """Turn caps blocks per reply."""
    turn = UIBlockTurn(available=True)
    for _ in range(MAX_UI_BLOCKS_PER_MESSAGE):
        turn.add(validate_block(CARD))
    with pytest.raises(UIBlockError, match=f"at most {MAX_UI_BLOCKS_PER_MESSAGE}"):
        turn.add(validate_block(CARD))


# -- toolbox ----------------------------------------------------------------


def _tool(db, turn, trace=None):
    context = ToolContext(db=db, user=_user(db, f"tb-{id(turn)}"), turn_trace=trace or TurnTrace(), ui_blocks=turn)
    (render_ui,) = build_ui_block_tools(context)
    assert render_ui.name == RENDER_UI_TOOL_NAME
    return render_ui


def test_render_ui_keeps_the_block_and_answers_a_short_confirmation(db):
    """Render ui keeps the block and answers a short confirmation."""
    turn = UIBlockTurn(available=True)
    answer = _tool(db, turn).invoke({"block": CARD})
    (block,) = turn.blocks()
    assert answer.startswith(f"Shown to the user: card block {block['id']}.")
    assert "Invoice overdue" not in answer


def test_render_ui_refuses_invalid_blocks_with_the_reason(db):
    """Render ui refuses invalid blocks with the reason."""
    turn = UIBlockTurn(available=True)
    with pytest.raises(RuntimeError, match=r"invalid UI block: table: .*row 0 has 2 cells"):
        _tool(db, turn).invoke({"block": {**TABLE, "rows": [["a", "b"]]}})
    assert turn.blocks() == []


def test_render_ui_errors_for_clients_that_cannot_render_blocks(db):
    """Render ui errors for clients that cannot render blocks."""
    for turn in (None, UIBlockTurn(available=False)):
        with pytest.raises(RuntimeError, match="this client cannot render UI blocks; answer in Markdown instead"):
            _tool(db, turn).invoke({"block": CARD})


def test_render_ui_only_offers_buttons_for_the_turns_tools(db):
    """Render ui only offers buttons for the turns tools."""
    turn = UIBlockTurn(available=True, offered_tool_names=frozenset({"snooze_topic", RENDER_UI_TOOL_NAME}))
    render_ui = _tool(db, turn)
    render_ui.invoke({"block": ACTIONS})
    with pytest.raises(RuntimeError, match="not available in this chat: send_email"):
        render_ui.invoke({"block": {"type": "actions", "buttons": [{"label": "Send", "tool": "send_email"}]}})
    assert [block["type"] for block in turn.blocks()] == ["actions"]


def test_render_ui_refuses_buttons_and_external_links_after_a_suspected_injection(db):
    """Render ui refuses buttons and external links after a suspected injection."""
    trace = TurnTrace()
    trace.record(TurnToolCall("mail", "get_email_detail", True, ("instruction_override",)))
    turn = UIBlockTurn(available=True, offered_tool_names=frozenset({"snooze_topic"}))
    render_ui = _tool(db, turn, trace)
    external = {**CARD, "links": [{"label": "Pay now", "href": "https://pay.example.com"}]}
    for block in (ACTIONS, external):
        assert render_ui.invoke({"block": block}).startswith("BLOCKED: render_ui was not run because content read")
    assert render_ui.invoke({"block": TABLE}).startswith("Shown to the user: table block")
    assert render_ui.invoke({"block": CARD}).startswith("Shown to the user: card block")
    assert [block["type"] for block in turn.blocks()] == ["table", "card"]


# -- chat-only fences ---------------------------------------------------------


def _prepare(db, user, mode, **kwargs):
    return RuntimeAdapter(db, user).prepare_step(
        task_id=f"blocks-{mode}",
        objective="show my tasks as a table",
        requested_tool_ids=("ui_blocks", "delegation", "core"),
        task_mode=mode,
        guard_tool_errors=True,
        **kwargs,
    )


def test_chat_step_offers_render_ui_but_never_grants_it_onward(db):
    """Chat step offers render ui but never grants it onward."""
    user = _user(db)
    turn = UIBlockTurn(available=True)
    prepared = _prepare(
        db, user, "chat", confirmed_tool_ids=frozenset({"ui_blocks", "delegation", "core"}), ui_blocks=turn
    )
    assert prepared.tool_ids_by_name[RENDER_UI_TOOL_NAME] == UI_BLOCKS_TOOL_ID
    assert UI_BLOCKS_TOOL_ID in TASK_MODE_ALLOWED_TOOL_IDS["chat"]
    # Blocks only show something; they are not escalated as a whole (buttons and links are, in the toolbox).
    assert RENDER_UI_TOOL_NAME not in INJECTION_ESCALATED_TOOL_NAMES
    assert UI_BLOCKS_TOOL_ID not in tool_policy.delegable_tool_ids(
        frozenset({UI_BLOCKS_TOOL_ID}), DEFAULT_TOOL_REGISTRY.entries()
    )
    entry = next(entry for entry in DEFAULT_TOOL_REGISTRY.entries() if entry.id == UI_BLOCKS_TOOL_ID)
    assert (entry.confirmation, entry.operation, entry.docs_ref) == ("never", "read", "tools.md#ui-blocks")


@pytest.mark.parametrize("mode", ["trigger", "automation", "delegated", "briefing", "digest", "ingestion"])
def test_render_ui_is_never_offered_outside_chat(db, mode):
    """No unattended or pipeline mode is offered the toolbox, whatever is requested or narrowed to."""
    user = _user(db, f"mode-{mode}")
    entries = DEFAULT_TOOL_REGISTRY.entries()
    if mode in tool_policy.UNATTENDED_TASK_MODES:
        offered, confirmed = tool_policy.unattended_tool_sets(
            db, user.id, entries, task_mode=mode, narrowed_to=frozenset({"ui_blocks", "core"})
        )
        assert UI_BLOCKS_TOOL_ID not in offered | confirmed
        prepared = _prepare(
            db, user, mode, allowed_tool_ids=frozenset({"ui_blocks", "core"}), confirmed_tool_ids=offered
        )
    else:
        assert UI_BLOCKS_TOOL_ID not in TASK_MODE_ALLOWED_TOOL_IDS[mode]
        prepared = _prepare(db, user, mode)
    assert UI_BLOCKS_TOOL_ID not in {entry.id for entry in prepared.selection.entries}
    assert RENDER_UI_TOOL_NAME not in prepared.tool_ids_by_name


def test_toolbox_is_disabled_outside_chat_even_when_allowlisted(db):
    """Toolbox is disabled outside chat even when allowlisted."""
    prepared = _prepare(
        db, _user(db), "trigger", allowed_tool_ids=frozenset({"ui_blocks"}), confirmed_tool_ids=frozenset({"ui_blocks"})
    )
    assert ("ui_blocks", "disabled_by_context") in [(denial.tool_id, denial.reason) for denial in prepared.denials]


def test_locked_switch_cannot_be_turned_on(db):
    """Locked switch cannot be turned on."""
    with pytest.raises(tool_policy.LockedToolError):
        tool_policy.set_policy(db, _user(db).id, "ui_blocks", True)
