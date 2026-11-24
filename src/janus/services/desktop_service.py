"""Desktop widget aggregates for authenticated Janus users."""

from __future__ import annotations

from sqlalchemy import func

from janus import models
from janus.schemas import planner as planner_schemas
from janus.services.base import BaseService


class DesktopService(BaseService):
    """Load compact per-user aggregates for desktop status widgets."""

    def get_summary(
        self,
        user: models.User,
        *,
        include_unread_emails: bool = True,
    ) -> planner_schemas.DesktopSummary:
        """Return task and unread-email counts scoped to one authenticated user."""
        open_task_count = (
            self.db.query(func.count(models.PlannerTask.id))
            .filter(
                models.PlannerTask.user_id == user.id,
                models.PlannerTask.status != models.TaskStatus.DONE,
            )
            .scalar()
            or 0
        )

        unread_email_count = None
        if include_unread_emails:
            unread_email_count = (
                self.db.query(func.count(models.EmailRecord.id))
                .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
                .filter(
                    models.EmailAccount.user_id == user.id,
                    models.EmailRecord.is_read.is_(False),
                )
                .scalar()
                or 0
            )

        return planner_schemas.DesktopSummary(
            open_tasks=open_task_count,
            unread_emails=unread_email_count,
        )
