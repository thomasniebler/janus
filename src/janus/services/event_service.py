"""Event log querying and filtering service.

Who may read which rows (JAN-272): ``models.Event.user_id`` records the user an
event concerns, and ``None`` marks a system row (including every row written
before the column existed). :func:`visible_events_condition` is the one rule
every reader applies:

* an admin reading interactively (the System log page, chat) sees every row;
* anyone else -- a non-admin, or *any* unattended run, admin-owned or not --
  sees only rows owned by that user, never system rows or another user's.
"""

from datetime import datetime

from sqlalchemy import ColumnElement, func, select

from janus import models
from janus.services.base import BaseService
from janus.time_utils import to_naive_utc


def visible_events_condition(viewer: models.User, *, own_rows_only: bool = False) -> ColumnElement[bool] | None:
    """Return the filter limiting the event log to what ``viewer`` may read (``None``: no limit).

    ``own_rows_only`` is set for unattended runs: with nobody watching, even an
    admin's run reads only the admin's own rows.
    """
    if viewer.is_admin and not own_rows_only:
        return None
    return models.Event.user_id == viewer.id


class EventService(BaseService):
    """Owns event log filtering, pagination, and querying."""

    def list_events(
        self,
        viewer: models.User,
        event_type: models.EventType | None = None,
        source: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[models.Event], int]:
        """Query and paginate events matching the given filters.

        Args:
            viewer: The user reading; see :func:`visible_events_condition`.
            event_type: Optional exact match on EventType enum.
            source: Optional exact match on source string.
            start: Optional inclusive lower timestamp bound (UTC if naive).
            end: Optional exclusive upper timestamp bound (UTC if naive).
            limit: Page size (1-200).
            offset: Pagination offset (>=0).

        Returns:
            Tuple of (events list, total count).

        Raises:
            ValueError: If start >= end after UTC normalization.
        """
        start_utc, end_utc = to_naive_utc(start), to_naive_utc(end)
        if start_utc is not None and end_utc is not None and start_utc >= end_utc:
            raise ValueError("start must be before end")

        conditions = []
        visibility = visible_events_condition(viewer)
        if visibility is not None:
            conditions.append(visibility)
        if event_type is not None:
            conditions.append(models.Event.event_type == event_type)
        if source is not None:
            conditions.append(models.Event.source == source)
        if start_utc is not None:
            conditions.append(models.Event.timestamp >= start_utc)
        if end_utc is not None:
            conditions.append(models.Event.timestamp < end_utc)

        total = self.db.scalar(select(func.count()).select_from(models.Event).where(*conditions)) or 0
        events = self.db.scalars(
            select(models.Event)
            .where(*conditions)
            .order_by(models.Event.timestamp.desc(), models.Event.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()

        return list(events), total

    def list_sources(self, viewer: models.User) -> list[str]:
        """Return the distinct sources of the events ``viewer`` may read, alphabetically sorted.

        Returns:
            List of source strings (excluding None).
        """
        conditions = [models.Event.source.is_not(None)]
        visibility = visible_events_condition(viewer)
        if visibility is not None:
            conditions.append(visibility)
        return list(
            self.db.scalars(
                select(models.Event.source).where(*conditions).distinct().order_by(models.Event.source)
            ).all()
        )
