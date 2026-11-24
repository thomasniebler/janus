"""Error grouping and compaction service."""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, update
from sqlalchemy.orm import Session

from janus import models, runtime, time_utils

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
PATH_PATTERN = re.compile(r"[/\\][\w/\\.-]+")
QUOTED_STRING_PATTERN = re.compile(r"(?P<quote>['\"])(?P<value>[^'\"]*)(?P=quote)")
NUMBER_PATTERN = re.compile(r"\b\d+\b")


def _utcnow_naive() -> datetime:
    """Return the current UTC timestamp normalized to naive UTC for SQLite-backed comparisons."""
    return (time_utils.normalize_timestamp(time_utils.utcnow()) or time_utils.utcnow()).replace(tzinfo=None)


def _normalize_event_timestamp(event_timestamp: datetime | None) -> datetime:
    """Normalize an event timestamp to naive UTC, falling back to the current time."""
    if event_timestamp is None:
        return _utcnow_naive()

    normalized = time_utils.normalize_timestamp(event_timestamp)
    return (normalized or _utcnow_naive()).replace(tzinfo=None)


class _ErrorCompactionLock:
    """Advisory lock that serializes error compaction runs."""

    def __init__(self) -> None:
        self.lock_path = runtime.get_instance_lock_path().with_name("error-compaction.lock")
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to acquire the lock without blocking."""
        if self._fd is not None:
            return True

        runtime.prepare_runtime_paths()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False

        self._fd = fd
        return True

    def release(self) -> None:
        """Release the lock if held."""
        if self._fd is None:
            return

        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


class ErrorService:
    """Manages error event grouping and compaction."""

    DEFAULT_RETENTION_DAYS = 28
    DEFAULT_BATCH_SIZE = 500

    def __init__(self, db: Session):
        """Initialize with a database session."""
        self.db = db

    @staticmethod
    def _serialize_details(details: dict[str, Any] | None) -> str | None:
        """Serialize optional details for storage."""
        if not details:
            return None
        return json.dumps(details, sort_keys=True)

    @staticmethod
    def normalize_message(message: str) -> str:
        """Normalize error message into a template by replacing variable parts."""
        normalized = EMAIL_PATTERN.sub("<EMAIL>", message)
        normalized = UUID_PATTERN.sub("<UUID>", normalized)
        normalized = PATH_PATTERN.sub("<PATH>", normalized)
        normalized = QUOTED_STRING_PATTERN.sub(
            lambda match: f"{match.group('quote')}<STR>{match.group('quote')}",
            normalized,
        )
        normalized = NUMBER_PATTERN.sub("<NUM>", normalized)
        normalized = " ".join(normalized.split())
        return normalized

    @staticmethod
    def compute_fingerprint(source: str, message_template: str) -> str:
        """Compute a stable fingerprint for error grouping."""
        content = f"{source}:{message_template}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def group_error(
        self,
        source: str,
        message: str,
        details: dict[str, Any] | None = None,
        event_timestamp: datetime | None = None,
    ) -> models.ErrorGroup:
        """Group an error event into an ErrorGroup, creating or updating as needed."""
        message_template = self.normalize_message(message)
        fingerprint = self.compute_fingerprint(source, message_template)
        event_time = _normalize_event_timestamp(event_timestamp)
        values = {
            "first_seen": func.min(models.ErrorGroup.first_seen, event_time),
            "last_seen": func.max(models.ErrorGroup.last_seen, event_time),
            "occurrence_count": models.ErrorGroup.occurrence_count + 1,
        }
        serialized_details = self._serialize_details(details)
        if serialized_details is not None:
            values["sample_details"] = serialized_details

        stmt = (
            update(models.ErrorGroup)
            .where(models.ErrorGroup.fingerprint == fingerprint)
            .values(**values)
            .returning(models.ErrorGroup.id)
        )
        result = self.db.execute(stmt)
        updated_id = result.scalar_one_or_none()

        if updated_id is not None:
            updated_group = self.db.get(models.ErrorGroup, updated_id)
            if updated_group is None:
                raise RuntimeError(f"Updated error group {updated_id} could not be reloaded")
            self.db.refresh(updated_group)
            return updated_group

        error_group = models.ErrorGroup(
            fingerprint=fingerprint,
            source=source,
            message_template=message_template,
            first_seen=event_time,
            last_seen=event_time,
            occurrence_count=1,
            sample_details=serialized_details,
            created_at=event_time,
        )
        self.db.add(error_group)
        self.db.flush()
        return error_group

    def compact_errors(
        self,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> dict[str, int]:
        """Compact ERROR events into ErrorGroups and clean up old events."""
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")

        cutoff_date = _utcnow_naive() - timedelta(days=retention_days)
        events_processed = 0
        processed_events_deleted = 0
        groups_touched = set()
        last_processed_id = 0
        compaction_lock = _ErrorCompactionLock()

        if not compaction_lock.acquire():
            logger.info("Skipping error compaction because another run is already in progress")
            return {
                "events_processed": 0,
                "groups_updated": 0,
                "events_deleted": 0,
                "groups_deleted": 0,
            }

        try:
            while True:
                error_events = (
                    self.db.query(models.Event)
                    .filter(
                        models.Event.event_type == models.EventType.ERROR,
                        models.Event.timestamp >= cutoff_date,
                        models.Event.id > last_processed_id,
                    )
                    .order_by(models.Event.id.asc())
                    .limit(batch_size)
                    .all()
                )
                if not error_events:
                    break

                batch_group_ids = set()
                for event in error_events:
                    details = None
                    if event.details:
                        try:
                            details = json.loads(event.details)
                        except (json.JSONDecodeError, TypeError):
                            details = {"raw": event.details}

                    group = self.group_error(event.source, event.message, details, event.timestamp)
                    groups_touched.add(group.id)
                    batch_group_ids.add(group.id)
                    events_processed += 1
                    last_processed_id = event.id

                processed_delete_result = self.db.execute(
                    delete(models.Event).where(models.Event.id.in_([event.id for event in error_events]))
                )
                processed_events_deleted += int(processed_delete_result.rowcount or 0)

                if batch_group_ids:
                    self.db.execute(
                        update(models.ErrorGroup)
                        .where(models.ErrorGroup.id.in_(batch_group_ids))
                        .values(last_compacted_at=_utcnow_naive())
                    )

                self.db.commit()

            deleted_result = self.db.execute(
                delete(models.Event).where(
                    and_(
                        models.Event.event_type == models.EventType.ERROR,
                        models.Event.timestamp < cutoff_date,
                    )
                )
            )
            old_group_result = self.db.execute(
                delete(models.ErrorGroup).where(models.ErrorGroup.last_seen < cutoff_date)
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        finally:
            compaction_lock.release()

        events_deleted = processed_events_deleted + int(deleted_result.rowcount or 0)
        groups_deleted = int(old_group_result.rowcount or 0)

        return {
            "events_processed": events_processed,
            "groups_updated": len(groups_touched),
            "events_deleted": events_deleted,
            "groups_deleted": groups_deleted,
        }

    def list_error_groups(
        self,
        limit: int = 100,
        source: str | None = None,
    ) -> list[models.ErrorGroup]:
        """List error groups, most recently seen first."""
        stmt = self.db.query(models.ErrorGroup)

        if source:
            stmt = stmt.filter(models.ErrorGroup.source == source)

        return stmt.order_by(models.ErrorGroup.last_seen.desc()).limit(limit).all()

    def get_error_group(self, group_id: int) -> models.ErrorGroup | None:
        """Retrieve a specific error group by ID."""
        return self.db.get(models.ErrorGroup, group_id)

    def delete_error_group(self, group_id: int) -> bool:
        """Delete an error group by ID. Returns True if deleted, False if not found."""
        group = self.db.get(models.ErrorGroup, group_id)
        if group is None:
            return False
        self.db.delete(group)
        self.db.commit()
        return True
