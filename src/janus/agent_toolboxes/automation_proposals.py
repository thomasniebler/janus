"""Chat-only tools that let Janus *draft* automations for the user to approve (JAN-235).

Registry id ``triggers.propose``. Janus proposes, the user approves:

* Every draft is saved inactive (``proposal_status="proposed"``,
  ``enabled=False``) and never runs until the user approves it on
  Settings -> Triggers. There is deliberately no tool here that approves,
  enables, runs, edits an approved definition, or deletes anything, and none
  of these tools writes an approval field -- only the user-authenticated API
  does (``api/trigger_definitions.py``).
* The owner always comes from :class:`~janus.agent_toolboxes.shared.ToolContext`,
  never from a model argument; another user's ids read as "not found".
* The toolbox is offered in chat only: it is in
  ``TASK_MODE_ALLOWED_TOOL_IDS["chat"]`` and in ``tool_policy.LOCKED_UNATTENDED``,
  so no trigger, automation, or delegated run can draft further automations.
* Which untrusted-content tools already ran in the drafting turn, and whether
  one of them looked like a prompt injection, are read from the step's shared
  trace (``ToolContext.turn_trace``, JAN-127) and stored as provenance, so the
  review card can say "drafted after reading email/web content". Neither blocks
  the draft: it stays inert until the user approves it.
"""

from __future__ import annotations

import inspect
import threading
from typing import Any

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from janus import models
from janus import trigger_proposal_service as proposals
from janus.agent_toolboxes.shared import ToolContext, iso, run_isolated_tool_mutation
from janus.trigger_events import TriggerEvent

#: Fixed reminder returned with every draft, so the model does not claim it is active.
AWAITING_APPROVAL_NOTE = (
    "Saved as a suggestion only. Nothing runs until the user reviews and approves it on "
    "Settings -> Triggers (Suggested automations). Tell the user plainly that it awaits their approval; "
    "do not say it is active or scheduled."
)


#: The propose tool's description; the event list comes from the catalog, so a new event reaches Janus.
PROPOSE_DESCRIPTION = inspect.cleandoc(
    """Draft a recurring or event-driven automation for the user to review. It does NOT run until approved.

        Tell the user what you are about to draft before calling this. ``kind``
        is ``manual``, ``schedule`` or ``event``. A schedule is
        ``{"type": "daily", "time": "HH:MM", "days": [0..6]}`` (0 = Monday) or
        ``{"type": "interval", "minutes": N}`` with N >= 60, in ``timezone``
        (IANA name). ``event_type`` is one of: {events}. For an event,
        ``event_filter`` optionally limits which events start a run: a list of
        ``{"field": <payload field>, "op": "in" | "not_in" | "contains",
        "values": [...]}``, all of which must match (e.g. only one WhatsApp chat
        via ``chat_id``). ``max_runs_per_hour`` caps runs; omit it for the
        event's default (10 for WhatsApp group events). ``tool_ids`` may only
        use: core, trigger-history, mail, mail.label, calendar, jira,
        planner.local, dashboard, memory, topics. ``rationale`` says in plain
        words why this helps. To change an approved suggestion, propose a new
        one with ``supersedes_id`` set to it. At most one proposal per turn.
        """
).replace("{events}", ", ".join(event.value for event in TriggerEvent))


def serialize_proposal(definition: models.TriggerDefinition) -> dict[str, Any]:
    """Return the definition fields Janus may see for one of its proposals (never run results)."""
    return {
        "proposal_id": definition.id,
        "status": definition.proposal_status,
        "name": definition.name,
        "objective": definition.objective,
        "tool_ids": list(definition.tool_ids or []),
        "kind": definition.kind,
        "schedule": definition.schedule,
        "timezone": definition.timezone,
        "event_type": definition.event_type,
        "event_filter": definition.event_filter,
        "max_runs_per_hour": definition.max_runs_per_hour,
        "rationale": definition.proposal_rationale,
        "supersedes_id": definition.supersedes_id,
        "enabled": definition.enabled
        if definition.proposal_status == models.TriggerProposalStatus.APPROVED.value
        else None,
        "rejection_reason": definition.rejection_reason,
        "proposed_at": iso(definition.proposed_at),
    }


def untrusted_reads(context: ToolContext) -> tuple[list[str], bool]:
    """Return the untrusted-content tools that already ran in this turn, and whether one looked like an injection.

    Both come from the step's shared trace (JAN-127): ``ToolRegistryEntry.reads_untrusted_content``
    decides what counts as untrusted, and the prompt-injection scanner's
    detections decide the second value. A draft is labelled with them, never
    blocked by them (JAN-235, resolved question 6): it cannot run until the
    user approves it, and the review card shows the warning.
    """
    trace = context.turn_trace
    if trace is None:
        return [], False
    return list(trace.untrusted_tool_ids()), trace.injection_suspected()


def build_automation_proposal_tools(context: ToolContext) -> list[BaseTool]:
    """Build the owner-scoped proposal toolbox for one chat turn."""
    user_id = context.user.id
    # The toolset is rebuilt for every chat turn, so this counter is per turn.
    turn_state = {"created": 0}
    turn_lock = threading.Lock()

    @tool("propose_trigger_definition", description=PROPOSE_DESCRIPTION)
    def propose_trigger_definition(
        name: str,
        objective: str,
        tool_ids: list[str],
        kind: str,
        rationale: str,
        schedule: dict[str, Any] | None = None,
        timezone: str | None = None,
        event_type: str | None = None,
        event_filter: list[dict[str, Any]] | None = None,
        max_runs_per_hour: int | None = None,
        supersedes_id: int | None = None,
    ) -> dict[str, Any]:
        with turn_lock:
            if turn_state["created"] >= proposals.MAX_PROPOSALS_PER_TURN:
                raise ValueError(f"at most {proposals.MAX_PROPOSALS_PER_TURN} automation can be proposed per chat turn")
            fields = {
                "name": name,
                "objective": objective,
                "tool_ids": tool_ids,
                "kind": kind,
                "schedule": schedule,
                "timezone": timezone,
                "event_type": event_type,
                "event_filter": event_filter,
            }
            if max_runs_per_hour is not None:
                fields["max_runs_per_hour"] = max_runs_per_hour
            untrusted, injection_suspected = untrusted_reads(context)

            def create(db: Session) -> dict[str, Any]:
                proposal = proposals.create_proposal(
                    db,
                    user_id,
                    rationale=rationale,
                    supersedes_id=supersedes_id,
                    conversation_id=context.conversation_id,
                    untrusted_context=untrusted,
                    injection_suspected=injection_suspected,
                    **fields,
                )
                return serialize_proposal(proposal)

            result = run_isolated_tool_mutation(context, lambda db: db, create)
            turn_state["created"] += 1
        return {**result, "note": AWAITING_APPROVAL_NOTE}

    @tool("revise_proposal")
    def revise_proposal(
        proposal_id: int,
        name: str | None = None,
        objective: str | None = None,
        tool_ids: list[str] | None = None,
        kind: str | None = None,
        schedule: dict[str, Any] | None = None,
        timezone: str | None = None,
        event_type: str | None = None,
        event_filter: list[dict[str, Any]] | None = None,
        max_runs_per_hour: int | None = None,
        rationale: str | None = None,
    ) -> dict[str, Any]:
        """Change one of your own suggestions that still awaits review. Approved ones cannot be revised."""
        changes = {
            "name": name,
            "objective": objective,
            "tool_ids": tool_ids,
            "kind": kind,
            "schedule": schedule,
            "timezone": timezone,
            "event_type": event_type,
            "event_filter": event_filter,
            "max_runs_per_hour": max_runs_per_hour,
        }
        untrusted, injection_suspected = untrusted_reads(context)

        def revise(db: Session) -> dict[str, Any]:
            proposal = proposals.revise_proposal(
                db,
                user_id,
                proposal_id,
                rationale=rationale,
                conversation_id=context.conversation_id,
                untrusted_context=untrusted,
                injection_suspected=injection_suspected,
                **changes,
            )
            return serialize_proposal(proposal)

        return {**run_isolated_tool_mutation(context, lambda db: db, revise), "note": AWAITING_APPROVAL_NOTE}

    @tool("list_my_proposals")
    def list_my_proposals(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """List your automation suggestions with their status and, if rejected, the user's reason."""
        return [
            serialize_proposal(proposal)
            for proposal in proposals.list_proposals(context.db, user_id, status=status, limit=max(1, min(limit, 50)))
        ]

    @tool("withdraw_proposal")
    def withdraw_proposal(proposal_id: int) -> dict[str, Any]:
        """Withdraw one of your own suggestions that still awaits review."""

        def withdraw(db: Session) -> dict[str, Any]:
            return serialize_proposal(proposals.withdraw_proposal(db, user_id, proposal_id))

        return run_isolated_tool_mutation(context, lambda db: db, withdraw)

    return [propose_trigger_definition, revise_proposal, list_my_proposals, withdraw_proposal]


__all__ = [
    "AWAITING_APPROVAL_NOTE",
    "build_automation_proposal_tools",
    "serialize_proposal",
    "untrusted_reads",
]
