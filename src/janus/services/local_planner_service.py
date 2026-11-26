"""Janus-owned planner CRUD, unified reads, and suggestion handling."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from janus import llm_service, models, time_utils
from janus.services.base import BaseService
from janus.services.email_service import EmailService
from janus.services.topic_service import TopicService
from janus.services.topic_text import strip_candidate_topics

_RECENT_EMAIL_CONTEXT_LIMIT = 100
_TASK_CONTEXT_LIMIT = 25
_UPCOMING_EVENT_CONTEXT_DAYS = 42
#: How long a dismissed or applied suggestion keeps the same suggestion from being proposed again.
REVIEWED_SUGGESTION_MEMORY_DAYS = 90
_URGENCY_RANK = {
    models.TaskUrgency.LOW: 0,
    models.TaskUrgency.NORMAL: 1,
    models.TaskUrgency.HIGH: 2,
    models.TaskUrgency.CRITICAL: 3,
}


def _utc_naive(value: datetime | str | None) -> datetime | None:
    """Normalize a timestamp to the naive-UTC shape used by Janus persistence."""
    normalized = time_utils.normalize_timestamp(value)
    if normalized is None:
        return None
    return normalized.astimezone(UTC).replace(tzinfo=None)


_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def normalize_suggestion_text(value: str | None) -> str:
    """Fold a suggestion title or dedupe key for matching: case, punctuation and spacing are ignored."""
    return " ".join(_NON_WORD.sub(" ", (value or "").casefold()).split())


def _suggestion_day(row: models.PlannerSuggestion | dict[str, Any]) -> Any:
    """The calendar day a suggestion is about (due, scheduled, or start), or ``None`` when undated."""
    if isinstance(row, dict):
        moment = row.get("due_at") or row.get("scheduled_for") or row.get("start_time")
        moment = _utc_naive(moment) if moment else None
    else:
        moment = row.due_at or row.scheduled_for or row.start_time
    return moment.date() if moment else None


class LocalPlannerService(BaseService):
    """Manage Janus-owned planner items and planner suggestions."""

    def _now(self) -> datetime:
        return time_utils.utcnow().replace(tzinfo=None)

    def _user_account_ids(self, user_id: int) -> list[int]:
        return [
            account_id
            for (account_id,) in (
                self.db.query(models.PlannerAccount.id).filter(models.PlannerAccount.user_id == user_id).all()
            )
        ]

    @staticmethod
    def _status_value(status: models.TaskStatus | str | None) -> str | None:
        if status is None:
            return None
        if isinstance(status, models.TaskStatus):
            return status.value
        return str(status)

    @staticmethod
    def _source_value(source: models.PlannerItemSource | str | None) -> str | None:
        if source is None:
            return None
        if isinstance(source, models.PlannerItemSource):
            return source.value
        return str(source)

    def _serialize_task(
        self, task: models.PlannerTask, *, children: list[models.PlannerTask] | None = None
    ) -> dict[str, Any]:
        child_rows = task.children if children is None else children
        return {
            "id": task.id,
            "kind": models.PlannerItemKind.TASK.value,
            "source": self._source_value(task.source),
            "source_label": task.source.value.replace("_", " ").title() if task.source else "Manual",
            "read_only": False,
            "title": task.title,
            "description": task.description,
            "status": self._status_value(task.status),
            "urgency": task.urgency.value if task.urgency else models.TaskUrgency.NORMAL.value,
            "parent_task_id": task.parent_task_id,
            "sort_order": task.sort_order,
            "child_count": len(child_rows),
            "open_child_count": sum(child.status != models.TaskStatus.DONE for child in child_rows),
            "priority": task.priority,
            "due_at": task.due_at,
            "scheduled_for": task.scheduled_for,
            "completed_at": task.completed_at,
            "confidence": task.confidence.value if task.confidence else None,
        }

    def _serialize_imported_task(self, task: models.TaskRecord, account_name: str | None = None) -> dict[str, Any]:
        return {
            "id": task.id,
            "kind": models.PlannerItemKind.TASK.value,
            "source": models.PlannerItemSource.IMPORTED.value,
            "source_label": account_name or "Imported",
            "read_only": True,
            "title": task.title,
            "description": None,
            "status": self._status_value(task.status),
            "urgency": models.TaskUrgency.NORMAL.value,
            "parent_task_id": None,
            "sort_order": None,
            "child_count": 0,
            "open_child_count": 0,
            "priority": None,
            "due_at": task.due_date,
            "scheduled_for": None,
            "completed_at": None,
            "confidence": None,
            "external_id": task.external_id,
            "planner_account_id": task.planner_account_id,
        }

    def _serialize_event(self, event: models.PlannerEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "kind": models.PlannerItemKind.EVENT.value,
            "source": self._source_value(event.source),
            "source_label": event.source.value.replace("_", " ").title() if event.source else "Manual",
            "read_only": False,
            "title": event.title,
            "description": event.description,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "location": event.location,
            "all_day": event.all_day,
            "confidence": event.confidence.value if event.confidence else None,
        }

    def _serialize_imported_event(
        self,
        event: models.CalendarEvent,
        account_name: str | None = None,
    ) -> dict[str, Any]:
        return {
            "id": event.id,
            "kind": models.PlannerItemKind.EVENT.value,
            "source": models.PlannerItemSource.IMPORTED.value,
            "source_label": account_name or "Imported",
            "read_only": True,
            "title": event.summary,
            "description": event.description,
            "start_time": event.start_time,
            "end_time": event.end_time,
            "location": event.location,
            "all_day": False,
            "confidence": None,
            "external_id": event.external_id,
            "planner_account_id": event.planner_account_id,
        }

    def _serialize_suggestion(self, suggestion: models.PlannerSuggestion) -> dict[str, Any]:
        return {
            "id": suggestion.id,
            "suggestion_type": suggestion.suggestion_type.value,
            "status": suggestion.status.value,
            "source": suggestion.source.value,
            "confidence": suggestion.confidence.value if suggestion.confidence else None,
            "reason": suggestion.reason,
            "dedupe_key": suggestion.dedupe_key,
            "title": suggestion.title,
            "description": suggestion.description,
            "task_status": suggestion.task_status.value if suggestion.task_status else None,
            "priority": suggestion.priority,
            "due_at": suggestion.due_at,
            "scheduled_for": suggestion.scheduled_for,
            "start_time": suggestion.start_time,
            "end_time": suggestion.end_time,
            "location": suggestion.location,
            "all_day": suggestion.all_day,
            "source_email_id": suggestion.source_email_id,
            "source_email_subject": suggestion.source_email.subject if suggestion.source_email else None,
            "source_email_sender": suggestion.source_email.sender if suggestion.source_email else None,
            "source_topic_id": suggestion.source_topic_id,
            "source_topic_title": suggestion.source_topic.title if suggestion.source_topic else None,
            "target_task_id": suggestion.target_task_id,
            "target_event_id": suggestion.target_event_id,
            "applied_at": suggestion.applied_at,
            "created_at": suggestion.created_at,
            "updated_at": suggestion.updated_at,
        }

    def _event_range(
        self,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
    ) -> tuple[datetime, datetime]:
        now = _utc_naive(start) if start is not None else self._now()
        if now is None:
            now = self._now()
        range_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        range_end = _utc_naive(end) if end is not None else range_start + timedelta(days=1)
        if range_end is None or range_end <= range_start:
            range_end = range_start + timedelta(days=1)
        return range_start, range_end

    def list_tasks(
        self,
        user_id: int,
        *,
        include_completed: bool = False,
        include_imported: bool = True,
        limit: int | None = None,
        root_only: bool = False,
        status: models.TaskStatus | None = None,
        urgency: models.TaskUrgency | None = None,
        due_before: datetime | str | None = None,
        due_after: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        """Return local tasks plus optional imported read-only task context."""
        local_query = self.db.query(models.PlannerTask).filter(models.PlannerTask.user_id == user_id)
        if not include_completed:
            local_query = local_query.filter(models.PlannerTask.status != models.TaskStatus.DONE)
        if root_only:
            local_query = local_query.filter(models.PlannerTask.parent_task_id.is_(None))
        if status is not None:
            local_query = local_query.filter(models.PlannerTask.status == status)
        if urgency is not None:
            local_query = local_query.filter(models.PlannerTask.urgency == urgency)
        normalized_due_before = _utc_naive(due_before)
        normalized_due_after = _utc_naive(due_after)
        if normalized_due_before is not None:
            local_query = local_query.filter(models.PlannerTask.due_at <= normalized_due_before)
        if normalized_due_after is not None:
            local_query = local_query.filter(models.PlannerTask.due_at >= normalized_due_after)
        local_rows = self._sort_tasks(local_query.all())
        if limit is not None:
            local_rows = local_rows[:limit]
        items = [self._serialize_task(row) for row in local_rows]

        if include_imported:
            items.extend(self.list_imported_tasks(user_id, include_completed=include_completed, limit=limit))
        return items

    def list_imported_tasks(
        self, user_id: int, *, include_completed: bool = False, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Return the user's read-only imported planner tasks (Jira, Google Tasks, ...)."""
        account_ids = self._user_account_ids(user_id)
        if not account_ids:
            return []
        account_names = {
            account.id: account.name
            for account in self.db.query(models.PlannerAccount).filter(models.PlannerAccount.id.in_(account_ids)).all()
        }
        imported_query = self.db.query(models.TaskRecord).filter(models.TaskRecord.planner_account_id.in_(account_ids))
        if not include_completed:
            imported_query = imported_query.filter(models.TaskRecord.status != models.TaskStatus.DONE)
        imported_query = imported_query.order_by(
            models.TaskRecord.due_date.asc().nulls_last(), models.TaskRecord.updated_at.desc()
        )
        if limit is not None:
            imported_query = imported_query.limit(limit)
        rows = imported_query.all()
        return [self._serialize_imported_task(row, account_names.get(row.planner_account_id)) for row in rows]

    def _sort_tasks(self, rows: list[models.PlannerTask]) -> list[models.PlannerTask]:
        """Apply the stable default local-task ordering in memory."""
        now = self._now()

        def key(task: models.PlannerTask) -> tuple[Any, ...]:
            due_at = task.due_at
            overdue = due_at is not None and due_at < now and task.status != models.TaskStatus.DONE
            urgency = _URGENCY_RANK.get(task.urgency, _URGENCY_RANK[models.TaskUrgency.NORMAL])
            due_timestamp = due_at.timestamp() if due_at is not None else float("inf")
            updated_timestamp = task.updated_at.timestamp() if task.updated_at is not None else 0
            return (
                task.status == models.TaskStatus.DONE,
                not overdue,
                -urgency,
                task.sort_order is None,
                task.sort_order if task.sort_order is not None else 0,
                due_timestamp,
                -updated_timestamp,
                task.id,
            )

        return sorted(rows, key=key)

    def list_local_task_tree(
        self,
        user_id: int,
        *,
        include_completed: bool = False,
        status: models.TaskStatus | None = None,
        urgency: models.TaskUrgency | None = None,
    ) -> list[dict[str, Any]]:
        """Return a recursively nested, user-scoped local task tree."""
        rows = self._sort_tasks(self.db.query(models.PlannerTask).filter(models.PlannerTask.user_id == user_id).all())
        by_id = {row.id: row for row in rows}

        def matches_filters(row: models.PlannerTask) -> bool:
            if not include_completed and row.status == models.TaskStatus.DONE:
                return False
            if status is not None and row.status != status:
                return False
            if urgency is not None and row.urgency != urgency:  # noqa: SIM103
                return False
            return True

        visible_ids = {row.id for row in rows if matches_filters(row)}
        for row_id in list(visible_ids):
            parent_id = by_id[row_id].parent_task_id
            while parent_id is not None and parent_id not in visible_ids:
                visible_ids.add(parent_id)
                parent = by_id.get(parent_id)
                parent_id = parent.parent_task_id if parent is not None else None
        rows = [row for row in rows if row.id in visible_ids]
        by_parent: dict[int | None, list[models.PlannerTask]] = {}
        for row in rows:
            by_parent.setdefault(row.parent_task_id, []).append(row)

        def serialize(row: models.PlannerTask) -> dict[str, Any]:
            children = by_parent.get(row.id, [])
            payload = self._serialize_task(row, children=children)
            payload["children"] = [serialize(child) for child in children]
            return payload

        return [serialize(row) for row in by_parent.get(None, [])]

    def list_events(
        self,
        user_id: int,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        include_imported: bool = True,
    ) -> list[dict[str, Any]]:
        """Return local events plus optional imported read-only event context."""
        range_start, range_end = self._event_range(start, end)
        local_rows = (
            self.db.query(models.PlannerEvent)
            .filter(
                models.PlannerEvent.user_id == user_id,
                models.PlannerEvent.start_time >= range_start,
                models.PlannerEvent.start_time < range_end,
            )
            .order_by(models.PlannerEvent.start_time.asc())
            .all()
        )
        items = [self._serialize_event(row) for row in local_rows]

        if include_imported:
            account_ids = self._user_account_ids(user_id)
            if account_ids:
                account_names = {
                    account.id: account.name
                    for account in self.db.query(models.PlannerAccount)
                    .filter(models.PlannerAccount.id.in_(account_ids))
                    .all()
                }
                imported_rows = (
                    self.db.query(models.CalendarEvent)
                    .filter(
                        models.CalendarEvent.planner_account_id.in_(account_ids),
                        models.CalendarEvent.start_time >= range_start,
                        models.CalendarEvent.start_time < range_end,
                    )
                    .order_by(models.CalendarEvent.start_time.asc())
                    .all()
                )
                items.extend(
                    self._serialize_imported_event(row, account_names.get(row.planner_account_id))
                    for row in imported_rows
                )
        items.sort(key=lambda item: item["start_time"])
        return items

    def get_local_task(self, user_id: int, task_id: int) -> models.PlannerTask | None:
        """Return one Janus-owned task when it belongs to the user."""
        return (
            self.db.query(models.PlannerTask)
            .filter(models.PlannerTask.user_id == user_id, models.PlannerTask.id == task_id)
            .first()
        )

    def _validate_parent(self, user_id: int, task_id: int | None, parent_task_id: int | None) -> None:
        """Validate ownership and reject self-ancestry before changing a parent."""
        if parent_task_id is None:
            return
        parent = self.get_local_task(user_id, parent_task_id)
        if parent is None:
            raise ValueError("parent task not found for the current user")
        if task_id is not None and parent.id == task_id:
            raise ValueError("a task cannot be its own parent")

        seen: set[int] = set()
        current: models.PlannerTask | None = parent
        while current is not None:
            if current.id in seen:
                raise ValueError("task hierarchy already contains a cycle")
            seen.add(current.id)
            if task_id is not None and current.id == task_id:
                raise ValueError("a task cannot be reparented below one of its descendants")
            current = self.get_local_task(user_id, current.parent_task_id) if current.parent_task_id else None

    def get_local_event(self, user_id: int, event_id: int) -> models.PlannerEvent | None:
        """Return one Janus-owned event when it belongs to the user."""
        return (
            self.db.query(models.PlannerEvent)
            .filter(models.PlannerEvent.user_id == user_id, models.PlannerEvent.id == event_id)
            .first()
        )

    def create_task(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a Janus-owned planner task."""
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("task title must not be empty")
        self._validate_parent(user_id, None, payload.get("parent_task_id"))
        task = models.PlannerTask(
            user_id=user_id,
            title=title,
            description=payload.get("description"),
            status=payload.get("status") or models.TaskStatus.TODO,
            urgency=payload.get("urgency") or models.TaskUrgency.NORMAL,
            parent_task_id=payload.get("parent_task_id"),
            sort_order=payload.get("sort_order"),
            priority=payload.get("priority"),
            due_at=_utc_naive(payload.get("due_at")),
            scheduled_for=_utc_naive(payload.get("scheduled_for")),
            source=payload.get("source") or models.PlannerItemSource.MANUAL,
            confidence=payload.get("confidence"),
        )
        if task.status == models.TaskStatus.DONE:
            task.completed_at = _utc_naive(payload.get("completed_at")) or self._now()
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return self._serialize_task(task)

    def update_task(self, user_id: int, task_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Update a Janus-owned planner task."""
        task = self.get_local_task(user_id, task_id)
        if task is None:
            return None

        if "title" in payload and payload["title"] is not None and not str(payload["title"]).strip():
            raise ValueError("task title must not be empty")
        if "parent_task_id" in payload:
            self._validate_parent(user_id, task_id, payload.get("parent_task_id"))

        if "title" in payload and payload["title"] is not None:
            task.title = str(payload["title"]).strip()
        if "description" in payload:
            task.description = payload.get("description")
        if "priority" in payload:
            task.priority = payload.get("priority")
        if "due_at" in payload:
            task.due_at = _utc_naive(payload.get("due_at"))
        if "scheduled_for" in payload:
            task.scheduled_for = _utc_naive(payload.get("scheduled_for"))
        if "status" in payload and payload["status"] is not None:
            task.status = payload["status"]
            if task.status == models.TaskStatus.DONE:
                task.completed_at = _utc_naive(payload.get("completed_at")) or task.completed_at or self._now()
            else:
                task.completed_at = None
        if "urgency" in payload and payload["urgency"] is not None:
            task.urgency = payload["urgency"]
        if "parent_task_id" in payload:
            task.parent_task_id = payload.get("parent_task_id")
        if "sort_order" in payload:
            task.sort_order = payload.get("sort_order")
        task.updated_at = self._now()
        self.db.commit()
        self.db.refresh(task)
        return self._serialize_task(task)

    def complete_task(self, user_id: int, task_id: int) -> dict[str, Any] | None:
        """Complete exactly one local task; children remain unchanged."""
        return self.update_task(
            user_id,
            task_id,
            {"status": models.TaskStatus.DONE, "completed_at": self._now()},
        )

    def move_task(
        self,
        user_id: int,
        task_id: int,
        *,
        parent_task_id: int | None,
        sort_order: int | None = None,
    ) -> dict[str, Any] | None:
        """Move one local task while validating ownership and cycles atomically."""
        return self.update_task(
            user_id,
            task_id,
            {"parent_task_id": parent_task_id, "sort_order": sort_order},
        )

    def create_event(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a Janus-owned planner event."""
        event = models.PlannerEvent(
            user_id=user_id,
            title=str(payload["title"]).strip(),
            description=payload.get("description"),
            start_time=_utc_naive(payload["start_time"]),
            end_time=_utc_naive(payload["end_time"]),
            location=payload.get("location"),
            all_day=bool(payload.get("all_day", False)),
            source=payload.get("source") or models.PlannerItemSource.MANUAL,
            confidence=payload.get("confidence"),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return self._serialize_event(event)

    def update_event(self, user_id: int, event_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Update a Janus-owned planner event."""
        event = self.get_local_event(user_id, event_id)
        if event is None:
            return None

        if "title" in payload and payload["title"] is not None:
            event.title = str(payload["title"]).strip()
        if "description" in payload:
            event.description = payload.get("description")
        if "start_time" in payload and payload["start_time"] is not None:
            event.start_time = _utc_naive(payload["start_time"])
        if "end_time" in payload and payload["end_time"] is not None:
            event.end_time = _utc_naive(payload["end_time"])
        if "location" in payload:
            event.location = payload.get("location")
        if "all_day" in payload:
            event.all_day = bool(payload.get("all_day"))
        event.updated_at = self._now()
        self.db.commit()
        self.db.refresh(event)
        return self._serialize_event(event)

    def list_suggestions(
        self,
        user_id: int,
        *,
        status: models.PlannerSuggestionStatus | None = models.PlannerSuggestionStatus.PENDING,
    ) -> list[dict[str, Any]]:
        """Return planner suggestions for one user."""
        query = (
            self.db.query(models.PlannerSuggestion)
            .filter(models.PlannerSuggestion.user_id == user_id)
            .order_by(models.PlannerSuggestion.created_at.desc())
        )
        if status is not None:
            query = query.filter(models.PlannerSuggestion.status == status)
        return [self._serialize_suggestion(row) for row in query.all()]

    def get_suggestion(self, user_id: int, suggestion_id: int) -> models.PlannerSuggestion | None:
        """Return one planner suggestion when it belongs to the user."""
        return (
            self.db.query(models.PlannerSuggestion)
            .filter(models.PlannerSuggestion.user_id == user_id, models.PlannerSuggestion.id == suggestion_id)
            .first()
        )

    def update_suggestion(self, user_id: int, suggestion_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Edit a planner suggestion before review."""
        suggestion = self.get_suggestion(user_id, suggestion_id)
        if suggestion is None:
            return None

        for field in ("title", "description", "priority", "reason", "location", "dedupe_key"):
            if field in payload:
                setattr(suggestion, field, payload.get(field))
        if "task_status" in payload and payload["task_status"] is not None:
            suggestion.task_status = payload["task_status"]
        if "due_at" in payload:
            suggestion.due_at = _utc_naive(payload.get("due_at"))
        if "scheduled_for" in payload:
            suggestion.scheduled_for = _utc_naive(payload.get("scheduled_for"))
        if "start_time" in payload:
            suggestion.start_time = _utc_naive(payload.get("start_time"))
        if "end_time" in payload:
            suggestion.end_time = _utc_naive(payload.get("end_time"))
        if "all_day" in payload:
            suggestion.all_day = bool(payload.get("all_day"))
        suggestion.updated_at = self._now()
        self.db.commit()
        self.db.refresh(suggestion)
        return self._serialize_suggestion(suggestion)

    def dismiss_suggestion(self, user_id: int, suggestion_id: int) -> dict[str, Any] | None:
        """Dismiss a planner suggestion without applying it."""
        suggestion = self.get_suggestion(user_id, suggestion_id)
        if suggestion is None:
            return None
        suggestion.status = models.PlannerSuggestionStatus.DISMISSED
        suggestion.updated_at = self._now()
        self.db.commit()
        self.db.refresh(suggestion)
        return self._serialize_suggestion(suggestion)

    def _pending_suggestion_query(
        self,
        user_id: int,
        *,
        suggestion_ids: list[int] | None = None,
        text: str | None = None,
        suggestion_type: models.PlannerSuggestionType | None = None,
        source_email_id: int | None = None,
        created_before: datetime | str | None = None,
    ):
        query = self.db.query(models.PlannerSuggestion).filter(
            models.PlannerSuggestion.user_id == user_id,
            models.PlannerSuggestion.status == models.PlannerSuggestionStatus.PENDING,
        )
        if suggestion_ids is not None:
            query = query.filter(models.PlannerSuggestion.id.in_(suggestion_ids))
        if suggestion_type is not None:
            query = query.filter(models.PlannerSuggestion.suggestion_type == suggestion_type)
        if source_email_id is not None:
            query = query.filter(models.PlannerSuggestion.source_email_id == source_email_id)
        if created_before is not None:
            query = query.filter(models.PlannerSuggestion.created_at < _utc_naive(created_before))
        rows = query.order_by(models.PlannerSuggestion.created_at.desc(), models.PlannerSuggestion.id.desc()).all()
        needle = normalize_suggestion_text(text)
        if needle:
            rows = [
                row
                for row in rows
                if needle in normalize_suggestion_text(" ".join(filter(None, (row.title, row.description, row.reason))))
            ]
        return rows

    def search_pending_suggestions(
        self,
        user_id: int,
        *,
        text: str | None = None,
        suggestion_type: models.PlannerSuggestionType | None = None,
        source_email_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return pending suggestions, newest first, narrowed by text (title/description/reason), type, or email."""
        rows = self._pending_suggestion_query(
            user_id, text=text, suggestion_type=suggestion_type, source_email_id=source_email_id
        )
        return [self._serialize_suggestion(row) for row in rows]

    def dismiss_suggestions(
        self,
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

        At least one filter is required unless ``all_pending`` is set, so an
        empty call can never clear the whole review list by accident.
        """
        has_filter = any(
            value is not None for value in (suggestion_ids, suggestion_type, source_email_id, created_before)
        ) or bool(normalize_suggestion_text(text))
        if not has_filter and not all_pending:
            raise ValueError("give suggestion ids or a filter, or set all_pending to dismiss every pending suggestion")
        rows = self._pending_suggestion_query(
            user_id,
            suggestion_ids=suggestion_ids,
            text=text,
            suggestion_type=suggestion_type,
            source_email_id=source_email_id,
            created_before=created_before,
        )
        now = self._now()
        for row in rows:
            row.status = models.PlannerSuggestionStatus.DISMISSED
            row.updated_at = now
        self.db.commit()
        return [row.id for row in rows]

    def restore_suggestions(self, user_id: int, suggestion_ids: list[int]) -> list[int]:
        """Put dismissed suggestions back into the review list; returns the restored ids."""
        rows = (
            self.db.query(models.PlannerSuggestion)
            .filter(
                models.PlannerSuggestion.user_id == user_id,
                models.PlannerSuggestion.id.in_(suggestion_ids),
                models.PlannerSuggestion.status == models.PlannerSuggestionStatus.DISMISSED,
            )
            .all()
        )
        now = self._now()
        for row in rows:
            row.status = models.PlannerSuggestionStatus.PENDING
            row.updated_at = now
        self.db.commit()
        return [row.id for row in rows]

    def duplicate_suggestion_groups(self, user_id: int) -> list[list[models.PlannerSuggestion]]:
        """Group pending suggestions that propose the same change, newest first within each group.

        Two suggestions are duplicates when they share a dedupe key, or have
        the same type and title, ignoring case, punctuation and spacing.
        """
        rows = self._pending_suggestion_query(user_id)
        parent = list(range(len(rows)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        first_by_key: dict[tuple[str, ...], int] = {}
        for index, row in enumerate(rows):
            keys = []
            dedupe_key = normalize_suggestion_text(row.dedupe_key)
            if dedupe_key:
                keys.append(("key", dedupe_key))
            title = normalize_suggestion_text(row.title)
            if title:
                keys.append(("title", row.suggestion_type.value, title))
            for key in keys:
                if key in first_by_key:
                    parent[find(index)] = find(first_by_key[key])
                else:
                    first_by_key[key] = index
        groups: dict[int, list[models.PlannerSuggestion]] = {}
        for index, row in enumerate(rows):
            groups.setdefault(find(index), []).append(row)
        return [group for group in groups.values() if len(group) > 1]

    def dismiss_duplicate_suggestions(self, user_id: int) -> dict[str, Any]:
        """Keep the newest suggestion of every duplicate group and dismiss the rest."""
        groups = self.duplicate_suggestion_groups(user_id)
        now = self._now()
        dismissed: list[int] = []
        for group in groups:
            for row in group[1:]:
                row.status = models.PlannerSuggestionStatus.DISMISSED
                row.updated_at = now
                dismissed.append(row.id)
        self.db.commit()
        return {"groups": len(groups), "kept_ids": [group[0].id for group in groups], "dismissed_ids": dismissed}

    def _ensure_link(
        self,
        *,
        user_id: int,
        planner_task_id: int | None = None,
        planner_event_id: int | None = None,
        source_type: models.PlannerLinkSource,
        source_id: int | None = None,
        source_detail: str | None = None,
    ) -> None:
        query = self.db.query(models.PlannerLink).filter(
            models.PlannerLink.user_id == user_id,
            models.PlannerLink.source_type == source_type,
            models.PlannerLink.source_id == source_id,
        )
        if planner_task_id is not None:
            query = query.filter(models.PlannerLink.planner_task_id == planner_task_id)
        else:
            query = query.filter(models.PlannerLink.planner_task_id.is_(None))
        if planner_event_id is not None:
            query = query.filter(models.PlannerLink.planner_event_id == planner_event_id)
        else:
            query = query.filter(models.PlannerLink.planner_event_id.is_(None))
        if query.first() is not None:
            return

        self.db.add(
            models.PlannerLink(
                user_id=user_id,
                planner_task_id=planner_task_id,
                planner_event_id=planner_event_id,
                source_type=source_type,
                source_id=source_id,
                source_detail=source_detail,
            )
        )

    def approve_suggestion(self, user_id: int, suggestion_id: int) -> dict[str, Any] | None:
        """Approve a suggestion and apply it to local planner state."""
        suggestion = self.get_suggestion(user_id, suggestion_id)
        if suggestion is None:
            return None

        suggestion.status = models.PlannerSuggestionStatus.APPROVED
        payload: dict[str, Any]

        if suggestion.suggestion_type == models.PlannerSuggestionType.TASK:
            payload = self.create_task(
                user_id,
                {
                    "title": suggestion.title or "Untitled task",
                    "description": suggestion.description,
                    "status": suggestion.task_status or models.TaskStatus.TODO,
                    "priority": suggestion.priority,
                    "due_at": suggestion.due_at,
                    "scheduled_for": suggestion.scheduled_for,
                    "source": suggestion.source,
                    "confidence": suggestion.confidence,
                },
            )
            suggestion.target_task_id = int(payload["id"])
        elif suggestion.suggestion_type == models.PlannerSuggestionType.EVENT:
            payload = self.create_event(
                user_id,
                {
                    "title": suggestion.title or "Untitled event",
                    "description": suggestion.description,
                    "start_time": suggestion.start_time,
                    "end_time": suggestion.end_time or suggestion.start_time,
                    "location": suggestion.location,
                    "all_day": suggestion.all_day,
                    "source": suggestion.source,
                    "confidence": suggestion.confidence,
                },
            )
            suggestion.target_event_id = int(payload["id"])
        elif suggestion.suggestion_type == models.PlannerSuggestionType.TASK_UPDATE and suggestion.target_task_id:
            payload = self.update_task(
                user_id,
                suggestion.target_task_id,
                {
                    "title": suggestion.title,
                    "description": suggestion.description,
                    "status": suggestion.task_status,
                    "priority": suggestion.priority,
                    "due_at": suggestion.due_at,
                    "scheduled_for": suggestion.scheduled_for,
                },
            )
            if payload is None:
                return None
        elif suggestion.suggestion_type == models.PlannerSuggestionType.EVENT_UPDATE and suggestion.target_event_id:
            payload = self.update_event(
                user_id,
                suggestion.target_event_id,
                {
                    "title": suggestion.title,
                    "description": suggestion.description,
                    "start_time": suggestion.start_time,
                    "end_time": suggestion.end_time,
                    "location": suggestion.location,
                    "all_day": suggestion.all_day,
                },
            )
            if payload is None:
                return None
        else:
            return None

        suggestion.status = models.PlannerSuggestionStatus.APPLIED
        suggestion.applied_at = self._now()
        suggestion.updated_at = self._now()

        if suggestion.target_task_id is not None:
            if suggestion.source_email_id is not None:
                self._ensure_link(
                    user_id=user_id,
                    planner_task_id=suggestion.target_task_id,
                    source_type=models.PlannerLinkSource.EMAIL,
                    source_id=suggestion.source_email_id,
                    source_detail=suggestion.source_email.subject if suggestion.source_email else None,
                )
            if suggestion.source_topic_id is not None:
                self._ensure_link(
                    user_id=user_id,
                    planner_task_id=suggestion.target_task_id,
                    source_type=models.PlannerLinkSource.TOPIC,
                    source_id=suggestion.source_topic_id,
                    source_detail=suggestion.source_topic.title if suggestion.source_topic else None,
                )
        if suggestion.target_event_id is not None:
            if suggestion.source_email_id is not None:
                self._ensure_link(
                    user_id=user_id,
                    planner_event_id=suggestion.target_event_id,
                    source_type=models.PlannerLinkSource.EMAIL,
                    source_id=suggestion.source_email_id,
                    source_detail=suggestion.source_email.subject if suggestion.source_email else None,
                )
            if suggestion.source_topic_id is not None:
                self._ensure_link(
                    user_id=user_id,
                    planner_event_id=suggestion.target_event_id,
                    source_type=models.PlannerLinkSource.TOPIC,
                    source_id=suggestion.source_topic_id,
                    source_detail=suggestion.source_topic.title if suggestion.source_topic else None,
                )

        self.db.commit()
        self.db.refresh(suggestion)
        return self._serialize_suggestion(suggestion)

    def build_context_data(
        self,
        user_id: int,
        *,
        recent_email_limit: int = _RECENT_EMAIL_CONTEXT_LIMIT,
    ) -> dict[str, Any]:
        """Build planner-aware context for mail and digest suggestion extraction."""
        now = self._now()
        event_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=_UPCOMING_EVENT_CONTEXT_DAYS
        )
        user = self.db.get(models.User, user_id)
        recent_emails = (
            self.db.query(models.EmailRecord)
            .join(models.EmailAccount)
            .filter(models.EmailAccount.user_id == user_id)
            .order_by(models.EmailRecord.received_at.desc())
            .limit(recent_email_limit)
            .all()
        )
        return {
            "local_tasks": self.list_tasks(
                user_id,
                include_completed=False,
                include_imported=False,
                limit=_TASK_CONTEXT_LIMIT,
            ),
            "local_events": self.list_events(user_id, start=now, end=event_end, include_imported=False),
            "imported_tasks": [
                item
                for item in self.list_tasks(
                    user_id,
                    include_completed=False,
                    include_imported=True,
                    limit=_TASK_CONTEXT_LIMIT,
                )
                if item["read_only"]
            ],
            "imported_events": [
                item
                for item in self.list_events(user_id, start=now, end=event_end, include_imported=True)
                if item["read_only"]
            ],
            "pending_suggestions": self.list_suggestions(user_id),
            "active_topics": TopicService(self.db).build_context_topic_summary(user_id=user_id),
            "recent_emails": strip_candidate_topics(EmailService(self.db).serialize_emails(recent_emails, user=user)),
        }

    def _topic_for_email(self, email_id: int, user_id: int | None) -> models.TrackedTopic | None:
        if user_id is None:
            return None
        return (
            self.db.query(models.TrackedTopic)
            .join(models.TopicEvidence, models.TopicEvidence.topic_id == models.TrackedTopic.id)
            .filter(
                models.TrackedTopic.user_id == user_id,
                models.TopicEvidence.source_type == models.TopicEvidenceSource.EMAIL,
                models.TopicEvidence.source_id == email_id,
            )
            .order_by(models.TrackedTopic.last_seen_at.desc())
            .first()
        )

    def _default_user_for_email(self, email: models.EmailRecord) -> models.User | None:
        account = self.db.get(models.EmailAccount, email.account_id)
        if account is not None and account.user_id is not None:
            return self.db.get(models.User, account.user_id)
        return TopicService(self.db).get_default_user()

    @staticmethod
    def _same_suggestion(row: models.PlannerSuggestion, payload: dict[str, Any]) -> bool:
        """Whether ``row`` proposes the same planner change as ``payload``.

        The model words a suggestion anew for every email that mentions it, so
        exact matches let the same task pile up: dedupe keys and titles are
        compared case-, punctuation- and spacing-insensitively, and a title
        match requires the same suggestion type.
        """
        key = normalize_suggestion_text(payload.get("dedupe_key"))
        if key and key == normalize_suggestion_text(row.dedupe_key):
            return True
        title = normalize_suggestion_text(payload.get("title"))
        return (
            bool(title)
            and row.suggestion_type == payload["suggestion_type"]
            and (title == normalize_suggestion_text(row.title))
        )

    def _matching_pending_suggestion(self, payload: dict[str, Any]) -> models.PlannerSuggestion | None:
        query = self.db.query(models.PlannerSuggestion).filter(
            models.PlannerSuggestion.user_id == payload["user_id"],
            models.PlannerSuggestion.status == models.PlannerSuggestionStatus.PENDING,
        )
        if payload.get("target_task_id") is not None and payload["suggestion_type"] in (
            models.PlannerSuggestionType.TASK,
            models.PlannerSuggestionType.TASK_UPDATE,
        ):
            match = query.filter(
                models.PlannerSuggestion.target_task_id == payload["target_task_id"],
                models.PlannerSuggestion.suggestion_type.in_(
                    [models.PlannerSuggestionType.TASK, models.PlannerSuggestionType.TASK_UPDATE]
                ),
            ).first()
            if match is not None:
                return match
        if payload.get("target_event_id") is not None and payload["suggestion_type"] in (
            models.PlannerSuggestionType.EVENT,
            models.PlannerSuggestionType.EVENT_UPDATE,
        ):
            match = query.filter(
                models.PlannerSuggestion.target_event_id == payload["target_event_id"],
                models.PlannerSuggestion.suggestion_type.in_(
                    [models.PlannerSuggestionType.EVENT, models.PlannerSuggestionType.EVENT_UPDATE]
                ),
            ).first()
            if match is not None:
                return match
        rows = query.order_by(models.PlannerSuggestion.created_at.desc(), models.PlannerSuggestion.id.desc()).all()
        return next((row for row in rows if self._same_suggestion(row, payload)), None)

    def _matching_reviewed_suggestion(self, payload: dict[str, Any]) -> models.PlannerSuggestion | None:
        """A recently dismissed or applied suggestion for the same change on the same day, which must not come back."""
        day = _suggestion_day(payload)
        rows = (
            self.db.query(models.PlannerSuggestion)
            .filter(
                models.PlannerSuggestion.user_id == payload["user_id"],
                models.PlannerSuggestion.status.in_(
                    [models.PlannerSuggestionStatus.DISMISSED, models.PlannerSuggestionStatus.APPLIED]
                ),
                models.PlannerSuggestion.updated_at >= self._now() - timedelta(days=REVIEWED_SUGGESTION_MEMORY_DAYS),
            )
            .order_by(models.PlannerSuggestion.updated_at.desc(), models.PlannerSuggestion.id.desc())
            .all()
        )
        return next(
            (row for row in rows if self._same_suggestion(row, payload) and _suggestion_day(row) == day),
            None,
        )

    def _upsert_suggestion(self, payload: dict[str, Any]) -> models.PlannerSuggestion:
        """Merge ``payload`` into the matching pending suggestion, or record a new one.

        A suggestion the user already dismissed or applied for the same day is
        returned unchanged instead of being proposed again.
        """
        suggestion = self._matching_pending_suggestion(payload)
        if suggestion is None:
            reviewed = self._matching_reviewed_suggestion(payload)
            if reviewed is not None:
                return reviewed
        identity_fields: tuple[str, ...] = ("dedupe_key", "title")
        if suggestion is None:
            suggestion = models.PlannerSuggestion(
                user_id=payload["user_id"],
                suggestion_type=payload["suggestion_type"],
            )
            self.db.add(suggestion)
        else:
            # Keep the first wording: it is what later emails are matched against and what the
            # mail run is shown, so a rewording must not move the target out from under them.
            identity_fields = tuple(field for field in identity_fields if not getattr(suggestion, field))
        for field in identity_fields:
            if field in payload:
                setattr(suggestion, field, payload.get(field))

        for field in (
            "status",
            "source",
            "confidence",
            "reason",
            "description",
            "task_status",
            "priority",
            "source_email_id",
            "source_topic_id",
            "target_task_id",
            "target_event_id",
        ):
            if field in payload:
                setattr(suggestion, field, payload.get(field))
        for field in ("due_at", "scheduled_for", "start_time", "end_time"):
            if field in payload:
                setattr(suggestion, field, _utc_naive(payload.get(field)))
        if "location" in payload:
            suggestion.location = payload.get("location")
        if "all_day" in payload:
            suggestion.all_day = bool(payload.get("all_day"))
        suggestion.updated_at = self._now()
        return suggestion

    @staticmethod
    def _parse_confidence(value: object) -> models.ConfidenceLevel | None:
        if isinstance(value, models.ConfidenceLevel):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for item in models.ConfidenceLevel:
            if cleaned in {item.value.lower(), item.name.lower()}:
                return item
        return None

    @staticmethod
    def _parse_task_status(value: object) -> models.TaskStatus | None:
        if isinstance(value, models.TaskStatus):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for item in models.TaskStatus:
            if cleaned in {item.value.lower(), item.name.lower()}:
                return item
        return None

    @staticmethod
    def _parse_suggestion_type(value: object) -> models.PlannerSuggestionType | None:
        if isinstance(value, models.PlannerSuggestionType):
            return value
        if not isinstance(value, str):
            return None
        cleaned = value.strip().lower()
        for item in models.PlannerSuggestionType:
            if cleaned in {item.value.lower(), item.name.lower()}:
                return item
        return None

    def _normalize_suggestion_payload(
        self,
        *,
        user_id: int,
        source: models.PlannerItemSource,
        suggestion: dict[str, Any],
        source_email_id: int | None = None,
        source_topic_id: int | None = None,
    ) -> dict[str, Any] | None:
        suggestion_type = self._parse_suggestion_type(suggestion.get("suggestion_type") or suggestion.get("type"))
        if suggestion_type is None:
            return None

        return {
            "user_id": user_id,
            "suggestion_type": suggestion_type,
            "status": models.PlannerSuggestionStatus.PENDING,
            "source": source,
            "confidence": self._parse_confidence(suggestion.get("confidence")),
            "reason": suggestion.get("reason"),
            "dedupe_key": suggestion.get("dedupe_hint") or suggestion.get("dedupe_key"),
            "title": suggestion.get("title"),
            "description": suggestion.get("description"),
            "task_status": self._parse_task_status(suggestion.get("task_status")),
            "priority": suggestion.get("priority"),
            "due_at": suggestion.get("due_at"),
            "scheduled_for": suggestion.get("scheduled_for"),
            "start_time": suggestion.get("start_time"),
            "end_time": suggestion.get("end_time"),
            "location": suggestion.get("location"),
            "all_day": bool(suggestion.get("all_day", False)),
            "source_email_id": source_email_id,
            "source_topic_id": source_topic_id,
            "target_task_id": suggestion.get("target_task_id"),
            "target_event_id": suggestion.get("target_event_id"),
        }

    def capture_email_suggestions(self, email: models.EmailRecord) -> list[dict[str, Any]]:
        """Extract and upsert pending planner suggestions from one email."""
        user = self._default_user_for_email(email)
        if user is None:
            return []

        planner_context = self.build_context_data(user.id)
        extracted = llm_service.LLMService(db=self.db).extract_planner_suggestions(
            {
                "message_id": email.message_id,
                "sender": email.sender,
                "subject": email.subject,
                "snippet": email.snippet,
                "body": email.body,
                "priority": email.priority.value if email.priority else None,
            },
            planner_context=planner_context,
        )
        return self.record_email_suggestions(email, extracted, user_id=user.id)

    def record_email_suggestions(
        self, email: models.EmailRecord, suggestions: list[dict[str, Any]], *, user_id: int
    ) -> list[dict[str, Any]]:
        """Upsert pending planner suggestions for one email (from the extractor or the mail tools)."""
        topic = self._topic_for_email(email.id, user_id)
        rows: list[models.PlannerSuggestion] = []
        for item in suggestions:
            normalized = self._normalize_suggestion_payload(
                user_id=user_id,
                source=models.PlannerItemSource.EMAIL_AGENT,
                suggestion=item,
                source_email_id=email.id,
                source_topic_id=topic.id if topic is not None else None,
            )
            if normalized is None:
                continue
            rows.append(self._upsert_suggestion(normalized))

        if rows:
            self.db.commit()
            for row in rows:
                self.db.refresh(row)
        return [self._serialize_suggestion(row) for row in rows]
