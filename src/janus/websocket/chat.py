"""Interactive agent chat WebSocket endpoint.

Runs entirely through the plan-driven runtime adapter (JAN-163): each
inbound message is turned into a plan step with a freshly authorized,
per-message toolset instead of a required ``agent_id`` selection and a
toolset cached for the lifetime of the conversation. Tool availability is
gated by real per-user connection state (mail/calendar/jira/dashboard, via
``capability_profile.user_connected_capabilities`` -- JAN-171); memory,
topics, and local-planner tools are always available (JAN-201), and
skill-library tools are admin-only. The system prompt is rendered from the
user's character constitution (``GET/PUT /api/character``) plus the flat
skill library.

``confirmation="required"`` tools are not pre-confirmed: each call pauses
for the user's approval (JAN-254, ``services/tool_approval.py``); one the
user switched *Off in chat* is not requested at all (JAN-306). The
socket is read by a background task (``websocket/chat_inbox.py``) so the
answer can arrive while the reply is still streaming. Page actions (JAN-251,
``services/ui_actions.py``) and UI introspection (JAN-284,
``services/ui_introspection.py``) use the same reader for the client's results.
UI blocks (JAN-252, ``services/ui_blocks.py``) are streamed after their
``render_ui`` (or ``generate_image``, JAN-253) call and stored with the reply.

Before each message the utility model narrows the authorized toolset to what
the message needs (JAN-300, ``janus.toolset_selection``); the conversation
keeps earlier selections, and ``request_tools`` adds a left-out toolbox from
the next message on. The choice and its reason travel in ``__TOOL_SELECTION__``.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from fastapi import Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from janus import (
    agent_runtime,
    config,
    database,
    event_logger,
    llm_service,
    llm_usage,
    models,
    prompt_injection,
    security,
    tool_policy,
    toolset_selection,
)
from janus.agent_toolboxes.remote_mcp import chat_allowed_tool_ids
from janus.agent_toolboxes.shared import TurnToolCall
from janus.agent_tools import build_managed_registry
from janus.capability_profile import user_connected_capabilities
from janus.runtime_adapter import INJECTION_REFUSAL_REASON, TASK_MODE_ALLOWED_TOOL_IDS, PreparedStep, RuntimeAdapter
from janus.services import (
    character_state_service,
    image_store,
    memory_curation,
    page_context,
    plan_step_events,
    tool_call_events,
    toolset_store,
    ui_blocks,
)
from janus.services.character_service import get_memory_instructions
from janus.services.conversation_service import ConversationService
from janus.services.memory_service import MemoryService
from janus.services.tool_approval import ToolApprovalBroker
from janus.services.ui_actions import UIActionBroker
from janus.services.ui_introspection import UIIntrospectionBroker
from janus.toolset_selection import ToolsetChoice
from janus.websocket.chat_inbox import ChatInbox
from janus.websocket.closing import send_error_and_close, send_message_and_close

logger = logging.getLogger("janus")

CONVERSATION_DELETED_MESSAGE = "This conversation was deleted."


class ConversationDeletedError(Exception):
    """The connection's conversation was deleted (for example from another tab) while a turn was running."""


def _conversation_exists(db: Session, conversation_id: int, user_id: int) -> bool:
    """Whether the user's conversation row is still there (owner-scoped)."""
    row = (
        db.query(models.Conversation.id)
        .filter(models.Conversation.id == conversation_id, models.Conversation.user_id == user_id)
        .first()
    )
    return row is not None


def _require_conversation(db: Session, conversation_id: int, user_id: int) -> None:
    """Raise :class:`ConversationDeletedError` unless the user's conversation row is still there."""
    if not _conversation_exists(db, conversation_id, user_id):
        raise ConversationDeletedError(conversation_id)


def _commit_turn(db: Session, conversation_id: int, user_id: int) -> None:
    """Commit the turn's writes; raise :class:`ConversationDeletedError` if the conversation is gone.

    A conversation deleted while a turn is in flight makes the turn's
    ``UPDATE conversations`` match no row: its writes are rolled back (no
    orphaned messages). A commit with no ``UPDATE conversations`` (the
    post-reply tail of a later message, when there is no title to store)
    raises nothing, so the row is checked after every commit as well. Either
    way the turn ends with a plain notice instead of a generic chat error.
    Any other stale-row conflict is re-raised unchanged.
    """
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        if not _conversation_exists(db, conversation_id, user_id):
            raise ConversationDeletedError(conversation_id) from None
        raise
    _require_conversation(db, conversation_id, user_id)


def _remember_always_allow(user_id: int, entry) -> None:
    """Switch ``entry`` to *Always allow* in chat after the approval card's button (JAN-359).

    Runs on the tool's worker thread, so it uses its own short-lived session.
    """
    db = database.SessionLocal()
    try:
        tool_policy.update_policy(db, user_id, entry.id, chat_mode=tool_policy.CHAT_MODE_ALWAYS, entry=entry)
    finally:
        db.close()


async def _end_on_error(websocket: WebSocket, error: Exception) -> None:
    """End the socket after a failed turn: a deleted conversation is a notice, anything else a chat error."""
    if isinstance(error, ConversationDeletedError):
        logger.info("WebSocket chat: conversation %s was deleted during a turn", error)
        await send_message_and_close(websocket, CONVERSATION_DELETED_MESSAGE, "chat")
        return
    logger.error(f"WebSocket chat error: {error}")
    await send_error_and_close(websocket, error, "chat")


#: Memory tools whose call in a turn means Janus already wrote memory itself, so the suggester stays quiet.
_MEMORY_WRITE_TOOLS = frozenset({"add_memory_fact", "update_fact", "group_facts"})


async def _suggest_memory_fact(  # noqa: PLR0913 - one turn's state, passed through from ws_chat
    db: Session,
    user: models.User,
    utility_llm,
    prepared: PreparedStep,
    recorder: tool_call_events.ToolCallRecorder,
    history: list[tuple[str, str]],
    user_msg: str,
    full_response: str,
    conversation_id: int,
) -> None:
    """Let the utility model store one durable fact from the turn, as an unverified agent fact.

    Memory is always on for the single Janus agent (JAN-201). It runs on every
    turn as a best-effort side effect: a failure here must not turn the
    already-streamed reply into an __ERROR__.
    """
    if _MEMORY_WRITE_TOOLS & {event.tool_name for event in recorder.events()}:
        # Janus already judged what to remember this turn; a second, paraphrased
        # copy from the suggester would only duplicate it.
        return
    try:
        if utility_llm is None:
            raise RuntimeError("utility model unavailable; memory suggestion skipped")
        if prepared.trace.injection_suspected():
            # The reply may restate the injected text, and this write
            # would bypass the guarded memory tools (JAN-127).
            raise RuntimeError("suspected prompt injection in this turn; memory suggestion skipped")
        candidate = await asyncio.to_thread(
            utility_llm.suggest_memory_fact,
            history,
            user_msg,
            full_response,
            instructions=get_memory_instructions(db, user.id),
        )
        if candidate is not None:
            MemoryService(db).create_fact(
                user_id=user.id,
                summary=candidate["summary"],
                detail=candidate.get("detail"),
                source=models.AgentMemorySource.AGENT,
                conversation_id=conversation_id,
            )
    except Exception as exc:
        logger.warning("Skipping memory suggestion for this chat turn: %s", exc)


def _build_janus_system_prompt(db: Session, user: models.User) -> str:
    """Return the chat system prompt from the user's character constitution and the skill library."""
    constitution, state = character_state_service.prompt_character(db, user.id)
    return agent_runtime.build_janus_system_prompt(
        constitution,
        agent_runtime.load_skill_library(db),
        character_state=state,
        progress_updates=True,
        memory_index=memory_curation.index_facts(db, user.id),
    )


def _safety_notice(prepared: PreparedStep) -> str:
    """Return the turn's prompt-injection notice (JAN-127), or ``""`` when nothing was flagged."""
    return prompt_injection.chat_safety_notice(
        ((call.tool_name, call.injection_rules) for call in prepared.trace.detections()),
        (record.tool_name for record in prepared.call_log.refused() if record.detail == INJECTION_REFUSAL_REASON),
    )


def _with_page_context(prepared: PreparedStep, user_msg: str, snapshot: dict[str, object] | None) -> str:
    """Return the model's copy of the message with the page snapshot as an untrusted block (JAN-250).

    A snapshot with item labels (other people's text) is recorded in the
    turn trace like an untrusted read, so a flagged label escalates
    consequential tools and appears in the safety notice (JAN-127).
    """
    if not snapshot:
        return user_msg
    scan = page_context.scan_page_context(snapshot)
    if page_context.carries_labels(snapshot):
        tool_id = page_context.PAGE_CONTEXT_TOOL_ID
        prepared.trace.record(TurnToolCall(tool_id, tool_id, True, scan.rules))
    if scan.suspected:
        # Rule ids only, never the untrusted text (see runtime_adapter._log_detection).
        event_logger.log(
            models.EventType.WARNING,
            "PromptInjectionGuard",
            "Suspected prompt injection in page_context",
            {"task_id": prepared.task.task_id, "step_id": prepared.step.step_id, "rules": list(scan.rules)},
        )
    return page_context.with_page_context(user_msg, snapshot, scan=scan)


#: Added to the selector's view of a message sent while a Janus page is open (no page content).
_PAGE_OPEN_NOTE = "(The user sent this from an open page of the Janus web UI.)"


async def _select_chat_toolset(
    db: Session,
    user: models.User,
    adapter: RuntimeAdapter,
    *,
    task_id: str,
    step_id: str,
    objective: str,
    requested_tool_ids: tuple[str, ...],
    allowed_tool_ids: frozenset[str],
    granted_capabilities: frozenset[str],
    utility_llm: llm_service.LLMService | None,
) -> ToolsetChoice | None:
    """Return this message's brain-selected toolset (JAN-300), or ``None`` when selection is off.

    The utility model picks from the message's authorized entries, off the
    event loop and bounded by ``tool_selection_timeout_seconds``; what earlier
    messages selected and what ``request_tools`` added stay in. Any failure
    falls back to every authorized tool.
    """
    if not config.settings.tool_selection_enabled:
        return None
    candidates = toolset_selection.authorized_candidates(
        adapter,
        task_id=task_id,
        step_id=step_id,
        task_mode="chat",
        requested_tool_ids=requested_tool_ids,
        allowed_tool_ids=allowed_tool_ids,
        granted_capabilities=granted_capabilities,
    )
    previous = toolset_store.latest_for_task(db, user.id, task_id)
    selector = None
    if utility_llm is not None and toolset_selection.needs_selector(candidates):
        try:
            # Its own client with a hard request timeout, so a hung provider frees its slot (JAN-300).
            bounded = toolset_selection.utility_service(db, config.settings.tool_selection_timeout_seconds)
            selector = toolset_selection.service_selector(bounded)
        except Exception as error:  # noqa: BLE001 - no selection: every authorized tool is offered
            logger.warning("Tool selection unavailable: %s", error.__class__.__name__)
    return await asyncio.to_thread(
        toolset_selection.choose_toolset,
        objective,
        candidates,
        selector,
        timeout_seconds=config.settings.tool_selection_timeout_seconds,
        sticky_tool_ids=toolset_store.sticky_tool_ids(previous),
        widened_tool_ids=previous.widened_tool_ids if previous is not None else (),
    )


def _record_chat_toolset(db: Session, user: models.User, prepared: PreparedStep, entries) -> list:
    """Store the message's effective toolset and return chat's ``request_tools`` tool, if anything was left out."""
    if prepared.toolset is None:
        return []
    # Plain values only: request_tools runs on a worker thread, where touching the
    # session-bound user (expired by the per-message commit, JAN-336) would query.
    task_id, step_id, user_id = prepared.task.task_id, prepared.step.step_id, user.id
    toolset_store.save(db, user_id, task_id, step_id, "chat", prepared.toolset)
    widen_tool = toolset_selection.build_widen_tool(
        prepared.toolset,
        entries,
        on_widen=lambda ids: toolset_store.add_widened(db, user_id, task_id, step_id, ids),
        injection_suspected=prepared.trace.injection_suspected,
    )
    return [widen_tool] if widen_tool is not None else []


async def _send_ui_blocks(send, turn: ui_blocks.UIBlockTurn) -> None:
    """Stream the turn's UI blocks that were not sent yet (JAN-252)."""
    for frame in turn.drain_frames():
        await send(frame)


def _build_utility_llm(db: Session) -> llm_service.LLMService | None:
    """Build the utility-model service, reporting (not hiding) an unusable explicit choice."""
    try:
        return llm_service.build_llm_service(
            db, llm_service.model_choice_from_settings(db, "utility"), purpose="utility"
        )
    except llm_service.ModelUnavailableError as error:
        event_logger.log(models.EventType.ERROR, "ChatWebSocket", str(error))
        return None


async def ws_chat(  # noqa: C901
    websocket: WebSocket,
    conversation_id: int | None = None,
    tool_events: bool = False,
    user: models.User | None = Depends(security.get_websocket_user_or_none),
):
    """Stream interactive chat responses through the plan-driven task runtime.

    The handshake's DB session is closed before the socket opens; the handler works in
    its one own session for the socket's lifetime (JAN-336).

    ``?tool_events=1`` opts the client into the structured ``__TOOL_EVENT__``
    frames (JAN-249), the ``__PLAN_EVENT__`` step timeline (JAN-316), the
    ``__TOOL_APPROVAL__`` requests (JAN-254), the
    ``__UI_ACTION__`` page actions (JAN-251), the ``__UI_BLOCK__`` blocks
    (JAN-252), and the ``__UI_INTROSPECT__`` requests (JAN-284); older clients
    (the iOS scaffold) never receive them, a confirmation-required tool answers
    ``BLOCKED`` for them instead of running unasked, and a page action, UI
    block, or introspection request errors. Tool calls and UI blocks
    are stored with the assistant message.
    """
    await websocket.accept()
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    history: list[tuple[str, str]] = []
    db = database.SessionLocal()
    # The authenticated user arrives detached; attach it to this handler's session
    # (without a query) so relationships load through the one session the socket uses.
    user = db.merge(user, load=False)
    # Everything this connection logs belongs to its user (JAN-272).
    owner_token = event_logger.bind_owner(user.id)
    surface_token = llm_usage.bind_surface("chat")  # usage accounting (JAN-408)
    inbox = ChatInbox(websocket)
    send = inbox.send
    try:
        conversation: models.Conversation | None = None
        if conversation_id is not None:
            conversation = ConversationService(db).get_conversation_for_user(conversation_id, user.id)
            if not conversation:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return

        if conversation is not None:
            await send(f"__CONVERSATION_ID__:{conversation.id}")

        stored_messages = []
        if conversation is not None:
            stored_messages = (
                db.query(models.ConversationMessage)
                .filter(models.ConversationMessage.conversation_id == conversation.id)
                .order_by(models.ConversationMessage.created_at)
                .all()
            )

        pending_user: str | None = None
        for msg in stored_messages:
            if msg.role == models.MessageRole.USER:
                pending_user = msg.content
            elif msg.role == models.MessageRole.ASSISTANT and pending_user is not None:
                history.append((pending_user, msg.content))
                pending_user = None
        if pending_user is not None:
            history.append((pending_user, ""))

        # Chat and the small per-turn helpers (title, memory suggestion) may
        # each run on their own model (JAN-233). An unusable explicit choice
        # fails loudly: the chat model ends the connection with an error, the
        # utility model is reported in the event log and its helpers are
        # skipped -- neither ever falls back to the default model.
        chat_model_choice = llm_service.model_choice_from_settings(db, "chat")
        llm = llm_service.build_llm_service(db, chat_model_choice, purpose="chat")
        utility_llm = _build_utility_llm(db)
        catalog_capabilities = user_connected_capabilities(db, user)
        broker = ToolApprovalBroker(
            send if tool_events else None,
            user_id=user.id,
            timeout_seconds=config.settings.chat_tool_approval_timeout_seconds,
            on_always_allow=lambda entry: _remember_always_allow(user.id, entry),
        )
        ui_broker = UIActionBroker(
            send if tool_events else None, timeout_seconds=config.settings.chat_ui_action_timeout_seconds
        )
        # Answers only on this, the user's own authenticated connection (JAN-283).
        introspection_broker = UIIntrospectionBroker(
            send if tool_events else None, timeout_seconds=config.settings.chat_ui_action_timeout_seconds
        )
        inbox.start(broker, ui_broker, introspection_broker)
        is_first_message = len(stored_messages) == 0
        message_index = len(history)

        while True:
            # End the session's transaction before waiting for the next message, so
            # an idle socket holds no pooled connection between turns (JAN-336): the
            # setup above only read, and every turn already committed its writes.
            # The session checks a connection out again on its next query.
            db.commit()
            # Plain text, or the {"type": "message", "text", "page_context"}
            # envelope with a sanitized page snapshot (JAN-250).
            inbound = page_context.parse_message_frame(await inbox.receive())
            user_msg = inbound.text

            if conversation is None:
                conversation = models.Conversation(user_id=user.id)
                db.add(conversation)
                # Commit, never just flush: mutating tools write through a
                # second session (run_isolated_tool_mutation), which would
                # otherwise block on this session's open write transaction
                # ("database is locked" on SQLite) -- JAN-189.
                db.commit()
                await send(f"__CONVERSATION_ID__:{conversation.id}")
            else:
                # Deleted while the socket was idle (another tab): end with the notice.
                _require_conversation(db, conversation_id, user.id)
            # Read once: after a commit the instance is expired, and refreshing a deleted row would raise.
            conversation_id = conversation.id

            user_record = models.ConversationMessage(
                conversation_id=conversation_id,
                role=models.MessageRole.USER,
                content=user_msg,
            )
            db.add(user_record)

            # Per-message plan step + authorized toolset (JAN-162/JAN-163): the
            # toolset is never cached across messages or reconnects. Chat is a
            # human-supervised surface: everything but the required-confirmation
            # entries is pre-confirmed, and those ask the user per call.
            message_index += 1
            task_id = f"chat-conversation-{conversation_id}"
            # Required tools the user switched Off in chat (JAN-306) are not
            # requested at all: not offered, not stubbed. Read per message so
            # a settings change applies from the next message on.
            # Rebuilt per message (JAN-343): the built-ins plus the user's offered remote MCP tools, so a
            # server or tool switched on or off in Settings applies from the next message on.
            adapter = RuntimeAdapter(db, user, registry=build_managed_registry(db, user))
            registry_entries = adapter.registry.entries()
            catalog_tool_ids = tuple(entry.id for entry in registry_entries)
            confirmation_by_tool_id = {entry.id: entry.confirmation for entry in registry_entries}
            chat_allowed = chat_allowed_tool_ids(TASK_MODE_ALLOWED_TOOL_IDS["chat"], adapter.registry)
            # Chat runs ``never``/``recommended`` tools freely; ``required`` ones go
            # through the per-call approval gate (JAN-254) unless the user switched
            # them to Always allow or chat to Allow all (JAN-359). Read per message,
            # so a settings change (or the card's Always allow) applies from the next one.
            preconfirmed_tool_ids = tool_policy.chat_preconfirmed_tool_ids(db, user.id, registry_entries)
            chat_withheld = tool_policy.chat_withheld_tool_ids(db, user.id, registry_entries)
            requested_tool_ids = tuple(tool_id for tool_id in catalog_tool_ids if tool_id not in chat_withheld)
            block_turn = ui_blocks.UIBlockTurn(available=tool_events)
            step_id = f"{task_id}::message-{message_index}"
            toolset = await _select_chat_toolset(
                db,
                user,
                adapter,
                task_id=task_id,
                step_id=step_id,
                objective="\n".join(
                    part for part in (user_msg, _PAGE_OPEN_NOTE if inbound.page_context else "") if part
                ),
                requested_tool_ids=requested_tool_ids,
                allowed_tool_ids=chat_allowed,
                granted_capabilities=catalog_capabilities,
                utility_llm=utility_llm,
            )
            prepared = adapter.prepare_step(
                task_id=task_id,
                step_id=step_id,
                objective=user_msg or "Continue the conversation",
                requested_tool_ids=requested_tool_ids,
                task_mode="chat",
                allowed_tool_ids=chat_allowed,
                granted_capabilities=catalog_capabilities,
                confirmed_tool_ids=preconfirmed_tool_ids,
                approval_gate=broker.gate,
                page_actions=ui_broker.turn(inbound.page_context),
                ui_blocks=block_turn,
                ui_introspection=introspection_broker,
                conversation_id=conversation_id,
                # A failing tool answers ERROR to the model instead of ending
                # the turn, so Janus can say what went wrong (JAN-229).
                guard_tool_errors=True,
                # Delegated children run on the chat model.
                model_choice=chat_model_choice,
                toolset=toolset,
            )
            cached_tools = list(prepared.tools)
            cached_tools.extend(_record_chat_toolset(db, user, prepared, registry_entries))
            # An ``actions`` block may only name tools this turn offers (JAN-252).
            block_turn.offered_tool_names = frozenset(prepared.tool_ids_by_name)
            # The snapshot is untrusted data for this turn only: never stored,
            # never in the history or objective (JAN-250).
            llm_user_msg = _with_page_context(prepared, user_msg, inbound.page_context)

            await send(
                "__TOOL_SELECTION__:"
                + json.dumps(
                    {
                        "task_id": prepared.task.task_id,
                        "step_id": prepared.step.step_id,
                        "tools": [
                            {"id": entry.id, "confirmation": confirmation_by_tool_id.get(entry.id, "never")}
                            for entry in prepared.selection.entries
                        ],
                        "denied": [{"tool_id": denial.tool_id, "reason": denial.reason} for denial in prepared.denials],
                        # What the brain selected for this message and why (JAN-300).
                        "toolset": toolset_selection.trace_payload(prepared.toolset),
                    }
                )
            )

            full_response = ""
            await send("__START__")
            recorder = tool_call_events.ToolCallRecorder(prepared.tool_ids_by_name, broker.decisions)
            # Re-rendered per message, so an approved character-state change (JAN-301) applies from the next one.
            agent_system_prompt = _build_janus_system_prompt(db, user)
            async for token in llm.stream_agent_chat(agent_system_prompt, cached_tools, history, llm_user_msg):
                if token.lstrip().startswith(llm_service.CHAT_STREAM_ERROR_PREFIX):
                    raise RuntimeError(token)
                if token.startswith(plan_step_events.PLAN_EVENT_PREFIX):
                    # The turn's step timeline (JAN-316): live only, never reply text.
                    if tool_events:
                        await send(token)
                    continue
                if token.startswith(tool_call_events.TOOL_EVENT_PREFIX):
                    frame = recorder.observe(token)
                    if frame is not None and tool_events:
                        await send(frame)
                        started = tool_call_events.parse_marker(frame)
                        if started is not None and started.phase == tool_call_events.PHASE_START:
                            # An approval request or a page action for this call may now follow it.
                            broker.note_started(started.call_id)
                            ui_broker.note_started(started.call_id, started.tool_name, started.args)
                        else:
                            # A render_ui call's block follows its terminal tool event. The tool runs on a
                            # worker thread, so its block can already be queued when the start event arrives.
                            await _send_ui_blocks(send, block_turn)
                    continue
                await send(token)
                if not tool_call_events.is_tool_protocol_frame(token):
                    full_response += token
            # Plain assistant text, so every client shows it and it is kept
            # with the conversation; the user's own input is never scanned.
            await _send_ui_blocks(send, block_turn)
            notice = _safety_notice(prepared)
            if notice:
                await send(notice)
                full_response += notice

            assistant_record = models.ConversationMessage(
                conversation_id=conversation_id,
                role=models.MessageRole.ASSISTANT,
                content=full_response,
            )
            recorder.attach_to(assistant_record)
            block_turn.attach_to(assistant_record)
            db.add(assistant_record)
            # Images generate_image stored this turn now belong to this reply (JAN-253).
            image_store.attach_turn_images(db, block_turn.blocks(), assistant_record, user.id)
            conversation.updated_at = datetime.now(UTC)
            # The reply, its tool calls, and its UI blocks are durable before __END__, so a client
            # that reloads the conversation right after the turn sees them; the
            # title and memory suggestion below are best-effort and commit later.
            _commit_turn(db, conversation_id, user.id)
            await send("__END__")

            if is_first_message and not conversation.title:
                try:
                    if utility_llm is None:
                        raise RuntimeError("utility model unavailable")
                    conversation.title = await utility_llm.generate_conversation_title(user_msg)
                except Exception:
                    conversation.title = user_msg[:60].strip() or "New Conversation"

            await _suggest_memory_fact(
                db, user, utility_llm, prepared, recorder, history, user_msg, full_response, conversation_id
            )

            _commit_turn(db, conversation_id, user.id)

            is_first_message = False
            history.append((user_msg, full_response))

    except WebSocketDisconnect:
        logger.info("WebSocket chat disconnected")
    except Exception as e:
        await _end_on_error(websocket, e)
    finally:
        await inbox.stop()
        event_logger.release_owner(owner_token)
        llm_usage.release_surface(surface_token)
        db.close()


def register_chat_websocket(api) -> None:
    """Register the chat WebSocket endpoint on an app."""
    api.websocket("/ws/chat")(ws_chat)
