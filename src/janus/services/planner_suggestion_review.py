"""Matching and bulk review of planner suggestions.

Mail processing and the afternoon digest word a suggestion anew for every email
that mentions it, so exact matching let the same task pile up. This module owns:

* **Matching** for ``LocalPlannerService._upsert_suggestion``: a new suggestion
  merges into a pending one that targets the same task/event, shares its dedupe
  key, or has the same type and title, with keys and titles compared after
  :func:`normalize_suggestion_text` (case, punctuation and spacing ignored). One
  the user dismissed or applied within :data:`REVIEWED_SUGGESTION_MEMORY_DAYS`
  for the same day is not proposed again.
* **Bulk review**: search, dismiss by ids or filters, collapse duplicate groups
  to their newest copy, and restore dismissed suggestions. Nothing here changes
  planner tasks or events.

Every function is scoped to one owner.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Query, Session

from janus import models, time_utils

#: How long a dismissed or applied suggestion keeps the same suggestion from being proposed again.
REVIEWED_SUGGESTION_MEMORY_DAYS = 90

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_TASK_TYPES = (models.PlannerSuggestionType.TASK, models.PlannerSuggestionType.TASK_UPDATE)
_EVENT_TYPES = (models.PlannerSuggestionType.EVENT, models.PlannerSuggestionType.EVENT_UPDATE)


def _now() -> datetime:
    return time_utils.utcnow().replace(tzinfo=None)


def _utc_naive(value: datetime | str | None) -> datetime | None:
    normalized = time_utils.normalize_timestamp(value)
    return normalized.astimezone(UTC).replace(tzinfo=None) if normalized is not None else None


def normalize_suggestion_text(value: str | None) -> str:
    """Fold a suggestion title or dedupe key for matching: case, punctuation and spacing are ignored."""
    return " ".join(_NON_WORD.sub(" ", (value or "").casefold()).split())


def _suggestion_day(row: models.PlannerSuggestion | dict[str, Any]) -> date | None:
    """The calendar day a suggestion is about (due, scheduled, or start), or ``None`` when undated."""
    if isinstance(row, dict):
        moment = _utc_naive(row.get("due_at") or row.get("scheduled_for") or row.get("start_time"))
    else:
        moment = row.due_at or row.scheduled_for or row.start_time
    return moment.date() if moment else None


def _targets_conflict(row: models.PlannerSuggestion, payload: dict[str, Any]) -> bool:
    """Whether both name a planner task or event and the two differ: updates to different items never merge."""
    return any(
        payload.get(field) is not None and getattr(row, field) is not None and payload[field] != getattr(row, field)
        for field in ("target_task_id", "target_event_id")
    )


def _same_suggestion(row: models.PlannerSuggestion, payload: dict[str, Any]) -> bool:
    """Whether ``row`` proposes the same planner change as ``payload`` (same key, or same type and title).

    Never for two suggestions aimed at different planner items, even with the same title
    (two separate "Weekly review" events).
    """
    if _targets_conflict(row, payload):
        return False
    key = normalize_suggestion_text(payload.get("dedupe_key"))
    if key and key == normalize_suggestion_text(row.dedupe_key):
        return True
    title = normalize_suggestion_text(payload.get("title"))
    return (
        bool(title)
        and row.suggestion_type == payload["suggestion_type"]
        and title == normalize_suggestion_text(row.title)
    )


def _owned(db: Session, user_id: int) -> Query:
    return db.query(models.PlannerSuggestion).filter(models.PlannerSuggestion.user_id == user_id)


def _newest_first(query: Query) -> list[models.PlannerSuggestion]:
    return query.order_by(models.PlannerSuggestion.created_at.desc(), models.PlannerSuggestion.id.desc()).all()


def matching_pending_suggestion(db: Session, payload: dict[str, Any]) -> models.PlannerSuggestion | None:
    """The pending suggestion ``payload`` should update instead of being added beside it."""
    pending = _owned(db, payload["user_id"]).filter(
        models.PlannerSuggestion.status == models.PlannerSuggestionStatus.PENDING
    )
    for target_field, types in (("target_task_id", _TASK_TYPES), ("target_event_id", _EVENT_TYPES)):
        if payload.get(target_field) is not None and payload["suggestion_type"] in types:
            match = pending.filter(
                getattr(models.PlannerSuggestion, target_field) == payload[target_field],
                models.PlannerSuggestion.suggestion_type.in_(types),
            ).first()
            if match is not None:
                return match
    return next((row for row in _newest_first(pending) if _same_suggestion(row, payload)), None)


def matching_reviewed_suggestion(db: Session, payload: dict[str, Any]) -> models.PlannerSuggestion | None:
    """A recently dismissed or applied suggestion for the same change on the same day, which must not come back."""
    day = _suggestion_day(payload)
    rows = (
        _owned(db, payload["user_id"])
        .filter(
            models.PlannerSuggestion.status.in_(
                [models.PlannerSuggestionStatus.DISMISSED, models.PlannerSuggestionStatus.APPLIED]
            ),
            models.PlannerSuggestion.updated_at >= _now() - timedelta(days=REVIEWED_SUGGESTION_MEMORY_DAYS),
        )
        .order_by(models.PlannerSuggestion.updated_at.desc(), models.PlannerSuggestion.id.desc())
        .all()
    )
    return next((row for row in rows if _same_suggestion(row, payload) and _suggestion_day(row) == day), None)


def record_suggestion(
    db: Session, user_id: int, item: dict[str, Any], *, source: models.PlannerItemSource
) -> dict[str, Any] | None:
    """Record one pending suggestion that no email caused, with the same dedupe as mail suggestions.

    Returns the stored (or matched) suggestion, or ``None`` when ``suggestion_type`` is unknown. A
    suggestion matching one the user dismissed or applied recently comes back with that status.
    """
    from janus.services.local_planner_service import LocalPlannerService  # lazy: it imports this module

    service = LocalPlannerService(db)
    payload = service._normalize_suggestion_payload(user_id=user_id, source=source, suggestion=item)  # noqa: SLF001
    if payload is None:
        return None
    row = service._upsert_suggestion(payload)  # noqa: SLF001 - the one upsert, shared with the mail path
    db.commit()
    db.refresh(row)
    return service._serialize_suggestion(row)  # noqa: SLF001


def _pending(
    db: Session,
    user_id: int,
    *,
    suggestion_ids: list[int] | None = None,
    text: str | None = None,
    suggestion_type: models.PlannerSuggestionType | None = None,
    source_email_id: int | None = None,
    created_before: datetime | str | None = None,
) -> list[models.PlannerSuggestion]:
    query = _owned(db, user_id).filter(models.PlannerSuggestion.status == models.PlannerSuggestionStatus.PENDING)
    if suggestion_ids is not None:
        query = query.filter(models.PlannerSuggestion.id.in_(suggestion_ids))
    if suggestion_type is not None:
        query = query.filter(models.PlannerSuggestion.suggestion_type == suggestion_type)
    if source_email_id is not None:
        query = query.filter(models.PlannerSuggestion.source_email_id == source_email_id)
    if created_before is not None:
        query = query.filter(models.PlannerSuggestion.created_at < _utc_naive(created_before))
    rows = _newest_first(query)
    needle = normalize_suggestion_text(text)
    if needle:
        rows = [
            row
            for row in rows
            if needle in normalize_suggestion_text(" ".join(filter(None, (row.title, row.description, row.reason))))
        ]
    return rows


def search_pending_suggestions(
    db: Session,
    user_id: int,
    *,
    text: str | None = None,
    suggestion_type: models.PlannerSuggestionType | None = None,
    source_email_id: int | None = None,
) -> list[dict[str, Any]]:
    """Return pending suggestions, newest first, narrowed by text (title/description/reason), type, or email."""
    from janus.services.local_planner_service import LocalPlannerService  # lazy: it imports this module

    serialize = LocalPlannerService(db)._serialize_suggestion  # noqa: SLF001 - the one suggestion serializer
    rows = _pending(db, user_id, text=text, suggestion_type=suggestion_type, source_email_id=source_email_id)
    return [serialize(row) for row in rows]


def _set_status(db: Session, rows: list[models.PlannerSuggestion], status: models.PlannerSuggestionStatus) -> None:
    now = _now()
    for row in rows:
        row.status = status
        row.updated_at = now
    db.commit()


def dismiss_suggestions(
    db: Session,
    user_id: int,
    *,
    suggestion_ids: list[int] | None = None,
    text: str | None = None,
    suggestion_type: models.PlannerSuggestionType | None = None,
    source_email_id: int | None = None,
    created_before: datetime | str | None = None,
    all_pending: bool = False,
) -> list[int]:
    """Dismiss every pending suggestion matching all given filters; returns the dismissed ids.

    At least one filter is required unless ``all_pending`` is set, so an empty
    call can never clear the whole review list by accident.
    """
    has_filter = any(
        value is not None for value in (suggestion_ids, suggestion_type, source_email_id, created_before)
    ) or bool(normalize_suggestion_text(text))
    if not has_filter and not all_pending:
        raise ValueError("give suggestion ids or a filter, or set all_pending to dismiss every pending suggestion")
    rows = _pending(
        db,
        user_id,
        suggestion_ids=suggestion_ids,
        text=text,
        suggestion_type=suggestion_type,
        source_email_id=source_email_id,
        created_before=created_before,
    )
    _set_status(db, rows, models.PlannerSuggestionStatus.DISMISSED)
    return [row.id for row in rows]


def restore_suggestions(db: Session, user_id: int, suggestion_ids: list[int]) -> list[int]:
    """Put dismissed suggestions back into the review list; returns the restored ids."""
    rows = (
        _owned(db, user_id)
        .filter(
            models.PlannerSuggestion.id.in_(suggestion_ids),
            models.PlannerSuggestion.status == models.PlannerSuggestionStatus.DISMISSED,
        )
        .all()
    )
    _set_status(db, rows, models.PlannerSuggestionStatus.PENDING)
    return [row.id for row in rows]


def duplicate_suggestion_groups(db: Session, user_id: int) -> list[list[models.PlannerSuggestion]]:
    """Group pending suggestions that propose the same change, newest first within each group.

    Two suggestions are duplicates when they share a dedupe key, or have the
    same type and title, ignoring case, punctuation and spacing, and aim at the
    same planner item (or none).
    """
    rows = _pending(db, user_id)
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    first_by_key: dict[tuple[str, ...], int] = {}
    for index, row in enumerate(rows):
        keys: list[tuple[str, ...]] = []
        if dedupe_key := normalize_suggestion_text(row.dedupe_key):
            keys.append(("key", dedupe_key))
        if title := normalize_suggestion_text(row.title):
            keys.append(("title", row.suggestion_type.value, title))
        # A key shared by suggestions aimed at different planner items does not make them duplicates.
        target = f"{row.target_task_id or ''}:{row.target_event_id or ''}"
        keys = [(*key, target) for key in keys]
        for key in keys:
            if key in first_by_key:
                parent[find(index)] = find(first_by_key[key])
            else:
                first_by_key[key] = index
    groups: dict[int, list[models.PlannerSuggestion]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(find(index), []).append(row)
    return [group for group in groups.values() if len(group) > 1]


def dismiss_duplicate_suggestions(db: Session, user_id: int) -> dict[str, Any]:
    """Keep the newest suggestion of every duplicate group and dismiss the rest."""
    groups = duplicate_suggestion_groups(db, user_id)
    duplicates = [row for group in groups for row in group[1:]]
    _set_status(db, duplicates, models.PlannerSuggestionStatus.DISMISSED)
    return {
        "groups": len(groups),
        "kept_ids": [group[0].id for group in groups],
        "dismissed_ids": [row.id for row in duplicates],
    }


__all__ = [
    "REVIEWED_SUGGESTION_MEMORY_DAYS",
    "dismiss_duplicate_suggestions",
    "dismiss_suggestions",
    "duplicate_suggestion_groups",
    "matching_pending_suggestion",
    "matching_reviewed_suggestion",
    "normalize_suggestion_text",
    "record_suggestion",
    "restore_suggestions",
    "search_pending_suggestions",
]
