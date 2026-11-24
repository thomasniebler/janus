"""Synchronous, human-absent agent tool-calling loop (JAN-192).

This is the non-streaming sibling of ``llm_service.LLMService.stream_agent_chat``.
Chat's loop is an async generator driven by ``astream_events`` and bound to a
websocket, which a background worker cannot reuse as-is; rather than writing a
second, divergent tool loop, this module drives the *same*
``create_tool_calling_agent``/``AgentExecutor`` machinery and the same prompt
scaffold synchronously.

Three things differ from chat, all because no human is watching:

* the iteration count is capped explicitly (``AGENT_TASK_MAX_ITERATIONS``)
  rather than inheriting ``AgentExecutor``'s silent library default,
* the trace of tools that actually ran is returned, so the caller can persist
  "what did this automation do" instead of only its final answer, and
* failures are *raised*, never swallowed into an in-band error string the way
  chat yields ``[Error during chat]`` -- an automation must not be able to
  report success for work that did not happen.

This module performs no authorization. ``tools`` must already be an
authorized, confirmation-gated toolset from
``RuntimeAdapter.prepare_step(...).tools``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import BaseTool

#: Hard bound on tool-calling rounds for one human-absent agent task.
AGENT_TASK_MAX_ITERATIONS = 8

#: Hard wall-clock bound for one human-absent agent task, in seconds.
#:
#: An iteration cap alone is not enough: a single round against a slow local
#: model has no inherent time limit. ``DurableTriggerWorker`` re-claims a
#: ``running`` trigger once its lease expires (``lease_seconds``, 300 by
#: default), so a run that outlives the lease would be *executed a second time
#: concurrently* -- duplicate autonomous work, plus a plan-state revision
#: conflict between the two racers. This must therefore stay comfortably below
#: that lease.
AGENT_TASK_MAX_SECONDS = 180.0

#: Upper bound on how much of one tool's output is kept in the recorded trace.
#: The trace is persisted into plan state and shown to humans and to the model,
#: so a tool returning a large payload must not bloat either surface.
AGENT_TOOL_RESULT_MAX_CHARS = 500


#: ``AgentExecutor`` does not raise when it runs out of iterations or time --
#: it *returns* one of these placeholder strings as the agent's "answer". For
#: an unattended run that placeholder would be persisted as the automation's
#: result and rendered to the user as though the work had been done, so it is
#: detected and converted into a failure instead.
_EXECUTION_LIMIT_OUTPUT_PREFIX = "Agent stopped due to"


class AgentTaskLimitError(RuntimeError):
    """Raised when an agent task hit its iteration or time ceiling unfinished."""


def _hit_execution_limit(output: str, tool_calls: tuple[AgentToolCall, ...], max_iterations: int) -> bool:
    """Return whether this run stopped at a ceiling rather than finishing.

    Two signals, because neither alone is reliable: the placeholder string is
    exact but its wording is a library detail that has changed between
    versions, while exhausting every allowed iteration is version-independent
    but would also be true of a run that genuinely finished on its last step.
    Treating the latter as a failure is the safe direction for unattended
    work -- the trigger reports an error a human can see, instead of
    persisting a possibly-truncated summary as a real answer.
    """
    return output.startswith(_EXECUTION_LIMIT_OUTPUT_PREFIX) or len(tool_calls) >= max_iterations


@dataclass(frozen=True)
class AgentToolCall:
    """One tool invocation recorded from a synchronous agent task run."""

    tool: str
    result: str


@dataclass(frozen=True)
class AgentTaskRun:
    """The outcome of one synchronous, human-absent agent task run."""

    output: str
    tool_calls: tuple[AgentToolCall, ...] = ()


def coerce_agent_output(output: object) -> str:
    """Return model output as text, tolerating content-block responses.

    Anthropic-style providers return a list of content blocks rather than a
    plain string, so a naive ``str(output)`` would persist a Python repr as the
    automation's user-visible result.
    """
    if isinstance(output, str):
        return output.strip()
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts).strip()
    return "" if output is None else str(output).strip()


def _render_observation(observation: object) -> str:
    """Render one tool's return value as truncated text for the trace.

    ``coerce_agent_output`` handles the provider's *message* shapes (a string,
    or text content blocks). A tool's observation is ordinarily neither: most
    Janus tools return a ``list[dict]`` of records with no ``"text"`` key,
    which that helper would flatten to an empty string. Falling back to
    ``str()`` keeps the trace informative instead of silently blank.
    """
    rendered = coerce_agent_output(observation)
    if not rendered and observation is not None:
        rendered = str(observation).strip()
    if len(rendered) > AGENT_TOOL_RESULT_MAX_CHARS:
        rendered = rendered[: AGENT_TOOL_RESULT_MAX_CHARS - 1].rstrip() + "…"
    return rendered


def summarize_intermediate_steps(steps: object) -> tuple[AgentToolCall, ...]:
    """Convert AgentExecutor intermediate steps into a compact, storable trace.

    Only the tool name and a truncated rendering of its observation are kept.
    Tool *arguments* are deliberately excluded: they are model-authored and may
    restate content the caller is not allowed to surface (for example a trigger
    payload's contents), whereas the tool name plus its outcome is exactly the
    "what did this automation actually do" signal JAN-192 needs.
    """
    if not isinstance(steps, list):
        return ()
    calls: list[AgentToolCall] = []
    for step in steps:
        if not isinstance(step, (tuple, list)) or len(step) != 2:
            continue
        action, observation = step
        tool_name = getattr(action, "tool", None)
        if not isinstance(tool_name, str) or not tool_name.strip():
            continue
        calls.append(AgentToolCall(tool=tool_name.strip(), result=_render_observation(observation)))
    return tuple(calls)


def run_agent_task(
    llm: BaseLanguageModel,
    system_prompt: str,
    tools: Sequence[BaseTool],
    objective: str,
    *,
    max_iterations: int = AGENT_TASK_MAX_ITERATIONS,
    max_seconds: float = AGENT_TASK_MAX_SECONDS,
) -> AgentTaskRun:
    """Run one objective to completion with a bounded synchronous tool loop.

    Raises:
        AgentTaskLimitError: when the run hit its iteration or time ceiling
            instead of finishing. ``AgentExecutor`` *returns* a placeholder
            string in that case rather than raising, which would otherwise be
            persisted and displayed as though it were a real answer.
        Exception: whatever the provider or a tool raises. The caller owns
            turning that into a recorded failure; silently returning a "failed"
            string here would let an automation report success for work that
            never happened.
    """
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            MessagesPlaceholder("history"),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )
    tool_list = list(tools)
    executor = AgentExecutor(
        agent=create_tool_calling_agent(llm, tool_list, prompt),
        tools=tool_list,
        verbose=False,
        max_iterations=max_iterations,
        max_execution_time=max_seconds,
        return_intermediate_steps=True,
        handle_parsing_errors=True,
    )
    response = executor.invoke({"input": objective, "history": [], "system_prompt": system_prompt})
    output = coerce_agent_output(response.get("output"))
    tool_calls = summarize_intermediate_steps(response.get("intermediate_steps"))
    if _hit_execution_limit(output, tool_calls, max_iterations):
        raise AgentTaskLimitError(
            f"agent task stopped at its limit after {len(tool_calls)} tool call(s) "
            f"(max_iterations={max_iterations}, max_seconds={max_seconds}) without producing an answer"
        )
    return AgentTaskRun(output=output, tool_calls=tool_calls)


__all__ = [
    "AGENT_TASK_MAX_ITERATIONS",
    "AGENT_TASK_MAX_SECONDS",
    "AGENT_TOOL_RESULT_MAX_CHARS",
    "AgentTaskLimitError",
    "AgentTaskRun",
    "AgentToolCall",
    "coerce_agent_output",
    "run_agent_task",
    "summarize_intermediate_steps",
]
