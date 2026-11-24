"""Agent-proposed automations: Janus proposes, the user approves (JAN-235).

Janus may draft a trigger definition from chat (``triggers.propose`` toolbox,
:mod:`janus.agent_toolboxes.automation_proposals`). The draft is an ordinary
``trigger_definitions`` row with ``origin="agent"``,
``proposal_status="proposed"`` and ``enabled=False``; nothing activates it
(see the guards in :mod:`janus.trigger_definition_service`). Only the user,
through the authenticated approval endpoint, can move it to ``approved`` --
which records ``approved_by_user_id``, ``approved_at`` and the
:func:`~janus.trigger_approval.approval_fingerprint` that
``tool_policy`` re-checks before it confirms the definition's tools.

The functions here split along that line on purpose:

* :func:`create_proposal`, :func:`revise_proposal`, :func:`withdraw_proposal`
  and :func:`list_proposals` are what the agent toolbox calls. None of them
  writes an approval field.
* :func:`approve_proposal` and :func:`reject_proposal` are called only by the
  user-authenticated API.

Limits are per owner, counted from the database, and enforced here rather than
in the toolbox, so every caller is bound by them. One-time chat follow-ups
(JAN-257) are created by the ``triggers`` toolbox, keep ``proposal_status``
``NULL``, and never pass through this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from janus import event_logger, models, trigger_event_filters
from janus import trigger_definition_service as definitions
from janus.trigger_approval import approval_fingerprint

#: Registry ids a proposal may name. Everything else -- in particular
#: ``triggers``/``triggers.propose`` (loops), ``events`` (not owner-scoped),
#: ``delegation`` (fan-out), every ``confirmation="required"`` entry
#: (``mail.send``, ``rules.write``, ``planner.local.write``, ``github``,
#: ``linear``, ``skills``, ``wiki.propose``, ``web_fetch``, ``shell``), remote MCP tools,
#: and ``web_search`` (an outbound channel) -- can only be added by the user
#: while reviewing the draft.
PROPOSABLE_TOOL_IDS = frozenset(
    {
        "core",
        "trigger-history",
        "mail",
        "mail.label",
        "calendar",
        "jira",
        "planner.local",
        "dashboard",
        "memory",
        "topics",
    }
)

MAX_OPEN_PROPOSALS = 5
MAX_PROPOSALS_PER_DAY = 3
MAX_PROPOSALS_PER_TURN = 1
MAX_OBJECTIVE_CHARS = 4000
MAX_RATIONALE_CHARS = 2000
MAX_REJECTION_REASON_CHARS = 1000
MIN_PROPOSAL_INTERVAL_MINUTES = 60
REJECTION_MEMORY = timedelta(days=30)
_PROPOSAL_WINDOW = timedelta(hours=24)

_SOURCE = "TriggerProposals"
_PROPOSED = models.TriggerProposalStatus.PROPOSED.value
_APPROVED = models.TriggerProposalStatus.APPROVED.value
_REJECTED = models.TriggerProposalStatus.REJECTED.value
_WITHDRAWN = models.TriggerProposalStatus.WITHDRAWN.value
_SUPERSEDED = models.TriggerProposalStatus.SUPERSEDED.value
_AGENT = models.TriggerDefinitionOrigin.AGENT.value

#: Fields the approval endpoint lets the user change while approving.
APPROVAL_EDIT_FIELDS = (
    "name",
    "objective",
    "tool_ids",
    "kind",
    "schedule",
    "timezone",
    "event_type",
    "event_filter",
    "max_runs_per_hour",
    "llm_provider",
    "llm_model",
)


class ProposalLimitError(definitions.TriggerDefinitionError):
    """A proposal limit or the dedupe rule refused a new draft."""


def _now(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _audit(action: str, definition: models.TriggerDefinition, actor: str, old: str | None, **extra: Any) -> None:
    event_logger.log(
        models.EventType.WARNING if action == "limit_refused" else models.EventType.INFO,
        _SOURCE,
        f"trigger_proposal.{action}",
        {
            "definition_id": definition.id,
            "user_id": definition.user_id,
            "actor": actor,
            "old_status": old,
            "new_status": definition.proposal_status,
            **extra,
        },
    )


def _refuse(owner_id: int, reason: str) -> ProposalLimitError:
    event_logger.log(
        models.EventType.WARNING,
        _SOURCE,
        "trigger_proposal.limit_refused",
        {"user_id": owner_id, "actor": "agent", "reason": reason},
    )
    return ProposalLimitError(reason)


def is_proposal(definition: models.TriggerDefinition | None) -> bool:
    """Whether ``definition`` is (or was) a Janus proposal rather than an ordinary definition."""
    return definition is not None and definition.proposal_status is not None


def _lock_owner(db: Session, owner_id: int) -> None:
    """Serialize proposal writes per owner, so concurrent turns cannot both pass the limits and dedupe.

    Must be the first statement of the transaction. PostgreSQL (and other
    row-locking backends) lock the owner's ``users`` row with ``SELECT ... FOR
    UPDATE``. SQLite has no row locks and ignores ``FOR UPDATE``; a no-op
    ``UPDATE`` on the owner row takes the database write lock up front instead,
    so a second writer waits (busy timeout) and then reads the committed state.
    The lock is released when the caller commits or rolls back.
    """
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE users SET id = id WHERE id = :owner_id"), {"owner_id": owner_id})
    else:
        db.execute(select(models.User.id).where(models.User.id == owner_id).with_for_update())


def _validate_content(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a proposal's content: the ordinary definition rules plus the proposal constraints."""
    values = definitions.normalize_definition_fields(
        name=fields.get("name", ""),
        objective=fields.get("objective", ""),
        tool_ids=fields.get("tool_ids") or [],
        kind=fields.get("kind") or models.TriggerDefinitionKind.MANUAL.value,
        schedule=fields.get("schedule"),
        tz_name=fields.get("timezone"),
        event_type=fields.get("event_type"),
        event_filter=fields.get("event_filter"),
        max_runs_per_hour=fields.get("max_runs_per_hour", trigger_event_filters.CATALOG_DEFAULT),
        llm_provider=fields.get("llm_provider"),
        llm_model=fields.get("llm_model"),
    )
    if len(values["objective"]) > MAX_OBJECTIVE_CHARS:
        raise definitions.TriggerDefinitionError(f"objective must be at most {MAX_OBJECTIVE_CHARS} characters")
    not_proposable = [tool_id for tool_id in values["tool_ids"] if tool_id not in PROPOSABLE_TOOL_IDS]
    if not_proposable:
        raise definitions.TriggerDefinitionError(
            f"these tools cannot be proposed: {', '.join(not_proposable)}. Proposable tools: "
            f"{', '.join(sorted(PROPOSABLE_TOOL_IDS))}. The user can add others while reviewing the draft."
        )
    schedule = values["schedule"] or {}
    if schedule.get("type") == definitions.SCHEDULE_INTERVAL and schedule["minutes"] < MIN_PROPOSAL_INTERVAL_MINUTES:
        raise definitions.TriggerDefinitionError(
            f"a proposed interval must be at least {MIN_PROPOSAL_INTERVAL_MINUTES} minutes"
        )
    return values


def _clean_rationale(rationale: str | None) -> str:
    cleaned = (rationale or "").strip()
    if not cleaned:
        raise definitions.TriggerDefinitionError("rationale must say why this automation helps the user")
    if len(cleaned) > MAX_RATIONALE_CHARS:
        raise definitions.TriggerDefinitionError(f"rationale must be at most {MAX_RATIONALE_CHARS} characters")
    return cleaned


def _owner_proposals(db: Session, owner_id: int) -> list[models.TriggerDefinition]:
    return list(
        db.scalars(
            select(models.TriggerDefinition).where(
                models.TriggerDefinition.user_id == owner_id,
                models.TriggerDefinition.origin == _AGENT,
                models.TriggerDefinition.proposal_status.is_not(None),
            )
        )
    )


def _require_budget(db: Session, owner_id: int, moment: datetime) -> None:
    open_count = db.scalar(
        select(func.count(models.TriggerDefinition.id)).where(
            models.TriggerDefinition.user_id == owner_id,
            models.TriggerDefinition.origin == _AGENT,
            models.TriggerDefinition.proposal_status == _PROPOSED,
        )
    )
    if open_count >= MAX_OPEN_PROPOSALS:
        raise _refuse(
            owner_id,
            f"{open_count} suggested automations already await review (limit {MAX_OPEN_PROPOSALS}); "
            "ask the user to review them, or withdraw one, before proposing another",
        )
    recent = db.scalar(
        select(func.count(models.TriggerDefinition.id)).where(
            models.TriggerDefinition.user_id == owner_id,
            models.TriggerDefinition.origin == _AGENT,
            models.TriggerDefinition.proposal_status.is_not(None),
            models.TriggerDefinition.proposed_at >= _naive(moment - _PROPOSAL_WINDOW),
        )
    )
    if recent >= MAX_PROPOSALS_PER_DAY:
        raise _refuse(owner_id, f"at most {MAX_PROPOSALS_PER_DAY} automations can be proposed per 24 hours")


def _require_not_duplicate(
    db: Session, owner_id: int, fingerprint: str, moment: datetime, *, exclude_id: int | None = None
) -> None:
    """Refuse content equal to an open, approved, or recently rejected proposal, or to a user definition."""
    rejected_since = _naive(moment - REJECTION_MEMORY)
    candidates = db.scalars(select(models.TriggerDefinition).where(models.TriggerDefinition.user_id == owner_id))
    for candidate in candidates:
        if candidate.id == exclude_id or approval_fingerprint(candidate) != fingerprint:
            continue
        status = candidate.proposal_status
        if status == _REJECTED and candidate.rejected_at is not None and candidate.rejected_at >= rejected_since:
            reason = f" The user's reason: {candidate.rejection_reason}" if candidate.rejection_reason else ""
            raise _refuse(
                owner_id,
                f"the user rejected this exact automation (#{candidate.id}) in the last "
                f"{REJECTION_MEMORY.days} days; do not propose it again unchanged.{reason}",
            )
        if status in (_PROPOSED, _APPROVED) or (status is None and candidate.origin != _AGENT):
            raise _refuse(owner_id, f"an identical automation already exists (#{candidate.id}, {status or 'yours'})")


def _resolve_supersedes(db: Session, owner_id: int, supersedes_id: int | None) -> models.TriggerDefinition | None:
    if supersedes_id is None:
        return None
    target = definitions.get_owned_definition(db, owner_id, supersedes_id)
    if target is None or target.origin != _AGENT or target.proposal_status != _APPROVED:
        raise definitions.TriggerDefinitionError(
            f"supersedes_id {supersedes_id} is not one of your approved suggested automations"
        )
    return target


def _source(
    conversation_id: int | None, untrusted_context: Iterable[str], injection_suspected: bool = False
) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "untrusted_context": sorted(set(untrusted_context)),
        # JAN-127 flagged something read in the drafting turn as a likely prompt injection.
        "injection_suspected": bool(injection_suspected),
    }


def create_proposal(
    db: Session,
    owner_id: int,
    *,
    rationale: str,
    supersedes_id: int | None = None,
    conversation_id: int | None = None,
    untrusted_context: Iterable[str] = (),
    injection_suspected: bool = False,
    now: datetime | None = None,
    **fields: Any,
) -> models.TriggerDefinition:
    """Save a new, inactive ``proposed`` definition drafted by Janus."""
    _lock_owner(db, owner_id)
    moment = _now(now)
    values = _validate_content(fields)
    cleaned_rationale = _clean_rationale(rationale)
    target = _resolve_supersedes(db, owner_id, supersedes_id)
    _require_budget(db, owner_id, moment)
    _require_not_duplicate(db, owner_id, approval_fingerprint(values), moment)
    replaced: list[models.TriggerDefinition] = []
    if target is not None:
        # One open replacement per approved definition: an older draft is withdrawn.
        replaced = [
            row
            for row in _owner_proposals(db, owner_id)
            if row.proposal_status == _PROPOSED and row.supersedes_id == target.id
        ]
        for row in replaced:
            row.proposal_status = _WITHDRAWN
    proposal = models.TriggerDefinition(
        user_id=owner_id,
        origin=_AGENT,
        enabled=False,
        next_run_at=None,
        proposal_status=_PROPOSED,
        proposal_rationale=cleaned_rationale,
        proposed_at=_naive(moment),
        proposal_source=_source(conversation_id, untrusted_context, injection_suspected),
        supersedes_id=target.id if target is not None else None,
        **values,
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    for row in replaced:
        _audit("withdrawn", row, "agent", _PROPOSED, replaced_by=proposal.id)
    _audit(
        "created",
        proposal,
        "agent",
        None,
        untrusted_context=proposal.proposal_source["untrusted_context"],
        injection_suspected=proposal.proposal_source["injection_suspected"],
    )
    return proposal


def _owned_agent_proposal(db: Session, owner_id: int, proposal_id: int) -> models.TriggerDefinition:
    definition = definitions.get_owned_definition(db, owner_id, proposal_id)
    if definition is None or definition.origin != _AGENT or definition.proposal_status is None:
        raise definitions.TriggerDefinitionError(f"no suggested automation of yours has the id {proposal_id}")
    return definition


def revise_proposal(
    db: Session,
    owner_id: int,
    proposal_id: int,
    *,
    rationale: str | None = None,
    conversation_id: int | None = None,
    untrusted_context: Iterable[str] = (),
    injection_suspected: bool = False,
    now: datetime | None = None,
    **changes: Any,
) -> models.TriggerDefinition:
    """Revise one of Janus's still-``proposed`` drafts in place."""
    _lock_owner(db, owner_id)
    moment = _now(now)
    proposal = _owned_agent_proposal(db, owner_id, proposal_id)
    if proposal.proposal_status != _PROPOSED:
        hint = " Propose a replacement with supersedes_id instead." if proposal.proposal_status == _APPROVED else ""
        raise definitions.TriggerDefinitionConflict(
            f"suggested automation {proposal_id} is {proposal.proposal_status} and cannot be revised.{hint}"
        )
    merged = {
        field: (changes[field] if changes.get(field) is not None else getattr(proposal, field))
        for field in APPROVAL_EDIT_FIELDS
    }
    if changes.get("kind") is not None and changes["kind"] != proposal.kind:
        # Switching activation drops the old activation's settings unless new ones are given.
        merged["schedule"] = changes.get("schedule")
        merged["event_type"] = changes.get("event_type")
        merged["event_filter"] = changes.get("event_filter")
        merged["max_runs_per_hour"] = changes.get("max_runs_per_hour", trigger_event_filters.CATALOG_DEFAULT)
    values = _validate_content(merged)
    cleaned_rationale = _clean_rationale(rationale) if rationale is not None else proposal.proposal_rationale
    _require_not_duplicate(db, owner_id, approval_fingerprint(values), moment, exclude_id=proposal.id)
    for key, value in values.items():
        setattr(proposal, key, value)
    proposal.proposal_rationale = cleaned_rationale
    previous = (proposal.proposal_source or {}).get("untrusted_context") or []
    proposal.proposal_source = _source(
        conversation_id if conversation_id is not None else (proposal.proposal_source or {}).get("conversation_id"),
        [*previous, *untrusted_context],
        bool((proposal.proposal_source or {}).get("injection_suspected")) or injection_suspected,
    )
    db.commit()
    db.refresh(proposal)
    _audit("revised", proposal, "agent", _PROPOSED)
    return proposal


def withdraw_proposal(db: Session, owner_id: int, proposal_id: int) -> models.TriggerDefinition:
    """Withdraw one of Janus's still-``proposed`` drafts."""
    proposal = _owned_agent_proposal(db, owner_id, proposal_id)
    if proposal.proposal_status != _PROPOSED:
        raise definitions.TriggerDefinitionConflict(
            f"suggested automation {proposal_id} is {proposal.proposal_status}; only open drafts can be withdrawn"
        )
    proposal.proposal_status = _WITHDRAWN
    db.commit()
    db.refresh(proposal)
    _audit("withdrawn", proposal, "agent", _PROPOSED)
    return proposal


def list_proposals(
    db: Session, owner_id: int, *, status: str | None = None, limit: int = 50
) -> list[models.TriggerDefinition]:
    """List one owner's proposals, newest first, optionally by status."""
    query = select(models.TriggerDefinition).where(
        models.TriggerDefinition.user_id == owner_id,
        models.TriggerDefinition.origin == _AGENT,
        models.TriggerDefinition.proposal_status.is_not(None),
    )
    if status is not None:
        try:
            parsed = models.TriggerProposalStatus(status.strip().lower()).value
        except ValueError as error:
            options = ", ".join(item.value for item in models.TriggerProposalStatus)
            raise definitions.TriggerDefinitionError(f"status must be one of: {options}") from error
        query = query.where(models.TriggerDefinition.proposal_status == parsed)
    query = query.order_by(models.TriggerDefinition.proposed_at.desc(), models.TriggerDefinition.id.desc())
    return list(db.scalars(query.limit(max(1, min(limit, 200)))))


def count_open_proposals(db: Session, owner_id: int) -> int:
    """Return how many proposals await the owner's review."""
    return db.scalar(
        select(func.count(models.TriggerDefinition.id)).where(
            models.TriggerDefinition.user_id == owner_id,
            models.TriggerDefinition.origin == _AGENT,
            models.TriggerDefinition.proposal_status == _PROPOSED,
        )
    )


def review_data(
    db: Session, owner_id: int, rows: Iterable[models.TriggerDefinition], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return, per proposal, what its review card needs beyond the row itself.

    ``blocked_tool_ids`` are the draft's tools that are off for unattended runs
    (approving lets this definition use them), ``untrusted_context`` the
    provenance, ``next_occurrence`` when it would first run if approved now, and
    ``supersedes`` the approved definition it would replace (for the diff).
    """
    # Imported lazily: tool_policy and agent_tools pull in the toolboxes, one of which imports this module.
    from janus import tool_policy
    from janus.agent_tools import DEFAULT_TOOL_REGISTRY

    allowed = tool_policy.unattended_allowed_tool_ids(db, owner_id, DEFAULT_TOOL_REGISTRY.entries())
    moment = _now(now)
    result = []
    for row in rows:
        target = (
            definitions.get_owned_definition(db, owner_id, row.supersedes_id) if row.supersedes_id is not None else None
        )
        result.append(
            {
                "blocked_tool_ids": [tool_id for tool_id in row.tool_ids or [] if tool_id not in allowed],
                "untrusted_context": list((row.proposal_source or {}).get("untrusted_context") or []),
                "injection_suspected": bool((row.proposal_source or {}).get("injection_suspected")),
                "next_occurrence": definitions.compute_next_run(row.kind, row.schedule, row.timezone, moment),
                "supersedes": (
                    {
                        "id": target.id,
                        "name": target.name,
                        "objective": target.objective,
                        "tool_ids": list(target.tool_ids or []),
                        "kind": target.kind,
                        "schedule": target.schedule,
                        "timezone": target.timezone,
                        "event_type": target.event_type,
                        "enabled": target.enabled,
                    }
                    if target is not None
                    else None
                ),
            }
        )
    return result


def _require_open(definition: models.TriggerDefinition) -> None:
    if definition.origin != _AGENT or definition.proposal_status is None:
        raise definitions.TriggerDefinitionConflict("this definition is not a suggested automation")
    if definition.proposal_status != _PROPOSED:
        raise definitions.TriggerDefinitionConflict(
            f"this suggested automation is already {definition.proposal_status}"
        )


def approve_proposal(
    db: Session,
    definition: models.TriggerDefinition,
    approver_id: int,
    *,
    edits: Mapping[str, Any] | None = None,
    enable: bool = True,
    now: datetime | None = None,
) -> models.TriggerDefinition:
    """Approve a ``proposed`` definition as the authenticated owner, applying their edits.

    Only the user-authenticated API calls this. The user's edits are validated
    by the ordinary definition rules (not the proposal allowlist): the user may
    grant any tool. If the proposal supersedes an approved definition, that one
    becomes ``superseded`` and is disabled in the same transaction.
    """
    if approver_id != definition.user_id:
        raise PermissionError("only the owner can approve a suggested automation")
    _require_open(definition)
    moment = _now(now)
    changes = {key: value for key, value in (edits or {}).items() if key in APPROVAL_EDIT_FIELDS}
    merged = {field: changes.get(field, getattr(definition, field)) for field in APPROVAL_EDIT_FIELDS}
    values = definitions.normalize_definition_fields(
        name=merged["name"],
        objective=merged["objective"],
        tool_ids=merged["tool_ids"] or [],
        kind=merged["kind"],
        schedule=merged["schedule"],
        tz_name=merged["timezone"],
        event_type=merged["event_type"],
        event_filter=merged["event_filter"],
        max_runs_per_hour=merged["max_runs_per_hour"],
        llm_provider=merged["llm_provider"],
        llm_model=merged["llm_model"],
    )
    for key, value in values.items():
        setattr(definition, key, value)
    definition.proposal_status = _APPROVED
    definition.approved_at = _naive(moment)
    definition.approved_by_user_id = approver_id
    definition.approval_fingerprint = approval_fingerprint(definition)
    definition.enabled = bool(enable)
    definition.next_run_at = (
        definitions.compute_next_run(definition.kind, definition.schedule, definition.timezone, moment)
        if definition.enabled
        else None
    )
    superseded: models.TriggerDefinition | None = None
    if definition.supersedes_id is not None:
        target = definitions.get_owned_definition(db, definition.user_id, definition.supersedes_id)
        if target is not None and target.origin == _AGENT and target.proposal_status == _APPROVED:
            target.proposal_status = _SUPERSEDED
            target.enabled = False
            target.next_run_at = None
            definitions.cancel_pending_runs(db, target, commit=False)
            superseded = target
    db.commit()
    db.refresh(definition)
    _audit("approved", definition, "user", _PROPOSED, enabled=definition.enabled)
    if superseded is not None:
        _audit("superseded", superseded, "user", _APPROVED, superseded_by=definition.id)
    return definition


def reject_proposal(
    db: Session,
    definition: models.TriggerDefinition,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> models.TriggerDefinition:
    """Reject a ``proposed`` definition; it is kept so Janus does not propose it again."""
    _require_open(definition)
    cleaned = (reason or "").strip()[:MAX_REJECTION_REASON_CHARS] or None
    definition.proposal_status = _REJECTED
    definition.rejected_at = _naive(_now(now))
    definition.rejection_reason = cleaned
    definition.enabled = False
    definition.next_run_at = None
    db.commit()
    db.refresh(definition)
    _audit("rejected", definition, "user", _PROPOSED)
    return definition


__all__ = [
    "APPROVAL_EDIT_FIELDS",
    "MAX_OBJECTIVE_CHARS",
    "MAX_OPEN_PROPOSALS",
    "MAX_PROPOSALS_PER_DAY",
    "MAX_PROPOSALS_PER_TURN",
    "MIN_PROPOSAL_INTERVAL_MINUTES",
    "PROPOSABLE_TOOL_IDS",
    "REJECTION_MEMORY",
    "ProposalLimitError",
    "approve_proposal",
    "count_open_proposals",
    "create_proposal",
    "is_proposal",
    "list_proposals",
    "reject_proposal",
    "review_data",
    "revise_proposal",
    "withdraw_proposal",
]
