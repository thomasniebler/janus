"""Service for processing incoming emails and determining their priority."""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING

import re2
from sqlalchemy.exc import IntegrityError

from janus import (
    agent_runtime,
    config,
    database,
    event_logger,
    llm_service,
    models,
    planner_service,
    reminder_service,
    time_utils,
    trigger_definition_service,
    trigger_event_filters,
)
from janus.capability_profile import user_connected_capabilities
from janus.services import knowledge_sources
from janus.services.character_state_service import prompt_character
from janus.services.email_service import EmailService
from janus.services.local_planner_service import LocalPlannerService
from janus.services.topic_service import TopicService
from janus.trigger_events import TriggerEvent

if TYPE_CHECKING:
    from janus.runtime_adapter import PreparedStep

logger = logging.getLogger(__name__)


def _compile_regex(pattern: str, *, case_sensitive: bool = True):
    """Compiles a regex with re2's options-based API."""
    options = re2.Options()
    options.case_sensitive = case_sensitive
    return re2.compile(pattern, options=options)


def validate_regex(pattern: str) -> bool:
    """Returns whether a rule pattern can be compiled by re2."""
    try:
        _compile_regex(pattern)
    except Exception:
        return False
    return True


#: Reasoning stored on an email that waits for its ``mail.received`` trigger run.
PENDING_TRIGGER_PROCESSING_REASON = "Waiting for Janus mail processing"


class EmailProcessor:
    """Processor for categorizing and saving incoming emails."""

    def __init__(self, db=None):
        """Initializes the EmailProcessor.

        Args:
            db (Session, optional): A database session. Defaults to a new SessionLocal.
        """
        self.db = db or database.SessionLocal()
        self.llm: llm_service.LLMService | None = None
        self.planner: planner_service.PlannerService | None = None
        self.local_planner: LocalPlannerService | None = None
        self._mail_ingestion_guidance_by_user: dict[int, str] = {}

    def _get_llm(self) -> llm_service.LLMService:
        """Creates the LLM service lazily so regex-only paths stay offline-safe."""
        if self.llm is None:
            self.llm = llm_service.LLMService()
        return self.llm

    def _get_planner(self) -> planner_service.PlannerService:
        """Creates the planner service lazily for LLM-backed classification paths."""
        if self.planner is None:
            self.planner = planner_service.PlannerService(self.db)
        return self.planner

    def _get_local_planner(self) -> LocalPlannerService:
        """Creates the local planner service lazily for planner-aware ingestion."""
        if self.local_planner is None:
            self.local_planner = LocalPlannerService(self.db)
        return self.local_planner

    def _resolve_user_id(self, email_data: dict) -> int | None:
        """Resolve the owning user for an incoming email when possible."""
        account_id = email_data.get("account_id")
        if account_id is not None:
            account = self.db.get(models.EmailAccount, account_id)
            if account is not None and account.user_id is not None:
                return account.user_id
        default_user = TopicService(self.db).get_default_user()
        return default_user.id if default_user is not None else None

    def _get_mail_ingestion_guidance(self, user_id: int | None) -> str | None:
        """Return classifier guidance from the owning user's character constitution and the skill library.

        Sourced from ``janus.character`` (JAN-201). Cached per user for the lifetime of this processor. Returns
        ``None`` (built-in classifier only) when no owning user resolves.
        """
        if user_id is None:
            return None
        if user_id not in self._mail_ingestion_guidance_by_user:
            constitution, character_state = prompt_character(self.db, user_id)
            self._mail_ingestion_guidance_by_user[user_id] = agent_runtime.build_janus_mail_ingestion_guidance(
                constitution, agent_runtime.load_skill_library(self.db), character_state=character_state
            )
        return self._mail_ingestion_guidance_by_user[user_id]

    def _prepare_ingestion_runtime_step(self, email_data: dict, user_id: int | None) -> PreparedStep | None:
        """Authorize the plan-driven runtime step backing this classification decision.

        Mail ingestion keeps regex rules as its fast path (see ``process_email``
        docstring); this only runs for the LLM judgment-call fallback so that
        fallback shares the same registry/adapter, capability gating, and
        auditable denial trail as chat and briefing (JAN-164). It intentionally
        does not change what ``classify_email`` does today -- it authorizes and
        records the bounded ingestion toolset the runtime allows, which is the
        seam later ingestion tool-calls (e.g. "look up planner context") can use.
        """
        if user_id is None:
            return None
        user = self.db.get(models.User, user_id)
        if user is None:
            return None
        # Imported lazily: the runtime adapter builds the tool registry, whose
        # rule toolbox imports this module.
        from janus.runtime_adapter import RuntimeAdapter

        adapter = RuntimeAdapter(self.db, user)
        registry_entries = adapter.registry.entries()
        requested_tool_ids = tuple(entry.id for entry in registry_entries)
        granted_capabilities = user_connected_capabilities(self.db, user)
        subject = (email_data.get("subject") or "").strip() or "(no subject)"
        task_id = f"ingestion-{email_data.get('message_id') or subject}"[:200]
        try:
            return adapter.prepare_step(
                task_id=task_id,
                objective=f"Classify email: {subject}",
                requested_tool_ids=requested_tool_ids,
                task_mode="ingestion",
                granted_capabilities=granted_capabilities,
                confirmed_tool_ids=frozenset(requested_tool_ids),
            )
        except ValueError:
            # A malformed task/step identity should never block classification.
            return None

    def process_email(self, email_data: dict) -> models.EmailRecord:
        """Processes incoming email data, categorizes it, and saves to the database.

        Args:
            email_data (dict): The raw email data to process.

        Returns:
            EmailRecord: The saved email record.
        """
        # Everything logged while filing this message belongs to the
        # account's owner (JAN-272); an unowned account logs system rows.
        with event_logger.owned_by(self._resolve_user_id(email_data)):
            return self._process_email(email_data)

    def _process_email(self, email_data: dict) -> models.EmailRecord:  # noqa: C901
        """File one message; see :meth:`process_email`."""
        # Check if email already exists
        existing = (
            self.db.query(models.EmailRecord).filter(models.EmailRecord.message_id == email_data["message_id"]).first()
        )
        if existing:
            return existing

        # Determine priority: Rules -> LLM (with Planner context) -> Default (DIGEST)
        priority = self._evaluate_rules(email_data)
        handed_over = self._hand_over_to_triggers(email_data, priority)
        if handed_over is not None:
            return handed_over
        reasoning = "Matched user rule"
        confidence = models.ConfidenceLevel.HIGH
        planner_context = None

        if priority is None:
            event_logger.log(
                models.EventType.INFO,
                "EmailProcessor",
                f"No rules matched for {email_data.get('subject')}. Evaluating with LLM...",
                {"email_subject": email_data.get("subject"), "email_sender": email_data.get("sender")},
            )
            try:
                user_id = self._resolve_user_id(email_data)
                planner_context = (
                    self._get_local_planner().build_context_data(user_id)
                    if user_id is not None
                    else self._get_planner().get_context_data()
                )
                prepared_step = self._prepare_ingestion_runtime_step(email_data, user_id)
                if prepared_step is not None and prepared_step.denials:
                    event_logger.log(
                        models.EventType.INFO,
                        "EmailProcessor",
                        "Ingestion runtime step denied some tools for this classification.",
                        {
                            "task_id": prepared_step.task.task_id,
                            "denied": [
                                {"tool_id": denial.tool_id, "reason": denial.reason} for denial in prepared_step.denials
                            ],
                        },
                    )
                priority, reasoning, confidence = self._get_llm().classify_email(
                    email_data,
                    planner_context,
                    system_prompt=self._get_mail_ingestion_guidance(user_id),
                )
            except Exception as exc:
                event_logger.log(
                    models.EventType.ERROR,
                    "EmailProcessor",
                    f"LLM-backed classification unavailable: {exc}",
                    {"email_subject": email_data.get("subject"), "email_sender": email_data.get("sender")},
                )

        # Final fallback
        if priority is None:
            event_logger.log(
                models.EventType.INFO,
                "EmailProcessor",
                f"LLM did not classify email {email_data.get('subject')}. Defaulting to DIGEST.",
                {"email_subject": email_data.get("subject"), "email_sender": email_data.get("sender")},
            )
            priority = models.Priority.DIGEST
            reasoning = "Fallback default"
            confidence = models.ConfidenceLevel.LOW

        new_email = models.EmailRecord(
            message_id=email_data["message_id"],
            account_id=email_data["account_id"],
            imap_uid=email_data.get("uid"),
            graph_message_id=email_data.get("graph_message_id"),
            subject=email_data["subject"],
            sender=email_data["sender"],
            snippet=email_data["snippet"],
            body=email_data.get("body") or email_data.get("snippet"),
            priority=priority,
            reasoning=reasoning,
            classification_confidence=confidence,
            received_at=self._resolve_received_at(email_data),
        )

        try:
            self.db.add(new_email)
            self.db.commit()
            self.db.refresh(new_email)
            self._stage_attachments(new_email, email_data, self._resolve_user_id(email_data))
            try:
                reminder_service.ReminderService(self.db).capture_email_reminders(new_email)
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                event_logger.log(
                    models.EventType.WARNING,
                    "ReminderService",
                    f"Failed to capture reminders for email {new_email.message_id}: {exc}",
                    {"message_id": new_email.message_id, "account_id": new_email.account_id},
                )
            try:
                if planner_context is None:
                    user_id = self._resolve_user_id(email_data)
                    planner_context = (
                        self._get_local_planner().build_context_data(user_id)
                        if user_id is not None
                        else self._get_planner().get_context_data()
                    )
                TopicService(self.db).capture_email_topics(
                    new_email, user_id=self._resolve_user_id(email_data), planner_context=planner_context
                )
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                event_logger.log(
                    models.EventType.WARNING,
                    "TopicService",
                    f"Failed to capture topics for email {new_email.message_id}: {exc}",
                    {"message_id": new_email.message_id, "account_id": new_email.account_id},
                )
            try:
                self._get_local_planner().capture_email_suggestions(new_email)
            except Exception as exc:
                self.db.rollback()
                event_logger.log(
                    models.EventType.WARNING,
                    "LocalPlannerService",
                    f"Failed to capture planner suggestions for email {new_email.message_id}: {exc}",
                    {"message_id": new_email.message_id, "account_id": new_email.account_id},
                )
            event_logger.log(
                models.EventType.SUCCESS,
                "EmailProcessor",
                f"Email {new_email.message_id} processed with priority {new_email.priority.value}.",
                {"subject": new_email.subject, "sender": new_email.sender, "reasoning": new_email.reasoning},
            )
            return new_email
        except IntegrityError as exc:
            self.db.rollback()
            existing = None
            if email_data.get("uid") is not None:
                existing = (
                    self.db.query(models.EmailRecord)
                    .filter(
                        models.EmailRecord.account_id == email_data["account_id"],
                        models.EmailRecord.imap_uid == email_data["uid"],
                    )
                    .first()
                )
            if existing is None and email_data.get("message_id"):
                existing = (
                    self.db.query(models.EmailRecord)
                    .filter(models.EmailRecord.message_id == email_data["message_id"])
                    .first()
                )
            if existing is not None:
                return existing
            event_logger.log(
                models.EventType.ERROR,
                "EmailProcessor",
                f"Failed to save email {new_email.message_id} to database: {exc}",
                {"subject": new_email.subject, "sender": new_email.sender},
            )
            raise
        except Exception as e:
            self.db.rollback()
            event_logger.log(
                models.EventType.ERROR,
                "EmailProcessor",
                f"Failed to save email {new_email.message_id} to database: {e}",
                {"subject": new_email.subject, "sender": new_email.sender},
            )
            raise

    def _hand_over_to_triggers(
        self, email_data: dict, rule_priority: models.Priority | None
    ) -> models.EmailRecord | None:
        """Store the email for ``mail.received`` trigger runs, or ``None`` to classify it synchronously."""
        owner_id = self._mail_event_owner(email_data, rule_priority)
        if owner_id is None:
            return None
        return self._store_for_trigger_processing(email_data, rule_priority, owner_id)

    def _mail_event_owner(self, email_data: dict, rule_priority: models.Priority | None) -> int | None:
        """Return the owner whose ``mail.received`` triggers should process this email, if any (JAN-232).

        Event-driven processing needs the durable trigger worker and at least
        one enabled event definition for ``mail.received``; otherwise (and for
        emails a rule already filed as SPAM/IGNORE) the synchronous classifier
        below stays in charge, so mail is never left unprocessed.
        """
        if rule_priority in {models.Priority.SPAM, models.Priority.IGNORE}:
            return None
        if not config.settings.runtime_trigger_worker_enabled:
            return None
        user_id = self._resolve_user_id(email_data)
        if user_id is None:
            return None
        subscribed = (
            self.db.query(models.TriggerDefinition)
            .filter(
                models.TriggerDefinition.user_id == user_id,
                models.TriggerDefinition.enabled.is_(True),
                models.TriggerDefinition.kind == models.TriggerDefinitionKind.EVENT.value,
                models.TriggerDefinition.event_type == TriggerEvent.MAIL_RECEIVED.value,
            )
            .all()
        )
        # Only hand mail over when a definition will take it (JAN-363): mail every definition's
        # filter or rate limit turns away is classified here instead of waiting forever.
        data = self._mail_event_data(email_data, rule_priority)
        accepted = any(trigger_event_filters.admit(self.db, d, data, record=False) for d in subscribed)
        return user_id if accepted else None

    def _mail_event_data(
        self, email_data: dict, rule_priority: models.Priority | None, email: models.EmailRecord | None = None
    ) -> dict:
        """The ``mail.received`` payload; before the email is stored, ``email_id`` is unknown."""
        account = email.account if email is not None else self.db.get(models.EmailAccount, email_data["account_id"])
        received_at = email.received_at if email is not None else self._resolve_received_at(email_data)
        body = email_data.get("body") or email_data.get("snippet") or ""
        return {
            "email_id": email.id if email is not None else None,
            "account": account.email if account is not None else None,
            "from": email_data["sender"],
            "subject": email_data["subject"],
            "received_at": received_at.isoformat() if received_at else None,
            "priority_from_rule": rule_priority.value if rule_priority is not None else None,
            "body": body[:4000],
        }

    def _stage_attachments(self, email_record: models.EmailRecord, email_data: dict, user_id: int | None) -> None:
        """Stages the email's attachments as knowledge sources (JAN-242); never fails the ingestion."""
        attachments = email_data.get("attachments") or []
        skipped = email_record.priority in (models.Priority.SPAM, models.Priority.IGNORE)
        if not attachments or user_id is None or skipped:
            return
        try:
            for part, attachment in enumerate(attachments):
                knowledge_sources.stage_document(
                    self.db,
                    user_id=user_id,
                    source_type=models.KnowledgeSourceType.EMAIL_ATTACHMENT,
                    owner_id=email_record.id,
                    part=part,
                    filename=attachment.get("filename"),
                    mime_type=attachment.get("mime_type"),
                    data=attachment.get("data") or b"",
                )
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            event_logger.log(
                models.EventType.WARNING,
                "KnowledgeSources",
                f"Failed to stage attachments of email {email_record.message_id}: {exc}",
                {"message_id": email_record.message_id, "account_id": email_record.account_id},
            )

    def _store_for_trigger_processing(
        self, email_data: dict, rule_priority: models.Priority | None, user_id: int
    ) -> models.EmailRecord | None:
        """Store the email and hand it to the owner's ``mail.received`` trigger definitions.

        Returns ``None`` when no definition took it after all: the pre-check in
        ``_mail_event_owner`` is a read, so a concurrent email can use up the last
        rate-limit slot first (JAN-363). The stored row is then removed and the
        caller classifies the email synchronously, so it never waits for a run
        that will not come.
        """
        pending = rule_priority is None
        new_email = models.EmailRecord(
            message_id=email_data["message_id"],
            account_id=email_data["account_id"],
            imap_uid=email_data.get("uid"),
            graph_message_id=email_data.get("graph_message_id"),
            subject=email_data["subject"],
            sender=email_data["sender"],
            snippet=email_data["snippet"],
            body=email_data.get("body") or email_data.get("snippet"),
            # Provisional until Janus classifies it with set_email_priority.
            priority=models.Priority.DIGEST if pending else rule_priority,
            reasoning=PENDING_TRIGGER_PROCESSING_REASON if pending else "Matched user rule",
            classification_confidence=models.ConfidenceLevel.LOW if pending else models.ConfidenceLevel.HIGH,
            received_at=self._resolve_received_at(email_data),
        )
        try:
            self.db.add(new_email)
            self.db.commit()
            self.db.refresh(new_email)
        except IntegrityError:
            self.db.rollback()
            existing = (
                self.db.query(models.EmailRecord)
                .filter(models.EmailRecord.message_id == email_data["message_id"])
                .first()
            )
            if existing is not None:
                return existing
            raise
        try:
            runs = trigger_definition_service.emit_event(
                self.db,
                user_id,
                TriggerEvent.MAIL_RECEIVED,
                new_email.message_id,
                self._mail_event_data(email_data, rule_priority, new_email),
            )
        except Exception as exc:
            self.db.rollback()
            logger.warning("Handing email %s to mail.received triggers failed: %s", new_email.message_id, exc)
            runs = []
        if not runs:
            self.db.delete(new_email)
            self.db.commit()
            return None
        self._stage_attachments(new_email, email_data, user_id)
        event_logger.log(
            models.EventType.INFO,
            "EmailProcessor",
            f"Email {new_email.message_id} stored and handed to mail.received triggers.",
            {"subject": new_email.subject, "sender": new_email.sender},
        )
        return new_email

    def _resolve_received_at(self, email_data: dict):
        """Normalizes an incoming timestamp and applies an explicit fallback."""
        raw_value = email_data.get("received_at")
        if raw_value is not None:
            try:
                normalized = time_utils.normalize_timestamp(raw_value)
                if normalized is not None:
                    return normalized.astimezone(UTC).replace(tzinfo=None)
            except Exception:
                event_logger.log(
                    models.EventType.WARNING,
                    "EmailProcessor",
                    f"Invalid received_at for email {email_data.get('message_id')}; using current UTC time.",
                    {"received_at": str(raw_value)},
                )
        return time_utils.utcnow().replace(tzinfo=None)

    def _safe_search(self, pattern: str, text: str, timeout: float = 1.0) -> bool:
        """Executes a rule match with re2.

        Args:
            pattern (str): The regex pattern to search for.
            text (str): The text to search in.
            timeout (float): Retained for backward compatibility with existing tests.

        Returns:
            bool: True if a match was found, else False.
        """
        del timeout
        try:
            compiled = _compile_regex(pattern, case_sensitive=False)
            return bool(compiled.search(text))
        except Exception as exc:
            logger.error("Error in regex search: %s", exc)
            return False

    def _evaluate_rules(self, email_data: dict) -> models.Priority | None:
        """Evaluates the mail owner's rules (sender, subject) against the incoming email.

        Only the rules of the mail's owner (the receiving account's owner, see
        :meth:`_resolve_user_id`) apply, so another user's rule never
        classifies this mail (JAN-295). Every rule has an owner (JAN-338).

        Args:
            email_data (dict): The raw email data to evaluate.

        Returns:
            Optional[Priority]: The priority if a rule matched, else None.
        """
        # Evaluate the owner's rules in ascending order; first match wins
        rules = EmailService(self.db).get_active_rules_for_mail_owner(self._resolve_user_id(email_data))

        for rule in rules:
            field_value = email_data.get(rule.field, "")
            if field_value and self._safe_search(rule.pattern, field_value):
                event_logger.log(
                    models.EventType.INFO,
                    "EmailProcessor",
                    f"Rule '{rule.name}' matched for {rule.field}: {rule.pattern}",
                    {"email_subject": email_data.get("subject"), "email_sender": email_data.get("sender")},
                )
                return rule.priority

        return None
