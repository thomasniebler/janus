"""WhatsApp account, live-ingestion, and list serialization helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from janus import models, time_utils
from janus.security import security_service
from janus.services.base import BaseService
from janus.whatsapp_neonize import NeonizeAdapter
from janus.whatsapp_translate import NeonizeConversationPayload, NeonizeMessagePayload

#: Longest message text Janus sends in one WhatsApp message (JAN-360).
MAX_SEND_CHARS = 4096


def phone_to_jid(phone_number: str) -> str:
    """Return the direct-chat JID for an international phone number, or raise :class:`ValueError`."""
    raw = phone_number.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace("/", "")
    if raw.startswith("+"):
        raw = raw[1:]
    elif raw.startswith("00"):
        raw = raw[2:]
    if not raw.isdigit() or not 7 <= len(raw) <= 15 or raw.startswith("0"):
        raise ValueError("phone_number must be an international number such as +4915112345678")
    return f"{raw}@s.whatsapp.net"


class WhatsAppService(BaseService):
    """Owns WhatsApp account CRUD, live-connector lifecycle, and list serialization."""

    def list_accounts(self, user: models.User) -> list[models.WhatsAppAccount]:
        """Return WhatsApp accounts owned by the current user."""
        return (
            self.db.query(models.WhatsAppAccount)
            .filter(models.WhatsAppAccount.user_id == user.id)
            .order_by(models.WhatsAppAccount.created_at.asc())
            .all()
        )

    def get_account_for_user(self, account_id: int, user: models.User) -> models.WhatsAppAccount | None:
        """Return one account only when owned by the current user."""
        return (
            self.db.query(models.WhatsAppAccount)
            .filter(models.WhatsAppAccount.id == account_id, models.WhatsAppAccount.user_id == user.id)
            .first()
        )

    def create_account(self, user: models.User, data: dict[str, Any]) -> models.WhatsAppAccount:
        """Create a WhatsApp account with encrypted session data when present."""
        payload = dict(data)
        session_data = payload.get("session_data")
        if session_data:
            payload["session_data"] = security_service.encrypt(session_data)
        account = models.WhatsAppAccount(user_id=user.id, **payload)
        self.db.add(account)
        self.db.commit()
        self.db.refresh(account)

        if account.is_enabled and (account.session_data or account.session_database_path):
            from janus.workers.whatsapp import start_account_connector

            start_account_connector(account)
        return account

    def update_account(self, account: models.WhatsAppAccount, data: dict[str, Any]) -> models.WhatsAppAccount:
        """Update a WhatsApp account in place, starting/stopping its connector on enable changes."""
        payload = dict(data)
        if payload.get("session_data"):
            payload["session_data"] = security_service.encrypt(payload["session_data"])
        previously_enabled = account.is_enabled
        for field, value in payload.items():
            setattr(account, field, value)
        self.db.commit()
        self.db.refresh(account)

        if "is_enabled" in payload and account.is_enabled != previously_enabled:
            from janus.workers.whatsapp import start_account_connector, stop_account_connector

            if account.is_enabled:
                start_account_connector(account)
            else:
                stop_account_connector(account.id)
        return account

    def update_account_for_user(
        self, account_id: int, user: models.User, data: dict[str, Any]
    ) -> models.WhatsAppAccount | None:
        """Update one of ``user``'s accounts; ``None`` if missing or foreign (JAN-288)."""
        account = self.get_account_for_user(account_id, user)
        if account is None:
            return None
        return self.update_account(account, data)

    def delete_account_for_user(self, account_id: int, user: models.User) -> bool:
        """Delete one of ``user``'s accounts and stop its live connector; ``False`` if missing or foreign."""
        account = self.get_account_for_user(account_id, user)
        if account is None:
            return False
        from janus.workers.whatsapp import stop_account_connector

        stop_account_connector(account.id)
        self.db.delete(account)
        self.db.commit()
        return True

    def serialize_account(self, account: models.WhatsAppAccount) -> dict[str, Any]:
        """Return an API-safe account payload without exposing secrets."""
        payload = models.to_dict(account)
        payload["session_configured"] = bool(account.session_data or account.session_database_path)
        payload.pop("session_data", None)
        return payload

    def list_conversations(self, user: models.User, limit: int = 50) -> list[models.WhatsAppConversation]:
        """Return recent conversations for the current user."""
        return (
            self.db.query(models.WhatsAppConversation)
            .filter(models.WhatsAppConversation.user_id == user.id)
            .order_by(
                models.WhatsAppConversation.latest_message_at.desc().nullslast(),
                models.WhatsAppConversation.updated_at.desc(),
            )
            .limit(limit)
            .all()
        )

    def list_messages(
        self,
        user: models.User,
        *,
        conversation_id: int | None = None,
        days: int = 3,
        limit: int = 100,
    ) -> list[models.WhatsAppMessage]:
        """Return recent WhatsApp messages for the current user."""
        query = self.db.query(models.WhatsAppMessage).filter(models.WhatsAppMessage.user_id == user.id)
        if conversation_id is not None:
            query = query.filter(models.WhatsAppMessage.whatsapp_conversation_id == conversation_id)
        else:
            since = time_utils.utcnow() - timedelta(days=days)
            query = query.filter(models.WhatsAppMessage.provider_timestamp >= since)
        return query.order_by(models.WhatsAppMessage.provider_timestamp.desc()).limit(limit).all()

    def update_message_priority(
        self,
        user: models.User,
        message_id: int,
        priority: models.Priority,
    ) -> models.WhatsAppMessage | None:
        """Update a WhatsApp message priority similarly to email relabeling."""
        message = (
            self.db.query(models.WhatsAppMessage)
            .filter(models.WhatsAppMessage.id == message_id, models.WhatsAppMessage.user_id == user.id)
            .first()
        )
        if message is None:
            return None
        message.priority = priority
        self.db.commit()
        self.db.refresh(message)
        return message

    def test_connection(self, account: models.WhatsAppAccount) -> tuple[bool, str]:
        """Return the adapter-level connection readiness check."""
        return NeonizeAdapter(account).test_connection()

    def upsert_conversation(
        self,
        user: models.User,
        account: models.WhatsAppAccount,
        payload: NeonizeConversationPayload,
    ) -> models.WhatsAppConversation:
        """Create or update one conversation from a normalized Neonize payload."""
        conversation = (
            self.db.query(models.WhatsAppConversation)
            .filter(
                models.WhatsAppConversation.whatsapp_account_id == account.id,
                models.WhatsAppConversation.provider_chat_id == payload.provider_chat_id,
            )
            .first()
        )
        if conversation is None:
            conversation = models.WhatsAppConversation(
                user_id=user.id,
                whatsapp_account_id=account.id,
                provider_chat_id=payload.provider_chat_id,
            )
            self.db.add(conversation)
        conversation.conversation_type = payload.conversation_type
        conversation.title = payload.title
        conversation.participant_summary = payload.participant_summary
        conversation.unread_count = payload.unread_count
        conversation.updated_at = time_utils.utcnow()
        self.db.flush()
        return conversation

    def message_exists(self, account: models.WhatsAppAccount, provider_message_id: str) -> bool:
        """Whether this account already stored the message (re-deliveries must not re-fire triggers)."""
        return (
            self.db.query(models.WhatsAppMessage.id)
            .filter(
                models.WhatsAppMessage.whatsapp_account_id == account.id,
                models.WhatsAppMessage.provider_message_id == provider_message_id,
            )
            .first()
            is not None
        )

    def upsert_message(
        self,
        user: models.User,
        account: models.WhatsAppAccount,
        conversation_id: int,
        payload: NeonizeMessagePayload,
    ) -> models.WhatsAppMessage:
        """Create or update one message from a normalized Neonize payload."""
        message = (
            self.db.query(models.WhatsAppMessage)
            .filter(
                models.WhatsAppMessage.whatsapp_account_id == account.id,
                models.WhatsAppMessage.provider_message_id == payload.provider_message_id,
            )
            .first()
        )
        if message is None:
            message = models.WhatsAppMessage(
                user_id=user.id,
                whatsapp_account_id=account.id,
                whatsapp_conversation_id=conversation_id,
                provider_message_id=payload.provider_message_id,
                priority=models.Priority.DIGEST,
            )
            self.db.add(message)
        message.provider_timestamp = payload.provider_timestamp
        message.direction = payload.direction
        message.sender_identifier = payload.sender_identifier
        message.sender_display_name = payload.sender_display_name
        message.body = payload.body
        message.snippet = payload.snippet
        message.message_type = payload.message_type
        message.attachment_summary = payload.attachment_summary
        message.reply_to_message_id = payload.reply_to_message_id
        self.db.flush()

        conversation = self.db.get(models.WhatsAppConversation, conversation_id)
        existing_latest = time_utils.normalize_timestamp(conversation.latest_message_at) if conversation else None
        incoming_timestamp = time_utils.normalize_timestamp(message.provider_timestamp)
        if conversation is not None and (existing_latest is None or incoming_timestamp >= existing_latest):
            conversation.latest_message_at = message.provider_timestamp
            conversation.latest_message_snippet = message.snippet
        return message

    def send_text(
        self,
        user: models.User,
        text: str,
        *,
        conversation_id: int | None = None,
        phone_number: str | None = None,
        account_id: int | None = None,
    ) -> tuple[models.WhatsAppConversation, models.WhatsAppMessage]:
        """Send a text message from one of ``user``'s enabled accounts and store it as outgoing (JAN-360).

        Exactly one target: an existing ``conversation_id`` of the user, or a
        ``phone_number`` (international format, digits with an optional
        leading ``+`` or ``00``) for a direct chat, which reuses that chat's
        conversation when one is stored. ``account_id`` picks the sending
        account for a phone number; it defaults to the user's only enabled
        account. Raises :class:`ValueError` for bad input and
        :class:`~janus.workers.whatsapp.WhatsAppNotConnectedError` when the
        account has no live connection.
        """
        from janus.workers.whatsapp import send_text_message

        body = (text or "").strip()
        if not body:
            raise ValueError("the message text is empty")
        if len(body) > MAX_SEND_CHARS:
            raise ValueError(f"the message is longer than {MAX_SEND_CHARS} characters")
        if (conversation_id is None) == (phone_number is None or not phone_number.strip()):
            raise ValueError("give exactly one of conversation_id or phone_number")
        enabled = [account for account in self.list_accounts(user) if account.is_enabled]
        conversation: models.WhatsAppConversation | None = None
        if conversation_id is not None:
            conversation = (
                self.db.query(models.WhatsAppConversation)
                .filter(
                    models.WhatsAppConversation.id == conversation_id,
                    models.WhatsAppConversation.user_id == user.id,
                )
                .first()
            )
            account = next((a for a in enabled if conversation and a.id == conversation.whatsapp_account_id), None)
            if conversation is None or account is None:
                raise ValueError(f"WhatsApp conversation {conversation_id} not found")
            chat_jid = conversation.provider_chat_id
        else:
            chat_jid = phone_to_jid(phone_number or "")
            if account_id is not None:
                account = next((a for a in enabled if a.id == account_id), None)
                if account is None:
                    raise ValueError(f"WhatsApp account {account_id} not found or disabled")
            elif len(enabled) == 1:
                account = enabled[0]
            elif not enabled:
                raise ValueError("no enabled WhatsApp account")
            else:
                raise ValueError("several WhatsApp accounts are enabled; pass account_id")
        message_id, sent_at = send_text_message(account.id, chat_jid, body)
        if conversation is None:
            existing = (
                self.db.query(models.WhatsAppConversation)
                .filter(
                    models.WhatsAppConversation.whatsapp_account_id == account.id,
                    models.WhatsAppConversation.provider_chat_id == chat_jid,
                )
                .first()
            )
            conversation = existing or self.upsert_conversation(
                user,
                account,
                NeonizeConversationPayload(
                    provider_chat_id=chat_jid,
                    conversation_type=models.WhatsAppConversationType.DIRECT,
                    title="+" + chat_jid.split("@", 1)[0],
                    participant_summary=None,
                ),
            )
        snippet = body if len(body) <= 140 else body[:139] + "…"
        message = self.upsert_message(
            user,
            account,
            conversation.id,
            NeonizeMessagePayload(
                provider_message_id=message_id,
                provider_chat_id=chat_jid,
                provider_timestamp=sent_at,
                direction=models.WhatsAppMessageDirection.OUTGOING,
                sender_identifier=None,
                sender_display_name=account.display_name,
                body=body,
                snippet=snippet,
                message_type="text",
            ),
        )
        self.db.commit()
        return conversation, message

    def restart_connector(self, account: models.WhatsAppAccount) -> dict[str, Any]:
        """Restart the live Neonize connector for one account as a best-effort connectivity refresh.

        There is no pull-based history API in Neonize, so this cannot force a resync of past
        messages; it only re-establishes the persistent connection that ingests new messages live.
        """
        from janus.workers.whatsapp import start_account_connector, stop_account_connector

        if not account.is_enabled:
            raise RuntimeError("This WhatsApp account is disabled.")
        if not (account.session_data or account.session_database_path):
            raise RuntimeError("No Neonize session is configured for this account yet.")
        stop_account_connector(account.id)
        start_account_connector(account)
        return {
            "account_id": account.id,
            "connector_started": True,
            "message": "WhatsApp connector restarted. New messages will sync automatically as they arrive.",
        }
