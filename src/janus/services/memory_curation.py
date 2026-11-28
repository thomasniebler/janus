"""The memory index and merged-fact review state, shared by ``memory_service`` and ``memory_review``.

The memory index is the small, always-injected top layer of Janus's memory:
the facts the user pinned, then the facts the memory review confirmed against
their source conversation and promoted. Merging overlapping facts (on-demand
compaction and the memory review) carries what the review learned about the
sources over to the merged fact.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from janus import models, time_utils

#: The memory index injected into every prompt: at most this many facts, and this many characters in all.
INDEX_MAX_FACTS = 15
INDEX_MAX_CHARS = 2_000
#: Characters of one fact's summary shown in the index.
INDEX_FACT_CHARS = 200
#: Verification states a fact must reach before the memory review may promote it to the index.
PROMOTABLE = (models.AgentMemoryVerification.CONFIRMED.value, models.AgentMemoryVerification.CORRECTED.value)
#: How long a checked fact rests before the memory review checks it again: stable facts, facts likely to
#: change within weeks, and facts the transcript did not mention. Halved for a fact read in the last week.
REVIEW_STABLE = timedelta(days=90)
REVIEW_VOLATILE = timedelta(days=14)
REVIEW_UNSUPPORTED = timedelta(days=30)


def _clean(value: object) -> str | None:
    """Collapse whitespace; ``None`` for anything but non-blank text."""
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def index_facts(db: Session, user_id: int) -> list[models.AgentMemoryFact]:
    """The facts of the memory index injected into every prompt, pinned ones first.

    A fact is in the index when it is active and the user pinned it, or the
    memory review promoted it after confirming it against its source and the
    user did not hide it. Bounded by ``INDEX_MAX_FACTS`` and ``INDEX_MAX_CHARS``.
    """
    fact = models.AgentMemoryFact
    pinned = models.AgentMemoryPin.PINNED.value
    rows = (
        db.query(fact)
        .filter(
            fact.user_id == user_id,
            fact.status == models.AgentMemoryStatus.ACTIVE,
            or_(
                fact.pin == pinned,
                (fact.pin.is_(None) & fact.promoted.is_(True) & fact.verification.in_(PROMOTABLE)),
            ),
        )
        .order_by((fact.pin == pinned).desc(), fact.access_count.desc(), fact.updated_at.desc(), fact.id)
        .limit(INDEX_MAX_FACTS)
        .all()
    )
    kept, used = [], 0
    for row in rows:
        used += min(len(row.summary), INDEX_FACT_CHARS) + 8
        if used > INDEX_MAX_CHARS:
            break
        kept.append(row)
    return kept


def apply_compactions(
    db: Session,
    user_id: int,
    facts: Sequence[models.AgentMemoryFact],
    suggestions: Iterable[dict],
) -> tuple[int, int]:
    """Merge each suggested group of ``facts`` into one new fact and archive the group; flushes.

    Returns ``(facts_created, facts_archived)``. A suggestion needs a summary
    and at least two of ``facts`` not used by an earlier suggestion. The merged
    fact keeps what the memory review learned about its sources (see
    :func:`merged_review_state`).
    """
    facts_by_id = {fact.id: fact for fact in facts}
    used_fact_ids: set[int] = set()
    facts_created = 0
    facts_archived = 0
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        raw_source_ids = suggestion.get("source_ids", [])
        if not isinstance(raw_source_ids, Iterable) or isinstance(raw_source_ids, (str, bytes)):
            continue
        source_ids = []
        for sid in raw_source_ids:
            try:
                int_id = int(sid)
            except (ValueError, TypeError):
                continue
            if int_id in facts_by_id and int_id not in used_fact_ids and int_id not in source_ids:
                source_ids.append(int_id)
        summary = _clean(suggestion.get("summary"))
        if summary is None or len(source_ids) < 2:
            continue

        sources = [facts_by_id[source_id] for source_id in source_ids]
        now = time_utils.utcnow()
        merged_fact = models.AgentMemoryFact(
            user_id=user_id,
            summary=summary,
            detail=_clean(suggestion.get("detail")),
            source=models.AgentMemorySource.COMPACTION,
            status=models.AgentMemoryStatus.ACTIVE,
            last_accessed_at=now,
            **merged_review_state(sources, now),
        )
        db.add(merged_fact)
        db.flush()

        for source_fact in sources:
            source_fact.status = models.AgentMemoryStatus.ARCHIVED
            source_fact.compacted_into_id = merged_fact.id
            source_fact.updated_at = now
            used_fact_ids.add(source_fact.id)
            facts_archived += 1
        facts_created += 1
    return facts_created, facts_archived


def merged_review_state(sources: Sequence[models.AgentMemoryFact], now) -> dict:
    """Review fields for a fact merged from ``sources``.

    The newest source conversation (so the fact can be checked again);
    ``confirmed`` when every source was confirmed or corrected, with their
    quotes, resting for ``REVIEW_STABLE``; the summed access count; the index
    promotion of any source; and the sources' pin (pinned wins, then hidden).
    """
    with_conversation = [fact for fact in sources if fact.source_conversation_id is not None]
    newest = max(with_conversation, key=lambda fact: fact.updated_at or now, default=None)
    verified = all(fact.verification in PROMOTABLE for fact in sources)
    quotes = [fact.evidence_quote for fact in sources if fact.evidence_quote]
    pins = {fact.pin for fact in sources}
    pin = next((value.value for value in models.AgentMemoryPin if value.value in pins), None)
    return {
        "source_conversation_id": newest.source_conversation_id if newest is not None else None,
        "verification": (
            models.AgentMemoryVerification.CONFIRMED.value
            if verified
            else models.AgentMemoryVerification.UNVERIFIED.value
        ),
        "verified_at": now if verified else None,
        "review_due_at": now + REVIEW_STABLE if verified else None,
        "evidence_quote": "\n".join(quotes) if verified and quotes else None,
        "access_count": sum(fact.access_count or 0 for fact in sources),
        "promoted": any(fact.promoted for fact in sources),
        "pin": pin,
    }
