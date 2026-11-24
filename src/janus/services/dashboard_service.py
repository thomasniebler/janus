"""Dashboard data, widget preference helpers, and briefing configuration wiring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from janus import event_logger, llm_service, models, planner_service, time_utils
from janus.schemas import planner as planner_schemas
from janus.services.base import BaseService
from janus.services.character_state_service import prompt_character
from janus.services.email_service import EmailService
from janus.services.local_planner_service import LocalPlannerService
from janus.services.topic_service import TopicService
from janus.services.topic_text import strip_candidate_topics
from janus.services.whatsapp_service import WhatsAppService

_BRIEFING_EMAIL_CANDIDATE_LIMIT = 100
_BRIEFING_EMAIL_DIGEST_LIMIT = 25

_DEFAULT_WIDGET_PREFERENCES: tuple[planner_schemas.DashboardWidgetPreference, ...] = (
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.BRIEFING,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.TOPIC_FOCUS,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.IMPORTANT_EMAILS,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.WHATSAPP_MESSAGES,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.SCHEDULE,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
    planner_schemas.DashboardWidgetPreference(
        widget_key=models.DashboardWidgetKey.TASKS,
        is_visible=True,
        is_collapsed=False,
        prompt_override=None,
        briefing_email_window=None,
    ),
)


@dataclass(frozen=True)
class BriefingConfiguration:
    """Resolved per-user briefing configuration."""

    prompt_override: str | None = None
    briefing_email_window: models.BriefingEmailWindow | None = None


def _resolve_briefing_email_window_bounds(
    email_window: models.BriefingEmailWindow,
    now: datetime,
) -> tuple[datetime, datetime]:
    """Return inclusive/exclusive bounds for a configured email window."""
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if email_window == models.BriefingEmailWindow.TODAY:
        return (start_of_today, start_of_today + timedelta(days=1))
    if email_window == models.BriefingEmailWindow.YESTERDAY:
        return (start_of_today - timedelta(days=1), start_of_today)
    if email_window == models.BriefingEmailWindow.LAST_24_HOURS:
        return (now - timedelta(hours=24), now)
    raise ValueError(f"unsupported briefing email window: {email_window.value}")


def _format_briefing_email_window_label(email_window: models.BriefingEmailWindow) -> str:
    """Return a user-facing label for the configured email window."""
    labels = {
        models.BriefingEmailWindow.TODAY: "Today",
        models.BriefingEmailWindow.YESTERDAY: "Yesterday",
        models.BriefingEmailWindow.LAST_24_HOURS: "Last 24 Hours",
    }
    return labels[email_window]


def _format_email_count_label(count: int) -> str:
    """Return a grammatically correct user-facing email count label."""
    return "1 email" if count == 1 else f"{count} emails"


class DashboardService(BaseService):
    """Aggregates dashboard data, persisted widget preferences, and briefing config."""

    def _get_user_planner_account_ids(self, user: models.User) -> list[int]:
        """Return planner account ids owned by the authenticated user."""
        return [
            account_id
            for (account_id,) in (
                self.db.query(models.PlannerAccount.id).filter(models.PlannerAccount.user_id == user.id).all()
            )
        ]

    def get_important_emails_widget_data(self, user: models.User) -> list[dict]:
        """Return ``user``'s recent important emails for the dashboard widget."""
        email_service = EmailService(self.db)
        important_emails = email_service.get_important_emails_for_user(user.id, hours=48)
        return email_service.serialize_emails(important_emails, user=user)

    def get_recent_emails_widget_data(self, user: models.User) -> list[dict]:
        """Return ``user``'s recent emails for the legacy aggregate dashboard payload."""
        email_service = EmailService(self.db)
        now = datetime.now(UTC)
        since = now - timedelta(hours=48)
        all_emails = (
            email_service.query_emails_for_user(user.id)
            .filter(models.EmailRecord.received_at >= since)
            .order_by(models.EmailRecord.received_at.desc())
            .limit(100)
            .all()
        )
        return email_service.serialize_emails(all_emails, user=user)

    def get_schedule_widget_data(self, user: models.User) -> list[dict]:
        """Return today's calendar events for the dashboard widget."""
        now = datetime.now(UTC)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1)
        return LocalPlannerService(self.db).list_events(user.id, start=start_time, end=end_time, include_imported=True)

    def get_tasks_widget_data(self, user: models.User) -> list[dict]:
        """Return active planner tasks for the dashboard widget as a parent/subtask tree.

        Local tasks come from the same nested tree the rest of Janus uses
        (``list_local_task_tree``): subtasks are nested under their parent's
        ``children`` instead of being flattened into sibling rows, and a done
        parent that still has open subtasks is kept as their grouping row.
        Imported (read-only) tasks follow as flat roots without children.
        """
        planner = LocalPlannerService(self.db)
        return [
            *planner.list_local_task_tree(user.id, include_completed=False),
            *planner.list_imported_tasks(user.id, include_completed=False),
        ]

    def get_topic_focus_widget_data(self, user: models.User | None = None) -> list[dict]:
        """Return tracked topics for the dashboard widget."""
        topics = TopicService(self.db).build_active_topic_summary(user.id if user is not None else None, limit=5)

        return topics

    def get_whatsapp_messages_widget_data(self, user: models.User) -> list[dict]:
        """Return recent WhatsApp messages for the dashboard widget."""
        return [models.to_dict(message) for message in WhatsAppService(self.db).list_messages(user, days=3, limit=8)]

    def get_dashboard_data(self, user: models.User) -> dict:
        """Return the legacy aggregate dashboard payload."""
        now = datetime.now(UTC)

        return {
            "important_emails": self.get_important_emails_widget_data(user),
            "whatsapp_messages": self.get_whatsapp_messages_widget_data(user),
            "all_emails": self.get_recent_emails_widget_data(user),
            "events": self.get_schedule_widget_data(user),
            "tasks": self.get_tasks_widget_data(user),
            "topics": self.get_topic_focus_widget_data(user),
            "now": now,
        }

    def list_widget_preferences(self, user: models.User) -> list[planner_schemas.DashboardWidgetPreference]:
        """Return persisted widget preferences overlaid on shipped defaults."""
        rows = (
            self.db.query(models.DashboardWidgetPreference)
            .filter(models.DashboardWidgetPreference.user_id == user.id)
            .all()
        )
        persisted = {row.widget_key: row for row in rows}

        preferences: list[planner_schemas.DashboardWidgetPreference] = []
        for default in _DEFAULT_WIDGET_PREFERENCES:
            row = persisted.get(default.widget_key)
            if row is None:
                preferences.append(default)
                continue
            preferences.append(
                planner_schemas.DashboardWidgetPreference(
                    widget_key=row.widget_key,
                    is_visible=row.is_visible,
                    is_collapsed=row.is_collapsed,
                    prompt_override=row.prompt_override,
                    briefing_email_window=row.briefing_email_window,
                )
            )
        return preferences

    def _get_widget_preference_row(
        self,
        user: models.User,
        widget_key: models.DashboardWidgetKey,
    ) -> models.DashboardWidgetPreference | None:
        """Return the persisted row for one user's widget preference."""
        return (
            self.db.query(models.DashboardWidgetPreference)
            .filter(
                models.DashboardWidgetPreference.user_id == user.id,
                models.DashboardWidgetPreference.widget_key == widget_key,
            )
            .first()
        )

    def get_widget_prompt_override(self, user: models.User, widget_key: models.DashboardWidgetKey) -> str | None:
        """Return the stored prompt override for a widget, if any."""
        row = self._get_widget_preference_row(user, widget_key)
        if row is None:
            return None
        return planner_schemas.normalize_prompt_override(row.prompt_override)

    def get_widget_briefing_email_window(
        self,
        user: models.User,
        widget_key: models.DashboardWidgetKey,
    ) -> models.BriefingEmailWindow | None:
        """Return the stored briefing email window for a widget, if any."""
        row = self._get_widget_preference_row(user, widget_key)
        if row is None:
            return None
        return row.briefing_email_window

    def get_briefing_configuration(self, user: models.User | None) -> BriefingConfiguration:
        """Resolve briefing prompt and email-window configuration for a user.

        The briefing persona comes from the user's character constitution.
        """
        if user is None:
            return BriefingConfiguration()
        row = self._get_widget_preference_row(user, models.DashboardWidgetKey.BRIEFING)
        if row is None:
            return BriefingConfiguration()
        return BriefingConfiguration(
            prompt_override=planner_schemas.normalize_prompt_override(row.prompt_override),
            briefing_email_window=row.briefing_email_window,
        )

    def _build_briefing_email_section(
        self,
        user: models.User,
        email_window: models.BriefingEmailWindow | None,
        *,
        surfaced_topic_ids: list[int] | None = None,
    ) -> str | None:
        """Return additive email context for the configured briefing window.

        This context uses UTC day boundaries and only digests a bounded recent
        subset of emails so briefing generation stays fast and scoped. The
        emails are listed as untrusted raw context for the briefing run's own
        model call; there is no separate summarizing LLM call.
        """
        if email_window is None:
            return None

        now = time_utils.utcnow()
        start_at, end_at = _resolve_briefing_email_window_bounds(email_window, now)
        email_service = EmailService(self.db)
        emails = (
            email_service.query_emails_for_user(user.id)
            .filter(
                models.EmailRecord.received_at >= start_at,
                models.EmailRecord.received_at < end_at,
            )
            .order_by(models.EmailRecord.received_at.desc())
            .limit(_BRIEFING_EMAIL_CANDIDATE_LIMIT)
            .all()
        )
        label = _format_briefing_email_window_label(email_window)
        payloads = strip_candidate_topics(email_service.serialize_emails(emails, user=user))
        if surfaced_topic_ids:
            visible_topic_ids = set(surfaced_topic_ids)
            payloads = [
                payload
                for payload in payloads
                if any(
                    isinstance(topic, dict) and topic.get("id") in visible_topic_ids
                    for topic in payload.get("topics", [])
                )
            ]
        payloads = payloads[:_BRIEFING_EMAIL_DIGEST_LIMIT]
        if not payloads:
            return f"📬 Email Summary ({label}):\nNo relevant emails were received in this window."

        header = f"📬 Email Summary ({label}, {_format_email_count_label(len(payloads))}):"
        listed = "\n".join(
            llm_service.format_email_content(
                index,
                payload.get("sender"),
                payload.get("subject"),
                payload.get("snippet"),
                priority=getattr(payload.get("priority"), "value", payload.get("priority")) or None,
                max_content_chars=500,
            )
            for index, payload in enumerate(payloads, start=1)
        )
        return f"{header}\n{listed}"

    def prepare_briefing_runtime_step(
        self,
        user: models.User,
        *,
        task_mode: str = "briefing",
        objective: str = "Generate the user's briefing",
    ) -> dict[str, object] | None:
        """Authorize the plan-driven runtime step backing a briefing/digest generation.

        Briefing and digest generation are one-shot text streams today, not a
        tool-calling loop, so this records an auditable, capability-gated
        toolset via the shared registry/adapter (JAN-164) without changing
        what gets generated. It is the same seam chat and ingestion use, ready
        for briefing/digest to grow real tool calls later. Used by both
        ``/ws/briefing`` and the ``janus brief``/``janus digest`` CLI commands.
        """
        # Imported lazily: agent_tools (imported by runtime_adapter) pulls in
        # janus.agent_toolboxes.dashboard, which imports this module, so a
        # module-level import here would be circular.
        from janus.capability_profile import user_connected_capabilities
        from janus.runtime_adapter import RuntimeAdapter

        adapter = RuntimeAdapter(self.db, user)
        registry_entries = adapter.registry.entries()
        requested_tool_ids = tuple(entry.id for entry in registry_entries)
        granted_capabilities = user_connected_capabilities(self.db, user)
        try:
            prepared = adapter.prepare_step(
                task_id=f"{task_mode}-{user.id}",
                objective=objective,
                requested_tool_ids=requested_tool_ids,
                task_mode=task_mode,
                granted_capabilities=granted_capabilities,
                confirmed_tool_ids=frozenset(requested_tool_ids),
            )
        except ValueError:
            return None

        if prepared.denials:
            event_logger.log(
                models.EventType.INFO,
                "DashboardService",
                "Briefing runtime step denied some tools for this generation.",
                {
                    "task_id": prepared.task.task_id,
                    "denied": [{"tool_id": denial.tool_id, "reason": denial.reason} for denial in prepared.denials],
                },
                user_id=user.id,
            )
        return {
            "task_id": prepared.task.task_id,
            "step_id": prepared.step.step_id,
            "tools": [entry.id for entry in prepared.selection.entries],
            "denied": [{"tool_id": denial.tool_id, "reason": denial.reason} for denial in prepared.denials],
        }

    def get_briefing_generation_context(
        self,
        user: models.User | None,
        *,
        include_calendar: bool = True,
        include_tasks: bool = True,
    ) -> dict[str, object]:
        """Return the planner summary plus any configured additive briefing context."""
        planner_payload = planner_service.PlannerService(self.db).get_briefing_payload(
            user_id=user.id if user is not None else None,
            include_calendar=include_calendar,
            include_tasks=include_tasks,
        )
        config = self.get_briefing_configuration(user)
        summary = str(planner_payload["summary"])
        if user is not None:
            email_section = self._build_briefing_email_section(
                user,
                config.briefing_email_window,
                surfaced_topic_ids=[
                    int(topic_id) for topic_id in planner_payload.get("topic_ids", []) if isinstance(topic_id, int)
                ],
            )
            if email_section:
                summary = f"{summary}\n\n{email_section}"
        tool_selection = self.prepare_briefing_runtime_step(user) if user else None
        character_prompt = None
        if user is not None:
            constitution, character_state = prompt_character(self.db, user.id)
            character_prompt = constitution.render_system_prompt(character_state)
        return {
            **planner_payload,
            "summary": summary,
            "prompt_override": config.prompt_override,
            "briefing_email_window": config.briefing_email_window.value if config.briefing_email_window else None,
            "character_prompt": character_prompt,
            "tool_selection": tool_selection,
        }

    def update_widget_preferences(
        self,
        user: models.User,
        updates: list[planner_schemas.DashboardWidgetPreferenceUpdate],
    ) -> list[planner_schemas.DashboardWidgetPreference]:
        """Persist widget preference updates and return the full effective set."""
        if not updates:
            return self.list_widget_preferences(user)

        existing_rows = (
            self.db.query(models.DashboardWidgetPreference)
            .filter(
                models.DashboardWidgetPreference.user_id == user.id,
                models.DashboardWidgetPreference.widget_key.in_([update.widget_key for update in updates]),
            )
            .all()
        )
        rows_by_key = {row.widget_key: row for row in existing_rows}

        now = time_utils.utcnow()
        for update in updates:
            row = rows_by_key.get(update.widget_key)
            if row is None:
                row = models.DashboardWidgetPreference(
                    user_id=user.id,
                    widget_key=update.widget_key,
                    created_at=now,
                    updated_at=now,
                )
                self.db.add(row)
                rows_by_key[update.widget_key] = row

            if update.is_visible is not None:
                row.is_visible = update.is_visible
            if update.is_collapsed is not None:
                row.is_collapsed = update.is_collapsed
            if "prompt_override" in update.model_fields_set:
                row.prompt_override = planner_schemas.normalize_prompt_override(update.prompt_override)
            if "briefing_email_window" in update.model_fields_set:
                row.briefing_email_window = update.briefing_email_window
            row.updated_at = now

        self.db.commit()
        return self.list_widget_preferences(user)
