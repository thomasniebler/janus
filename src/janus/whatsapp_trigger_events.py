"""WhatsApp trigger events (JAN-361): which new live messages start trigger definitions.

``whatsapp_neonize.NeonizeConnector`` calls :func:`publish` after it committed a
message that arrived live, is incoming, and was not stored before. Your own
messages (including what Janus sends with ``whatsapp.send``) never get here, so
a reaction cannot trigger itself; neither do history-sync backfill and
re-deliveries.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from janus import models, trigger_definition_service
from janus.trigger_events import TriggerEvent

logger = logging.getLogger(__name__)

#: Top-level message fields that carry no content of their own: reactions, edits,
#: deletions (protocol messages), poll votes, pins, key distribution. A message made
#: only of these never fires a trigger event.
_NON_CONTENT_FIELDS = frozenset(
    {
        "protocolMessage",
        "reactionMessage",
        "encReactionMessage",
        "editedMessage",
        "pollUpdateMessage",
        "keepInChatMessage",
        "pinInChatMessage",
        "senderKeyDistributionMessage",
        "messageContextInfo",
        "messageHistoryBundle",
        "messageHistoryNotice",
        "encEventResponseMessage",
    }
)

#: Chat servers that are not a person or a group: Status updates, broadcast lists, channels.
_NON_CHAT_SERVERS = ("@broadcast", "@newsletter")


def has_content(message_proto: Any) -> bool:
    """Whether a message carries content (text, media, a poll, ...) rather than only a reaction or an edit."""
    try:
        return any(descriptor.name not in _NON_CONTENT_FIELDS for descriptor, _ in message_proto.ListFields())
    except Exception:
        return False


def _jid_user(jid: str | None) -> str:
    """The user part of a JID string: ``49123:4@s.whatsapp.net`` -> ``49123``."""
    return (jid or "").split("@", 1)[0].split(":", 1)[0]


def referenced_users(message_proto: Any) -> tuple[str, ...]:
    """User parts of everyone a message @-mentions or quotes, from any ``contextInfo`` it carries.

    WhatsApp puts ``contextInfo`` on whichever sub-message holds the content
    (extended text, image, video, ...), so every set field is checked.
    """
    users: set[str] = set()
    try:
        fields = message_proto.ListFields()
    except Exception:
        return ()
    for _descriptor, value in fields:
        context = getattr(value, "contextInfo", None)
        if context is None:
            continue
        users.update(_jid_user(jid) for jid in context.mentionedJID)
        if context.participant and context.stanzaID:  # a reply quotes a message (stanzaID)
            users.add(_jid_user(context.participant))
    users.discard("")
    return tuple(sorted(users))


def own_users(client: Any, *, quiet: bool = False) -> frozenset[str]:
    """The linked account's own user ids (phone-number JID and LID); empty, and mentions off, on failure."""
    try:
        me = client.get_me()
        return frozenset(user for user in (me.JID.User, me.LID.User) if user)
    except Exception as exc:
        if not quiet:
            logger.warning("Could not read the linked WhatsApp identity; mention events are off: %s", exc)
        return frozenset()


def publish(
    db: Session,
    account: models.WhatsAppAccount,
    conversation: models.WhatsAppConversation,
    message: models.WhatsAppMessage,
    *,
    own: frozenset[str],
    referenced: tuple[str, ...],
) -> None:
    """Emit the events one new incoming live message matches; one failing event does not stop the others."""
    if conversation.provider_chat_id.endswith(_NON_CHAT_SERVERS):
        return
    if conversation.conversation_type == models.WhatsAppConversationType.GROUP:
        events = [TriggerEvent.WHATSAPP_GROUP_MESSAGE_RECEIVED]
        if own.intersection(referenced):
            events.append(TriggerEvent.WHATSAPP_GROUP_MENTION)
    else:
        events = [TriggerEvent.WHATSAPP_DIRECT_MESSAGE_RECEIVED]
    data = {
        "message_id": message.id,
        "account": account.display_name,
        "chat": conversation.title,
        "chat_id": conversation.provider_chat_id,
        "sender": message.sender_display_name,
        "sender_id": message.sender_identifier,
        "received_at": message.provider_timestamp.isoformat() if message.provider_timestamp else None,
        "message_type": message.message_type,
        "body": (message.body or "")[:4000],
        "attachment": message.attachment_summary,
    }
    event_id = f"{account.id}:{message.provider_message_id}"
    for event in events:
        try:
            trigger_definition_service.emit_event(db, account.user_id, event, event_id, data)
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to publish %s for WhatsApp account %s: %s", event.value, account.id, exc)
