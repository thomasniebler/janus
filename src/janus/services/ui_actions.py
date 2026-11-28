"""Allowlisted page actions for the web chat (JAN-251).

Janus can act on the page the user is looking at -- navigate, set a filter,
open, highlight, or scroll to something, prefill a form -- through the
chat-only ``ui_actions`` toolbox (``agent_toolboxes/ui_actions.py``). Those
tools change nothing on the server. This module holds everything they need
that is not LangChain glue:

* the allowlists (routes, filters, item types, sections, forms) and the
  validation of one request against them and against the turn's
  ``page_context`` snapshot (JAN-250) -- :func:`validate_action`;
* :class:`UIActionBroker`, one per ``/ws/chat`` connection: it sends a
  ``__UI_ACTION__:<json>`` frame, waits for the client's
  ``{"type": "ui_action_result"}`` answer (routed to it by
  ``websocket/chat_inbox.py``), and hands the result back to the tool's
  worker thread -- the same thread/future pattern as
  :class:`janus.services.tool_approval.ToolApprovalBroker`;
* :class:`PageActionTurn`, the per-turn binding of a broker to the snapshot
  the turn's message carried, put on ``ToolContext.page_actions``.

The client checks every request again against what its current page
registered (``frontend/src/contexts/uiActions.ts``). See "Chat page actions"
in ``docs/docs/tech-spec/background-workers-runtime.md``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from janus.services.tool_call_events import preview_args

UI_ACTION_PREFIX = "__UI_ACTION__:"
#: ``type`` of the client's answer frame.
UI_ACTION_RESULT_FRAME_TYPE = "ui_action_result"

#: Tool name of each action (the frame's ``action`` is the bare verb).
TOOL_NAMES: dict[str, str] = {
    "navigate": "ui_navigate",
    "set_filter": "ui_set_filter",
    "open_item": "ui_open_item",
    "highlight": "ui_highlight",
    "scroll_to": "ui_scroll_to",
    "prefill_form": "ui_prefill_form",
}
UI_ACTION_TOOL_NAMES: frozenset[str] = frozenset(TOOL_NAMES.values())

MEMORY_TABS = ("facts", "topics", "documents", "knowledge", "wiki", "rules")
SETTINGS_TABS = (
    "system",
    "connections",
    "tools",
    "character",
    "skills",
    "runtime",
    "briefing",
    "security",
    "appearance",
    "status",
    "stats",
    "events",
    # Admin-only in the UI; Settings.tsx redirects non-admins away from it.
    "errors",
)
#: Navigation targets: path -> allowed ``tab`` values (``()`` means no query at all).
ROUTES: dict[str, tuple[str, ...]] = {
    "/": (),
    "/chat": (),
    "/briefing": (),
    "/emails": (),
    "/planner": (),
    "/docs": (),
    "/memory": MEMORY_TABS,
    "/settings": SETTINGS_TABS,
}

#: Marks a free-text filter (the value is any short string).
FREE_TEXT: tuple[str, ...] = ()
MAX_FREE_TEXT_CHARS = 80
#: ``page`` -> ``key`` -> allowed values (:data:`FREE_TEXT` for free text).
FILTERS: dict[str, dict[str, tuple[str, ...]]] = {
    "emails": {"priority": ("all", "important", "digest", "spam")},
    "topics": {
        "status": ("active", "candidate", "snoozed", "resolved", "ignored", "all"),
        "search": FREE_TEXT,
    },
    "triggers": {
        "run_status": ("all", "queued", "running", "waiting", "completed", "failed", "exhausted", "cancelled"),
        "run_kind": ("all", "manual", "scheduled", "event"),
        "show_finished": ("true", "false"),
    },
}

#: Item types ``open_item`` and ``highlight`` accept (ids must be in the snapshot).
ITEM_TYPES = ("email", "topic", "calendar_event", "trigger_definition")

#: Section ids ``scroll_to`` accepts: the Dashboard widgets and the Triggers tab's parts.
SECTIONS = (
    "briefing",
    "topic_focus",
    "important_emails",
    "whatsapp_messages",
    "schedule",
    "tasks",
    "trigger_definitions",
    "trigger_runs",
)

#: Forms ``prefill_form`` may fill (never submit), with their field names.
FORMS: dict[str, tuple[str, ...]] = {"topic.create": ("title", "summary", "marker_keywords")}
MAX_FORM_VALUE_CHARS = 1000

MAX_CLIENT_ERROR_CHARS = 300
#: How long a request waits for its call's ``start`` event to be forwarded.
START_WAIT_SECONDS = 2.0
#: Extra time the worker thread waits on top of the broker's own timeout.
_THREAD_GRACE_SECONDS = 5.0

OK = "ok"
FAILED = "failed"
TIMEOUT = "timeout"
DISCONNECTED = "disconnected"
UNAVAILABLE = "unavailable"


def call_from_worker[T](
    loop: asyncio.AbstractEventLoop, make: Callable[[], Coroutine[Any, Any, T]], wait_seconds: float
) -> T | str:
    """Run ``make()`` on a chat connection's ``loop`` from a tool's worker thread and wait for its result.

    The thread/future bridge shared by the page-action and UI-introspection
    brokers (JAN-251, JAN-284). Instead of a result it returns
    :data:`UNAVAILABLE` when called on ``loop``'s own thread (blocking there
    would deadlock), :data:`DISCONNECTED` when the loop is gone, and
    :data:`TIMEOUT` for any failure to get an answer -- never a success.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        return UNAVAILABLE
    coroutine = make()
    try:
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    except RuntimeError:  # the connection's loop is gone
        coroutine.close()
        return DISCONNECTED
    try:
        return future.result(timeout=wait_seconds)
    except Exception:  # noqa: BLE001 - any failure to get an answer is a timeout, never a success
        future.cancel()
        return TIMEOUT


class UIActionError(ValueError):
    """A page action request that is not allowed; the message says why (shown to the model)."""


@dataclass(frozen=True)
class ValidatedAction:
    """One allowed request: the frame's ``action`` and ``params`` and the chip's ``label``."""

    action: str
    params: dict[str, Any]
    label: str


def _text(value: object, name: str) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise UIActionError(f"{name} must be a string")
    text = str(value).strip()
    if not text:
        raise UIActionError(f"{name} is empty")
    return text


def _choices(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def validate_route(route: object) -> ValidatedAction:
    """Return the normalized ``navigate`` request for an allowlisted app route."""
    text = _text(route, "route")
    parts = urlsplit(text)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/"):
        raise UIActionError("route must be an app path such as /emails, without a host or fragment")
    path = parts.path.rstrip("/") or "/"
    if path not in ROUTES:
        raise UIActionError(f"unknown route {path}; known routes: {_choices(tuple(ROUTES))}")
    query = parse_qsl(parts.query, keep_blank_values=True)
    tabs = ROUTES[path]
    if not query:
        return ValidatedAction("navigate", {"route": path}, path)
    if not tabs or len(query) != 1 or query[0][0] != "tab" or query[0][1] not in tabs:
        allowed = f"tab one of {_choices(tabs)}" if tabs else "no query"
        raise UIActionError(f"route {path} takes {allowed}")
    normalized = f"{path}?tab={query[0][1]}"
    return ValidatedAction("navigate", {"route": normalized}, normalized)


def validate_filter(page: object, key: object, value: object) -> ValidatedAction:
    """Return the ``set_filter`` request for an allowlisted page filter and value."""
    page_name = _text(page, "page").lower()
    filters = FILTERS.get(page_name)
    if filters is None:
        raise UIActionError(f"no filters on page {page_name}; pages with filters: {_choices(tuple(FILTERS))}")
    key_name = _text(key, "key")
    if key_name not in filters:
        raise UIActionError(f"unknown filter {key_name} on {page_name}; filters: {_choices(tuple(filters))}")
    allowed = filters[key_name]
    if isinstance(value, bool):
        value = "true" if value else "false"
    if not allowed:  # FREE_TEXT
        if not isinstance(value, str) or len(value) > MAX_FREE_TEXT_CHARS:
            raise UIActionError(f"{key_name} must be text of at most {MAX_FREE_TEXT_CHARS} characters")
        text = value.strip()
    else:
        text = _text(value, "value").lower()
        if text not in allowed:
            raise UIActionError(f"{key_name} must be one of {_choices(allowed)}")
    params = {"page": page_name, "key": key_name, "value": text}
    return ValidatedAction("set_filter", params, f"{key_name} = {text or '(empty)'}")


def snapshot_items(snapshot: Mapping[str, object] | None) -> list[Mapping[str, object]]:
    """Return the snapshot's selection and visible items (the ids an action may name)."""
    if not snapshot:
        return []
    items: list[Mapping[str, object]] = []
    selection = snapshot.get("selection")
    if isinstance(selection, Mapping):
        items.append(selection)
    visible = snapshot.get("visible_items")
    if isinstance(visible, list):
        items.extend(item for item in visible if isinstance(item, Mapping))
    return items


def validate_item(
    action: str, snapshot: Mapping[str, object] | None, item_type: object, item_id: object
) -> ValidatedAction:
    """Return the ``open_item``/``highlight`` request for an item that is on the user's screen."""
    type_name = _text(item_type, "type")
    if type_name not in ITEM_TYPES:
        raise UIActionError(f"unknown item type {type_name}; types: {_choices(ITEM_TYPES)}")
    wanted = _text(item_id, "id")
    for item in snapshot_items(snapshot):
        if item.get("type") == type_name and str(item.get("id")) == wanted:
            raw_id = item.get("id")
            label = str(item.get("label") or f"{type_name} {wanted}")
            params = {"type": type_name, "id": raw_id if isinstance(raw_id, int) else wanted}
            return ValidatedAction(action, params, label)
    raise UIActionError(f"{type_name} {wanted} is not on the user's screen (only page-context items can be used)")


def validate_section(section: object) -> ValidatedAction:
    """Return the ``scroll_to`` request for a known section id."""
    name = _text(section, "section")
    if name not in SECTIONS:
        raise UIActionError(f"unknown section {name}; sections: {_choices(SECTIONS)}")
    return ValidatedAction("scroll_to", {"section": name}, name)


def validate_form(form_id: object, fields: object) -> ValidatedAction:
    """Return the ``prefill_form`` request for a known form and its known fields."""
    name = _text(form_id, "form_id")
    known = FORMS.get(name)
    if known is None:
        raise UIActionError(f"unknown form {name}; forms: {_choices(tuple(FORMS))}")
    if not isinstance(fields, Mapping) or not fields:
        raise UIActionError("fields must name at least one field")
    values: dict[str, str] = {}
    for key, value in fields.items():
        if key not in known:
            raise UIActionError(f"unknown field {key} of {name}; fields: {_choices(known)}")
        if not isinstance(value, str) or len(value) > MAX_FORM_VALUE_CHARS:
            raise UIActionError(f"{key} must be text of at most {MAX_FORM_VALUE_CHARS} characters")
        values[str(key)] = value
    return ValidatedAction("prefill_form", {"form_id": name, "fields": values}, name)


def validate_action(action: str, snapshot: Mapping[str, object] | None, args: Mapping[str, object]) -> ValidatedAction:
    """Validate one request of ``action`` against the allowlists and the turn's snapshot."""
    if action == "navigate":
        return validate_route(args.get("route"))
    if action == "set_filter":
        return validate_filter(args.get("page"), args.get("key"), args.get("value"))
    if action in ("open_item", "highlight"):
        return validate_item(action, snapshot, args.get("type"), args.get("id"))
    if action == "scroll_to":
        return validate_section(args.get("section"))
    if action == "prefill_form":
        return validate_form(args.get("form_id"), args.get("fields"))
    raise UIActionError(f"unknown page action {action}")


@dataclass
class UIActionRequest:
    """One ``__UI_ACTION__`` frame: a validated action for the client to perform."""

    action_id: str
    call_id: str
    action: str
    params: Any
    label: str
    timeout_seconds: float

    def to_marker(self) -> str:
        """Return the frame streamed to the chat client."""
        return UI_ACTION_PREFIX + json.dumps(asdict(self), ensure_ascii=False)


@dataclass(frozen=True)
class UIActionOutcome:
    """What became of one request (``status``: ``ok``, ``failed``, ``timeout``, ``disconnected``, ``unavailable``)."""

    status: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Return whether the client performed the action."""
        return self.status == OK


@dataclass
class _Start:
    call_id: str
    args: Any


class UIActionBroker:
    """Sends page actions to one chat connection's client and waits for its results.

    ``send`` streams a frame to the client; ``None`` means the client cannot
    perform page actions (it did not open ``/ws/chat?tool_events=1``), so
    every request answers ``unavailable`` at once. Construct it inside the
    connection's event loop.
    """

    def __init__(self, send: Callable[[str], Awaitable[None]] | None, *, timeout_seconds: float) -> None:
        """Bind the broker to the running loop and the client sender."""
        self._send = send
        self.timeout_seconds = timeout_seconds
        self._loop = asyncio.get_running_loop()
        self._pending: dict[str, asyncio.Future[UIActionOutcome]] = {}
        self._unclaimed: dict[str, list[_Start]] = {}
        self._start_signal = asyncio.Event()
        self._closed = False

    @property
    def available(self) -> bool:
        """Return whether the client opted in to page actions (a closed socket answers ``disconnected``)."""
        return self._send is not None

    def turn(self, snapshot: Mapping[str, object] | None) -> PageActionTurn:
        """Start a new chat turn: forget unclaimed starts and bind the turn's snapshot."""
        self._unclaimed.clear()
        return PageActionTurn(self, dict(snapshot) if snapshot else None)

    # -- event-loop side -------------------------------------------------

    def note_started(self, call_id: str, tool_name: str, args: Any) -> None:
        """Record that the ``start`` event of a page-action call was forwarded to the client."""
        if tool_name not in UI_ACTION_TOOL_NAMES:
            return
        self._unclaimed.setdefault(tool_name, []).append(_Start(call_id, args))
        self._start_signal.set()

    def _claim(self, tool_name: str, args: Any) -> str | None:
        starts = self._unclaimed.get(tool_name)
        if not starts:
            return None
        match = next((start for start in starts if start.args == args), starts[0])
        starts.remove(match)
        return match.call_id

    async def _call_id(self, tool_name: str, args: Any) -> str:
        deadline = self._loop.time() + START_WAIT_SECONDS
        while True:
            claimed = self._claim(tool_name, args)
            if claimed is not None:
                return claimed
            remaining = deadline - self._loop.time()
            if remaining <= 0 or self._closed:
                return str(uuid.uuid4())
            self._start_signal.clear()
            try:  # noqa: SIM105
                await asyncio.wait_for(self._start_signal.wait(), remaining)
            except TimeoutError:
                pass

    async def request(self, tool_name: str, validated: ValidatedAction, args: Any) -> UIActionOutcome:
        """Send one validated action to the client and return its outcome (never raises)."""
        if self._send is None:
            return UIActionOutcome(UNAVAILABLE)
        if self._closed:
            return UIActionOutcome(DISCONNECTED)
        call_id = await self._call_id(tool_name, args)
        params, _ = preview_args(validated.params)
        request = UIActionRequest(
            action_id=uuid.uuid4().hex,
            call_id=call_id,
            action=validated.action,
            params=params,
            label=validated.label,
            timeout_seconds=self.timeout_seconds,
        )
        future: asyncio.Future[UIActionOutcome] = self._loop.create_future()
        self._pending[request.action_id] = future
        try:
            await self._send(request.to_marker())
            return await asyncio.wait_for(future, self.timeout_seconds)
        except TimeoutError:
            return UIActionOutcome(TIMEOUT)
        finally:
            self._pending.pop(request.action_id, None)

    def handle_frame(self, raw: str) -> bool:
        """Apply a client ``ui_action_result`` frame; return whether ``raw`` was one (and so consumed).

        A result for an action that is not waiting (unknown, already
        answered, or of another connection), or without a boolean ``ok``, is
        ignored.
        """
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("type") != UI_ACTION_RESULT_FRAME_TYPE:
            return False
        ok = payload.get("ok")
        future = self._pending.get(str(payload.get("action_id")))
        if isinstance(ok, bool) and future is not None and not future.done():
            error = payload.get("error")
            detail = str(error)[:MAX_CLIENT_ERROR_CHARS] if error else None
            future.set_result(UIActionOutcome(OK) if ok else UIActionOutcome(FAILED, detail))
        return True

    def close(self) -> None:
        """Answer every waiting action as ``disconnected``; later requests are refused at once."""
        self._closed = True
        self._start_signal.set()
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(UIActionOutcome(DISCONNECTED))

    # -- worker-thread side ----------------------------------------------

    def perform(self, tool_name: str, validated: ValidatedAction, args: Any) -> UIActionOutcome:
        """Run :meth:`request` on the connection's loop from a tool's worker thread and wait for it."""
        if self._send is None:
            return UIActionOutcome(UNAVAILABLE)
        result = call_from_worker(
            self._loop,
            lambda: self.request(tool_name, validated, args),
            self.timeout_seconds + START_WAIT_SECONDS + _THREAD_GRACE_SECONDS,
        )
        return UIActionOutcome(result) if isinstance(result, str) else result


@dataclass(frozen=True)
class PageActionTurn:
    """One chat turn's page-action channel: the connection's broker and the turn's sanitized snapshot."""

    broker: UIActionBroker = field(compare=False)
    snapshot: dict[str, object] | None = None


__all__ = [
    "FILTERS",
    "FORMS",
    "ITEM_TYPES",
    "PageActionTurn",
    "ROUTES",
    "SECTIONS",
    "TOOL_NAMES",
    "UI_ACTION_PREFIX",
    "UI_ACTION_RESULT_FRAME_TYPE",
    "UI_ACTION_TOOL_NAMES",
    "UIActionBroker",
    "UIActionError",
    "UIActionOutcome",
    "UIActionRequest",
    "ValidatedAction",
    "call_from_worker",
    "snapshot_items",
    "validate_action",
]
