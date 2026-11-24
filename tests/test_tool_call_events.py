"""Contract tests for the structured chat tool-call events (JAN-249)."""

import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from janus import models, runtime_adapter
from janus.services import tool_call_events as events
from janus.services.tool_call_events import (
    ARGS_MAX_CHARS,
    REDACTED,
    RESULT_PREVIEW_MAX_CHARS,
    ToolCallRecorder,
    ToolEventTracker,
)


def _decode(marker: str) -> dict:
    assert marker.startswith(events.TOOL_EVENT_PREFIX), marker
    return json.loads(marker[len(events.TOOL_EVENT_PREFIX) :])


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------------------
# Redaction and truncation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "user_password",
        "token",
        "access_token",
        "refreshToken",
        "secret",
        "client_secret",
        "api_key",
        "API-Key",
        "apiKey",
        "Authorization",
        "cookie",
        "Set-Cookie",
        "credentials",
        "session_id",
        "private_key",
        "auth",
        "otp",
    ],
)
def test_credential_like_keys_are_redacted(key):
    """Every common spelling of a credential field is replaced, not shown."""
    assert events.redact({key: "hunter2"}) == {key: REDACTED}


@pytest.mark.parametrize("key", ["query", "to", "subject", "author", "limit", "body", "event_id"])
def test_ordinary_keys_are_kept(key):
    """Plain arguments stay visible."""
    assert events.redact({key: "value"}) == {key: "value"}


def test_redaction_reaches_nested_values_and_bearer_strings():
    """Nested dicts/lists are walked, and header-shaped values are redacted by content."""
    args = {
        "request": {"headers": {"Authorization": "Bearer abc", "Accept": "json"}, "items": [{"token": "t"}]},
        "note": "Bearer sk-live-123",
        "plain": "Basic is a word but not a header",
        "count": 3,
        "flag": True,
        "missing": None,
    }
    assert events.redact(args) == {
        "request": {"headers": {"Authorization": REDACTED, "Accept": "json"}, "items": [{"token": REDACTED}]},
        "note": REDACTED,
        "plain": "Basic is a word but not a header",
        "count": 3,
        "flag": True,
        "missing": None,
    }


def test_redaction_stringifies_unknown_values_and_caps_depth():
    """Non-JSON values become strings and very deep nesting is cut."""
    deep: dict = {}
    cursor = deep
    for _ in range(10):
        cursor["next"] = {}
        cursor = cursor["next"]
    redacted = events.redact({"when": object, "deep": deep})
    assert redacted["when"] == str(object)
    assert "…" in json.dumps(redacted, ensure_ascii=False)


def test_preview_args_marks_depth_capped_arguments_as_truncated():
    """Arguments nested past the depth cap are cut, and the cut is reported."""
    deep: dict = {"leaf": "x"}
    for _ in range(10):
        deep = {"next": deep}
    args, cut = events.preview_args({"query": "q", "deep": deep})
    assert cut is True
    assert "…" in json.dumps(args, ensure_ascii=False)
    assert "leaf" not in json.dumps(args)

    shallow, cut = events.preview_args({"a": {"b": {"c": "d"}}})
    assert (shallow, cut) == ({"a": {"b": {"c": "d"}}}, False)


def test_truncate_text_marks_cut_text():
    """Text over the limit is cut to the limit, ending in an ellipsis."""
    assert events.truncate_text("short", 10) == ("short", False)
    text, cut = events.truncate_text("x" * 50, 10)
    assert cut is True
    assert len(text) == 10
    assert text.endswith("…")


def test_preview_args_shortens_long_strings_and_falls_back_to_cut_json():
    """Long string values are shortened; oversized arguments become cut JSON text."""
    args, cut = events.preview_args({"body": "y" * 5000, "password": "p"})
    assert cut is True
    assert args["password"] == REDACTED
    assert len(args["body"]) == events.ARG_STRING_MAX_CHARS

    many = {f"field_{index}": "z" * 900 for index in range(20)}
    text, cut = events.preview_args(many)
    assert cut is True
    assert isinstance(text, str)
    assert len(text) == ARGS_MAX_CHARS

    small, cut = events.preview_args({"query": "inbox"})
    assert (small, cut) == ({"query": "inbox"}, False)


def test_render_output_handles_tool_messages_and_structures():
    """Tool answers become plain text whatever shape the tool returned."""
    assert events.render_output("text") == "text"
    assert events.render_output(ToolMessage(content="from message", tool_call_id="1")) == "from message"
    assert json.loads(events.render_output({"a": [1, 2]})) == {"a": [1, 2]}


def test_classify_result_only_trusts_the_runtime_refusal_and_failure_shapes():
    """BLOCKED/ERROR answers name the tool; other text starting with those words is a normal result."""
    blocked = "BLOCKED: send_email was not run because the integration it needs is not connected."
    assert events.classify_result("send_email", blocked) == events.PHASE_BLOCKED
    assert events.classify_result("send_email", "ERROR: send_email failed: boom") == events.PHASE_ERROR
    assert events.classify_result("get_email", "BLOCKED: send_email was not run because") == events.PHASE_END
    assert events.classify_result("get_email", "BLOCKED on review") == events.PHASE_END


def test_parse_marker_rejects_other_and_malformed_frames():
    """Only well-formed __TOOL_EVENT__ frames parse."""
    assert events.parse_marker("hello") is None
    assert events.parse_marker("__TOOL_EVENT__:{not json") is None
    assert events.parse_marker('__TOOL_EVENT__:{"unexpected": 1}') is None
    assert events.is_tool_protocol_frame("__TOOL_CALL__:x")
    assert events.is_tool_protocol_frame("__TOOL_RESULT__:x")
    assert events.is_tool_protocol_frame("__TOOL_EVENT__:{}")
    assert not events.is_tool_protocol_frame("Hello")


# ---------------------------------------------------------------------------
# Tracker: LangChain stream events -> frames
# ---------------------------------------------------------------------------


def test_tracker_reports_start_and_end_with_duration_and_preview():
    """A call's frames share its call_id; the end frame carries args, preview and duration."""
    clock = _Clock()
    tracker = ToolEventTracker(clock=clock)
    start = _decode(
        tracker.start(
            {"name": "search_emails", "run_id": "r1", "data": {"input": {"query": "invoice", "token": "abc"}}}
        )
    )
    clock.now += 0.25
    end = _decode(tracker.end({"name": "search_emails", "run_id": "r1", "data": {"output": "z" * 5000}}))

    assert start == {
        "call_id": "r1",
        "tool_id": None,
        "tool_name": "search_emails",
        "phase": "start",
        "args": {"query": "invoice", "token": REDACTED},
        "args_truncated": False,
        "result_preview": None,
        "result_truncated": False,
        "duration_ms": None,
        "approval": None,
        "step_id": None,
    }
    assert end["phase"] == "end"
    assert end["args"] == start["args"]
    assert end["duration_ms"] == 250
    assert end["result_truncated"] is True
    assert len(end["result_preview"]) == RESULT_PREVIEW_MAX_CHARS


def test_tracker_unwraps_langchain_tool_call_inputs():
    """A tool invoked with a ToolCall dict reports its arguments, not the envelope."""
    tracker = ToolEventTracker()
    tool_call = {"type": "tool_call", "id": "x", "name": "t", "args": {"query": "q", "secret": "s"}}
    start = _decode(tracker.start({"name": "t", "run_id": "r", "data": {"input": tool_call}}))
    assert start["args"] == {"query": "q", "secret": REDACTED}


def test_tracker_ignores_nested_wrapper_runs():
    """The runtime's wrappers re-invoke the tool; only the outermost run is a call."""
    tracker = ToolEventTracker()
    assert tracker.start({"name": "t", "run_id": "outer", "data": {"input": {}}}) is not None
    assert tracker.start({"name": "t", "run_id": "inner", "parent_ids": ["outer"], "data": {"input": {}}}) is None
    assert tracker.end({"name": "t", "run_id": "inner", "parent_ids": ["outer"], "data": {"output": "x"}}) is None
    assert _decode(tracker.end({"name": "t", "run_id": "outer", "data": {"output": "x"}}))["phase"] == "end"


def test_tracker_reports_raised_errors_and_end_without_start():
    """on_tool_error ends a call as an error; an end with no start still yields a frame."""
    tracker = ToolEventTracker()
    tracker.start({"name": "t", "run_id": "r", "data": {"input": {}}})
    error = _decode(tracker.error({"name": "t", "run_id": "r", "data": {"error": RuntimeError("boom")}}))
    assert (error["phase"], error["result_preview"]) == ("error", "boom")
    orphan = _decode(tracker.end({"name": "t", "run_id": "other", "data": {"output": "ok"}}))
    assert (orphan["phase"], orphan["duration_ms"]) == ("end", None)


def _run_through_tracker(tool, args):
    """Run ``tool`` like the agent executor would and return the tracker's frames."""

    async def collect():
        tracker = ToolEventTracker()
        frames = []
        async for event in tool.astream_events(args, version="v2"):
            handler = {"on_tool_start": tracker.start, "on_tool_end": tracker.end, "on_tool_error": tracker.error}
            if event["event"] in handler:
                frame = handler[event["event"]](event)
                if frame is not None:
                    frames.append(_decode(frame))
        return frames

    return asyncio.run(collect())


def _send_email(to: str, password: str = "") -> str:
    """Send an email."""
    return "sent"


def test_refusal_stub_surfaces_as_blocked():
    """A tool-policy refusal (runtime_adapter's BLOCKED answer) ends in phase 'blocked'."""
    base = StructuredTool.from_function(_send_email)
    stub = runtime_adapter._refusal_stub(
        base, "mail.send", "missing_capability", runtime_adapter.ToolCallLog(), user_id=1
    )
    frames = _run_through_tracker(stub, {"to": "a@example.com", "password": "pw"})
    assert [frame["phase"] for frame in frames] == ["start", "blocked"]
    assert frames[-1]["args"] == {"to": "a@example.com", "password": REDACTED}
    assert frames[-1]["result_preview"].startswith("BLOCKED: _send_email was not run")


def test_guarded_tool_failure_surfaces_as_error_once():
    """A raising tool behind the chat error guard is one call that ends in phase 'error'."""

    def explode(to: str) -> str:
        """Explode."""
        raise RuntimeError("smtp down")

    base = StructuredTool.from_function(explode)
    guarded = runtime_adapter._guarded(base, "mail.send", runtime_adapter.ToolCallLog())
    frames = _run_through_tracker(guarded, {"to": "a@example.com"})
    assert [frame["phase"] for frame in frames] == ["start", "error"]
    assert "smtp down" in frames[-1]["result_preview"]


# ---------------------------------------------------------------------------
# Recorder: tool ids and persistence rows
# ---------------------------------------------------------------------------


def test_recorder_names_the_capability_and_keeps_the_final_state(db):
    """The recorder fills in tool_id and stores one row per call in start order."""
    tracker = ToolEventTracker()
    recorder = ToolCallRecorder({"search_emails": "mail"})
    frames = [
        tracker.start({"name": "search_emails", "run_id": "a", "data": {"input": {"query": "x"}}}),
        tracker.start({"name": "unknown_tool", "run_id": "b", "data": {"input": {}}}),
        tracker.end({"name": "unknown_tool", "run_id": "b", "data": {"output": "BLOCKED: unknown_tool was not run"}}),
        tracker.end({"name": "search_emails", "run_id": "a", "data": {"output": "2 mails"}}),
    ]
    enriched = [_decode(recorder.observe(frame)) for frame in frames]
    assert enriched[0]["tool_id"] == "mail"
    assert enriched[1]["tool_id"] is None
    assert recorder.observe("plain text") is None

    user = models.User(username="recorder", hashed_password="x")
    db.add(user)
    db.flush()
    conversation = models.Conversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    message = models.ConversationMessage(
        conversation_id=conversation.id, role=models.MessageRole.ASSISTANT, content="done"
    )
    recorder.attach_to(message)
    db.add(message)
    db.commit()

    rows = db.query(models.ConversationToolCall).order_by(models.ConversationToolCall.position).all()
    assert [(row.call_id, row.tool_id, row.phase, row.position) for row in rows] == [
        ("a", "mail", "end", 0),
        ("b", None, "blocked", 1),
    ]
    assert rows[0].args == {"query": "x"}
    assert rows[0].result_preview == "2 mails"
