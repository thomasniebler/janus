"""Owner-scoped activity statistics for the Settings → Stats tab (JAN-258).

Every figure is computed for one user only. Daily series are bucketed by the
calendar day in the caller's IANA timezone, so "today" on the chart is the
user's today, not UTC's. Timestamps are stored as naive UTC; only the columns
a series needs are loaded and bucketed in Python, which keeps the date
arithmetic identical across SQLite and any other backend.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select

from janus import models
from janus.models_knowledge import KnowledgeSource, WikiPage
from janus.services.base import BaseService

#: Ranges the Stats tab offers; anything else is rejected rather than clamped.
ALLOWED_RANGES_DAYS = (7, 30, 90)

#: Run outcome buckets shown on the trigger chart.
_TRIGGER_BUCKETS: dict[str, str] = {
    models.RuntimeTriggerStatus.COMPLETED.value: "completed",
    models.RuntimeTriggerStatus.FAILED.value: "failed",
    models.RuntimeTriggerStatus.EXHAUSTED.value: "failed",
    models.RuntimeTriggerStatus.CANCELLED.value: "cancelled",
    models.RuntimeTriggerStatus.QUEUED.value: "pending",
    models.RuntimeTriggerStatus.RUNNING.value: "pending",
    models.RuntimeTriggerStatus.WAITING.value: "pending",
}
_MAIL_BUCKETS = tuple(priority.value for priority in models.Priority)

#: Review outcomes of automations Janus drafted (JAN-235), counted by proposal date.
_AUTOMATION_OUTCOMES = tuple(status.value for status in models.TriggerProposalStatus)
#: Chat approval decisions on ``confirmation="required"`` tool calls (JAN-254).
_CHAT_TOOL_OUTCOMES = ("approved", "denied", "timeout", "disconnected", "unavailable")
#: Character state proposals Janus filed from chat (JAN-301).
_CHARACTER_OUTCOMES = ("proposed", "approved", "rejected")


class StatsError(ValueError):
    """An invalid stats request (surfaced as 422)."""


def _zone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((tz_name or "UTC").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise StatsError(f"unknown timezone: {tz_name}") from error


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _naive_utc(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


class StatsService(BaseService):
    """Computes the activity summary for one user and one date range."""

    def summary(
        self, user_id: int, *, days: int, tz_name: str | None = None, now: datetime | None = None
    ) -> dict[str, Any]:
        """Return mail, trigger, memory/knowledge, messaging, approval, and LLM usage statistics for ``days`` days."""
        if days not in ALLOWED_RANGES_DAYS:
            raise StatsError(f"days must be one of: {', '.join(str(value) for value in ALLOWED_RANGES_DAYS)}")
        zone = _zone(tz_name)
        today = _as_utc(now or datetime.now(UTC)).astimezone(zone).date()
        first_day = today - timedelta(days=days - 1)
        dates = [first_day + timedelta(days=offset) for offset in range(days)]
        start = _naive_utc(datetime.combine(first_day, time.min, tzinfo=zone))
        end = _naive_utc(datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone))

        def day_of(value: datetime) -> date:
            return _as_utc(value).astimezone(zone).date()

        return {
            "days": days,
            "timezone": zone.key,
            "start": start.replace(tzinfo=UTC),
            "end": end.replace(tzinfo=UTC),
            "dates": [day.isoformat() for day in dates],
            "mail": self._mail(user_id, start, end, dates, day_of),
            "triggers": self._triggers(user_id, start, end, dates, day_of),
            "memory": self._memory(user_id, start, end, dates, day_of),
            "messaging": self._messaging(user_id, start, end, dates, day_of),
            "approvals": self._approvals(user_id, start, end),
            "llm": self._llm(user_id, dates),
        }

    def _llm(self, user_id: int, dates: list[date]) -> dict[str, Any]:
        """LLM calls and tokens (JAN-408); rows are kept per UTC day, so day edges follow UTC."""
        table = models.LlmUsageDay
        rows = self.db.execute(
            select(
                table.day,
                table.surface,
                table.model,
                table.calls,
                table.input_tokens,
                table.output_tokens,
                table.unreported_calls,
            ).where(table.user_id == user_id, table.day >= dates[0], table.day <= dates[-1])
        )
        per_day: dict[date, Counter] = defaultdict(Counter)
        by_surface: dict[str, Counter] = defaultdict(Counter)
        by_model: dict[str, Counter] = defaultdict(Counter)
        totals: Counter = Counter()
        for day, surface, model, calls, input_tokens, output_tokens, unreported in rows:
            values = {"calls": calls, "input_tokens": input_tokens, "output_tokens": output_tokens}
            for bucket in (per_day[day], by_surface[surface], by_model[model], totals):
                bucket.update(values)
            totals["unreported_calls"] += unreported
        keys = ("calls", "input_tokens", "output_tokens")
        return {
            "per_day": self._series(dates, per_day, keys),
            "by_surface": [{"surface": name, **{k: c[k] for k in keys}} for name, c in sorted(by_surface.items())],
            "by_model": [
                {"model": name, **{k: c[k] for k in keys}}
                for name, c in sorted(
                    by_model.items(), key=lambda item: -item[1]["input_tokens"] - item[1]["output_tokens"]
                )
            ],
            "totals": {key: totals[key] for key in (*keys, "unreported_calls")},
        }

    @staticmethod
    def _series(dates: list[date], counts: dict[date, Counter], keys: Iterable[str]) -> list[dict[str, Any]]:
        keys = tuple(keys)
        return [{"date": day.isoformat(), **{key: counts[day][key] for key in keys}} for day in dates]

    def _mail(self, user_id, start, end, dates, day_of) -> dict[str, Any]:
        account_ids = select(models.EmailAccount.id).where(models.EmailAccount.user_id == user_id)
        rows = self.db.execute(
            select(models.EmailRecord.received_at, models.EmailRecord.priority).where(
                models.EmailRecord.account_id.in_(account_ids),
                models.EmailRecord.received_at >= start,
                models.EmailRecord.received_at < end,
            )
        )
        counts: dict[date, Counter] = defaultdict(Counter)
        totals: Counter = Counter()
        for received_at, priority in rows:
            bucket = priority.value if priority is not None else models.Priority.DIGEST.value
            counts[day_of(received_at)][bucket] += 1
            totals[bucket] += 1
        return {
            "per_day": self._series(dates, counts, _MAIL_BUCKETS),
            "totals": {key: totals[key] for key in _MAIL_BUCKETS},
            "total": sum(totals.values()),
        }

    def _triggers(self, user_id, start, end, dates, day_of) -> dict[str, Any]:
        rows = list(
            self.db.execute(
                select(
                    models.RuntimeTrigger.created_at,
                    models.RuntimeTrigger.status,
                    models.RuntimeTrigger.definition_id,
                    models.RuntimeTrigger.claimed_at,
                    models.RuntimeTrigger.completed_at,
                ).where(
                    models.RuntimeTrigger.user_id == user_id,
                    models.RuntimeTrigger.created_at >= start,
                    models.RuntimeTrigger.created_at < end,
                )
            )
        )
        bucket_keys = ("completed", "failed", "cancelled", "pending")
        owned = {
            definition.id: definition
            for definition in self.db.scalars(
                select(models.TriggerDefinition).where(
                    models.TriggerDefinition.user_id == user_id,
                    models.TriggerDefinition.id.in_({row.definition_id for row in rows if row.definition_id}),
                )
            )
        }
        counts: dict[date, Counter] = defaultdict(Counter)
        totals: Counter = Counter()
        per_definition: dict[int | None, dict[str, Any]] = {}
        for created_at, status, definition_id, claimed_at, completed_at in rows:
            bucket = _TRIGGER_BUCKETS.get(status, "pending")
            counts[day_of(created_at)][bucket] += 1
            totals[bucket] += 1
            # A deleted definition's runs can keep a dangling id; they are
            # reported with the ad-hoc runs rather than under a name that is gone.
            key = definition_id if definition_id in owned else None
            entry = per_definition.setdefault(
                key, {"runs": 0, "outcomes": Counter(), "durations": [], "last_run_at": created_at}
            )
            entry["runs"] += 1
            entry["outcomes"][bucket] += 1
            entry["last_run_at"] = max(entry["last_run_at"], created_at)
            if bucket == "completed" and claimed_at is not None and completed_at is not None:
                entry["durations"].append(max((completed_at - claimed_at).total_seconds(), 0.0))

        definitions = []
        for key, entry in per_definition.items():
            definition = owned.get(key) if key is not None else None
            outcomes, durations = entry["outcomes"], entry["durations"]
            definitions.append(
                {
                    "definition_id": key,
                    "name": definition.name if definition is not None else None,
                    "origin": definition.origin if definition is not None else None,
                    "runs": entry["runs"],
                    **{bucket: outcomes[bucket] for bucket in bucket_keys},
                    "success_rate": _rate(outcomes["completed"], outcomes["completed"] + outcomes["failed"]),
                    "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
                    "last_run_at": _as_utc(entry["last_run_at"]),
                }
            )
        return {
            "per_day": self._series(dates, counts, bucket_keys),
            "totals": {key: totals[key] for key in bucket_keys},
            "total": sum(totals.values()),
            "success_rate": _rate(totals["completed"], totals["completed"] + totals["failed"]),
            "definitions": sorted(definitions, key=lambda item: (-item["runs"], item["name"] or "")),
        }

    def _count(self, statement) -> int:
        return int(self.db.scalar(statement) or 0)

    def _memory(self, user_id, start, end, dates, day_of) -> dict[str, Any]:
        facts = models.AgentMemoryFact
        created = self.db.scalars(
            select(facts.created_at).where(facts.user_id == user_id, facts.created_at >= start, facts.created_at < end)
        )
        counts: dict[date, Counter] = defaultdict(Counter)
        for created_at in created:
            counts[day_of(created_at)]["new_facts"] += 1
        topics = models.TrackedTopic
        return {
            "per_day": self._series(dates, counts, ("new_facts",)),
            "new_facts": sum(counter["new_facts"] for counter in counts.values()),
            "active_facts": self._count(
                select(func.count())
                .select_from(facts)
                .where(facts.user_id == user_id, facts.status == models.AgentMemoryStatus.ACTIVE)
            ),
            "archived_facts": self._count(
                select(func.count())
                .select_from(facts)
                .where(facts.user_id == user_id, facts.status == models.AgentMemoryStatus.ARCHIVED)
            ),
            "active_topics": self._count(
                select(func.count())
                .select_from(topics)
                .where(topics.user_id == user_id, topics.status == models.TopicStatus.ACTIVE)
            ),
            "total_topics": self._count(select(func.count()).select_from(topics).where(topics.user_id == user_id)),
            "wiki_pages": self._count(select(func.count()).select_from(WikiPage).where(WikiPage.user_id == user_id)),
            "knowledge_sources": self._count(
                select(func.count()).select_from(KnowledgeSource).where(KnowledgeSource.user_id == user_id)
            ),
        }

    def _messaging(self, user_id, start, end, dates, day_of) -> dict[str, Any]:
        counts: dict[date, Counter] = defaultdict(Counter)
        messages = models.WhatsAppMessage
        for timestamp, direction in self.db.execute(
            select(messages.provider_timestamp, messages.direction).where(
                messages.user_id == user_id,
                messages.provider_timestamp >= start,
                messages.provider_timestamp < end,
            )
        ):
            incoming = direction == models.WhatsAppMessageDirection.INCOMING
            counts[day_of(timestamp)]["whatsapp_in" if incoming else "whatsapp_out"] += 1
        chat = models.ConversationMessage
        for created_at, role in self.db.execute(
            select(chat.created_at, chat.role)
            .join(models.Conversation, models.Conversation.id == chat.conversation_id)
            .where(models.Conversation.user_id == user_id, chat.created_at >= start, chat.created_at < end)
        ):
            counts[day_of(created_at)]["chat_user" if role == models.MessageRole.USER else "chat_assistant"] += 1
        keys = ("whatsapp_in", "whatsapp_out", "chat_user", "chat_assistant")
        return {
            "per_day": self._series(dates, counts, keys),
            "totals": {key: sum(counter[key] for counter in counts.values()) for key in keys},
        }

    @staticmethod
    def _decision_summary(outcomes: Counter, keys: tuple[str, ...], hours: list[float]) -> dict[str, Any]:
        """Counts per outcome, the approval rate among decided items, and the median decision time."""
        decided = outcomes["approved"] + outcomes["rejected"] + outcomes["denied"]
        ordered = sorted(hours)
        middle = len(ordered) // 2
        median = None
        if ordered:
            median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        return {
            "totals": {key: outcomes[key] for key in keys},
            "total": sum(outcomes[key] for key in keys),
            #: ``approved / (approved + rejected-or-denied)``; timeouts and
            #: withdrawals are not decisions, so they stay out of the rate.
            "approval_rate": _rate(outcomes["approved"], decided),
            "median_hours_to_decision": round(median, 2) if median is not None else None,
        }

    def _approvals(self, user_id, start, end) -> dict[str, Any]:
        """How often the user accepts what Janus proposes (JAN-371).

        Each item is counted in the range it was proposed in. Decision time is
        proposal to approval/rejection; chat tool calls do not record one.
        """
        definitions = models.TriggerDefinition
        automation_outcomes: Counter = Counter()
        automation_hours: list[float] = []
        for status, proposed_at, approved_at, rejected_at in self.db.execute(
            select(
                definitions.proposal_status,
                definitions.proposed_at,
                definitions.approved_at,
                definitions.rejected_at,
            ).where(
                definitions.user_id == user_id,
                definitions.proposal_status.is_not(None),
                definitions.proposed_at >= start,
                definitions.proposed_at < end,
            )
        ):
            automation_outcomes[status] += 1
            decided_at = approved_at if status == "approved" else rejected_at if status == "rejected" else None
            if decided_at is not None:
                automation_hours.append(max((decided_at - proposed_at).total_seconds(), 0.0) / 3600)

        calls = models.ConversationToolCall
        chat_outcomes: Counter = Counter(
            dict(
                self.db.execute(
                    select(calls.approval, func.count())
                    .join(models.ConversationMessage, models.ConversationMessage.id == calls.message_id)
                    .join(models.Conversation, models.Conversation.id == models.ConversationMessage.conversation_id)
                    .where(
                        models.Conversation.user_id == user_id,
                        calls.approval.is_not(None),
                        calls.created_at >= start,
                        calls.created_at < end,
                    )
                    .group_by(calls.approval)
                ).all()
            )
        )

        character_outcomes: Counter = Counter()
        character_hours: list[float] = []
        for status, created_at, decided_at in self.db.execute(
            select(
                models.CharacterStateProposal.status,
                models.CharacterStateProposal.created_at,
                models.CharacterStateProposal.decided_at,
            ).where(
                models.CharacterStateProposal.user_id == user_id,
                models.CharacterStateProposal.created_at >= start,
                models.CharacterStateProposal.created_at < end,
            )
        ):
            character_outcomes[status] += 1
            if status in ("approved", "rejected") and decided_at is not None:
                character_hours.append(max((decided_at - created_at).total_seconds(), 0.0) / 3600)

        return {
            "automations": self._decision_summary(automation_outcomes, _AUTOMATION_OUTCOMES, automation_hours),
            "chat_tools": self._decision_summary(chat_outcomes, _CHAT_TOOL_OUTCOMES, []),
            "character": self._decision_summary(character_outcomes, _CHARACTER_OUTCOMES, character_hours),
        }
