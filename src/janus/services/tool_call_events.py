"""Structured, redacted chat tool-call events (JAN-249).

``llm_service.stream_agent_chat`` turns LangChain's ``on_tool_*`` stream events
into ``__TOOL_EVENT__:<json>`` frames through :class:`ToolEventTracker`;
``websocket/chat.py`` forwards them to clients that asked for them and stores
the final state of each call through :class:`ToolCallRecorder` as
``conversation_tool_calls`` rows of the assistant message.

Everything a frame carries is safe to show and to store:

* ``args`` are the model's tool arguments with credential-like fields replaced
  by ``[redacted]`` (:func:`redact`), long strings shortened, and the whole
  JSON capped at :data:`ARGS_MAX_CHARS` (then sent as a truncated JSON string).
* ``result_preview`` is the tool's answer as the model saw it, as plain text,
  capped at :data:`RESULT_PREVIEW_MAX_CHARS`. It is untrusted content (mail,
  web pages, MCP servers): clients show it as monospace text and never render
  or interpret it.

A tool the runtime refused (``BLOCKED: <tool> was not run because ...``, see
``runtime_adapter._refusal_stub`` and the prompt-injection escalation) ends in
phase ``blocked``; one that failed (``ERROR: <tool> failed: ...`` from the
chat tool guard, or a raised error) ends in phase ``error``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from janus import models

TOOL_EVENT_PREFIX = "__TOOL_EVENT__:"
#: Deprecated one-word markers (JAN-249): still streamed for one release so the
#: iOS scaffold keeps working, never part of the stored reply.
LEGACY_TOOL_MARKER_PREFIXES = ("__TOOL_CALL__:", "__TOOL_RESULT__:")

PHASE_START = "start"
PHASE_END = "end"
PHASE_ERROR = "error"
PHASE_BLOCKED = "blocked"
TERMINAL_PHASES = frozenset({PHASE_END, PHASE_ERROR, PHASE_BLOCKED})
#: LangChain stream event -> :class:`ToolEventTracker` method.
TOOL_EVENT_HANDLERS = {"on_tool_start": "start", "on_tool_end": "end", "on_tool_error": "error"}

RESULT_PREVIEW_MAX_CHARS = 4096
ARGS_MAX_CHARS = 4096
ARG_STRING_MAX_CHARS = 1024
MAX_ARG_DEPTH = 6
REDACTED = "[redacted]"
TRUNCATION_SUFFIX = "…"

# Matched against the key lowercased with everything but letters and digits
# removed, so ``api_key``, ``API-Key`` and ``apiKey`` all hit ``apikey``.
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "authorization",
    "cookie",
    "credential",
    "sessionid",
    "bearer",
)
_SECRET_EXACT_KEYS = frozenset({"auth", "pwd", "otp", "pin"})
# A whole value that is an HTTP auth header ("Bearer <token>"), not prose that
# merely starts with the word.
_SECRET_VALUE_PATTERN = re.compile(r"^\s*(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}\s*$", re.IGNORECASE)


def is_secret_key(key: object) -> bool:
    """Return whether an argument name looks like it holds a credential."""
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return normalized in _SECRET_EXACT_KEYS or any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Return ``text`` cut to at most ``limit`` characters, and whether it was cut."""
    if len(text) <= limit:
        return text, False
    return text[: max(limit - len(TRUNCATION_SUFFIX), 0)] + TRUNCATION_SUFFIX, True


def _redact(value: Any, depth: int) -> tuple[Any, bool]:
    """Return the redacted, JSON-safe copy of ``value`` and whether nesting past the depth cap was cut."""
    if depth > MAX_ARG_DEPTH:
        return TRUNCATION_SUFFIX, True
    if isinstance(value, Mapping):
        redacted, cut = {}, False
        for key, item in value.items():
            if is_secret_key(key):
                redacted[str(key)] = REDACTED
                continue
            redacted[str(key)], item_cut = _redact(item, depth + 1)
            cut = cut or item_cut
        return redacted, cut
    if isinstance(value, (list, tuple, set, frozenset)):
        pairs = [_redact(item, depth + 1) for item in value]
        return [item for item, _ in pairs], any(item_cut for _, item_cut in pairs)
    if isinstance(value, str):
        return (REDACTED if _SECRET_VALUE_PATTERN.match(value) else value), False
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    return str(value), False


def redact(value: Any) -> Any:
    """Return a JSON-safe copy of ``value`` with credential-like fields replaced by ``[redacted]``.

    Nesting deeper than :data:`MAX_ARG_DEPTH` levels is replaced by ``…``.
    """
    return _redact(value, 0)[0]


def _shorten_strings(value: Any) -> tuple[Any, bool]:
    if isinstance(value, dict):
        shortened, cut = {}, False
        for key, item in value.items():
            shortened[key], item_cut = _shorten_strings(item)
            cut = cut or item_cut
        return shortened, cut
    if isinstance(value, list):
        pairs = [_shorten_strings(item) for item in value]
        return [item for item, _ in pairs], any(item_cut for _, item_cut in pairs)
    if isinstance(value, str):
        return truncate_text(value, ARG_STRING_MAX_CHARS)
    return value, False


def preview_args(args: Any) -> tuple[Any, bool]:
    """Return the redacted, size-capped arguments to show, and whether anything was cut.

    Small arguments stay a JSON object. When even the shortened object is
    larger than :data:`ARGS_MAX_CHARS`, the result is its (cut) JSON text.
    """
    redacted, depth_cut = _redact(args, 0)
    shortened, string_cut = _shorten_strings(redacted)
    cut = depth_cut or string_cut
    rendered = json.dumps(shortened, ensure_ascii=False)
    if len(rendered) <= ARGS_MAX_CHARS:
        return shortened, cut
    text, _ = truncate_text(rendered, ARGS_MAX_CHARS)
    return text, True


def render_output(output: Any) -> str:
    """Return a tool's answer as plain text (a ``ToolMessage``'s content, a string, or JSON)."""
    content = getattr(output, "content", output)
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def classify_result(tool_name: str, text: str) -> str:
    """Return ``blocked``/``error``/``end`` for a finished call from its answer to the model.

    Only the runtime's own refusal and failure shapes count (they name the
    tool), so a tool merely *returning* text that starts with "BLOCKED" does
    not mark the call as refused.
    """
    stripped = text.lstrip()
    if stripped.startswith(f"BLOCKED: {tool_name} was not run"):
        return PHASE_BLOCKED
    if stripped.startswith(f"ERROR: {tool_name} failed"):
        return PHASE_ERROR
    return PHASE_END


@dataclass
class ToolCallEvent:
    """One ``__TOOL_EVENT__`` frame: the state of one tool call at one phase."""

    call_id: str
    tool_id: str | None
    tool_name: str
    phase: str
    args: Any = None
    args_truncated: bool = False
    result_preview: str | None = None
    result_truncated: bool = False
    duration_ms: int | None = None
    #: The user's chat approval decision (JAN-254, ``services/tool_approval.py``)
    #: for a confirmation-required call; ``None`` when none was asked.
    approval: str | None = None
    #: The turn step the call ran in (JAN-316, ``services/plan_step_events.py``);
    #: live only, never stored.
    step_id: str | None = None

    def to_marker(self) -> str:
        """Return the frame streamed to chat clients."""
        return TOOL_EVENT_PREFIX + json.dumps(asdict(self), ensure_ascii=False)


def parse_marker(token: str) -> ToolCallEvent | None:
    """Return the event a ``__TOOL_EVENT__`` frame carries, or ``None`` for anything else."""
    if not token.startswith(TOOL_EVENT_PREFIX):
        return None
    try:
        payload = json.loads(token[len(TOOL_EVENT_PREFIX) :])
        return ToolCallEvent(**payload)
    except (TypeError, ValueError):
        return None


def is_tool_protocol_frame(token: str) -> bool:
    """Return whether a streamed token is tool lifecycle metadata rather than reply text."""
    return token.startswith(TOOL_EVENT_PREFIX) or token.startswith(LEGACY_TOOL_MARKER_PREFIXES)


def _tool_input(raw: Any) -> Any:
    """Return the call's arguments, unwrapping a LangChain ``ToolCall`` dict when the tool got one."""
    if isinstance(raw, Mapping) and raw.get("type") == "tool_call" and "args" in raw:
        return raw["args"]
    return raw


@dataclass
class _OpenCall:
    started: float
    args: Any
    args_truncated: bool
    step_id: str | None = None


class ToolEventTracker:
    """Turns LangChain ``astream_events`` v2 tool events into :class:`ToolCallEvent` frames.

    ``tool_id`` stays ``None`` here: the LLM layer does not know the tool
    registry; :class:`ToolCallRecorder` fills it in for the chat surface.

    Only the outermost run of a call is reported: the runtime wraps each tool
    (``_guarded`` -> ``_traced`` -> the tool), and each wrapper's nested
    ``invoke`` emits its own ``on_tool_*`` events with the outer run in
    ``parent_ids``.

    ``step_id`` returns the turn step a starting call belongs to (JAN-316,
    ``plan_step_events.TurnEventStream``); every frame of the call carries it.
    """

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, step_id: Callable[[], str | None] | None = None
    ) -> None:
        """Track calls, timing them with ``clock`` (seconds)."""
        self._clock = clock
        self._step_id = step_id
        self._open: dict[str, _OpenCall] = {}

    @property
    def has_open_calls(self) -> bool:
        """Return whether any reported call has started and not finished."""
        return bool(self._open)

    def is_nested(self, event: Mapping[str, Any]) -> bool:
        """Return whether ``event`` belongs to a wrapper run inside an open call."""
        return any(str(parent) in self._open for parent in event.get("parent_ids") or ())

    def _base(self, event: Mapping[str, Any], phase: str) -> ToolCallEvent:
        name = str(event.get("name") or "")
        return ToolCallEvent(call_id=str(event.get("run_id") or ""), tool_id=None, tool_name=name, phase=phase)

    def start(self, event: Mapping[str, Any]) -> str | None:
        """Return the ``start`` frame for an ``on_tool_start`` event (``None`` for a nested run)."""
        if self.is_nested(event):
            return None
        frame = self._base(event, PHASE_START)
        frame.args, frame.args_truncated = preview_args(_tool_input((event.get("data") or {}).get("input")))
        frame.step_id = self._step_id() if self._step_id is not None else None
        self._open[frame.call_id] = _OpenCall(self._clock(), frame.args, frame.args_truncated, frame.step_id)
        return frame.to_marker()

    def _finish(self, event: Mapping[str, Any], text: str, phase: str | None) -> str | None:
        call_id = str(event.get("run_id") or "")
        opened = self._open.pop(call_id, None)
        if opened is None and self.is_nested(event):
            return None
        frame = self._base(event, phase or classify_result(str(event.get("name") or ""), text))
        if opened is not None:
            frame.args, frame.args_truncated = opened.args, opened.args_truncated
            frame.step_id = opened.step_id
            frame.duration_ms = max(int((self._clock() - opened.started) * 1000), 0)
        frame.result_preview, frame.result_truncated = truncate_text(text, RESULT_PREVIEW_MAX_CHARS)
        return frame.to_marker()

    def end(self, event: Mapping[str, Any]) -> str | None:
        """Return the ``end``/``blocked``/``error`` frame for an ``on_tool_end`` event."""
        return self._finish(event, render_output((event.get("data") or {}).get("output")), None)

    def error(self, event: Mapping[str, Any]) -> str | None:
        """Return the ``error`` frame for an ``on_tool_error`` event."""
        error = (event.get("data") or {}).get("error")
        return self._finish(event, str(error) if error is not None else "", PHASE_ERROR)


class ToolCallRecorder:
    """Names each streamed tool call's capability and keeps its final state for persistence.

    ``tool_ids`` maps tool names to registry entry ids (``PreparedStep.tool_ids_by_name``).
    ``approvals`` is a live view of the turn's chat approval decisions by
    ``call_id`` (``ToolApprovalBroker.decisions``, JAN-254); a call's frames
    carry its decision once one was made.
    """

    def __init__(self, tool_ids: Mapping[str, str] | None = None, approvals: Mapping[str, str] | None = None) -> None:
        """Start an empty record for one assistant message."""
        self._tool_ids = dict(tool_ids or {})
        self._approvals: Mapping[str, str] = approvals if approvals is not None else {}
        self._calls: dict[str, ToolCallEvent] = {}

    def observe(self, token: str) -> str | None:
        """Record a ``__TOOL_EVENT__`` frame and return it with ``tool_id`` set; ``None`` for anything else."""
        event = parse_marker(token)
        if event is None or not event.call_id:
            return None
        event.tool_id = event.tool_id or self._tool_ids.get(event.tool_name)
        event.approval = event.approval or self._approvals.get(event.call_id)
        self._calls[event.call_id] = event
        return event.to_marker()

    def events(self) -> list[ToolCallEvent]:
        """Return the latest event of every call, in the order the calls started."""
        for event in self._calls.values():
            event.approval = event.approval or self._approvals.get(event.call_id)
        return list(self._calls.values())

    def attach_to(self, message: models.ConversationMessage) -> None:
        """Add one ``ConversationToolCall`` row per recorded call to ``message``."""
        for position, event in enumerate(self.events()):
            message.tool_calls.append(
                models.ConversationToolCall(
                    position=position,
                    call_id=event.call_id,
                    tool_id=event.tool_id,
                    tool_name=event.tool_name,
                    phase=event.phase,
                    args=event.args,
                    args_truncated=event.args_truncated,
                    result_preview=event.result_preview,
                    result_truncated=event.result_truncated,
                    duration_ms=event.duration_ms,
                    approval=event.approval,
                )
            )


__all__ = [
    "ARGS_MAX_CHARS",
    "LEGACY_TOOL_MARKER_PREFIXES",
    "RESULT_PREVIEW_MAX_CHARS",
    "REDACTED",
    "TOOL_EVENT_HANDLERS",
    "TOOL_EVENT_PREFIX",
    "ToolCallEvent",
    "ToolCallRecorder",
    "ToolEventTracker",
    "classify_result",
    "is_secret_key",
    "is_tool_protocol_frame",
    "parse_marker",
    "preview_args",
    "redact",
    "render_output",
    "truncate_text",
]
