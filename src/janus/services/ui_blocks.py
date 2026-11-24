"""Validated generative-UI blocks for chat replies (JAN-252).

Janus can answer with structured UI as well as Markdown through the chat-only
``render_ui`` tool (``agent_toolboxes/ui_blocks.py``). This module holds
everything that tool needs that is not LangChain glue:

* the fixed block catalog (:data:`UI_BLOCK_SCHEMA_VERSION`), one pydantic
  model per block type with its size budgets, and :func:`validate_block`,
  which turns the model's block into the wire/storage shape (``id``,
  ``version``, ``type`` plus the type's fields) or raises
  :class:`UIBlockError` with the validation errors for the model;
* :class:`UIBlockTurn`, one per chat turn: it collects the turn's validated
  blocks (from the tool's worker thread), hands their ``__UI_BLOCK__`` frames
  to ``websocket/chat.py`` once (:meth:`UIBlockTurn.drain_frames`), and
  attaches every block to the assistant message as
  ``conversation_ui_blocks`` rows (:meth:`UIBlockTurn.attach_to`).

The web client validates every block again with the same catalog in zod
(``frontend/src/lib/uiBlocks.ts``); keep both in sync. See "Chat UI blocks"
in ``docs/docs/tech-spec/background-workers-runtime.md``.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from janus import models

UI_BLOCK_PREFIX = "__UI_BLOCK__:"
#: Catalog version stamped on every block; bump it when a block's shape changes.
UI_BLOCK_SCHEMA_VERSION = 1
RENDER_UI_TOOL_NAME = "render_ui"

#: Budget of one block's UTF-8 JSON (as streamed and stored).
UI_BLOCK_MAX_BYTES = 8192
MAX_UI_BLOCKS_PER_MESSAGE = 8

MAX_TITLE_CHARS = 120
MAX_TABLE_COLUMNS = 8
MAX_TABLE_ROWS = 50
MAX_CHART_POINTS = 50
MAX_CHART_SERIES = 5
MAX_CHECKLIST_ITEMS = 50
MAX_KEY_VALUE_ITEMS = 30
MAX_ACTION_BUTTONS = 6
MAX_CARD_FIELDS = 12
MAX_CARD_LINKS = 4

BLOCK_TYPES = ("card", "table", "chart", "checklist", "key_value", "actions", "image")
#: The only image source a block may name: an authenticated, same-origin image (JAN-253).
IMAGE_URL_PATTERN = re.compile(r"^/api/images/[A-Za-z0-9_-]{1,64}$")
_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"
_MAX_ERRORS_SHOWN = 6


class UIBlockError(ValueError):
    """A block the catalog does not accept; the message says why (shown to the model)."""


def is_allowed_href(href: str) -> bool:
    """Return whether ``href`` is a same-origin path or an ``https://`` URL with a host."""
    if not href or "\\" in href or any(char.isspace() or ord(char) < 32 for char in href):
        return False
    if href.startswith("/"):
        return not href.startswith("//")
    parts = urlsplit(href)
    return parts.scheme == "https" and bool(parts.hostname)


def is_external_href(href: str) -> bool:
    """Return whether an allowed ``href`` leaves Janus (an ``https://`` URL)."""
    return not href.startswith("/")


Text = Annotated[str, Field(max_length=300)]
Cell = Annotated[str, Field(max_length=300)] | int | float | StrictBool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Link(_Strict):
    """A link: a same-origin path or an ``https://`` URL."""

    label: str = Field(min_length=1, max_length=80)
    href: str = Field(min_length=1, max_length=500)

    @field_validator("href")
    @classmethod
    def _allowed(cls, value: str) -> str:
        if not is_allowed_href(value):
            raise ValueError("href must be a same-origin path (/...) or an https:// URL")
        return value


class _Block(_Strict):
    title: str | None = Field(default=None, max_length=MAX_TITLE_CHARS)


class CardField(_Strict):
    """One ``label: value`` line of a card."""

    label: str = Field(min_length=1, max_length=60)
    value: Text


class CardBlock(_Block):
    """A summary card: title, optional subtitle, body text, fields, and links."""

    type: Literal["card"]
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    subtitle: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    fields: list[CardField] | None = Field(default=None, max_length=MAX_CARD_FIELDS)
    links: list[Link] | None = Field(default=None, max_length=MAX_CARD_LINKS)


class TableBlock(_Block):
    """A small table: column headers and rows of plain cells."""

    type: Literal["table"]
    columns: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(
        min_length=1, max_length=MAX_TABLE_COLUMNS
    )
    rows: list[list[Cell]] = Field(max_length=MAX_TABLE_ROWS)

    @model_validator(mode="after")
    def _rectangular(self) -> TableBlock:
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"row {index} has {len(row)} cells but there are {len(self.columns)} columns")
        return self


class ChartSeries(_Strict):
    """One named series of a chart: one value per label."""

    name: str = Field(min_length=1, max_length=60)
    values: list[float] = Field(min_length=1, max_length=MAX_CHART_POINTS)


class ChartBlock(_Block):
    """A small bar, line, or pie chart."""

    type: Literal["chart"]
    kind: Literal["bar", "line", "pie"]
    labels: list[Annotated[str, Field(max_length=40)]] = Field(min_length=1, max_length=MAX_CHART_POINTS)
    series: list[ChartSeries] = Field(min_length=1, max_length=MAX_CHART_SERIES)
    unit: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _consistent(self) -> ChartBlock:
        for series in self.series:
            if len(series.values) != len(self.labels):
                raise ValueError(
                    f"series {series.name!r} has {len(series.values)} values but there are {len(self.labels)} labels"
                )
        if self.kind == "pie":
            if len(self.series) != 1:
                raise ValueError("a pie chart has exactly one series")
            values = self.series[0].values
            if any(value < 0 for value in values) or sum(values) <= 0:
                raise ValueError("a pie chart needs non-negative values with a positive sum")
        return self


class ChecklistItem(_Strict):
    """One checklist line (display only)."""

    text: str = Field(min_length=1, max_length=300)
    checked: StrictBool = False


class ChecklistBlock(_Block):
    """A read-only checklist."""

    type: Literal["checklist"]
    items: list[ChecklistItem] = Field(min_length=1, max_length=MAX_CHECKLIST_ITEMS)


class KeyValueItem(_Strict):
    """One ``key: value`` pair."""

    key: str = Field(min_length=1, max_length=80)
    value: Annotated[str, Field(max_length=500)] | int | float | StrictBool


class KeyValueBlock(_Block):
    """A list of key/value facts."""

    type: Literal["key_value"]
    items: list[KeyValueItem] = Field(min_length=1, max_length=MAX_KEY_VALUE_ITEMS)


class ActionButton(_Strict):
    """A button proposing one tool call; clicking it sends a normal chat message."""

    label: str = Field(min_length=1, max_length=40)
    tool: str = Field(pattern=_TOOL_NAME_PATTERN)
    args: dict[str, Any] = Field(default_factory=dict)
    style: Literal["primary", "secondary", "danger"] = "secondary"


class ActionsBlock(_Block):
    """A row of action buttons."""

    type: Literal["actions"]
    buttons: list[ActionButton] = Field(min_length=1, max_length=MAX_ACTION_BUTTONS)


class ImageBlock(_Block):
    """An image served by Janus itself (``/api/images/<id>``), or just its alt text."""

    type: Literal["image"]
    alt: str = Field(min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=100)
    caption: str | None = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def _same_origin(cls, value: str | None) -> str | None:
        if value is not None and not IMAGE_URL_PATTERN.match(value):
            raise ValueError("url must be an authenticated Janus image path (/api/images/<id>); no external images")
        return value


UIBlock = Annotated[
    CardBlock | TableBlock | ChartBlock | ChecklistBlock | KeyValueBlock | ActionsBlock | ImageBlock,
    Field(discriminator="type"),
]
_BLOCK_ADAPTER: TypeAdapter[Any] = TypeAdapter(UIBlock)


def _format_errors(error: ValidationError) -> str:
    details = []
    for item in error.errors()[:_MAX_ERRORS_SHOWN]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        details.append(f"{location}: {item.get('msg')}" if location else str(item.get("msg")))
    more = len(error.errors()) - _MAX_ERRORS_SHOWN
    return "; ".join(details) + (f"; and {more} more" if more > 0 else "")


def block_links(block: Mapping[str, Any]) -> list[str]:
    """Return every link ``href`` a validated block carries."""
    return [str(link.get("href", "")) for link in block.get("links") or () if isinstance(link, Mapping)]


def validate_block(raw: Any, *, offered_tool_names: Iterable[str] | None = None) -> dict[str, Any]:
    """Return the wire/storage shape of the model's ``raw`` block, or raise :class:`UIBlockError`.

    ``offered_tool_names`` are the tools of the turn an ``actions`` button may
    name (``render_ui`` itself never); ``None`` skips that check.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as error:
            raise UIBlockError("block must be a JSON object") from error
    if not isinstance(raw, Mapping):
        raise UIBlockError("block must be a JSON object with a 'type'")
    content = {key: value for key, value in raw.items() if key not in ("id", "version")}
    version = raw.get("version", UI_BLOCK_SCHEMA_VERSION)
    if version != UI_BLOCK_SCHEMA_VERSION:
        raise UIBlockError(f"version must be {UI_BLOCK_SCHEMA_VERSION}")
    if content.get("type") not in BLOCK_TYPES:
        raise UIBlockError(f"type must be one of: {', '.join(BLOCK_TYPES)}")
    try:
        model = _BLOCK_ADAPTER.validate_python(content)
    except ValidationError as error:
        raise UIBlockError(_format_errors(error)) from error
    block: dict[str, Any] = {
        "id": f"blk_{uuid.uuid4().hex[:12]}",
        "version": UI_BLOCK_SCHEMA_VERSION,
        **model.model_dump(mode="json", exclude_none=True),
    }
    if isinstance(model, ActionsBlock) and offered_tool_names is not None:
        offered = set(offered_tool_names) - {RENDER_UI_TOOL_NAME}
        unknown = sorted({button.tool for button in model.buttons} - offered)
        if unknown:
            raise UIBlockError(f"buttons name tools that are not available in this chat: {', '.join(unknown)}")
    size = len(json.dumps(block, ensure_ascii=False).encode("utf-8"))
    if size > UI_BLOCK_MAX_BYTES:
        raise UIBlockError(f"block is {size} bytes of JSON; the limit is {UI_BLOCK_MAX_BYTES}")
    return block


def needs_trust(block: Mapping[str, Any]) -> bool:
    """Return whether a block is refused after a suspected prompt injection (buttons, external links)."""
    return block.get("type") == "actions" or any(is_external_href(href) for href in block_links(block))


def to_marker(block: Mapping[str, Any]) -> str:
    """Return the ``__UI_BLOCK__`` frame of a validated block."""
    return UI_BLOCK_PREFIX + json.dumps(block, ensure_ascii=False)


@dataclass
class UIBlockTurn:
    """One chat turn's UI blocks (``ToolContext.ui_blocks``).

    ``available`` is whether the client can render blocks (it opened
    ``/ws/chat?tool_events=1``). ``offered_tool_names`` are the turn's tools an
    ``actions`` button may name, set by ``websocket/chat.py`` once the step is
    prepared. :meth:`add` runs on the tool's worker thread, the rest on the
    connection's event loop, hence the lock.
    """

    available: bool
    offered_tool_names: frozenset[str] = frozenset()
    _blocks: list[dict[str, Any]] = field(default_factory=list)
    _sent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, block: dict[str, Any]) -> None:
        """Keep a validated block for the reply, or raise :class:`UIBlockError` past the per-reply cap."""
        with self._lock:
            if len(self._blocks) >= MAX_UI_BLOCKS_PER_MESSAGE:
                raise UIBlockError(f"a reply can show at most {MAX_UI_BLOCKS_PER_MESSAGE} UI blocks")
            self._blocks.append(block)

    def blocks(self) -> list[dict[str, Any]]:
        """Return the turn's blocks in render order."""
        with self._lock:
            return list(self._blocks)

    def drain_frames(self) -> list[str]:
        """Return the ``__UI_BLOCK__`` frames of blocks not handed out yet."""
        with self._lock:
            pending, self._sent = self._blocks[self._sent :], len(self._blocks)
        return [to_marker(block) for block in pending]

    def attach_to(self, message: models.ConversationMessage) -> None:
        """Add one ``ConversationUIBlock`` row per block to ``message``."""
        for position, block in enumerate(self.blocks()):
            message.ui_blocks.append(
                models.ConversationUIBlock(
                    position=position,
                    block_id=block["id"],
                    block_type=block["type"],
                    schema_version=block["version"],
                    payload=block,
                )
            )


__all__ = [
    "BLOCK_TYPES",
    "IMAGE_URL_PATTERN",
    "MAX_UI_BLOCKS_PER_MESSAGE",
    "RENDER_UI_TOOL_NAME",
    "UI_BLOCK_MAX_BYTES",
    "UI_BLOCK_PREFIX",
    "UI_BLOCK_SCHEMA_VERSION",
    "UIBlockError",
    "UIBlockTurn",
    "is_allowed_href",
    "needs_trust",
    "to_marker",
    "validate_block",
]
