"""Generative UI: Janus shows a card, table, chart, ... in its chat reply (JAN-252).

``render_ui`` changes nothing. It validates the model's block against the
fixed catalog (``services/ui_blocks.py``), keeps it on the turn's
:class:`~janus.services.ui_blocks.UIBlockTurn` (``ToolContext.ui_blocks``),
and answers a short confirmation. ``websocket/chat.py`` streams the block as a
``__UI_BLOCK__`` frame after the call and stores it with the assistant
message. An invalid block, a client that cannot render blocks, or a reply
past the block cap raises, so chat's tool error guard answers
``ERROR: render_ui failed: ...`` and the model can fix the block or answer in
Markdown.

Chat only: the registry entry is in ``TASK_MODE_ALLOWED_TOOL_IDS["chat"]`` and
``tool_policy.LOCKED_UNATTENDED`` and is enabled only in the ``chat`` task
mode. After a suspected prompt injection in the turn, blocks with buttons or
external links are refused (``ui_blocks.needs_trust``).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.services import ui_blocks
from janus.services.ui_blocks import RENDER_UI_TOOL_NAME, UIBlockTurn
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry

UI_BLOCKS_TOOL_ID = "ui_blocks"


class UIBlockFailed(RuntimeError):
    """The block was not shown; the message says why."""


_DESCRIPTION = (
    "Show the user a structured UI block in your chat reply instead of (or next to) Markdown. "
    "Pass one block object with a 'type'. Catalog (version 1; all text is plain text, no Markdown or HTML):\n"
    "- card: title, subtitle?, body?, fields? [{label, value}] (max 12), links? [{label, href}] (max 4)\n"
    "- table: title?, columns [str] (1-8), rows [[cell per column]] (max 50); a cell is a string, number, "
    "boolean, or null\n"
    "- chart: title?, kind bar|line|pie, labels [str] (1-50), series [{name, values [number per label]}] "
    "(1-5; pie: exactly one series of non-negative values), unit?\n"
    "- checklist: title?, items [{text, checked}] (1-50), display only\n"
    "- key_value: title?, items [{key, value}] (1-30)\n"
    "- actions: title?, buttons [{label, tool, args, style primary|secondary|danger}] (1-6); 'tool' is the "
    "name of one of your tools and 'args' its arguments. Clicking sends the user's request to run it to you "
    "as a normal message; it does not run anything by itself.\n"
    "- image: title?, alt, url? (only /api/images/<id>), caption?\n"
    "Links (href) must be a Janus path (/...) or an https:// URL. A block is at most 8 KB of JSON; at most "
    f"{ui_blocks.MAX_UI_BLOCKS_PER_MESSAGE} blocks per reply. An invalid block answers an error with the "
    "reasons: fix it and call again. If the answer says this client cannot render UI blocks, answer in Markdown "
    "instead. Do not repeat a shown block's contents as text."
)


def _injection_refusal() -> str:
    # The runtime adapter's escalation shape (runtime_adapter._traced), so the call ends ``blocked``.
    return (
        f"BLOCKED: {RENDER_UI_TOOL_NAME} was not run because content read earlier looked like a prompt "
        "injection (instructions hidden in mail, web, or other outside content). Blocks with buttons or "
        "external links are not shown in this reply; show the information without them or as text. "
        "See the docs: tools.md#how-permissions-work."
    )


def build_ui_block_tools(context: ToolContext) -> list[BaseTool]:
    """Build the ``render_ui`` tool for one chat turn."""

    @tool(RENDER_UI_TOOL_NAME, description=_DESCRIPTION)
    def render_ui(block: dict[str, Any]) -> str:
        turn = context.ui_blocks
        if not isinstance(turn, UIBlockTurn) or not turn.available:
            raise UIBlockFailed("this client cannot render UI blocks; answer in Markdown instead")
        try:
            validated = ui_blocks.validate_block(block, offered_tool_names=turn.offered_tool_names)
        except ui_blocks.UIBlockError as error:
            raise UIBlockFailed(f"invalid UI block: {error}") from error
        trace = context.turn_trace
        if trace is not None and trace.injection_suspected() and ui_blocks.needs_trust(validated):
            return _injection_refusal()
        try:
            turn.add(validated)
        except ui_blocks.UIBlockError as error:
            raise UIBlockFailed(str(error)) from error
        return (
            f"Shown to the user: {validated['type']} block {validated['id']}. "
            "Do not repeat its contents as text; add only what the block does not say."
        )

    return [render_ui]


def _chat_only(context: ToolBuildContext) -> bool:
    return context.task_mode == "chat"


UI_BLOCKS_ENTRY = ToolRegistryEntry(
    id=UI_BLOCKS_TOOL_ID,
    display_name="UI blocks",
    description=(
        "Show structured UI in chat replies (cards, tables, charts, checklists, key/value lists, action "
        "buttons, images); display only, nothing is changed."
    ),
    required_capabilities=(),
    # Nothing on the server is read or written; the block is only shown.
    operation="read",
    risk_level="low",
    confirmation="never",
    docs_ref="tools.md#ui-blocks",
    builder=lambda context: build_ui_block_tools(context.tool_context),
    # Chat only (also in tool_policy.LOCKED_UNATTENDED): a block needs the web chat to render it.
    enabled_when=_chat_only,
)

__all__ = ["UIBlockFailed", "UI_BLOCKS_ENTRY", "UI_BLOCKS_TOOL_ID", "build_ui_block_tools"]
