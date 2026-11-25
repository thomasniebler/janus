"""Telegram alerts to the owner from unattended runs (JAN-405).

``notify_me_on_telegram`` sends one short message to the chat the owner linked to
their own bot. The recipient is never an argument, so the tool can only reach the
owner. Like the WhatsApp alert: unattended runs only, refused after a suspected
prompt injection, no links or images, at most 600 characters, and a daily cap.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry

TELEGRAM_NOTIFY_TOOL_ID = "telegram.notify"
TOOL_NAME = "notify_me_on_telegram"
MAX_MESSAGE_CHARS = 600
_TASK_MODES = frozenset({"trigger", "automation"})


def build_telegram_notify_tools(context: ToolContext) -> list[BaseTool]:
    """Build ``notify_me_on_telegram`` for one unattended step."""
    from janus.services import telegram_service
    from janus.services.conversation_service import JanusConversationError, refuse_external_references

    user_id = context.user.id

    @tool(TOOL_NAME)
    def notify_me_on_telegram(message: str) -> str:
        """Send the owner one short Telegram message in the chat they linked to their Janus bot.

        Use it only when the owner should see something soon. ``message`` is plain text,
        at most 600 characters, with no URLs, links, or images. A message the owner does
        not need today is wasted trust.
        """
        trace = context.turn_trace
        if trace is not None and trace.injection_suspected():
            return (
                f"BLOCKED: {TOOL_NAME} was not run because content read earlier looked like a prompt injection. "
                "Nothing was sent; put what you found in this run's result instead."
            )
        text = (message or "").strip()
        if len(text) > MAX_MESSAGE_CHARS:
            return f"ERROR: {TOOL_NAME} failed: the message is longer than {MAX_MESSAGE_CHARS} characters."
        try:
            refuse_external_references(text)
            telegram_service.send_to_owner(context.db, user_id, text)
        except (JanusConversationError, telegram_service.TelegramError) as exc:
            return f"ERROR: {TOOL_NAME} failed: {exc}."
        context.db.commit()
        return "Sent to the owner's Telegram."

    return [notify_me_on_telegram]


def _enabled(context: ToolBuildContext) -> bool:
    return context.task_mode in _TASK_MODES and "telegram" in context.tool_context.connected_capabilities


TELEGRAM_NOTIFY_ENTRY = ToolRegistryEntry(
    id=TELEGRAM_NOTIFY_TOOL_ID,
    display_name="Telegram alerts to me",
    description=(
        "Let a trigger run send you one short Telegram message in the chat you linked to your Janus bot "
        "(no links or images; a daily cap)."
    ),
    required_capabilities=("telegram",),
    # Writes only to the owner's own linked chat; the recipient is not an argument.
    operation="write",
    risk_level="low",
    confirmation="recommended",
    docs_ref="tools.md#telegram-alerts",
    builder=lambda context: build_telegram_notify_tools(context.tool_context),
    enabled_when=_enabled,
)

__all__ = ["TELEGRAM_NOTIFY_ENTRY", "TELEGRAM_NOTIFY_TOOL_ID", "TOOL_NAME", "build_telegram_notify_tools"]
