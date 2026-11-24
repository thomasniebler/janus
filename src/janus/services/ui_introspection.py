"""UI introspection for the web chat (JAN-282..285).

Janus can look at the page the user has open -- a bounded semantic outline
(``get_ui_snapshot``) and the computed styles of one element of it
(``get_element_styles``) -- through the chat-only ``ui_introspection``
toolbox (``agent_toolboxes/ui_introspection.py``). Nothing on the server is
read or written: the user's web client serializes its own page
(``frontend/src/lib/uiIntrospection.ts``). This module holds everything the
tools need that is not LangChain glue:

* the protocol: the ``__UI_INTROSPECT__:<json>`` request frame and the
  client's ``{"type": "ui_introspection_result"}`` answer;
* :func:`sanitize_snapshot` and :func:`sanitize_styles`, which re-apply every
  budget server-side without trusting the client: known keys, roles and
  style properties only, cut strings, clamped numbers, at most
  :data:`MAX_SNAPSHOT_NODES` nodes and :data:`MAX_SNAPSHOT_BYTES` bytes;
* :class:`UIIntrospectionBroker`, one per ``/ws/chat`` connection: it sends a
  request frame, waits for the answer (routed to it by
  ``websocket/chat_inbox.py``), and hands the sanitized result back to the
  tool's worker thread -- the thread/future pattern of
  :class:`janus.services.ui_actions.UIActionBroker`, sharing its
  :func:`~janus.services.ui_actions.call_from_worker` bridge. It also
  remembers the refs of the connection's latest snapshot, so
  ``get_element_styles`` only asks about a ref that snapshot named.

The outline shows text other people wrote (mail subjects, topic titles), so
it is untrusted: the registry entry is ``reads_untrusted_content=True`` and
the runtime adapter spotlights, scans, and records every answer (JAN-127).
See "Chat UI introspection" in
``docs/docs/tech-spec/background-workers-runtime.md``.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

from janus.services.page_context import clean_text
from janus.services.ui_actions import (
    DISCONNECTED,
    FAILED,
    MAX_CLIENT_ERROR_CHARS,
    OK,
    TIMEOUT,
    UNAVAILABLE,
    call_from_worker,
)

UI_INTROSPECTION_PREFIX = "__UI_INTROSPECT__:"
#: ``type`` of the client's answer frame.
UI_INTROSPECTION_RESULT_FRAME_TYPE = "ui_introspection_result"

#: Frame ``kind`` of each request.
KIND_SNAPSHOT = "snapshot"
KIND_ELEMENT_STYLES = "element_styles"
KINDS = (KIND_SNAPSHOT, KIND_ELEMENT_STYLES)

#: An answer frame larger than this (characters of raw JSON) fails its request unread.
MAX_RESULT_FRAME_CHARS = 256 * 1024
#: Hard budget for a sanitized snapshot: UTF-8 bytes of its compact JSON.
MAX_SNAPSHOT_BYTES = 24 * 1024
#: Nodes read from a snapshot (the client caps at the same number).
MAX_SNAPSHOT_NODES = 200
MAX_NAME_CHARS = 120
MAX_TEXT_CHARS = 200
MAX_TITLE_CHARS = 120
MAX_ROUTE_CHARS = 200
MAX_HREF_CHARS = 200
MAX_INPUT_TYPE_CHARS = 20
MAX_STYLE_VALUE_CHARS = 120
MAX_DEPTH = 20
MAX_VIEWPORT_PIXELS = 100_000
MAX_COUNTER = 100_000

#: Node roles a snapshot may contain; a node with any other role is dropped.
LANDMARK_ROLES = (
    "banner",
    "navigation",
    "main",
    "complementary",
    "contentinfo",
    "region",
    "form",
    "search",
    "dialog",
    "alertdialog",
)
CONTROL_ROLES = (
    "textbox",
    "searchbox",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "switch",
    "slider",
    "spinbutton",
)
ROLES: frozenset[str] = frozenset(
    (*LANDMARK_ROLES, *CONTROL_ROLES, "alert", "status", "heading", "button", "link", "tab", "tablist", "img", "text")
)
COLOR_SCHEMES = ("light", "dark")
UI_SHELLS = ("v1", "v2", "v3")

#: Computed style properties ``get_element_styles`` may return; mirrored in the frontend.
_STYLE_PROPERTY_NAMES = (
    "display position visibility opacity z-index overflow overflow-x overflow-y box-sizing width height "
    "min-width min-height max-width max-height margin padding top right bottom left flex-direction "
    "flex-wrap justify-content align-items grid-template-columns gap color background-color border "
    "border-radius outline font-family font-size font-weight line-height text-align text-overflow "
    "white-space cursor pointer-events transform"
)
STYLE_PROPERTIES: tuple[str, ...] = tuple(_STYLE_PROPERTY_NAMES.split())

_REF = re.compile(r"^e[0-9]{1,5}$")
_TAG = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_INPUT_TYPE = re.compile(r"^[a-z-]{1,20}$")
_BOOLEAN_FLAGS = ("disabled", "required", "invalid", "offscreen")


class UIIntrospectionError(ValueError):
    """A request or answer that is not allowed; the message says why (shown to the model)."""


def is_ref(value: object) -> bool:
    """Return whether ``value`` has the shape of a snapshot ref (``e<n>``)."""
    return isinstance(value, str) and bool(_REF.match(value))


def _int(value: object, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return max(low, min(high, int(round(value))))


def _number(value: object, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(max(low, min(high, number)), 2)


def _short(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = clean_text(value, limit)
    return text or None


def _href(value: object) -> str | None:
    """Return a link target without query or fragment; an absolute URL keeps only its origin."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme or parts.netloc:
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        suffix = f":{port}" if port else ""
        return clean_text(f"{parts.scheme}://{parts.hostname}{suffix}", MAX_HREF_CHARS)
    if not parts.path.startswith("/"):
        return None
    return clean_text(parts.path, MAX_HREF_CHARS) or None


def _node(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    ref = value.get("ref")
    role = value.get("role")
    if not is_ref(ref) or not isinstance(role, str) or role not in ROLES:
        return None
    node: dict[str, object] = {"ref": ref, "role": role, "depth": _int(value.get("depth"), 0, MAX_DEPTH) or 0}
    name = _short(value.get("name"), MAX_NAME_CHARS)
    if name:
        node["name"] = name
    text = _short(value.get("text"), MAX_TEXT_CHARS)
    if text:
        node["text"] = text
    if role == "heading":
        level = _int(value.get("level"), 1, 6)
        if level is not None:
            node["level"] = level
    if role == "link":
        href = _href(value.get("href"))
        if href:
            node["href"] = href
    if role in CONTROL_ROLES:
        input_type = value.get("input_type")
        if isinstance(input_type, str) and _INPUT_TYPE.match(input_type.lower()):
            if input_type.lower() == "password":
                return None  # the client never sends one; a hostile client's is dropped
            node["input_type"] = input_type.lower()
    for flag in _BOOLEAN_FLAGS:
        if value.get(flag) is True:
            node[flag] = True
    if role == "text" and "text" not in node:
        return None
    return node


def _viewport(value: object) -> dict[str, object]:
    raw = value if isinstance(value, Mapping) else {}
    viewport: dict[str, object] = {}
    for key in ("width", "height"):
        number = _int(raw.get(key), 0, MAX_VIEWPORT_PIXELS)
        if number is not None:
            viewport[key] = number
    for key in ("scroll_x", "scroll_y"):
        number = _int(raw.get(key), -MAX_VIEWPORT_PIXELS * 10, MAX_VIEWPORT_PIXELS * 10)
        if number is not None:
            viewport[key] = number
    ratio = _number(raw.get("device_pixel_ratio"), 0.1, 10)
    if ratio is not None:
        viewport["device_pixel_ratio"] = ratio
    if raw.get("color_scheme") in COLOR_SCHEMES:
        viewport["color_scheme"] = raw["color_scheme"]
    if raw.get("ui_shell") in UI_SHELLS:
        viewport["ui_shell"] = raw["ui_shell"]
    return viewport


def _encoded_size(value: Mapping[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def fit_snapshot(snapshot: dict[str, object], max_bytes: int = MAX_SNAPSHOT_BYTES) -> dict[str, object]:
    """Drop nodes from the end (counted in ``omitted_nodes``) until the snapshot's JSON fits ``max_bytes``."""
    fitted = dict(snapshot)
    raw_nodes = fitted.get("nodes")
    nodes: list[object] = list(raw_nodes) if isinstance(raw_nodes, list) else []
    raw_omitted = fitted.get("omitted_nodes")
    omitted = raw_omitted if isinstance(raw_omitted, int) else 0
    if _encoded_size(fitted) <= max_bytes:
        return fitted
    # Bisect on the node count instead of re-encoding after every pop.
    low, high = 0, len(nodes)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = {**fitted, "nodes": nodes[:middle], "omitted_nodes": omitted + len(nodes) - middle}
        if _encoded_size(candidate) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    fitted["nodes"] = nodes[:low]
    fitted["omitted_nodes"] = omitted + len(nodes) - low
    return fitted


def sanitize_snapshot(value: object) -> dict[str, object]:
    """Return the client's outline with only its own keys, cut to size and within the byte budget.

    Raises :class:`UIIntrospectionError` for anything that is not a snapshot.
    """
    if not isinstance(value, Mapping):
        raise UIIntrospectionError("the page's answer is not an outline")
    raw_nodes = value.get("nodes")
    if not isinstance(raw_nodes, list):
        raise UIIntrospectionError("the page's answer has no nodes")
    snapshot: dict[str, object] = {}
    route = _short(value.get("route"), MAX_ROUTE_CHARS)
    if route:
        snapshot["route"] = route
    title = _short(value.get("title"), MAX_TITLE_CHARS)
    if title:
        snapshot["title"] = title
    snapshot["viewport"] = _viewport(value.get("viewport"))
    nodes: list[dict[str, object]] = []
    seen: set[object] = set()
    for raw in raw_nodes[:MAX_SNAPSHOT_NODES]:
        node = _node(raw)
        if node is None or node["ref"] in seen:
            continue
        seen.add(node["ref"])
        nodes.append(node)
    snapshot["nodes"] = nodes
    omitted = len(raw_nodes) - len(raw_nodes[:MAX_SNAPSHOT_NODES])
    client_omitted = _int(value.get("omitted_nodes"), 0, MAX_COUNTER)
    omitted += client_omitted or 0
    if omitted:
        snapshot["omitted_nodes"] = omitted
    skipped_private = _int(value.get("skipped_private"), 0, MAX_COUNTER)
    if skipped_private:
        snapshot["skipped_private"] = skipped_private
    return fit_snapshot(snapshot)


def snapshot_refs(snapshot: Mapping[str, object]) -> frozenset[str]:
    """Return the refs a sanitized snapshot names."""
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return frozenset()
    return frozenset(str(node["ref"]) for node in nodes if isinstance(node, Mapping) and is_ref(node.get("ref")))


def sanitize_styles(value: object, ref: str) -> dict[str, object]:
    """Return the client's styles answer for ``ref`` with allowlisted properties only.

    Raises :class:`UIIntrospectionError` for anything that is not a styles
    answer for that ref.
    """
    if not isinstance(value, Mapping) or value.get("ref") != ref:
        raise UIIntrospectionError(f"the page's answer is not about {ref}")
    result: dict[str, object] = {"ref": ref}
    tag = value.get("tag")
    if isinstance(tag, str) and _TAG.match(tag.lower()):
        result["tag"] = tag.lower()
    raw_box = value.get("box")
    if isinstance(raw_box, Mapping):
        box = {
            key: number
            for key in ("x", "y", "width", "height")
            if (number := _number(raw_box.get(key), -MAX_VIEWPORT_PIXELS * 10, MAX_VIEWPORT_PIXELS * 10)) is not None
        }
        if box:
            result["box"] = box
    raw_styles = value.get("styles")
    styles: dict[str, str] = {}
    if isinstance(raw_styles, Mapping):
        for prop in STYLE_PROPERTIES:
            raw = raw_styles.get(prop)
            if not isinstance(raw, str) or "url(" in raw.lower():
                continue
            cleaned = clean_text(raw, MAX_STYLE_VALUE_CHARS)
            if cleaned:
                styles[prop] = cleaned
    result["styles"] = styles
    return result


@dataclass
class UIIntrospectionRequest:
    """One ``__UI_INTROSPECT__`` frame: a read-only request for the client's page."""

    request_id: str
    kind: str
    params: dict[str, str]
    timeout_seconds: float

    def to_marker(self) -> str:
        """Return the frame streamed to the chat client."""
        return UI_INTROSPECTION_PREFIX + json.dumps(asdict(self), ensure_ascii=False)


@dataclass(frozen=True)
class UIIntrospectionOutcome:
    """What became of one request (``status``: ``ok``, ``failed``, ``timeout``, ``disconnected``, ``unavailable``).

    ``result`` is the client's raw answer (sanitized by the broker before the
    tool sees it) when ``status`` is ``ok``.
    """

    status: str
    result: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Return whether the client answered."""
        return self.status == OK


#: Extra time the worker thread waits on top of the broker's own timeout.
_THREAD_GRACE_SECONDS = 5.0


class UIIntrospectionBroker:
    """Asks one chat connection's client about its page and waits for the answer.

    ``send`` streams a frame to the client; ``None`` means the client cannot
    answer (it did not open ``/ws/chat?tool_events=1``: the iOS scaffold, the
    CLI), so every request answers ``unavailable`` at once. Construct it
    inside the connection's event loop.
    """

    def __init__(self, send: Callable[[str], Awaitable[None]] | None, *, timeout_seconds: float) -> None:
        """Bind the broker to the running loop and the client sender."""
        self._send = send
        self.timeout_seconds = timeout_seconds
        self._loop = asyncio.get_running_loop()
        self._pending: dict[str, asyncio.Future[UIIntrospectionOutcome]] = {}
        self._closed = False
        #: Refs of this connection's latest sanitized snapshot (set from the worker thread).
        self.latest_refs: frozenset[str] = frozenset()

    @property
    def available(self) -> bool:
        """Return whether the client can answer introspection requests."""
        return self._send is not None

    # -- event-loop side -------------------------------------------------

    async def request(self, kind: str, params: dict[str, str]) -> UIIntrospectionOutcome:
        """Send one request to the client and return its outcome (never raises)."""
        if self._send is None:
            return UIIntrospectionOutcome(UNAVAILABLE)
        if self._closed:
            return UIIntrospectionOutcome(DISCONNECTED)
        request = UIIntrospectionRequest(
            request_id=uuid.uuid4().hex, kind=kind, params=dict(params), timeout_seconds=self.timeout_seconds
        )
        future: asyncio.Future[UIIntrospectionOutcome] = self._loop.create_future()
        self._pending[request.request_id] = future
        try:
            await self._send(request.to_marker())
            return await asyncio.wait_for(future, self.timeout_seconds)
        except TimeoutError:
            return UIIntrospectionOutcome(TIMEOUT)
        finally:
            self._pending.pop(request.request_id, None)

    def handle_frame(self, raw: str) -> bool:
        """Apply a client ``ui_introspection_result`` frame; return whether ``raw`` was one (and so consumed).

        A result for a request that is not waiting (unknown, already answered,
        or of another connection), or without a boolean ``ok``, is ignored. A
        frame larger than :data:`MAX_RESULT_FRAME_CHARS` fails its request.
        """
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict) or payload.get("type") != UI_INTROSPECTION_RESULT_FRAME_TYPE:
            return False
        ok = payload.get("ok")
        future = self._pending.get(str(payload.get("request_id")))
        if not isinstance(ok, bool) or future is None or future.done():
            return True
        if len(raw) > MAX_RESULT_FRAME_CHARS:
            future.set_result(UIIntrospectionOutcome(FAILED, error="the answer was too large"))
        elif ok:
            future.set_result(UIIntrospectionOutcome(OK, result=payload.get("result")))
        else:
            error = payload.get("error")
            detail = str(error)[:MAX_CLIENT_ERROR_CHARS] if error else None
            future.set_result(UIIntrospectionOutcome(FAILED, error=detail))
        return True

    def close(self) -> None:
        """Answer every waiting request as ``disconnected``; later requests are refused at once."""
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result(UIIntrospectionOutcome(DISCONNECTED))

    # -- worker-thread side ----------------------------------------------

    def ask(self, kind: str, params: dict[str, str] | None = None) -> UIIntrospectionOutcome:
        """Run :meth:`request` on the connection's loop from a tool's worker thread and wait for it."""
        if self._send is None:
            return UIIntrospectionOutcome(UNAVAILABLE)
        result = call_from_worker(
            self._loop, lambda: self.request(kind, params or {}), self.timeout_seconds + _THREAD_GRACE_SECONDS
        )
        return UIIntrospectionOutcome(result) if isinstance(result, str) else result


__all__ = [
    "KINDS",
    "KIND_ELEMENT_STYLES",
    "KIND_SNAPSHOT",
    "MAX_RESULT_FRAME_CHARS",
    "MAX_SNAPSHOT_BYTES",
    "MAX_SNAPSHOT_NODES",
    "ROLES",
    "STYLE_PROPERTIES",
    "UI_INTROSPECTION_PREFIX",
    "UI_INTROSPECTION_RESULT_FRAME_TYPE",
    "UIIntrospectionBroker",
    "UIIntrospectionError",
    "UIIntrospectionOutcome",
    "UIIntrospectionRequest",
    "fit_snapshot",
    "is_ref",
    "sanitize_snapshot",
    "sanitize_styles",
    "snapshot_refs",
]
