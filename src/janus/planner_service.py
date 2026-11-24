"""Service for synchronizing planner data from various providers."""

import logging
from datetime import UTC, datetime, timedelta

from janus import database, event_logger, models, planner_trigger_events, reminder_service, status_service
from janus.connectors import google, jira, microsoft_calendar
from janus.planner_trigger_events import CALENDAR_EVENT_FIELDS, TASK_FIELDS, PendingPlannerEvent
from janus.services import microsoft_connect_service, oauth_connection_service, topic_service
from janus.services.local_planner_service import LocalPlannerService
from janus.time_utils import format_english_month_day, normalize_timestamp
from janus.trigger_events import TriggerEvent

logger = logging.getLogger(__name__)


class PlannerService:
    """Service for managing planner synchronizations."""

    def __init__(self, db=None):
        """Initializes the PlannerService.

        Args:
            db (Session, optional): A database session. Defaults to a new SessionLocal.
        """
        self.db = db or database.SessionLocal()

    def get_connector(self, account: models.PlannerAccount) -> object | None:
        """Factory for connectors based on provider type.

        Args:
            account (PlannerAccount): The planner account to get a connector for.

        Returns:
            Optional[object]: A connector instance or None if provider not supported.
        """
        if account.provider == models.Provider.GOOGLE_CALENDAR:
            return google.GoogleCalendarConnector(account)
        elif account.provider == models.Provider.GOOGLE_TASKS:
            return google.GoogleTasksConnector(account)
        elif account.provider == models.Provider.MICROSOFT_CALENDAR:
            return self._microsoft_calendar_connector(account)
        elif account.provider == models.Provider.JIRA:
            return jira.JiraConnector(account)
        # Placeholder for ADO
        return None

    def _microsoft_calendar_connector(self, account: models.PlannerAccount):
        """Builds the Outlook connector on the account owner's connected Microsoft account."""
        connection = oauth_connection_service.get_connection(
            self.db, account.user_id, oauth_connection_service.MICROSOFT
        )
        if connection is None:
            raise RuntimeError("Microsoft is not connected. Connect it in Settings.")
        return microsoft_calendar.MicrosoftCalendarConnector(
            account, lambda: microsoft_connect_service.graph_client(self.db, connection)
        )

    def _active_accounts(self, user_id: int | None) -> list[models.PlannerAccount]:
        """Returns the active planner accounts, restricted to one owner when ``user_id`` is given."""
        query = self.db.query(models.PlannerAccount).filter(models.PlannerAccount.is_active)
        if user_id is not None:
            query = query.filter(models.PlannerAccount.user_id == user_id)
        return query.all()

    def sync_all(self, user_id: int | None = None):
        """Syncs all active planner accounts (tasks and calendar events), or only ``user_id``'s."""
        accounts = self._active_accounts(user_id)

        # Ranges for calendar (Sync today and tomorrow)
        now = datetime.now(UTC)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=2)

        for account in accounts:
            # Each account syncs as its owner, so its events are owned (JAN-272).
            with event_logger.owned_by(account.user_id):
                previous_sync_at = account.last_sync_at
                previous_error = account.last_sync_error
                try:
                    event_logger.log(
                        models.EventType.INFO,
                        "PlannerService",
                        f"Starting sync for planner account: {account.name}",
                        {"provider": account.provider.value},
                    )
                    connector = self.get_connector(account)
                    if not connector:
                        continue
                    # The first sync is a baseline: it must not fire one trigger run per existing item (JAN-362).
                    baseline = previous_sync_at is None
                    pending: list[PendingPlannerEvent] = []

                    # Sync Tasks
                    new_tasks = connector.fetch_tasks()
                    for task_data in map(planner_trigger_events.to_utc, new_tasks):
                        # Update existing task or create new
                        existing = (
                            self.db.query(models.TaskRecord)
                            .filter(
                                models.TaskRecord.planner_account_id == account.id,
                                models.TaskRecord.external_id == task_data["external_id"],
                            )
                            .first()
                        )

                        if existing:
                            before = planner_trigger_events.snapshot(existing, TASK_FIELDS)
                            changed = planner_trigger_events.changes(before, task_data)
                            if changed and not baseline:
                                pending.append(
                                    PendingPlannerEvent(TriggerEvent.PLANNER_TASK_CHANGED, existing, changed)
                                )
                            planner_trigger_events.apply(existing, task_data, TASK_FIELDS)
                            existing.updated_at = now
                            event_logger.log(
                                models.EventType.INFO,
                                "PlannerService",
                                f"Updated task: {task_data['title']}",
                                {"account": account.name, "external_id": task_data["external_id"]},
                            )
                        else:
                            new_task = models.TaskRecord(
                                planner_account_id=account.id,
                                external_id=task_data["external_id"],
                                title=task_data["title"],
                                status=task_data["status"],
                                due_date=task_data.get("due_date"),
                            )
                            self.db.add(new_task)
                            if not baseline:
                                pending.append(PendingPlannerEvent(TriggerEvent.PLANNER_TASK_CREATED, new_task))
                            event_logger.log(
                                models.EventType.INFO,
                                "PlannerService",
                                f"Added new task: {task_data['title']}",
                                {"account": account.name, "external_id": task_data["external_id"]},
                            )

                    # Sync Calendar Events
                    new_events = connector.fetch_events(start_time, end_time)
                    for event_data in map(planner_trigger_events.to_utc, new_events):
                        existing = (
                            self.db.query(models.CalendarEvent)
                            .filter(
                                models.CalendarEvent.planner_account_id == account.id,
                                models.CalendarEvent.external_id == event_data["external_id"],
                            )
                            .first()
                        )

                        if existing:
                            before = planner_trigger_events.snapshot(existing, CALENDAR_EVENT_FIELDS)
                            changed = planner_trigger_events.changes(before, event_data)
                            if changed and not baseline:
                                pending.append(
                                    PendingPlannerEvent(TriggerEvent.PLANNER_CALENDAR_EVENT_CHANGED, existing, changed)
                                )
                            planner_trigger_events.apply(existing, event_data, CALENDAR_EVENT_FIELDS)
                            event_logger.log(
                                models.EventType.INFO,
                                "PlannerService",
                                f"Updated event: {event_data['summary']}",
                                {"account": account.name, "external_id": event_data["external_id"]},
                            )
                        else:
                            new_event = models.CalendarEvent(
                                planner_account_id=account.id,
                                external_id=event_data["external_id"],
                                summary=event_data["summary"],
                                start_time=event_data["start_time"],
                                end_time=event_data["end_time"],
                                location=event_data.get("location"),
                            )
                            self.db.add(new_event)
                            if not baseline:
                                pending.append(
                                    PendingPlannerEvent(TriggerEvent.PLANNER_CALENDAR_EVENT_CREATED, new_event)
                                )
                            event_logger.log(
                                models.EventType.INFO,
                                "PlannerService",
                                f"Added new event: {event_data['summary']}",
                                {"account": account.name, "external_id": event_data["external_id"]},
                            )

                    self.db.commit()
                    account.last_sync_at = datetime.now(UTC)
                    account.last_sync_error = None
                    self.db.commit()
                    # Right after the commit, before the status bookkeeping below can fail (JAN-362).
                    planner_trigger_events.publish(self.db, account, pending, synced_at=now)
                    previous_status, _ = status_service._planner_status_details(previous_sync_at, previous_error, now)
                    new_status, new_message = status_service._planner_status_details(
                        account.last_sync_at, account.last_sync_error, now
                    )
                    if previous_status != new_status:
                        status_service.log_status_transition(
                            service_id="planner-sync",
                            service_name="Planner Sync",
                            component_id=f"planner-account-{account.id}",
                            component_name=account.name,
                            previous_status=previous_status,
                            new_status=new_status,
                            message=new_message,
                        )
                    event_logger.log(
                        models.EventType.SUCCESS,
                        "PlannerService",
                        f"Successfully synced planner account: {account.name}",
                    )
                except Exception as e:
                    self.db.rollback()
                    error_message = str(e)
                    account.last_sync_error = error_message
                    try:  # noqa: SIM105
                        self.db.commit()
                    except Exception:
                        pass
                    previous_status, _ = status_service._planner_status_details(previous_sync_at, previous_error, now)
                    new_status, new_message = status_service._planner_status_details(
                        previous_sync_at, error_message, now
                    )
                    if previous_status != new_status:
                        status_service.log_status_transition(
                            service_id="planner-sync",
                            service_name="Planner Sync",
                            component_id=f"planner-account-{account.id}",
                            component_name=account.name,
                            previous_status=previous_status,
                            new_status=new_status,
                            message=new_message,
                        )
                    event_logger.log(
                        models.EventType.ERROR, "PlannerService", f"Failed to sync planner account {account.name}: {e}"
                    )

    def get_briefing_payload(  # noqa: C901
        self,
        *,
        user_id: int | None = None,
        include_calendar: bool = True,
        include_tasks: bool = True,
    ) -> dict[str, object]:
        """Return briefing summary text plus the topic IDs surfaced in that context.

        Args:
            user_id: Optional explicit owner for topic-aware context.
            include_calendar (bool): Whether to include calendar events and reminders.
            include_tasks (bool): Whether to include tasks/Jira items.

        Returns:
            Dict[str, object]: Summary text and surfaced topic IDs.
        """
        now = datetime.now(UTC)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1)
        reminders = reminder_service.ReminderService(self.db)
        topics = topic_service.TopicService(self.db)
        resolved_user_id = user_id
        if resolved_user_id is None:
            default_user = topics.get_default_user()
            resolved_user_id = default_user.id if default_user is not None else None
        local_planner = LocalPlannerService(self.db)

        summary_lines: list[str] = []
        active_topics: list[dict[str, object]] = []

        if include_calendar:
            if resolved_user_id is not None:
                events = local_planner.list_events(
                    resolved_user_id,
                    start=start_time,
                    end=end_time,
                    include_imported=True,
                )
            else:
                events = []
            active_reminders = reminders.get_active_reminders(resolved_user_id, now)
            preparation_outlook = reminders.get_preparation_outlook(resolved_user_id, now)

            summary_lines.append("🗓️ Today's Calendar:")
            if not events:
                summary_lines.append("No events scheduled.")
            for event in events:
                event_time = normalize_timestamp(event["start_time"]) or event["start_time"]
                summary_lines.append(f"- {event_time.strftime('%H:%M')}: {event['title']}")

            summary_lines.append("\n⏰ Active Reminders:")
            if not active_reminders:
                summary_lines.append("No active reminders.")
            for reminder in active_reminders[:10]:
                anchor = normalize_timestamp(reminder.prepare_for_at or reminder.due_at)
                summary_lines.append(f"- [{reminder.kind.value}] {anchor.strftime('%Y-%m-%d %H:%M')}: {reminder.title}")

            summary_lines.append("\n🔭 Preparation Outlook (Next 6 Weeks):")
            if not preparation_outlook:
                summary_lines.append("No upcoming planner events currently need review.")
            for event in preparation_outlook[:10]:
                event_time = normalize_timestamp(event.start_time) or event.start_time
                summary_lines.append(f"- {event_time.strftime('%Y-%m-%d')}: {event.summary}")

            active_topics = topics.build_context_topic_summary(user_id=resolved_user_id)
            summary_lines.append("\n🧭 Topic Threads:")
            if not active_topics:
                summary_lines.append("No active topics identified yet.")
            for topic in active_topics:
                line = f"- {topic['title']}"
                if topic.get("summary"):
                    line += f": {topic['summary']}"
                metadata: list[str] = []
                if topic.get("pinned"):
                    metadata.append("pinned")
                if topic.get("evidence_count"):
                    metadata.append(f"{topic['evidence_count']} linked signals")
                last_seen = normalize_timestamp(topic.get("last_seen_at"))
                if last_seen is not None:
                    metadata.append(
                        "fresh today"
                        if last_seen.date() == now.date()
                        else f"updated {format_english_month_day(last_seen)}"
                    )
                if metadata:
                    line += f" ({', '.join(metadata)})"
                summary_lines.append(line)

        if include_tasks:
            if resolved_user_id is not None:
                tasks = local_planner.list_tasks(resolved_user_id, include_completed=False, include_imported=True)
            else:
                tasks = []

            summary_lines.append("\n📝 Active Tasks:")
            if not tasks:
                summary_lines.append("No active tasks found.")
            for task in tasks:
                source_note = f" ({task['source_label']})" if task.get("source_label") else ""
                summary_lines.append(f"- [{task['status']}] {task['title']}{source_note}")

        return {
            "summary": "\n".join(summary_lines),
            "topic_ids": [int(topic["id"]) for topic in active_topics if isinstance(topic.get("id"), int)],
            "active_topics": active_topics,
        }

    def get_todays_summary(
        self,
        *,
        user_id: int | None = None,
        include_calendar: bool = True,
        include_tasks: bool = True,
    ) -> str:
        """Returns a string summary of today's tasks and events for the briefing."""
        payload = self.get_briefing_payload(
            user_id=user_id,
            include_calendar=include_calendar,
            include_tasks=include_tasks,
        )
        return str(payload["summary"])

    def get_context_data(self, *, user_id: int | None = None) -> dict:
        """Returns raw data to be injected into LLM context.

        Returns:
            Dict: A dictionary of calendar events and tasks.
        """
        now = datetime.now(UTC)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1)
        reminders = reminder_service.ReminderService(self.db)
        topics = topic_service.TopicService(self.db)
        resolved_user_id = user_id
        if resolved_user_id is None:
            default_user = topics.get_default_user()
            resolved_user_id = default_user.id if default_user is not None else None
        local_planner = LocalPlannerService(self.db)

        if resolved_user_id is not None:
            events_today = local_planner.list_events(
                resolved_user_id,
                start=start_time,
                end=end_time,
                include_imported=True,
            )
            tasks = local_planner.list_tasks(resolved_user_id, include_completed=False, include_imported=True)
        else:
            events_today = []
            tasks = []
        active_reminders = reminders.get_active_reminders(resolved_user_id, now)
        upcoming_events = reminders.get_preparation_outlook(resolved_user_id, now)
        calendar_today = [
            {
                "summary": event["title"],
                "time": (normalize_timestamp(event["start_time"]) or event["start_time"]).isoformat(),
                "source": event["source"],
            }
            for event in events_today
        ]

        return {
            "calendar": calendar_today,
            "calendar_today": calendar_today,
            "calendar_upcoming": [
                {
                    "summary": e.summary,
                    "time": (normalize_timestamp(e.start_time) or e.start_time).isoformat(),
                }
                for e in upcoming_events
            ],
            "tasks": [{"title": t["title"], "status": t["status"], "source": t["source"]} for t in tasks],
            "active_reminders": [
                {
                    "title": reminder.title,
                    "kind": reminder.kind.value,
                    "due_at": reminder.due_at.isoformat(),
                    "prepare_for_at": reminder.prepare_for_at.isoformat() if reminder.prepare_for_at else None,
                    "notes": reminder.notes,
                }
                for reminder in active_reminders
            ],
            "active_topics": topics.build_context_topic_summary(user_id=resolved_user_id),
            "pending_planner_suggestions": local_planner.list_suggestions(resolved_user_id)
            if resolved_user_id is not None
            else [],
            "preparation_outlook": [
                {"summary": event.summary, "time": event.start_time.isoformat()} for event in upcoming_events
            ],
        }
