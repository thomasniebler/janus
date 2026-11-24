"""Deleting Janus-owned planner tasks and events (JAN-221).

Owner-scoped: a foreign, imported, or missing id deletes nothing and answers
``False``. A deleted task's subtasks are kept and move up to its own parent
(or the root). The item's ``PlannerLink`` evidence rows go with it, and
suggestions that targeted it lose that target; a pending update suggestion
for it has nothing left to update, so it is dismissed. A deleted task's
knowledge source (and its chunks, vectors and graph evidence) is deleted in the
same transaction: SQLite can hand the freed id to the next task, and the
orphan sweep would then never see the old source as orphaned.
"""

from typing import Any

from sqlalchemy.orm import Session

from janus import models
from janus.services import knowledge_sources
from janus.services.local_planner_service import LocalPlannerService


def _detach(db: Session, user_id: int, link_column: Any, target_column: Any, item_id: int) -> None:
    """Removes a deleted item's links and clears suggestions' pointers to it; does not commit."""
    db.query(models.PlannerLink).filter(models.PlannerLink.user_id == user_id, link_column == item_id).delete(
        synchronize_session=False
    )
    suggestions = db.query(models.PlannerSuggestion).filter(
        models.PlannerSuggestion.user_id == user_id, target_column == item_id
    )
    suggestions.filter(models.PlannerSuggestion.status == models.PlannerSuggestionStatus.PENDING).update(
        {models.PlannerSuggestion.status: models.PlannerSuggestionStatus.DISMISSED}, synchronize_session=False
    )
    suggestions.update({target_column: None}, synchronize_session=False)


def _forget_task_knowledge(db: Session, user_id: int, task_id: int) -> None:
    """Deletes the user's knowledge sources built from the task, with their chunks; does not commit."""
    sources = (
        db.query(models.KnowledgeSource)
        .filter(
            models.KnowledgeSource.user_id == user_id,
            models.KnowledgeSource.source_type == models.KnowledgeSourceType.PLANNER_TASK.value,
            models.KnowledgeSource.owner_id == task_id,
        )
        .all()
    )
    for source in sources:
        knowledge_sources.delete_source(db, source)


def delete_local_task(db: Session, user_id: int, task_id: int) -> bool:
    """Deletes one of ``user_id``'s local tasks and commits; ``False`` if missing or foreign."""
    task = LocalPlannerService(db).get_local_task(user_id, task_id)
    if task is None:
        return False
    db.query(models.PlannerTask).filter(
        models.PlannerTask.user_id == user_id,
        models.PlannerTask.parent_task_id == task.id,
    ).update({models.PlannerTask.parent_task_id: task.parent_task_id}, synchronize_session=False)
    _detach(db, user_id, models.PlannerLink.planner_task_id, models.PlannerSuggestion.target_task_id, task.id)
    _forget_task_knowledge(db, user_id, task.id)
    db.delete(task)
    db.commit()
    return True


def delete_local_event(db: Session, user_id: int, event_id: int) -> bool:
    """Deletes one of ``user_id``'s local events and commits; ``False`` if missing or foreign."""
    event = LocalPlannerService(db).get_local_event(user_id, event_id)
    if event is None:
        return False
    _detach(db, user_id, models.PlannerLink.planner_event_id, models.PlannerSuggestion.target_event_id, event.id)
    db.delete(event)
    db.commit()
    return True
