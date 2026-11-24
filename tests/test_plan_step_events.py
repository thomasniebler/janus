"""Turn step (``__PLAN_EVENT__``) frames of a chat turn (JAN-316)."""

import asyncio
import json

from langchain_core.messages import AIMessage

from janus import llm_service
from janus.plan_graph import PlanStepStatus
from janus.services.plan_step_events import (
    PLAN_EVENT_PREFIX,
    PlanStepEvent,
    TurnEventStream,
    parse_marker,
    requested_tool_names,
)
from janus.services.tool_call_events import TOOL_EVENT_PREFIX


def _model_start() -> dict:
    return {"event": "on_chat_model_start", "name": "ChatModel", "run_id": "m", "parent_ids": []}


def _model_end(*tool_names: str) -> dict:
    calls = [{"name": name, "args": {}, "id": f"tc-{i}"} for i, name in enumerate(tool_names)]
    return {
        "event": "on_chat_model_end",
        "name": "ChatModel",
        "run_id": "m",
        "parent_ids": [],
        "data": {"output": AIMessage(content="", tool_calls=calls)},
    }


def _tool(kind: str, name: str, run_id: str, *, output: str = "ok", parent_ids=()) -> dict:
    data = {"input": {"q": "x"}} if kind == "on_tool_start" else {"output": output}
    return {"event": kind, "name": name, "run_id": run_id, "parent_ids": list(parent_ids), "data": data}


def _decode(frames: list[str]) -> list[tuple[str, dict]]:
    decoded = []
    for frame in frames:
        for prefix, kind in ((PLAN_EVENT_PREFIX, "plan"), (TOOL_EVENT_PREFIX, "tool")):
            if frame.startswith(prefix):
                decoded.append((kind, json.loads(frame[len(prefix) :])))
    return decoded


def _run(events: list[dict], *, failed: bool = False) -> list[tuple[str, dict]]:
    turn = TurnEventStream(clock=lambda: 0.0)
    frames = [frame for event in events for frame in turn.observe(event)]
    return _decode(frames + turn.finish(failed=failed))


def _summary(decoded: list[tuple[str, dict]]) -> list[tuple]:
    return [
        ("plan", payload["step_id"], payload["status"])
        if kind == "plan"
        else ("tool", payload["call_id"], payload["phase"], payload["step_id"])
        for kind, payload in decoded
    ]


def test_two_tool_calls_in_two_rounds_stream_two_steps_in_order():
    """Each model round that asks for tools is one step; its calls carry the step id."""
    decoded = _run(
        [
            _model_start(),
            _model_end("list_memory_facts"),
            _tool("on_tool_start", "list_memory_facts", "call-1"),
            _tool("on_tool_end", "list_memory_facts", "call-1"),
            _model_start(),
            _model_end("search_emails"),
            _tool("on_tool_start", "search_emails", "call-2"),
            _tool("on_tool_end", "search_emails", "call-2", output="ERROR: search_emails failed: boom"),
            _model_start(),
            # The final round only writes the reply: not a step.
            _model_end(),
        ]
    )

    assert _summary(decoded) == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "call-1", "start", "step-1"),
        ("tool", "call-1", "end", "step-1"),
        ("plan", "step-1", "completed"),
        ("plan", "step-2", "pending"),
        ("plan", "step-2", "running"),
        ("tool", "call-2", "start", "step-2"),
        ("tool", "call-2", "error", "step-2"),
        ("plan", "step-2", "failed"),
    ]
    first = decoded[0][1]
    assert first == {"step_id": "step-1", "index": 1, "status": "pending", "tool_names": ["list_memory_facts"]}
    assert decoded[5][1]["index"] == 2


def test_step_statuses_are_the_plan_graph_vocabulary():
    """The wire statuses are PlanStepStatus values, shared with the Tasks/Plans surface (JAN-204)."""
    decoded = _run([_model_end("a"), _tool("on_tool_start", "a", "c"), _tool("on_tool_end", "a", "c")])
    statuses = {payload["status"] for kind, payload in decoded if kind == "plan"}
    assert statuses <= {status.value for status in PlanStepStatus}


def test_parallel_calls_share_one_step_that_closes_after_the_last():
    """A round asking for two tools is one step; a blocked call fails it."""
    decoded = _run(
        [
            _model_end("a", "b"),
            _tool("on_tool_start", "a", "c-a"),
            _tool("on_tool_start", "b", "c-b"),
            _tool("on_tool_end", "a", "c-a"),
            _tool("on_tool_end", "b", "c-b", output="BLOCKED: b was not run because the user declined."),
        ]
    )
    assert _summary(decoded) == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "c-a", "start", "step-1"),
        ("tool", "c-b", "start", "step-1"),
        ("tool", "c-a", "end", "step-1"),
        ("tool", "c-b", "blocked", "step-1"),
        ("plan", "step-1", "failed"),
    ]
    assert decoded[0][1]["tool_names"] == ["a", "b"]


def test_call_without_model_events_opens_an_implicit_step_closed_at_stream_end():
    """Providers that report no model events still group every call under a step."""
    decoded = _run([_tool("on_tool_start", "a", "c"), _tool("on_tool_end", "a", "c")])
    assert _summary(decoded) == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "c", "start", "step-1"),
        ("tool", "c", "end", "step-1"),
        ("plan", "step-1", "completed"),
    ]
    assert decoded[0][1]["tool_names"] == []


def test_step_with_a_call_that_never_ran_closes_on_the_next_round():
    """A requested call LangChain answered without a tool run does not keep the step open."""
    decoded = _run(
        [
            _model_end("a", "not_a_tool"),
            _tool("on_tool_start", "a", "c"),
            _tool("on_tool_end", "a", "c"),
            _model_start(),
        ]
    )
    assert _summary(decoded)[-1] == ("plan", "step-1", "completed")
    assert [entry for entry in _summary(decoded) if entry[0] == "plan"].count(("plan", "step-1", "completed")) == 1


def test_stream_failure_fails_the_open_step():
    """A turn that breaks mid-step closes the step as failed."""
    decoded = _run([_model_end("a"), _tool("on_tool_start", "a", "c")], failed=True)
    assert _summary(decoded)[-1] == ("plan", "step-1", "failed")


def test_model_events_inside_a_tool_call_are_not_rounds():
    """A tool that runs a model internally neither opens nor closes a step."""
    decoded = _run(
        [
            _model_end("summarize"),
            _tool("on_tool_start", "summarize", "c"),
            _model_start(),
            _model_end("nested_tool"),
            _tool("on_tool_end", "summarize", "c"),
        ]
    )
    assert _summary(decoded) == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "c", "start", "step-1"),
        ("tool", "c", "end", "step-1"),
        ("plan", "step-1", "completed"),
    ]


def test_nested_wrapper_runs_do_not_count_as_calls():
    """The runtime's wrapper runs (JAN-249) are one call, so the step closes once."""
    decoded = _run(
        [
            _model_end("a"),
            _tool("on_tool_start", "a", "outer"),
            _tool("on_tool_start", "a", "inner", parent_ids=["outer"]),
            _tool("on_tool_end", "a", "inner", parent_ids=["outer"]),
            _tool("on_tool_end", "a", "outer"),
        ]
    )
    assert [entry for entry in _summary(decoded) if entry[0] == "tool"] == [
        ("tool", "outer", "start", "step-1"),
        ("tool", "outer", "end", "step-1"),
    ]
    assert _summary(decoded)[-1] == ("plan", "step-1", "completed")


def test_parse_marker_and_requested_tool_names():
    """Frames round-trip; anything else is not a plan event."""
    event = PlanStepEvent(step_id="step-3", index=3, status="running", tool_names=["a"])
    assert parse_marker(event.to_marker()) == event
    assert parse_marker("hello") is None
    assert parse_marker(PLAN_EVENT_PREFIX + "{not json") is None
    assert requested_tool_names({"tool_calls": [{"name": "a"}, {"name": ""}, {}]}) == ["a"]
    assert requested_tool_names("plain text") == []


def test_stream_agent_chat_streams_plan_events_and_fails_the_step_on_error(monkeypatch):
    """The LLM stream yields the step frames and closes an open step as failed before the error text."""
    service = object.__new__(llm_service.LLMService)
    service.llm = object()

    class FakeAgentExecutor:
        def __init__(self, *, agent, tools, verbose):
            pass

        async def astream_events(self, payload, version):
            yield _model_start()
            yield _model_end("list_memory_facts")
            yield _tool("on_tool_start", "list_memory_facts", "call-1")
            raise RuntimeError("provider went away")

    monkeypatch.setattr(llm_service, "create_tool_calling_agent", lambda llm, tools, prompt: "agent")
    monkeypatch.setattr(llm_service, "AgentExecutor", FakeAgentExecutor)

    async def _collect():
        return [token async for token in service.stream_agent_chat("sys", [], [], "hi")]

    tokens = asyncio.run(_collect())
    assert _summary(_decode(tokens)) == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("tool", "call-1", "start", "step-1"),
        ("plan", "step-1", "failed"),
    ]
    assert tokens[-1].startswith("\n\n[Error during chat]: provider went away")


def test_real_agent_executor_reports_one_step_per_tool_round():
    """Wired to LangChain's real AgentExecutor, two tool rounds stream as two steps."""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool

    class _ToolCallingFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def alpha(q: str) -> str:
        """Return a."""
        return "a"

    @tool
    def beta(q: str) -> str:
        """Return b."""
        return "b"

    service = object.__new__(llm_service.LLMService)
    service.llm = _ToolCallingFake(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "alpha", "args": {"q": "1"}, "id": "c1"}]),
            AIMessage(content="", tool_calls=[{"name": "beta", "args": {"q": "2"}, "id": "c2"}]),
            AIMessage(content="All done."),
        ]
    )

    async def _collect():
        return [token async for token in service.stream_agent_chat("sys", [alpha, beta], [], "go")]

    summary = _summary(_decode(asyncio.run(_collect())))
    assert [entry for entry in summary if entry[0] == "plan"] == [
        ("plan", "step-1", "pending"),
        ("plan", "step-1", "running"),
        ("plan", "step-1", "completed"),
        ("plan", "step-2", "pending"),
        ("plan", "step-2", "running"),
        ("plan", "step-2", "completed"),
    ]
    assert [(entry[2], entry[3]) for entry in summary if entry[0] == "tool"] == [
        ("start", "step-1"),
        ("end", "step-1"),
        ("start", "step-2"),
        ("end", "step-2"),
    ]
