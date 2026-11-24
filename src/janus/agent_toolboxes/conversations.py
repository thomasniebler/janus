"""Janus-started conversations (JAN-319).

``start_conversation`` lets an unattended run -- an ad-hoc trigger, a Janus
follow-up (JAN-257), or a saved definition that lists the tool -- open a new
chat conversation for the run's owner whose first message is Janus's. The
conversation shows up in the owner's conversation list marked *Started by
Janus* and *new* until opened, the open UI refreshes through ``/ws/changes``
(the ``conversations`` domain), and a local install also gets a desktop
notification.

Safety fences, all enforced in code:

* **Owner only.** The owner is the run's user (``ToolContext.user``), never a
  model argument, and the tool takes no conversation id: it can only create a
  new conversation for that owner, never write into an existing one or into
  another user's.
* **Unattended only.** The entry is enabled only in the ``trigger`` and
  ``automation`` task modes and is not in any fixed allowlist, so chat (where
  Janus is already talking to the user), briefing/digest/ingestion, and
  delegated children (read-only tools only) never get it. It follows the
  owner's Unattended switch like any other tool (default *Allowed*,
  ``confirmation="recommended"``).
* **Bounded.** One conversation per run, five per owner per rolling day
  (:mod:`janus.services.conversation_service`), and a step without a durable
  run identity starts nothing (fail closed). Each run in a follow-up chain
  (JAN-274) spends from the same daily budget.
* **No exfiltration.** The text stays inside the owner's own Janus: no URL,
  link to another site, or image is accepted (an image would be fetched the
  moment the conversation is opened). ``start_conversation`` is in
  ``runtime_adapter.INJECTION_ESCALATED_TOOL_NAMES``, and the tool checks the
  turn trace itself, so after a suspected prompt injection it starts nothing.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from janus import event_logger, models
from janus.agent_toolboxes.shared import ToolContext, iso, run_isolated_tool_mutation
from janus.notifications import NotificationService
from janus.services.conversation_service import ConversationService, JanusConversationError
from janus.tool_registry import ToolBuildContext, ToolRegistryEntry
from janus.trigger_service import get_owned_trigger

CONVERSATIONS_START_TOOL_ID = "conversations.start"
START_CONVERSATION_TOOL_NAME = "start_conversation"
#: Task modes the entry is enabled in: unattended runs with a durable run identity (never ``delegated``).
_ENABLED_TASK_MODES = frozenset({"trigger", "automation"})
_LOG_SOURCE = "JanusConversations"


def build_conversation_tools(context: ToolContext, *, run_trigger_id: str | None) -> list[BaseTool]:
    """Build ``start_conversation`` for one unattended step of the run ``run_trigger_id``."""
    user_id = context.user.id

    @tool(START_CONVERSATION_TOOL_NAME)
    def start_conversation(title: str, message: str) -> dict[str, Any] | str:
        """Start a new chat conversation with the user, opening with your message.

        Use it only for something the user should read and may want to answer
        (a question, a decision, a finding that needs follow-up), not for
        routine results, which belong in this run's summary. ``title`` is one
        short line for the conversation list; ``message`` is your opening
        message (Markdown, at most 4000 characters, no URLs, links to other
        sites, or images). One conversation per run, a few per day.
        """
        trace = context.turn_trace
        if trace is not None and trace.injection_suspected():
            event_logger.log(
                models.EventType.WARNING,
                _LOG_SOURCE,
                "Refused to start a conversation after a suspected injection",
                {"user_id": user_id, "trigger_id": run_trigger_id},
                user_id=user_id,
            )
            return (
                f"BLOCKED: {START_CONVERSATION_TOOL_NAME} was not run because content read earlier looked like a "
                "prompt injection (instructions hidden in mail, web, or other outside content). No conversation "
                "was started; put what you found in this run's result instead. "
                "See the docs: tools.md#how-permissions-work."
            )
        if run_trigger_id is None:
            raise PermissionError("this step has no trigger run to start a conversation from")

        def start(db: Session) -> dict[str, Any]:
            if get_owned_trigger(db, user_id, run_trigger_id) is None:
                raise PermissionError("this step's trigger run could not be found")
            try:
                conversation = ConversationService(db).start_janus_conversation(
                    user_id, title=title, message=message, trigger_id=run_trigger_id
                )
            except JanusConversationError as error:
                raise ValueError(str(error)) from error
            event_logger.log(
                models.EventType.INFO,
                _LOG_SOURCE,
                "Janus started a conversation",
                {"user_id": user_id, "conversation_id": conversation.id, "trigger_id": run_trigger_id},
                user_id=user_id,
            )
            return {
                "conversation_id": conversation.id,
                "title": conversation.title,
                "created_at": iso(conversation.created_at),
            }

        result = run_isolated_tool_mutation(context, lambda db: db, start)
        NotificationService.notify_conversation(result["title"])
        return result

    return [start_conversation]


def _unattended_run(context: ToolBuildContext) -> bool:
    return context.task_mode in _ENABLED_TASK_MODES


CONVERSATIONS_START_ENTRY = ToolRegistryEntry(
    id=CONVERSATIONS_START_TOOL_ID,
    display_name="Start conversations",
    description=(
        "Let a trigger run start a new chat conversation with you, opening with a message from Janus "
        "(at most one per run and five a day; no links or images)."
    ),
    required_capabilities=(),
    # Writes only to the owner's own conversation list, and the text never leaves Janus.
    operation="write",
    risk_level="low",
    confirmation="recommended",
    docs_ref="tools.md#start-conversations",
    builder=lambda context: build_conversation_tools(context.tool_context, run_trigger_id=context.trigger_id),
    enabled_when=_unattended_run,
)

__all__ = [
    "CONVERSATIONS_START_ENTRY",
    "CONVERSATIONS_START_TOOL_ID",
    "START_CONVERSATION_TOOL_NAME",
    "build_conversation_tools",
]
