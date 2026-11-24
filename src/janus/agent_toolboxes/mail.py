"""Mail-focused Janus agent tools."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy import or_

from janus import models, time_utils
from janus.agent_toolboxes.shared import ToolContext, iso, parse_priority
from janus.services.email_service import EmailService

_BODY_SEARCH_RECENT_CANDIDATE_LIMIT = 250


def _serialize_email_summary(row: models.EmailRecord) -> dict[str, Any]:
    """Return a summary payload for an email record."""
    return {
        "id": row.id,
        "sender": row.sender,
        "subject": row.subject,
        "snippet": row.snippet,
        "priority": row.priority.value,
        "received_at": iso(row.received_at),
    }


def _serialize_email_detail(row: models.EmailRecord) -> dict[str, Any]:
    """Return a detailed payload for an email record."""
    payload = _serialize_email_summary(row)
    payload.update(
        {
            "body": row.body,
            "reasoning": row.reasoning,
            "classification_confidence": row.classification_confidence.value if row.classification_confidence else None,
            "account_id": row.account_id,
            "is_read": row.is_read,
        }
    )
    return payload


def _normalize_recipients(values: list[str] | None) -> list[str]:
    """Normalize and validate a recipient list."""
    cleaned = [value.strip() for value in values or [] if value and value.strip()]
    if len(cleaned) != len(set(cleaned)):
        # Preserve caller order while removing duplicates.
        cleaned = list(dict.fromkeys(cleaned))
    return cleaned


def _normalize_email_ids(email_ids: list[int]) -> list[int]:
    """Validate and de-duplicate email ids while preserving order."""
    if not email_ids:
        raise ValueError("email_ids must not be empty")
    normalized: list[int] = []
    seen: set[int] = set()
    for email_id in email_ids:
        if email_id <= 0:
            raise ValueError("email_ids must contain positive integers")
        if email_id not in seen:
            normalized.append(email_id)
            seen.add(email_id)
    return normalized


def _parse_email_datetime(value: str, *, field_name: str) -> datetime:
    """Parse an ISO-8601 datetime string into a normalized UTC timestamp."""
    try:
        parsed = time_utils.normalize_timestamp(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    if parsed is None:
        raise ValueError(f"{field_name} must be a valid ISO-8601 datetime")
    return parsed.astimezone(UTC)


def _resolve_email_time_bounds(
    *,
    since_hours: int | None,
    start_at: str | None,
    end_at: str | None,
    date_preset: str | None,
) -> tuple[datetime | None, datetime | None]:
    """Resolve supported email time filters into concrete UTC bounds.

    ``today`` and ``yesterday`` use UTC day boundaries because Janus does not
    currently persist a per-user timezone for mail-tool filtering.
    """
    if since_hours is not None and since_hours < 0:
        raise ValueError("since_hours must be greater than or equal to 0")
    if date_preset and (start_at is not None or end_at is not None):
        raise ValueError("date_preset cannot be combined with start_at or end_at")

    now = time_utils.utcnow()
    resolved_start: datetime | None = None
    resolved_end: datetime | None = None

    if date_preset:
        cleaned_preset = date_preset.strip().lower()
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if cleaned_preset == "today":
            resolved_start = start_of_today
            resolved_end = start_of_today + timedelta(days=1)
        elif cleaned_preset == "yesterday":
            resolved_start = start_of_today - timedelta(days=1)
            resolved_end = start_of_today
        elif cleaned_preset == "last_24_hours":
            resolved_start = now - timedelta(hours=24)
            resolved_end = now
        else:
            raise ValueError("date_preset must be one of: today, yesterday, last_24_hours")

    if since_hours is not None:
        since_start = now - timedelta(hours=since_hours)
        resolved_start = since_start if resolved_start is None else max(resolved_start, since_start)

    if start_at is not None:
        resolved_start = _parse_email_datetime(start_at, field_name="start_at")
    if end_at is not None:
        resolved_end = _parse_email_datetime(end_at, field_name="end_at")
    if resolved_start is not None and resolved_end is not None and resolved_start >= resolved_end:
        raise ValueError("start_at must be earlier than end_at")

    return (resolved_start, resolved_end)


def _body_contains_query(row: models.EmailRecord, query: str) -> bool:
    """Return whether a stored email body contains the case-insensitive query."""
    body = row.body or ""
    return query.casefold() in body.casefold()


def build_mail_tools(context: ToolContext) -> list[BaseTool]:  # noqa: C901
    """Build tools for reading, relabeling, batching, and sending mail."""
    db = context.db
    service = EmailService(db)

    @tool("search_emails")
    def search_emails(
        query: str = "",
        limit: int = 10,
        since_hours: int | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
        date_preset: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search stored emails by text and/or a bounded UTC time window.

        Subject, sender, and snippet matches run in SQL. Body-only matches are
        checked against a bounded set of the most recent candidate emails so the
        tool avoids an unbounded full-body scan.
        """
        if limit <= 0:
            return []
        cleaned_query = query.strip()
        start_bound, end_bound = _resolve_email_time_bounds(
            since_hours=since_hours,
            start_at=start_at,
            end_at=end_at,
            date_preset=date_preset,
        )
        if not cleaned_query and start_bound is None:
            raise ValueError("provide query or a bounded lower time filter")
        stmt = service.query_emails_for_user(context.user.id)
        if start_bound is not None:
            stmt = stmt.filter(models.EmailRecord.received_at >= start_bound)
        if end_bound is not None:
            stmt = stmt.filter(models.EmailRecord.received_at < end_bound)
        ordered_stmt = stmt.order_by(models.EmailRecord.received_at.desc())

        if cleaned_query:
            like = f"%{cleaned_query}%"
            metadata_rows = (
                ordered_stmt.filter(
                    or_(
                        models.EmailRecord.subject.ilike(like),
                        models.EmailRecord.sender.ilike(like),
                        models.EmailRecord.snippet.ilike(like),
                    )
                )
                .limit(limit)
                .all()
            )
            if len(metadata_rows) >= limit:
                rows = metadata_rows
            else:
                seen_ids = {row.id for row in metadata_rows}
                body_candidates = ordered_stmt.limit(max(limit, _BODY_SEARCH_RECENT_CANDIDATE_LIMIT)).all()
                body_rows = [
                    row
                    for row in body_candidates
                    if row.id not in seen_ids and _body_contains_query(row, cleaned_query)
                ]
                rows = sorted(
                    [*metadata_rows, *body_rows],
                    key=lambda row: row.received_at or datetime.min.replace(tzinfo=UTC),
                    reverse=True,
                )[:limit]
        else:
            rows = ordered_stmt.limit(limit).all()
        return [_serialize_email_summary(row) for row in rows]

    @tool("get_email_detail")
    def get_email_detail(email_id: int) -> dict[str, Any]:
        """Returns detail for a single stored email."""
        row = service.get_email_for_user(email_id, context.user.id)
        if row is None:
            raise ValueError(f"email {email_id} not found")
        return _serialize_email_detail(row)

    @tool("get_email_details_batch")
    def get_email_details_batch(email_ids: list[int]) -> dict[str, Any]:
        """Returns detailed payloads for multiple emails by id."""
        normalized_ids = _normalize_email_ids(email_ids)
        rows = service.get_emails_for_user(normalized_ids, context.user.id)
        rows_by_id = {row.id: row for row in rows}
        found_ids = [email_id for email_id in normalized_ids if email_id in rows_by_id]
        return {
            "emails": [_serialize_email_detail(rows_by_id[email_id]) for email_id in found_ids],
            "missing_ids": [email_id for email_id in normalized_ids if email_id not in rows_by_id],
        }

    @tool("relabel_email")
    def relabel_email(email_id: int, priority: str) -> dict[str, Any]:
        """Updates EmailRecord.priority (IMPORTANT/DIGEST/IGNORE/SPAM)."""
        row = service.update_email_priority_for_user(email_id, parse_priority(priority), context.user)
        if row is None:
            raise ValueError(f"email {email_id} not found")
        return {
            "id": row.id,
            "priority": row.priority.value,
        }

    @tool("relabel_emails_batch")
    def relabel_emails_batch(email_ids: list[int], priority: str) -> dict[str, Any]:
        """Updates the priority for multiple emails and reports any missing ids."""
        normalized_ids = _normalize_email_ids(email_ids)
        parsed_priority = parse_priority(priority)
        updated: list[dict[str, Any]] = []
        missing_ids: list[int] = []
        for email_id in normalized_ids:
            row = service.update_email_priority_for_user(email_id, parsed_priority, context.user)
            if row is None:
                missing_ids.append(email_id)
                continue
            updated.append({"id": row.id, "priority": row.priority.value})
        return {
            "priority": parsed_priority.value,
            "updated": updated,
            "missing_ids": missing_ids,
        }

    @tool("send_email")
    def send_email(
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
        """Sends a plain-text email using a configured Janus mail account."""
        normalized_to = _normalize_recipients(to)
        normalized_cc = _normalize_recipients(cc)
        normalized_bcc = _normalize_recipients(bcc)
        if not normalized_to:
            raise ValueError("at least one recipient is required")
        if not subject.strip():
            raise ValueError("subject must not be blank")
        if not body.strip():
            raise ValueError("body must not be blank")
        return service.send_email(
            user_id=context.user.id,
            account_id=account_id,
            to=normalized_to,
            subject=subject.strip(),
            body=body,
            cc=normalized_cc,
            bcc=normalized_bcc,
            smtp_server=smtp_server.strip() if smtp_server else None,
            smtp_port=smtp_port,
            use_ssl=use_ssl,
        )

    return [
        search_emails,
        get_email_detail,
        get_email_details_batch,
        relabel_email,
        relabel_emails_batch,
        send_email,
    ]
