"""Email and rule service for Janus."""

import re
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy import false, inspect
from sqlalchemy.orm import Query, selectinload

from janus import event_logger, llm_service, models, planner_service, security
from janus.services.base import BaseService
from janus.services.topic_service import TopicService

_KNOWN_SMTP_HOSTS_BY_IMAP_HOST = {
    "imap.gmail.com": "smtp.gmail.com",
    "imap.mail.yahoo.com": "smtp.mail.yahoo.com",
    "imap.mail.me.com": "smtp.mail.me.com",
    "imap.fastmail.com": "smtp.fastmail.com",
    "imap.zoho.com": "smtp.zoho.com",
    "imap.mail.att.net": "smtp.mail.att.net",
    "imap-mail.outlook.com": "smtp-mail.outlook.com",
    "outlook.office365.com": "smtp.office365.com",
}
_KNOWN_SMTP_HOSTS_BY_EMAIL_DOMAIN = {
    "gmail.com": "smtp.gmail.com",
    "googlemail.com": "smtp.gmail.com",
    "yahoo.com": "smtp.mail.yahoo.com",
    "icloud.com": "smtp.mail.me.com",
    "mac.com": "smtp.mail.me.com",
    "me.com": "smtp.mail.me.com",
    "outlook.com": "smtp-mail.outlook.com",
    "hotmail.com": "smtp-mail.outlook.com",
    "live.com": "smtp-mail.outlook.com",
    "msn.com": "smtp-mail.outlook.com",
}


class EmailService(BaseService):
    """Handles email accounts and prioritization rules."""

    _EMAIL_SEND_FAILURE_MESSAGE = "unable to send email with the configured SMTP settings"
    _LEGACY_UNKNOWN_SENDER = "Unknown sender"
    _LEGACY_NO_SUBJECT = "(no subject)"

    @staticmethod
    def _rule_scope(user_id: int | None):
        """Return the rules that apply to and may be learned for ``user_id``'s mail (JAN-287, JAN-295).

        Only ``user_id``'s own rules; another user's rule never is. Every rule has an owner
        (``rules.user_id`` is ``NOT NULL`` since ``37b30d7c85e1``, which gave pre-ownership rules to
        the first admin; JAN-338), so ``None`` (mail without an owner) matches no rule.
        """
        if user_id is None:
            return false()
        return models.Rule.user_id == user_id

    def get_active_rules_for_mail_owner(self, user_id: int | None) -> list[models.Rule]:
        """Returns the active rules, in evaluation order, the IMAP rule evaluator applies to ``user_id``'s mail."""
        query = self.db.query(models.Rule).filter(models.Rule.is_active, self._rule_scope(user_id))
        return query.order_by(models.Rule.order.asc(), models.Rule.id.asc()).all()

    def _next_rule_order(self, user: models.User) -> int:
        """Returns the next rule evaluation order among the rules ``user`` may touch."""
        last_rule = (
            self.db.query(models.Rule).filter(self._rule_scope(user.id)).order_by(models.Rule.order.desc()).first()
        )
        return 0 if last_rule is None else last_rule.order + 1

    def _topic_badges_available(self) -> bool:
        """Returns whether the optional topic-badge tables exist in the current database."""
        bind = self.db.get_bind()
        if bind is None:
            return False
        table_names = set(inspect(bind).get_table_names())
        return {"topic_evidence", "tracked_topics"} <= table_names

    def _serialize_email(self, email: models.EmailRecord) -> dict[str, Any]:
        """Serialize one email, normalizing legacy nullable fields for API consumers."""
        payload = models.to_dict(email)
        payload["message_id"] = email.message_id or f"<legacy-email-{email.id}@janus.local>"
        payload["sender"] = (email.sender or "").strip() or self._LEGACY_UNKNOWN_SENDER
        payload["subject"] = (email.subject or "").strip() or self._LEGACY_NO_SUBJECT
        payload["snippet"] = (email.snippet or email.body or "").strip()
        return payload

    def _build_rule_suggestion(self, email: models.EmailRecord, priority: models.Priority) -> dict[str, Any]:
        """Builds a conservative rule suggestion from a corrected email."""
        if email.sender and "@" in email.sender:
            escaped_sender = re.escape(email.sender.strip())
            return {
                "suggested_rule_name": f"Messages from {email.sender}",
                "suggested_rule_pattern": f"^{escaped_sender}$",
                "suggested_rule_field": "sender",
                "suggested_rule_priority": priority,
                "suggestion_confidence": models.ConfidenceLevel.HIGH,
                "suggestion_reason": (
                    f"You corrected an email from {email.sender} to {priority.value}. "
                    "Janus suggests an exact-sender rule because that is specific and low-risk."
                ),
            }

        subject = (email.subject or "").strip()
        fallback_subject = subject[:80] or "similar messages"
        escaped_subject = re.escape(fallback_subject)
        return {
            "suggested_rule_name": f"Messages like '{fallback_subject}'",
            "suggested_rule_pattern": escaped_subject,
            "suggested_rule_field": "subject",
            "suggested_rule_priority": priority,
            "suggestion_confidence": models.ConfidenceLevel.LOW,
            "suggestion_reason": (
                "Janus could not derive a stable sender match, so it suggested a subject-based rule for manual review."
            ),
        }

    def _find_matching_rules(self, suggestion: dict[str, Any], user: models.User) -> list[models.Rule]:
        """Returns ``user``'s rules sharing a correction-derived rule's identity."""
        return (
            self.db.query(models.Rule)
            .filter(
                self._rule_scope(user.id),
                models.Rule.field == suggestion["suggested_rule_field"],
                models.Rule.pattern == suggestion["suggested_rule_pattern"],
            )
            .order_by(models.Rule.is_active.desc(), models.Rule.order.desc(), models.Rule.id.desc())
            .all()
        )

    def _apply_suggestion_to_matching_rules(
        self,
        matching_rules: list[models.Rule],
        suggestion: dict[str, Any],
        user: models.User,
    ) -> models.Rule:
        """Re-points ``user``'s preferred matching rule at ``suggestion`` and retires its duplicates.

        ``matching_rules`` must come from ``_find_matching_rules(..., user)``,
        so every row is ``user``'s own.
        """
        existing_rule = matching_rules[0]
        existing_rule.name = suggestion["suggested_rule_name"]
        existing_rule.priority = suggestion["suggested_rule_priority"]
        existing_rule.is_active = True
        existing_rule.order = self._next_rule_order(user)
        for duplicate_rule in matching_rules[1:]:
            duplicate_rule.is_active = False
        self.db.flush()
        return existing_rule

    def _persist_rule_from_correction(
        self,
        email: models.EmailRecord,
        priority: models.Priority,
        user: models.User,
    ) -> models.Rule:
        """Creates or updates ``user``'s reusable active rule from a manual correction."""
        suggestion = self._build_rule_suggestion(email, priority)
        matching_rules = self._find_matching_rules(suggestion, user)
        if matching_rules:
            return self._apply_suggestion_to_matching_rules(matching_rules, suggestion, user)

        new_rule = models.Rule(
            name=suggestion["suggested_rule_name"],
            pattern=suggestion["suggested_rule_pattern"],
            field=suggestion["suggested_rule_field"],
            priority=suggestion["suggested_rule_priority"],
            order=self._next_rule_order(user),
            user_id=user.id,
        )
        self.db.add(new_rule)
        self.db.flush()
        return new_rule

    def _queue_clarification_request(
        self,
        email: models.EmailRecord,
        previous_priority: models.Priority,
        requested_priority: models.Priority,
        user: models.User,
    ) -> None:
        """Stores a pending clarification item for a manual email correction."""
        suggestion = self._build_rule_suggestion(email, requested_priority)
        if self._find_matching_rules(suggestion, user):
            return

        existing_request = (
            self.db.query(models.EmailClarificationRequest)
            .filter(
                models.EmailClarificationRequest.user_id == user.id,
                models.EmailClarificationRequest.email_id == email.id,
                models.EmailClarificationRequest.requested_priority == requested_priority,
                models.EmailClarificationRequest.status == models.ClarificationStatus.PENDING,
            )
            .first()
        )
        if existing_request is not None:
            existing_request.previous_priority = previous_priority
            existing_request.suggested_rule_name = suggestion["suggested_rule_name"]
            existing_request.suggested_rule_pattern = suggestion["suggested_rule_pattern"]
            existing_request.suggested_rule_field = suggestion["suggested_rule_field"]
            existing_request.suggested_rule_priority = suggestion["suggested_rule_priority"]
            existing_request.suggestion_confidence = suggestion["suggestion_confidence"]
            existing_request.suggestion_reason = suggestion["suggestion_reason"]
            return

        self.db.add(
            models.EmailClarificationRequest(
                user_id=user.id,
                email_id=email.id,
                previous_priority=previous_priority,
                requested_priority=requested_priority,
                **suggestion,
            )
        )

    def list_clarification_requests(self, user: models.User) -> list[models.EmailClarificationRequest]:
        """Returns clarification items for the authenticated user."""
        return (
            self.db.query(models.EmailClarificationRequest)
            .options(selectinload(models.EmailClarificationRequest.email))
            .filter(models.EmailClarificationRequest.user_id == user.id)
            .order_by(
                models.EmailClarificationRequest.status.asc(),
                models.EmailClarificationRequest.updated_at.desc(),
            )
            .all()
        )

    def approve_clarification_request(
        self,
        clarification_id: int,
        user: models.User,
    ) -> models.EmailClarificationRequest | None:
        """Approves a clarification item and creates its suggested rule."""
        clarification = (
            self.db.query(models.EmailClarificationRequest)
            .filter(
                models.EmailClarificationRequest.id == clarification_id,
                models.EmailClarificationRequest.user_id == user.id,
            )
            .first()
        )
        if clarification is None:
            return None

        suggestion = {
            "suggested_rule_name": clarification.suggested_rule_name,
            "suggested_rule_field": clarification.suggested_rule_field,
            "suggested_rule_pattern": clarification.suggested_rule_pattern,
            "suggested_rule_priority": clarification.suggested_rule_priority,
        }
        matching_rules = self._find_matching_rules(suggestion, user)
        if matching_rules:
            existing_rule = self._apply_suggestion_to_matching_rules(matching_rules, suggestion, user)
        else:
            existing_rule = models.Rule(
                name=clarification.suggested_rule_name,
                pattern=clarification.suggested_rule_pattern,
                field=clarification.suggested_rule_field,
                priority=clarification.suggested_rule_priority,
                order=self._next_rule_order(user),
                user_id=user.id,
            )
            self.db.add(existing_rule)
            self.db.flush()

        clarification.status = models.ClarificationStatus.APPROVED
        clarification.applied_rule_id = existing_rule.id
        self.db.commit()
        self.db.refresh(clarification)
        return clarification

    def dismiss_clarification_request(
        self,
        clarification_id: int,
        user: models.User,
    ) -> models.EmailClarificationRequest | None:
        """Dismisses a clarification item without creating a rule."""
        clarification = (
            self.db.query(models.EmailClarificationRequest)
            .filter(
                models.EmailClarificationRequest.id == clarification_id,
                models.EmailClarificationRequest.user_id == user.id,
            )
            .first()
        )
        if clarification is None:
            return None

        clarification.status = models.ClarificationStatus.DISMISSED
        self.db.commit()
        self.db.refresh(clarification)
        return clarification

    def get_accounts(self, user_id: int) -> list[models.EmailAccount]:
        """Returns the email accounts owned by ``user_id``."""
        return self.db.query(models.EmailAccount).filter(models.EmailAccount.user_id == user_id).all()

    def get_account_for_user(self, account_id: int, user_id: int) -> models.EmailAccount | None:
        """Returns an email account only if it belongs to the given user."""
        return (
            self.db.query(models.EmailAccount)
            .filter(models.EmailAccount.id == account_id, models.EmailAccount.user_id == user_id)
            .first()
        )

    def serialize_emails(
        self,
        emails: list[models.EmailRecord],
        *,
        user: models.User | None = None,
    ) -> list[dict[str, Any]]:
        """Serialize emails and attach related topic badges for UI navigation."""
        payloads = [self._serialize_email(email) for email in emails]
        if not payloads:
            return payloads

        topic_badges_by_email: dict[int, list[dict[str, Any]]] = {}
        if user is not None and self._topic_badges_available():
            topic_badges_by_email = TopicService(self.db).list_topic_badges_for_source_ids(
                user_id=user.id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_ids=[email.id for email in emails],
            )

        for payload in payloads:
            payload["topics"] = topic_badges_by_email.get(payload["id"], [])

        return payloads

    def add_account(self, data: dict, user_id: int | None = None) -> models.EmailAccount:
        """Adds a new email account."""
        if "password" in data:
            data["password"] = security.security_service.encrypt(data["password"])
        if user_id is not None:
            data["user_id"] = user_id
        new_account = models.EmailAccount(**data)
        self.db.add(new_account)
        self.db.commit()
        self.db.refresh(new_account)
        return new_account

    def update_account_for_user(self, account_id: int, user_id: int, data: dict) -> models.EmailAccount | None:
        """Updates an email account owned by ``user_id``.

        Returns ``None`` when the account does not exist or belongs to another
        user, so callers answer both cases with the same non-disclosing 404.
        """
        account = self.get_account_for_user(account_id, user_id)
        if not account:
            return None

        if account.auth_type != "password":
            # Gmail/Outlook mailboxes read through their connection; their server and identity are fixed.
            for locked in ("password", "imap_server", "imap_port", "username", "email"):
                data.pop(locked, None)
        password = data.pop("password", None)
        for field, value in data.items():
            setattr(account, field, value)
        if password:
            account.password = security.security_service.encrypt(password)

        self.db.commit()
        self.db.refresh(account)
        return account

    def delete_account_for_user(self, account_id: int, user_id: int) -> bool:
        """Removes an email account owned by ``user_id``; ``False`` if missing or foreign."""
        account = self.get_account_for_user(account_id, user_id)
        if not account:
            return False
        self.db.delete(account)
        self.db.commit()
        return True

    def get_rules(self, user_id: int) -> list[models.Rule]:
        """Returns ``user_id``'s prioritization rules ordered by evaluation order."""
        return self.db.query(models.Rule).filter(models.Rule.user_id == user_id).order_by(models.Rule.order.asc()).all()

    def get_rule_for_user(self, rule_id: int, user_id: int) -> models.Rule | None:
        """Returns a rule only if it belongs to the given user."""
        return self.db.query(models.Rule).filter(models.Rule.id == rule_id, models.Rule.user_id == user_id).first()

    def _get_or_create_notification_preference(self, user: models.User) -> models.NotificationPreference:
        """Returns the current user's notification preferences, creating defaults when absent."""
        preference = (
            self.db.query(models.NotificationPreference)
            .filter(models.NotificationPreference.user_id == user.id)
            .first()
        )
        if preference is not None:
            return preference

        preference = models.NotificationPreference(user_id=user.id)
        self.db.add(preference)
        self.db.commit()
        self.db.refresh(preference)
        return preference

    def get_notification_preference(self, user: models.User) -> models.NotificationPreference:
        """Returns a user's notification preferences."""
        return self._get_or_create_notification_preference(user)

    def update_notification_preference(self, user: models.User, data: dict[str, Any]) -> models.NotificationPreference:
        """Updates a user's notification preferences."""
        preference = self._get_or_create_notification_preference(user)
        for field, value in data.items():
            if field in {"sender_allowlist", "sender_blocklist", "keyword_allowlist", "keyword_blocklist"}:
                setattr(preference, field, value or None)
            else:
                setattr(preference, field, value)

        self.db.commit()
        self.db.refresh(preference)
        return preference

    def add_rule(self, data: dict, user_id: int | None = None) -> models.Rule:
        """Adds a new prioritization rule."""
        if user_id is not None:
            data["user_id"] = user_id
        new_rule = models.Rule(**data)
        self.db.add(new_rule)
        self.db.commit()
        self.db.refresh(new_rule)
        return new_rule

    def update_rule_for_user(
        self,
        rule_id: int,
        user_id: int,
        *,
        order: int | None = None,
        is_active: bool | None = None,
    ) -> models.Rule | None:
        """Reorders or toggles a rule owned by ``user_id``; ``None`` if missing or foreign."""
        rule = self.get_rule_for_user(rule_id, user_id)
        if not rule:
            return None
        if order is not None:
            rule.order = order
        if is_active is not None:
            rule.is_active = is_active
        self.db.commit()
        self.db.refresh(rule)
        return rule

    def delete_rule_for_user(self, rule_id: int, user_id: int) -> bool:
        """Removes a rule owned by ``user_id``; ``False`` if missing or foreign."""
        rule = self.get_rule_for_user(rule_id, user_id)
        if not rule:
            return False
        self.db.delete(rule)
        self.db.commit()
        return True

    def query_emails_for_user(self, user_id: int) -> Query:
        """Returns a query over the emails that arrived on one of ``user_id``'s accounts.

        Every user- or run-scoped email read starts here (JAN-137, JAN-286):
        an email is owned through its account, so emails without an account
        have no owner and are never returned. Callers add their own filters,
        ordering, and limits.
        """
        return (
            self.db.query(models.EmailRecord)
            .join(models.EmailAccount, models.EmailRecord.account_id == models.EmailAccount.id)
            .filter(models.EmailAccount.user_id == user_id)
        )

    def get_email_for_user(self, email_id: int, user_id: int) -> models.EmailRecord | None:
        """Returns an email only if it arrived on one of ``user_id``'s accounts."""
        return self.query_emails_for_user(user_id).filter(models.EmailRecord.id == email_id).first()

    def get_emails_for_user(self, email_ids: list[int], user_id: int) -> list[models.EmailRecord]:
        """Returns the emails among ``email_ids`` owned by ``user_id``, newest first; foreign ids are dropped."""
        return (
            self.query_emails_for_user(user_id)
            .filter(models.EmailRecord.id.in_(email_ids))
            .order_by(models.EmailRecord.received_at.desc())
            .all()
        )

    def list_emails_for_user(
        self, user_id: int, limit: int = 100, priority: models.Priority | None = None
    ) -> list[models.EmailRecord]:
        """``user_id``'s newest emails: the inbox (all but SPAM), or only ``priority``'s, e.g. spam (JAN-219)."""
        column = models.EmailRecord.priority
        query = self.query_emails_for_user(user_id).filter(
            column != models.Priority.SPAM if priority is None else column == priority
        )
        return query.order_by(models.EmailRecord.received_at.desc(), models.EmailRecord.id.desc()).limit(limit).all()

    def get_important_emails_for_user(self, user_id: int, hours: int = 48) -> list[models.EmailRecord]:
        """Returns ``user_id``'s IMPORTANT emails from the last ``hours`` hours, newest first."""
        since = datetime.now(UTC) - timedelta(hours=hours)
        return (
            self.query_emails_for_user(user_id)
            .filter(models.EmailRecord.priority == models.Priority.IMPORTANT, models.EmailRecord.received_at >= since)
            .order_by(models.EmailRecord.received_at.desc())
            .all()
        )

    def _resolve_outgoing_account_for_user(self, account_id: int | None, user_id: int) -> models.EmailAccount:
        """Resolve which of ``user_id``'s own active accounts sends outgoing mail (JAN-286).

        Another user's account answers exactly like a missing one, and the
        implicit choice only ever considers ``user_id``'s accounts.
        """
        if account_id is not None:
            account = self.get_account_for_user(account_id, user_id)
            if account is None:
                raise ValueError(f"email account {account_id} not found")
            if not account.is_active:
                raise ValueError(f"email account {account_id} is inactive")
            if account.auth_type != "password":
                raise ValueError(f"email account {account_id} signs in with {account.auth_type} and cannot send mail")
            return account

        active_accounts = (
            self.db.query(models.EmailAccount)
            .filter(
                models.EmailAccount.user_id == user_id,
                models.EmailAccount.is_active.is_(True),
                models.EmailAccount.auth_type == "password",
            )
            .order_by(models.EmailAccount.id.asc())
            .all()
        )
        if not active_accounts:
            raise ValueError("no active email accounts are configured")
        if len(active_accounts) > 1:
            raise ValueError("multiple active email accounts exist; provide account_id explicitly")
        return active_accounts[0]

    def _resolve_smtp_host(self, account: models.EmailAccount, smtp_server: str | None) -> str:
        """Resolve the SMTP host to use for an outgoing email."""
        if smtp_server:
            return smtp_server

        imap_host = (account.imap_server or "").strip()
        if not imap_host:
            raise ValueError("selected email account is missing an IMAP server")

        normalized_imap_host = imap_host.lower().rstrip(".")
        mapped_host = _KNOWN_SMTP_HOSTS_BY_IMAP_HOST.get(normalized_imap_host)
        if mapped_host is not None:
            return mapped_host

        account_domain = None
        for candidate in ((account.email or "").strip(), (account.username or "").strip()):
            if "@" not in candidate:
                continue
            account_domain = candidate.rsplit("@", 1)[1].lower()
            mapped_host = _KNOWN_SMTP_HOSTS_BY_EMAIL_DOMAIN.get(account_domain)
            if mapped_host is not None:
                return mapped_host

        if normalized_imap_host.startswith("imap.") and len(normalized_imap_host) > len("imap."):
            return f"smtp.{normalized_imap_host[5:]}"

        return imap_host

    def send_email(
        self,
        *,
        user_id: int,
        to: list[str],
        subject: str,
        body: str,
        account_id: int | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        smtp_server: str | None = None,
        smtp_port: int | None = None,
        use_ssl: bool = False,
    ) -> dict[str, Any]:
        """Send a plain-text email from one of ``user_id``'s own active accounts."""
        if not to:
            raise ValueError("at least one recipient is required")

        account = self._resolve_outgoing_account_for_user(account_id, user_id)
        username = (account.username or account.email or "").strip()
        if not username:
            raise ValueError("selected email account is missing a username")
        password = security.security_service.decrypt(account.password)
        host = self._resolve_smtp_host(account, smtp_server)
        port = smtp_port if smtp_port is not None else (465 if use_ssl else 587)
        sender = (account.email or "").strip()
        if not sender:
            raise ValueError("selected email account is missing a sender email address")

        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)

        recipients = [*to, *(cc or []), *(bcc or [])]
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        try:
            with smtp_cls(host, port, timeout=30) as smtp:
                if not use_ssl:
                    smtp.ehlo()
                    smtp.starttls()
                    smtp.ehlo()
                smtp.login(username, password)
                smtp.send_message(message, from_addr=sender, to_addrs=recipients)
        except (smtplib.SMTPException, OSError) as exc:
            event_logger.log(
                models.EventType.WARNING,
                "EmailService",
                "Failed to send outgoing email.",
                {
                    "account_id": account.id,
                    "smtp_server": host,
                    "smtp_port": port,
                    "use_ssl": use_ssl,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                user_id=account.user_id,
            )
            raise ValueError(self._EMAIL_SEND_FAILURE_MESSAGE) from exc

        return {
            "status": "sent",
            "account_id": account.id,
            "from": sender,
            "to": to,
            "cc": cc or [],
            "bcc_count": len(bcc or []),
            "subject": subject,
            "smtp_server": host,
            "smtp_port": port,
            "use_ssl": use_ssl,
        }

    def update_email_priority_for_user(
        self,
        email_id: int,
        priority: models.Priority,
        user: models.User,
    ) -> models.EmailRecord | None:
        """Updates the priority of an email owned by ``user`` and optionally regenerates reasoning.

        Args:
            email_id (int): The ID of the email record.
            priority (models.Priority): The new priority.
            user (models.User): The user making the correction; must own the email's account.

        Returns:
            Optional[models.EmailRecord]: The updated email record, or ``None`` when the
            email does not exist, has no account, or belongs to another user.
        """
        email = self.get_email_for_user(email_id, user.id)
        if not email:
            return None

        old_priority = email.priority
        if old_priority == priority:
            return email

        email.priority = priority
        email.classification_confidence = models.ConfidenceLevel.LOW
        persisted_rule = self._persist_rule_from_correction(email, priority, user)
        rule_reason = f"Janus saved this correction as an active {persisted_rule.field} rule for similar messages."

        if priority == models.Priority.IMPORTANT and old_priority != models.Priority.IMPORTANT:
            # Generate reasoning
            try:
                llm = llm_service.LLMService()
                planner = planner_service.PlannerService(self.db)
                context = planner.get_context_data(user_id=user.id)
                email_data = {"sender": email.sender, "subject": email.subject, "snippet": email.snippet}
                _, reasoning, _ = llm.classify_email(email_data, context)
                email.reasoning = f"Corrected by user. {reasoning} {rule_reason}"
            except Exception as exc:
                event_logger.log(
                    models.EventType.WARNING,
                    "EmailService",
                    f"Falling back to manual reasoning for email {email.id}: {exc}",
                    user_id=user.id,
                )
                email.reasoning = f"Corrected by user. Marked as important manually. {rule_reason}"
        else:
            email.reasoning = f"Corrected by user from {old_priority.value} to {priority.value}. {rule_reason}"

        if priority == models.Priority.DIGEST:
            email.is_read = True  # Locally mark as read if it's in digest
        elif priority == models.Priority.IMPORTANT:
            email.is_read = False
        elif priority in {models.Priority.IGNORE, models.Priority.SPAM}:
            email.is_read = True

        self.db.commit()
        self.db.refresh(email)
        return email
