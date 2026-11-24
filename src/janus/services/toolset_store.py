"""Persistence of brain-selected toolsets (JAN-300): ``runtime_tool_selections``.

A retried or resumed step reuses its stored choice; chat carries a
conversation's earlier selection and ``request_tools`` widenings forward.
Writes use a short-lived session, never the caller's (SQLite locking, JAN-189).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from janus import models
from janus.toolset_selection import SOURCE_PERSISTED, SOURCE_SELECTED, ToolsetChoice, clean_reason

logger = logging.getLogger(__name__)


def _isolated(db: Session) -> Session:
    # A short-lived session: chat must not open a write transaction on its
    # long-lived session before the stream (SQLite locking, JAN-189).
    return sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)()


def _ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item.strip())


def _from_row(row: models.RuntimeToolSelection) -> ToolsetChoice:
    stored = None if row.tool_ids is None else _ids(row.tool_ids)
    return ToolsetChoice(
        source=row.source,
        reason=row.reason or "",
        tool_ids=stored,
        proposed_tool_ids=_ids(row.proposed_tool_ids),
        trimmed_tool_ids=_ids(row.trimmed_tool_ids),
        widened_tool_ids=_ids(row.widened_tool_ids),
    )


def _as_persisted(choice: ToolsetChoice) -> ToolsetChoice:
    """Return a stored choice as the request a resumed step makes (re-trimmed by ``narrow``)."""
    return replace(
        choice,
        source=SOURCE_PERSISTED,
        reason=clean_reason(f"Reused the {choice.source} choice of this task. {choice.reason}"),
        trimmed_tool_ids=(),
        authorized_tool_ids=(),
    )


def load_for_step(db: Session, user_id: int, task_id: str, step_id: str) -> ToolsetChoice | None:
    """Return the stored choice of this exact step, else of the task's latest step, as a persisted request."""
    rows = db.scalars(
        select(models.RuntimeToolSelection)
        .where(models.RuntimeToolSelection.user_id == user_id, models.RuntimeToolSelection.task_id == task_id)
        .order_by(models.RuntimeToolSelection.id.desc())
    ).all()
    for row in rows:
        if row.step_id == step_id:
            return _as_persisted(_from_row(row))
    return _as_persisted(_from_row(rows[0])) if rows else None


def latest_for_task(db: Session, user_id: int, task_id: str) -> ToolsetChoice | None:
    """Return the task's most recent stored choice as stored (chat's sticky selection and widenings)."""
    row = db.scalars(
        select(models.RuntimeToolSelection)
        .where(models.RuntimeToolSelection.user_id == user_id, models.RuntimeToolSelection.task_id == task_id)
        .order_by(models.RuntimeToolSelection.id.desc())
        .limit(1)
    ).first()
    return _from_row(row) if row is not None else None


def sticky_tool_ids(previous: ToolsetChoice | None) -> tuple[str, ...]:
    """Return what an earlier chat message selected, carried into the next selection."""
    if previous is None or previous.tool_ids is None or previous.source not in {SOURCE_SELECTED, SOURCE_PERSISTED}:
        return ()
    return previous.tool_ids


def save(db: Session, user_id: int, task_id: str, step_id: str, task_mode: str, choice: ToolsetChoice) -> None:
    """Store (or replace) the effective choice of one step; failures are logged, never raised."""
    session = _isolated(db)
    try:
        row = session.scalar(
            select(models.RuntimeToolSelection).where(
                models.RuntimeToolSelection.user_id == user_id,
                models.RuntimeToolSelection.task_id == task_id,
                models.RuntimeToolSelection.step_id == step_id,
            )
        )
        if row is None:
            row = models.RuntimeToolSelection(user_id=user_id, task_id=task_id, step_id=step_id)
            session.add(row)
        row.task_mode = task_mode
        row.source = choice.source
        row.reason = choice.reason
        row.tool_ids = list(choice.tool_ids) if choice.tool_ids is not None else None
        row.proposed_tool_ids = list(choice.proposed_tool_ids)
        row.trimmed_tool_ids = list(choice.trimmed_tool_ids)
        row.widened_tool_ids = list(choice.widened_tool_ids)
        session.commit()
    except Exception as error:  # noqa: BLE001 - the trace is best-effort; the step still runs
        session.rollback()
        logger.warning("Could not store the tool selection of %s: %s", step_id, error)
    finally:
        session.close()


def add_widened(db: Session, user_id: int, task_id: str, step_id: str, tool_ids: Sequence[str]) -> None:
    """Add ``tool_ids`` to the widened ids stored for one step (chat ``request_tools``)."""
    session = _isolated(db)
    try:
        row = session.scalar(
            select(models.RuntimeToolSelection).where(
                models.RuntimeToolSelection.user_id == user_id,
                models.RuntimeToolSelection.task_id == task_id,
                models.RuntimeToolSelection.step_id == step_id,
            )
        )
        if row is None:
            return
        row.widened_tool_ids = list(dict.fromkeys((*_ids(row.widened_tool_ids), *tool_ids)))
        session.commit()
    finally:
        session.close()


def forget_task(db: Session, user_id: int, task_id: str) -> None:
    """Delete one task's stored choices (in ``db``'s transaction), e.g. when a conversation is deleted."""
    db.execute(
        delete(models.RuntimeToolSelection).where(
            models.RuntimeToolSelection.user_id == user_id, models.RuntimeToolSelection.task_id == task_id
        )
    )


__all__ = ["add_widened", "forget_task", "latest_for_task", "load_for_step", "save", "sticky_tool_ids"]
