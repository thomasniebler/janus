"""Translate Neonize protobuf events into normalized WhatsApp conversation/message payloads."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from janus import models, time_utils, whatsapp_trigger_events

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NeonizeConversationPayload:
    """Normalized conversation payload derived from a Neonize event."""

    provider_chat_id: str
    title: str
    conversation_type: models.WhatsAppConversationType
    participant_summary: str | None = None
    unread_count: int = 0


@dataclass(frozen=True)
class NeonizeMessagePayload:
    """Normalized message payload derived from a Neonize event."""

    provider_message_id: str
    provider_chat_id: str
    provider_timestamp: datetime
    direction: models.WhatsAppMessageDirection
    sender_identifier: str | None
    sender_display_name: str | None
    body: str | None
    snippet: str
    message_type: str | None = None
    attachment_summary: str | None = None
    reply_to_message_id: str | None = None
    # User parts of the JIDs this message @-mentions or quotes (replies to), for
    # the ``whatsapp.group_mention`` trigger event (JAN-361). Live messages only.
    referenced_users: tuple[str, ...] = ()
    # False for reactions, edits, deletions and other content-less messages, which never fire events.
    has_content: bool = True


def describe_message(message_proto: Any) -> tuple[str | None, str, str | None, str | None]:
    """Return (body, snippet, message_type, attachment_summary) for one WhatsApp message."""
    from neonize.utils.message import extract_text

    audio = message_proto.audioMessage
    if audio.ListFields():
        seconds = getattr(audio, "seconds", 0) or 0
        mimetype = getattr(audio, "mimetype", "") or "audio"
        if getattr(audio, "PTT", False):
            return (None, "🎤 Voice message", "voice_note", f"Voice message ({seconds}s, {mimetype})")
        return (None, "🎵 Audio message", "audio", f"Audio message ({seconds}s, {mimetype})")

    text = extract_text(message_proto).strip()
    if text:
        return text, text[:100], "text", None

    try:
        from neonize.utils.enum import MediaTypeToMMS

        media_kind = MediaTypeToMMS.from_message(message_proto).value
        return None, f"[{media_kind}]", media_kind, f"Unsupported {media_kind} attachment"
    except Exception:
        return None, "(unsupported message)", None, None


def translate_live_message(event: Any) -> tuple[NeonizeConversationPayload, NeonizeMessagePayload] | None:
    """Translate a live MessageEv into normalized conversation/message payloads."""
    from neonize.utils.jid import Jid2String

    info = event.Info
    source = info.MessageSource
    if not (source.Chat.User or source.Chat.Server):
        return None

    provider_chat_id = Jid2String(source.Chat)
    is_group = bool(source.IsGroup) or provider_chat_id.endswith("@g.us")
    sender_identifier = Jid2String(source.Sender) if (source.Sender.User or source.Sender.Server) else provider_chat_id
    sender_display_name = info.Pushname or None
    body, snippet, message_type, attachment_summary = describe_message(event.Message)
    provider_timestamp = time_utils.epoch_to_utc(getattr(info, "Timestamp", None))

    conversation_type = models.WhatsAppConversationType.GROUP if is_group else models.WhatsAppConversationType.DIRECT
    direction = (
        models.WhatsAppMessageDirection.OUTGOING if source.IsFromMe else models.WhatsAppMessageDirection.INCOMING
    )
    # Pushname identifies the message sender, not the chat. For a 1:1 incoming
    # message that's a reasonable chat title, but for a group (where it's just
    # one participant) or an outgoing message (where it's the local user) it
    # would mislabel the conversation, so fall back to the stable chat id instead.
    is_representative_of_chat = not is_group and not source.IsFromMe
    conversation_title = (
        sender_display_name if (is_representative_of_chat and sender_display_name) else None
    ) or provider_chat_id.split("@", 1)[0]
    conversation_payload = NeonizeConversationPayload(
        provider_chat_id=provider_chat_id,
        title=conversation_title,
        conversation_type=conversation_type,
    )
    message_payload = NeonizeMessagePayload(
        provider_message_id=info.ID,
        provider_chat_id=provider_chat_id,
        provider_timestamp=provider_timestamp,
        direction=direction,
        sender_identifier=sender_identifier,
        sender_display_name=sender_display_name,
        body=body,
        snippet=snippet,
        message_type=message_type,
        attachment_summary=attachment_summary,
        referenced_users=whatsapp_trigger_events.referenced_users(event.Message),
        has_content=whatsapp_trigger_events.has_content(event.Message),
    )
    return conversation_payload, message_payload


def translate_history_sync(event: Any) -> Iterator[tuple[NeonizeConversationPayload, NeonizeMessagePayload]]:
    """Translate a one-time HistorySyncEv payload into normalized conversation/message payloads.

    WhatsApp decides how much backlog to push here; there is no way to request a specific
    date range from Neonize, so this is a best-effort seed for the "last 3 days" experience.
    """
    for conversation in event.Data.conversations:
        try:
            provider_chat_id = conversation.ID
            if not provider_chat_id:
                continue
            is_group = provider_chat_id.endswith("@g.us")
            title = conversation.name or conversation.displayName or provider_chat_id.split("@", 1)[0]
            conversation_type = (
                models.WhatsAppConversationType.GROUP if is_group else models.WhatsAppConversationType.DIRECT
            )
            conversation_payload = NeonizeConversationPayload(
                provider_chat_id=provider_chat_id,
                title=title,
                conversation_type=conversation_type,
                unread_count=conversation.unreadCount or 0,
            )
        except Exception:
            logger.warning("Skipping malformed WhatsApp history-sync conversation entry.")
            continue

        for history_message in conversation.messages:
            try:
                web_message = history_message.message
                key = web_message.key
                if not key.ID:
                    continue
                body, snippet, message_type, attachment_summary = describe_message(web_message.message)
                provider_timestamp = time_utils.epoch_to_utc(web_message.messageTimestamp)
                message_payload = NeonizeMessagePayload(
                    provider_message_id=key.ID,
                    provider_chat_id=provider_chat_id,
                    provider_timestamp=provider_timestamp,
                    direction=(
                        models.WhatsAppMessageDirection.OUTGOING
                        if key.fromMe
                        else models.WhatsAppMessageDirection.INCOMING
                    ),
                    sender_identifier=key.participant or provider_chat_id,
                    sender_display_name=web_message.pushName or None,
                    body=body,
                    snippet=snippet,
                    message_type=message_type,
                    attachment_summary=attachment_summary,
                )
            except Exception:
                logger.warning("Skipping malformed WhatsApp history-sync message entry.")
                continue
            yield conversation_payload, message_payload
