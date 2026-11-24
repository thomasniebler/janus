"""Schemas for the owner-scoped activity statistics endpoint (JAN-258)."""

from datetime import datetime

from pydantic import BaseModel

from janus.models import TriggerDefinitionOrigin


class MailDay(BaseModel):
    """New mail received on one local calendar day, by priority."""

    date: str
    important: int
    digest: int
    ignore: int
    spam: int


class MailStats(BaseModel):
    """Mail received in the range."""

    per_day: list[MailDay]
    totals: dict[str, int]
    total: int


class TriggerDay(BaseModel):
    """Trigger runs whose occurrence falls on one local day, by outcome."""

    date: str
    completed: int
    #: ``failed`` plus ``exhausted`` runs.
    failed: int
    cancelled: int
    #: ``queued``, ``running``, and ``waiting`` runs.
    pending: int


class TriggerDefinitionStats(BaseModel):
    """Run outcomes of one definition in the range; ``definition_id`` is ``None`` for ad-hoc runs."""

    definition_id: int | None
    name: str | None
    origin: TriggerDefinitionOrigin | None
    runs: int
    completed: int
    failed: int
    cancelled: int
    pending: int
    #: ``completed / (completed + failed)``; ``None`` when nothing has finished.
    success_rate: float | None
    #: Mean claim-to-completion time of completed runs, in seconds.
    avg_duration_seconds: float | None
    last_run_at: datetime


class TriggerStats(BaseModel):
    """Trigger runs in the range, per day and per definition."""

    per_day: list[TriggerDay]
    totals: dict[str, int]
    total: int
    success_rate: float | None
    definitions: list[TriggerDefinitionStats]


class MemoryDay(BaseModel):
    """Memory facts created on one local day."""

    date: str
    new_facts: int


class MemoryStats(BaseModel):
    """Memory facts, topics, and knowledge counts; ``per_day`` covers new facts only."""

    per_day: list[MemoryDay]
    new_facts: int
    active_facts: int
    archived_facts: int
    active_topics: int
    total_topics: int
    wiki_pages: int
    knowledge_sources: int


class MessagingDay(BaseModel):
    """WhatsApp and chat messages on one local day."""

    date: str
    whatsapp_in: int
    whatsapp_out: int
    chat_user: int
    chat_assistant: int


class MessagingStats(BaseModel):
    """WhatsApp and chat message volume in the range."""

    per_day: list[MessagingDay]
    totals: dict[str, int]


class DecisionStats(BaseModel):
    """Review outcomes of one kind of Janus proposal, counted by the date it was proposed (JAN-371)."""

    #: Count per outcome; the keys depend on the kind (see ``ApprovalStats``).
    totals: dict[str, int]
    total: int
    #: ``approved / (approved + rejected or denied)``; ``None`` when nothing was decided.
    #: Timeouts, withdrawals and supersessions are not decisions and stay out of the rate.
    approval_rate: float | None
    #: Median hours from proposal to approval or rejection; ``None`` when not recorded.
    median_hours_to_decision: float | None


class ApprovalStats(BaseModel):
    """How often the user accepts what Janus proposes.

    ``automations``: proposed/approved/rejected/withdrawn/superseded trigger definitions
    Janus drafted. ``chat_tools``: approved/denied/timeout/disconnected/unavailable chat
    approval cards (no decision time is recorded). ``character``: proposed/approved/rejected
    character state proposals.
    """

    automations: DecisionStats
    chat_tools: DecisionStats
    character: DecisionStats


class StatsSummary(BaseModel):
    """Everything the Stats tab shows for one range, bucketed in ``timezone``."""

    days: int
    timezone: str
    start: datetime
    end: datetime
    dates: list[str]
    mail: MailStats
    triggers: TriggerStats
    memory: MemoryStats
    messaging: MessagingStats
    approvals: ApprovalStats
