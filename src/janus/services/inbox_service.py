"""The action inbox: one owner-scoped list of what waits for the user, and the low-risk actions on it.

The inbox aggregates existing sources and adds no state of its own; each item stays owned by its model,
so acting in the dashboard and from a device cannot disagree. A device token may only take the actions
listed per item (``mark_read``, ``dismiss``); approvals stay in the web app (``risk = "needs_web"``
or simply not offered), because approving, for example, a trigger proposal writes the fingerprint that
lets an unattended run use its tools (JAN-235).

Titles and summaries are written from outside content (mail, calendar, models), so they are cut to a
short plain-text form (:func:`clean_text`) and documented as untrusted for every client.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import UTC, datetime
from typing import Any

from janus import models
from janus.schemas.inbox import InboxItem
from janus.services.base import BaseService
from janus.services.local_planner_service import LocalPlannerService
from janus.services.topic_service import TopicService

#: Items taken from each source, and the list's overall cap.
PER_SOURCE_LIMIT = 20
MAX_ITEMS = 50
TITLE_CHARS = 120
SUMMARY_CHARS = 300


#: kind -> the actions a device token may take on it.
ACTIONS: dict[str, tuple[str, ...]] = {
    "conversation": ("mark_read",),
    "planner_suggestion": ("dismiss",),
    "topic_suggestion": ("dismiss",),
    "trigger_proposal": (),
}


class InboxError(LookupError):
    """Raised for an unknown kind or action, or an item that is not the owner's."""


def clean_text(value: object, limit: int) -> str:
    """Collapse whitespace, drop control and format (zero-width, bidi) characters, and cut to ``limit`` characters.

    Unicode categories rather than a regex over escaped code-point ranges: such a character class crashes a
    Semgrep community rule's parser, which would fail the security scan.
    """
    text = "".join(" " if unicodedata.category(char) in ("Cc", "Cf") else char for char in str(value or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _naive_utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(UTC).replace(tzinfo=None)
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _item(kind: str, item_id: int, title: str, summary: str, created_at: datetime | None, open_url: str) -> InboxItem:
    actions = ACTIONS[kind]
    return InboxItem(
        kind=kind,  # type: ignore[arg-type]
        id=item_id,
        title=clean_text(title, TITLE_CHARS) or "(untitled)",
        summary=clean_text(summary, SUMMARY_CHARS),
        created_at=_naive_utc(created_at),
        risk="low" if actions else "needs_web",
        actions=list(actions),  # type: ignore[arg-type]
        open_url=open_url,
    )


class InboxService(BaseService):
    """Lists the owner's open items and runs the safe actions on them."""

    def list_items(self, user: models.User) -> list[InboxItem]:
        """Return the owner's open items, newest first, capped at :data:`MAX_ITEMS`."""
        items = [
            *self._conversations(user),
            *self._planner_suggestions(user),
            *self._topic_suggestions(user),
            *self._trigger_proposals(user),
        ]
        items.sort(key=lambda entry: (entry.created_at, entry.id), reverse=True)
        return items[:MAX_ITEMS]

    def act(self, user: models.User, kind: str, item_id: int, action: str) -> None:
        """Run ``action`` on one of the owner's items; unknown or foreign items look the same."""
        if action not in ACTIONS.get(kind, ()):
            raise InboxError("unknown inbox item kind or action")
        if kind == "conversation":
            conversation = self._owned_unread_conversation(user, item_id)
            if conversation is None:
                raise InboxError("not found")
            conversation.unread = False
            self.db.commit()
        elif kind == "planner_suggestion":
            self._require_pending(models.PlannerSuggestion, item_id, user.id, models.PlannerSuggestionStatus.PENDING)
            if LocalPlannerService(self.db).dismiss_suggestion(user.id, item_id) is None:
                raise InboxError("not found")
        elif kind == "topic_suggestion":
            self._require_pending(models.TopicSuggestion, item_id, user.id, models.TopicSuggestionStatus.PENDING)
            try:
                TopicService(self.db).update_suggestion_status(
                    user_id=user.id, suggestion_id=item_id, status=models.TopicSuggestionStatus.DISMISSED
                )
            except ValueError as error:
                raise InboxError("not found") from error

    def _require_pending(self, model: Any, item_id: int, user_id: int, pending: Any) -> None:
        """Refuse an action on an item that is not the owner's or no longer open: the inbox lists only open items."""
        found = (
            self.db.query(model.id)
            .filter(model.id == item_id, model.user_id == user_id, model.status == pending)
            .first()
        )
        if found is None:
            raise InboxError("not found")

    def _owned_unread_conversation(self, user: models.User, conversation_id: int) -> models.Conversation | None:
        return (
            self.db.query(models.Conversation)
            .filter(
                models.Conversation.id == conversation_id,
                models.Conversation.user_id == user.id,
                models.Conversation.origin == models.ConversationOrigin.JANUS.value,
            )
            .first()
        )

    def _conversations(self, user: models.User) -> list[InboxItem]:
        rows = (
            self.db.query(models.Conversation)
            .filter(
                models.Conversation.user_id == user.id,
                models.Conversation.origin == models.ConversationOrigin.JANUS.value,
                models.Conversation.unread.is_(True),
            )
            .order_by(models.Conversation.created_at.desc())
            .limit(PER_SOURCE_LIMIT)
            .all()
        )
        items = []
        for conversation in rows:
            first = conversation.messages[0].content if conversation.messages else ""
            items.append(
                _item(
                    "conversation",
                    conversation.id,
                    conversation.title or "Janus started a conversation",
                    first,
                    conversation.created_at,
                    f"/chat?conversation={conversation.id}",
                )
            )
        return items

    def _planner_suggestions(self, user: models.User) -> list[InboxItem]:
        suggestions: list[dict[str, Any]] = LocalPlannerService(self.db).list_suggestions(user.id)[:PER_SOURCE_LIMIT]
        return [
            _item(
                "planner_suggestion",
                int(entry["id"]),
                entry.get("title") or str(entry.get("suggestion_type") or "Planner suggestion"),
                entry.get("reason") or entry.get("description") or "",
                entry.get("created_at"),
                "/planner",
            )
            for entry in suggestions
        ]

    def _topic_suggestions(self, user: models.User) -> list[InboxItem]:
        rows = TopicService(self.db).list_suggestions(user.id, status=models.TopicSuggestionStatus.PENDING)
        items = []
        for suggestion in rows[:PER_SOURCE_LIMIT]:
            try:
                payload = json.loads(suggestion.payload_json or "{}")
            except ValueError:
                payload = {}
            name = payload.get("title") or payload.get("name") if isinstance(payload, dict) else None
            kind_label = str(getattr(suggestion.suggestion_type, "value", suggestion.suggestion_type)).replace("_", " ")
            items.append(
                _item(
                    "topic_suggestion",
                    suggestion.id,
                    f"Topic: {name}" if name else f"Topic suggestion ({kind_label})",
                    suggestion.reason,
                    suggestion.created_at,
                    "/memory?tab=topics",
                )
            )
        return items

    def _trigger_proposals(self, user: models.User) -> list[InboxItem]:
        rows = (
            self.db.query(models.TriggerDefinition)
            .filter(
                models.TriggerDefinition.user_id == user.id,
                models.TriggerDefinition.proposal_status == models.TriggerProposalStatus.PROPOSED.value,
            )
            .order_by(models.TriggerDefinition.created_at.desc())
            .limit(PER_SOURCE_LIMIT)
            .all()
        )
        return [
            _item(
                "trigger_proposal",
                row.id,
                f"Automation proposed: {row.name}",
                row.objective,
                row.created_at,
                "/settings?tab=runtime",
            )
            for row in rows
        ]


__all__ = ["ACTIONS", "MAX_ITEMS", "InboxError", "InboxService", "clean_text"]
