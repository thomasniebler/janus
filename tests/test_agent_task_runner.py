"""Tests for the synchronous, human-absent agent tool loop (JAN-192)."""

from types import SimpleNamespace

import pytest

from janus.agent_task_runner import (
    AGENT_TASK_MAX_SECONDS,
    AGENT_TOOL_RESULT_MAX_CHARS,
    AgentTaskLimitError,
    coerce_agent_output,
    run_agent_task,
    summarize_intermediate_steps,
)


def test_coerce_agent_output_handles_content_block_responses():
    """Content-block providers must not have a Python repr persisted as the result."""
    assert coerce_agent_output("  done  ") == "done"
    assert coerce_agent_output([{"type": "text", "text": "part one "}, {"text": "part two"}]) == "part one part two"
    assert coerce_agent_output(["a", {"text": "b"}, {"image": "ignored"}]) == "ab"
    assert coerce_agent_output(None) == ""


def test_summarize_intermediate_steps_records_tool_names_and_truncated_results():
    """The stored trace answers "which tools ran" without unbounded tool output."""
    steps = [
        (SimpleNamespace(tool="list_recent_events"), "three events"),
        (SimpleNamespace(tool="get_current_time"), "x" * (AGENT_TOOL_RESULT_MAX_CHARS + 200)),
    ]

    calls = summarize_intermediate_steps(steps)

    assert [call.tool for call in calls] == ["list_recent_events", "get_current_time"]
    assert calls[0].result == "three events"
    assert len(calls[1].result) <= AGENT_TOOL_RESULT_MAX_CHARS
    assert calls[1].result.endswith("…")


def test_summarize_intermediate_steps_renders_record_list_observations():
    """Most Janus tools return list[dict] records, not text content blocks.

    Reusing the provider's content-block coercion alone would flatten those to
    an empty string, making the trace silently blank for exactly the tools a
    trigger actually calls.
    """
    steps = [(SimpleNamespace(tool="list_calendar_events"), [{"title": "Standup", "start": "09:00"}])]

    (call,) = summarize_intermediate_steps(steps)

    assert call.tool == "list_calendar_events"
    assert "Standup" in call.result


def test_summarize_intermediate_steps_skips_malformed_entries():
    """A provider returning an unexpected shape must not crash the whole run."""
    assert summarize_intermediate_steps(None) == ()
    assert summarize_intermediate_steps("not a list") == ()
    assert summarize_intermediate_steps([("only-one",), (SimpleNamespace(tool="  "), "x")]) == ()


def test_summarize_intermediate_steps_never_records_tool_arguments():
    """Tool arguments are model-authored and may restate hidden input (JAN-185's rule)."""
    steps = [(SimpleNamespace(tool="list_recent_events", tool_input={"secret": "do-not-leak"}), "ok")]

    assert "do-not-leak" not in str(summarize_intermediate_steps(steps))


def test_run_agent_task_raises_instead_of_swallowing_provider_failures(monkeypatch):
    """An automation must never be able to report success for work that did not run.

    Chat yields an in-band ``[Error during chat]`` string; a human-absent run
    must not, because the caller would store it as a completed result.
    """
    import janus.agent_task_runner as runner

    monkeypatch.setattr(runner, "create_tool_calling_agent", lambda *_args, **_kwargs: object())

    class Boom:
        def __init__(self, **_kwargs):
            pass

        def invoke(self, _payload):
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr(runner, "AgentExecutor", Boom)

    with pytest.raises(RuntimeError, match="provider unreachable"):
        run_agent_task(object(), "system", [], "do the thing")


def _executor_returning(monkeypatch, response: dict, captured: dict | None = None):
    """Patch AgentExecutor to return a canned response, recording its kwargs."""
    import janus.agent_task_runner as runner

    monkeypatch.setattr(runner, "create_tool_calling_agent", lambda *_a, **_k: object())

    class FakeExecutor:
        def __init__(self, **kwargs):
            if captured is not None:
                captured.update(kwargs)

        def invoke(self, payload):
            if captured is not None:
                captured["payload"] = payload
            return response

    monkeypatch.setattr(runner, "AgentExecutor", FakeExecutor)


def test_run_agent_task_rejects_the_iteration_limit_placeholder_as_a_result(monkeypatch):
    """AgentExecutor returns a placeholder instead of raising when it runs out.

    Persisting "Agent stopped due to iteration limit or time limit." as the
    step result would render in the UI and to the model as a genuine answer,
    which is exactly the "report success for work that did not happen" failure
    this module exists to prevent.
    """
    _executor_returning(
        monkeypatch,
        {
            "output": "Agent stopped due to iteration limit or time limit.",
            "intermediate_steps": [(SimpleNamespace(tool="list_recent_events"), "ok")],
        },
    )

    with pytest.raises(AgentTaskLimitError, match="stopped at its limit"):
        run_agent_task(object(), "system", [], "do the thing", max_iterations=8)


def test_run_agent_task_rejects_a_run_that_used_every_allowed_iteration(monkeypatch):
    """Version-independent backstop: the placeholder's wording is a library detail.

    Exhausting the cap is treated as a failure even when the output looks
    plausible -- for unattended work, a false failure a human can see beats a
    silently truncated summary presented as fact.
    """
    _executor_returning(
        monkeypatch,
        {
            "output": "Here is a partial answer",
            "intermediate_steps": [(SimpleNamespace(tool="get_current_time"), "ok")] * 3,
        },
    )

    with pytest.raises(AgentTaskLimitError):
        run_agent_task(object(), "system", [], "do the thing", max_iterations=3)


def test_run_agent_task_bounds_wall_clock_time_not_just_iterations(monkeypatch):
    """A single round against a slow local model has no inherent time limit.

    ``DurableTriggerWorker`` re-claims a ``running`` trigger once its 300s
    lease expires, so an unbounded run would be executed a second time
    concurrently. The cap must stay comfortably below that lease.
    """
    captured: dict = {}
    _executor_returning(monkeypatch, {"output": "done", "intermediate_steps": []}, captured)

    run_agent_task(object(), "system", [], "do the thing")

    assert captured["max_execution_time"] == AGENT_TASK_MAX_SECONDS
    assert AGENT_TASK_MAX_SECONDS < 300, "must stay under DurableTriggerWorker's default lease"


def test_run_agent_task_bounds_the_loop_and_returns_the_trace(monkeypatch):
    """The loop is explicitly capped and hands back what actually ran."""
    import janus.agent_task_runner as runner

    monkeypatch.setattr(runner, "create_tool_calling_agent", lambda *_args, **_kwargs: object())
    captured: dict = {}

    class FakeExecutor:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, payload):
            captured["payload"] = payload
            return {
                "output": "all quiet",
                "intermediate_steps": [(SimpleNamespace(tool="get_current_time"), "08:30")],
            }

    monkeypatch.setattr(runner, "AgentExecutor", FakeExecutor)

    result = run_agent_task(object(), "system prompt", [], "check the day", max_iterations=3)

    assert result.output == "all quiet"
    assert [call.tool for call in result.tool_calls] == ["get_current_time"]
    assert captured["max_iterations"] == 3
    assert captured["return_intermediate_steps"] is True
    assert captured["payload"]["input"] == "check the day"
    # No conversation history: a trigger run is self-contained.
    assert captured["payload"]["history"] == []
