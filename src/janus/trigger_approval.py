"""Which trigger definitions may run, and when a Janus proposal's approval is still valid (JAN-235).

Small and dependency-free (``models`` only) because three layers read it:
:mod:`janus.trigger_definition_service` (activation guards),
:mod:`janus.tool_policy` (tool confirmation), and :mod:`janus.trigger_runtime`
(a last check before a run executes). The proposal lifecycle itself lives in
:mod:`janus.trigger_proposal_service`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import or_

from janus import models

#: Content an approval covers. Any change to one of these fields that does not
#: go through a user-authenticated path invalidates the approval.
FINGERPRINT_FIELDS = (
    "objective",
    "tool_ids",
    "kind",
    "schedule",
    "timezone",
    "event_type",
    "llm_provider",
    "llm_model",
)

#: Approvable fields added later (JAN-363). They join the fingerprint only when set,
#: so approvals recorded before they existed (all unset) keep their fingerprint.
OPTIONAL_FINGERPRINT_FIELDS = ("event_filter", "max_runs_per_hour")

_APPROVED = models.TriggerProposalStatus.APPROVED.value


def approval_fingerprint(source: models.TriggerDefinition | Mapping[str, Any]) -> str:
    """Return the SHA-256 over the canonical JSON of a definition's approvable content."""

    def read(field: str) -> Any:
        return source.get(field) if isinstance(source, Mapping) else getattr(source, field)

    content = {field: read(field) for field in FINGERPRINT_FIELDS}
    content.update({field: value for field in OPTIONAL_FINGERPRINT_FIELDS if (value := read(field)) is not None})
    content["tool_ids"] = sorted(content["tool_ids"] or [])
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_runnable(definition: models.TriggerDefinition | None) -> bool:
    """Whether a definition may be activated at all (not a pending or declined Janus proposal)."""
    return definition is not None and definition.proposal_status in (None, _APPROVED)


def runnable_filter():
    """SQL filter matching :func:`is_runnable` rows."""
    return or_(
        models.TriggerDefinition.proposal_status.is_(None),
        models.TriggerDefinition.proposal_status == _APPROVED,
    )


def proposal_conflict(definition: models.TriggerDefinition) -> str | None:
    """Why the ordinary edit/run/delete paths must refuse this Janus proposal, if they must."""
    status = definition.proposal_status
    if status is None or status == _APPROVED:
        return None
    if status == models.TriggerProposalStatus.PROPOSED.value:
        return "this is a suggested automation awaiting review; approve or reject it first"
    return f"this suggested automation was {status} and is read-only"


def has_valid_approval(definition: models.TriggerDefinition) -> bool:
    """Whether an ``agent`` definition carries the owner's approval and it still covers its content."""
    return (
        definition.origin == models.TriggerDefinitionOrigin.AGENT.value
        and definition.proposal_status == _APPROVED
        and definition.approved_by_user_id is not None
        and definition.approved_by_user_id == definition.user_id
        and definition.approved_at is not None
        and definition.approval_fingerprint is not None
        and definition.approval_fingerprint == approval_fingerprint(definition)
    )


__all__ = [
    "FINGERPRINT_FIELDS",
    "approval_fingerprint",
    "has_valid_approval",
    "is_runnable",
    "proposal_conflict",
    "runnable_filter",
]
