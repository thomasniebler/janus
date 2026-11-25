"""Suggest skills from repeated tool sequences in the user's chats (JAN-407).

Pure computation, no model call: the user's chat turns from the last
:data:`WINDOW_DAYS` days are read as tool-name sequences (one per assistant
message, in call order, each call once). Every consecutive run of
:data:`MIN_LENGTH`..:data:`MAX_LENGTH` tools that appears in at least
:data:`MIN_TURNS` different turns is a candidate; a candidate fully contained
in a longer one that appears just as often is dropped. Patterns the user
dismissed stay hidden for :data:`DISMISS_DAYS` days.

A suggestion is only an offer. Drafting goes through the existing skill
design assistant (``POST /api/skills/design-assist``) with :func:`brief`, and
nothing joins the skill library until the user saves the draft. Tool names
are Janus's own registry names; conversation titles are shown as the user's
own data.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import models, time_utils

WINDOW_DAYS = 30
MIN_LENGTH = 2
MAX_LENGTH = 4
MIN_TURNS = 3
DISMISS_DAYS = 30
MAX_SUGGESTIONS = 5
MAX_EXAMPLES = 3


@dataclass
class Suggestion:
    """A repeated tool sequence worth turning into a skill."""

    key: str
    tools: tuple[str, ...]
    turns: int
    examples: list[str] = field(default_factory=list)

    @property
    def brief(self) -> str:
        """The goal handed to the skill design assistant."""
        return brief(self.tools, self.examples)


def pattern_key(tools: tuple[str, ...]) -> str:
    """Stable id of a tool sequence."""
    return hashlib.sha256("\x1f".join(tools).encode()).hexdigest()[:32]


def brief(tools: tuple[str, ...], examples: list[str]) -> str:
    """Describe the pattern so the design assistant can draft a skill for it."""
    chain = " -> ".join(tools)
    seen = "; ".join(f'"{title}"' for title in examples) or "several chats"
    return (
        f"In recent chats Janus repeatedly used the tools {chain}, in this order, for requests like {seen}. "
        "Write a skill that tells Janus when this routine applies, which tools to use in which order, "
        "what to check between the steps, and what the answer should look like."
    )


def _turn_sequences(db: Session, user_id: int, since) -> list[tuple[str, tuple[str, ...]]]:
    """``(conversation title, tool names in order)`` for each of the user's assistant turns with tool calls."""
    rows = db.execute(
        select(
            models.ConversationToolCall.message_id,
            models.ConversationToolCall.call_id,
            models.ConversationToolCall.tool_name,
            models.Conversation.title,
        )
        .join(models.ConversationMessage, models.ConversationMessage.id == models.ConversationToolCall.message_id)
        .join(models.Conversation, models.Conversation.id == models.ConversationMessage.conversation_id)
        .where(models.Conversation.user_id == user_id, models.ConversationToolCall.created_at >= since)
        .order_by(models.ConversationToolCall.message_id, models.ConversationToolCall.position)
    )
    turns: dict[int, list[str]] = defaultdict(list)
    seen_calls: dict[int, set[str]] = defaultdict(set)
    titles: dict[int, str] = {}
    for message_id, call_id, tool_name, title in rows:
        if call_id in seen_calls[message_id]:
            continue
        seen_calls[message_id].add(call_id)
        turns[message_id].append(tool_name)
        titles[message_id] = (title or "Untitled chat").strip()[:80]
    return [(titles[message_id], tuple(names)) for message_id, names in turns.items()]


def _contains(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
    return any(longer[i : i + len(shorter)] == shorter for i in range(len(longer) - len(shorter) + 1))


def suggestions(db: Session, user_id: int, *, now=None) -> list[Suggestion]:
    """The user's current suggestions, most repeated (then longest) first."""
    now = now or time_utils.utcnow()
    turns_with: dict[tuple[str, ...], set[int]] = defaultdict(set)
    examples: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for index, (title, names) in enumerate(_turn_sequences(db, user_id, now - timedelta(days=WINDOW_DAYS))):
        for length in range(MIN_LENGTH, MAX_LENGTH + 1):
            for start in range(len(names) - length + 1):
                gram = names[start : start + length]
                if len(set(gram)) < 2:  # the same tool repeated is not a routine
                    continue
                if index not in turns_with[gram]:
                    turns_with[gram].add(index)
                    if title not in examples[gram] and len(examples[gram]) < MAX_EXAMPLES:
                        examples[gram].append(title)
    frequent = {gram: len(indexes) for gram, indexes in turns_with.items() if len(indexes) >= MIN_TURNS}
    kept = [
        gram
        for gram, count in frequent.items()
        if not any(len(other) > len(gram) and frequent[other] >= count and _contains(other, gram) for other in frequent)
    ]
    cutoff = now - timedelta(days=DISMISS_DAYS)
    dismissed = set(
        db.scalars(
            select(models.SkillSuggestionDismissal.pattern_key).where(
                models.SkillSuggestionDismissal.user_id == user_id,
                models.SkillSuggestionDismissal.dismissed_at >= cutoff.replace(tzinfo=None),
            )
        )
    )
    result = [
        Suggestion(key=pattern_key(gram), tools=gram, turns=frequent[gram], examples=examples[gram])
        for gram in kept
        if pattern_key(gram) not in dismissed
    ]
    result.sort(key=lambda item: (-item.turns, -len(item.tools), item.tools))
    return result[:MAX_SUGGESTIONS]


def dismiss(db: Session, user_id: int, key: str) -> None:
    """Hide a suggestion for :data:`DISMISS_DAYS` days (again from now if it was dismissed before)."""
    row = db.scalar(
        select(models.SkillSuggestionDismissal).where(
            models.SkillSuggestionDismissal.user_id == user_id, models.SkillSuggestionDismissal.pattern_key == key
        )
    )
    if row is None:
        db.add(models.SkillSuggestionDismissal(user_id=user_id, pattern_key=key[:32]))
    else:
        row.dismissed_at = time_utils.utcnow()
    db.flush()


__all__ = ["DISMISS_DAYS", "MIN_TURNS", "Suggestion", "brief", "dismiss", "pattern_key", "suggestions"]
