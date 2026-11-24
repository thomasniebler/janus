"""Bounded adaptive character state with audit and rollback (JAN-301).

The adaptive half of the character (tone, verbosity, proactivity, current
focus) sits beside the stable constitution. Rules:

* **The owner sets the envelope.** ``character_profiles.state_envelope``
  holds, per dimension, the allowed values and the default (``NULL`` = the
  built-in envelope: every value allowed). Any state outside it is refused,
  whoever asks; changing the envelope pulls the current state back inside it.
* **Janus proposes, the owner approves** (as in JAN-235). Janus can only file a
  :class:`~janus.models.CharacterStateProposal` from a chat turn
  (``character.propose``, chat-only and in ``tool_policy.LOCKED_UNATTENDED``);
  only the owner-authenticated API approves it. Owner edits apply directly.
* **Every change is audited** in ``character_state_changes`` with the actor,
  the previous and the new state, so any earlier state can be restored
  (rollback) and the defaults can always be restored (reset).
* ``current_focus`` is free text: one line, at most ``FOCUS_MAX_CHARS``
  characters, and rendered into prompts only as an escaped data block
  (``prompt_injection.character_focus_block``). A proposed focus that looks
  like a prompt injection is refused.
* Everything is owner-scoped: another user's ids read as not found.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from janus import models, prompt_injection
from janus.character import STATE_DIMENSIONS, CharacterConstitution, CharacterState
from janus.schemas.character_state import CharacterStateEnvelope, CharacterStateValues
from janus.services.character_service import (
    DEFAULT_CHARACTER,
    get_character_constitution,
    get_character_profile,
    save_character_profile,
)
from janus.time_utils import utcnow

OWNER = "owner"
JANUS = "janus"
PROPOSED = "proposed"
APPROVED = "approved"
REJECTED = "rejected"

#: Open proposals one owner may have at a time.
MAX_PENDING_PROPOSALS = 5
RATIONALE_MAX_CHARS = 1000
#: The keys a proposal or an owner edit may name.
STATE_KEYS: tuple[str, ...] = (*STATE_DIMENSIONS, "current_focus")


class CharacterStateError(ValueError):
    """A refused change: outside the envelope, invalid, or a proposal already decided."""


class CharacterStateNotFoundError(LookupError):
    """The proposal or audit row does not exist for this owner."""


def get_envelope(db: Session, user_id: int) -> CharacterStateEnvelope:
    """Return the owner's envelope, or the built-in one."""
    profile = get_character_profile(db, user_id)
    raw = profile.state_envelope if profile is not None else None
    if not raw:
        return CharacterStateEnvelope()
    try:
        return CharacterStateEnvelope.model_validate(raw)
    except ValidationError:
        return CharacterStateEnvelope()


def defaults(envelope: CharacterStateEnvelope) -> dict[str, Any]:
    """Return the state a reset restores: each dimension's default and no focus."""
    return {
        "tone": envelope.tone.default,
        "verbosity": envelope.verbosity.default,
        "proactivity": envelope.proactivity.default,
        "current_focus": None,
    }


def _record(db: Session, user_id: int) -> models.CharacterStateRecord | None:
    return db.query(models.CharacterStateRecord).filter(models.CharacterStateRecord.user_id == user_id).first()


def _validated(values: dict[str, Any]) -> dict[str, Any]:
    """Return ``values`` as a complete, normalized state, or raise :class:`CharacterStateError`."""
    try:
        return CharacterStateValues.model_validate(values).model_dump()
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'state'}: {err['msg']}" for err in exc.errors())
        raise CharacterStateError(f"invalid character state: {problems}") from exc


def current_values(db: Session, user_id: int) -> dict[str, Any]:
    """Return the owner's current state (the envelope defaults until something changed it)."""
    record = _record(db, user_id)
    if record is None:
        return defaults(get_envelope(db, user_id))
    try:
        return _validated(
            {
                "tone": record.tone,
                "verbosity": record.verbosity,
                "proactivity": record.proactivity,
                "current_focus": record.current_focus,
            }
        )
    except CharacterStateError:
        return defaults(get_envelope(db, user_id))


def envelope_violations(values: dict[str, Any], envelope: CharacterStateEnvelope) -> list[str]:
    """Return why ``values`` lies outside ``envelope`` (empty when it is inside)."""
    problems = []
    for dimension in STATE_DIMENSIONS:
        allowed = list(getattr(envelope, dimension).allowed)
        if values[dimension] not in allowed:
            problems.append(f"{dimension} '{values[dimension]}' is outside the allowed values ({', '.join(allowed)})")
    if values.get("current_focus") and not envelope.focus_allowed:
        problems.append("a current focus is switched off")
    return problems


def _require_within(values: dict[str, Any], envelope: CharacterStateEnvelope) -> None:
    problems = envelope_violations(values, envelope)
    if problems:
        raise CharacterStateError("refused: " + "; ".join(problems))


def _write(
    db: Session,
    user_id: int,
    new_values: dict[str, Any],
    *,
    actor: str,
    action: str,
    previous: dict[str, Any] | None = None,
    chat_turn_id: str | None = None,
    proposal_id: int | None = None,
    rollback_of_id: int | None = None,
) -> models.CharacterStateChange | None:
    """Store ``new_values`` and its audit row; the caller commits. A no-op writes nothing."""
    previous = previous if previous is not None else current_values(db, user_id)
    record = _record(db, user_id)
    if previous == new_values:
        return None
    now = utcnow()
    if record is None:
        record = models.CharacterStateRecord(user_id=user_id, revision=0)
        db.add(record)
    for key in STATE_KEYS:
        setattr(record, key, new_values[key])
    record.revision = (record.revision or 0) + 1
    record.updated_at = now
    change = models.CharacterStateChange(
        user_id=user_id,
        revision=record.revision,
        actor=actor,
        action=action,
        chat_turn_id=chat_turn_id,
        proposal_id=proposal_id,
        rollback_of_id=rollback_of_id,
        previous_state=previous,
        new_state=new_values,
        created_at=now,
    )
    db.add(change)
    return change


def set_state(db: Session, user_id: int, values: dict[str, Any]) -> None:
    """Apply an owner edit directly, inside the envelope."""
    new_values = _validated(values)
    _require_within(new_values, get_envelope(db, user_id))
    _write(db, user_id, new_values, actor=OWNER, action="edit")
    db.commit()


def reset_state(db: Session, user_id: int) -> None:
    """Restore the envelope defaults (always allowed)."""
    _write(db, user_id, defaults(get_envelope(db, user_id)), actor=OWNER, action="reset")
    db.commit()


def rollback_state(db: Session, user_id: int, change_id: int) -> None:
    """Restore the state an earlier audit row of this owner produced, if the envelope still allows it."""
    change = (
        db.query(models.CharacterStateChange)
        .filter(models.CharacterStateChange.id == change_id, models.CharacterStateChange.user_id == user_id)
        .first()
    )
    if change is None:
        raise CharacterStateNotFoundError("character state change not found")
    target = _validated(dict(change.new_state or {}))
    _require_within(target, get_envelope(db, user_id))
    _write(db, user_id, target, actor=OWNER, action="rollback", rollback_of_id=change.id)
    db.commit()


def set_envelope(db: Session, user_id: int, envelope: CharacterStateEnvelope) -> None:
    """Store the owner's envelope and pull the current state back inside it (audited)."""
    before = current_values(db, user_id)
    had_record = _record(db, user_id) is not None
    profile = get_character_profile(db, user_id) or save_character_profile(db, user_id, DEFAULT_CHARACTER)
    profile.state_envelope = envelope.model_dump()
    if had_record:
        after = dict(before)
        for dimension in STATE_DIMENSIONS:
            dimension_envelope = getattr(envelope, dimension)
            if after[dimension] not in dimension_envelope.allowed:
                after[dimension] = dimension_envelope.default
        if not envelope.focus_allowed:
            after["current_focus"] = None
    else:
        after = defaults(envelope)
    if after != before:
        _write(db, user_id, after, actor=OWNER, action="envelope", previous=before)
    db.commit()


def _owned_proposal(db: Session, user_id: int, proposal_id: int) -> models.CharacterStateProposal:
    proposal = (
        db.query(models.CharacterStateProposal)
        .filter(models.CharacterStateProposal.id == proposal_id, models.CharacterStateProposal.user_id == user_id)
        .first()
    )
    if proposal is None:
        raise CharacterStateNotFoundError("character state proposal not found")
    return proposal


def create_proposal(
    db: Session,
    user_id: int,
    changes: dict[str, Any],
    rationale: str,
    *,
    chat_turn_id: str | None,
    conversation_id: int | None,
) -> models.CharacterStateProposal:
    """File Janus's proposal; it changes nothing until the owner approves it."""
    rationale = " ".join((rationale or "").split())
    if not rationale:
        raise CharacterStateError("a proposal needs a rationale the owner can read")
    if len(rationale) > RATIONALE_MAX_CHARS:
        raise CharacterStateError(f"the rationale must be at most {RATIONALE_MAX_CHARS} characters")
    unknown = sorted(set(changes) - set(STATE_KEYS))
    if unknown:
        raise CharacterStateError(f"unknown character state keys: {', '.join(unknown)}")
    if not changes:
        raise CharacterStateError("propose at least one change")
    current = current_values(db, user_id)
    proposed = _validated({**current, **changes})
    normalized = {key: proposed[key] for key in changes}
    if all(normalized[key] == current[key] for key in normalized):
        raise CharacterStateError("that is already the current character state")
    _require_within(proposed, get_envelope(db, user_id))
    focus = normalized.get("current_focus")
    if focus and prompt_injection.scan_text(focus).suspected:
        raise CharacterStateError("refused: the proposed focus reads like instructions, not a topic")
    pending = (
        db.query(models.CharacterStateProposal)
        .filter(models.CharacterStateProposal.user_id == user_id, models.CharacterStateProposal.status == PROPOSED)
        .count()
    )
    if pending >= MAX_PENDING_PROPOSALS:
        raise CharacterStateError(
            f"the owner already has {pending} open proposals; wait until they review them on Settings -> Character"
        )
    proposal = models.CharacterStateProposal(
        user_id=user_id,
        status=PROPOSED,
        changes=normalized,
        rationale=rationale,
        chat_turn_id=chat_turn_id,
        conversation_id=conversation_id,
        created_at=utcnow(),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def approve_proposal(db: Session, user_id: int, proposal_id: int) -> None:
    """Apply an open proposal on top of the current state, inside the current envelope (owner only)."""
    proposal = _owned_proposal(db, user_id, proposal_id)
    if proposal.status != PROPOSED:
        raise CharacterStateError(f"this proposal was already {proposal.status}")
    new_values = _validated({**current_values(db, user_id), **dict(proposal.changes or {})})
    _require_within(new_values, get_envelope(db, user_id))
    _write(
        db,
        user_id,
        new_values,
        actor=JANUS,
        action="proposal",
        chat_turn_id=proposal.chat_turn_id,
        proposal_id=proposal.id,
    )
    proposal.status = APPROVED
    proposal.decided_at = utcnow()
    db.commit()


def reject_proposal(db: Session, user_id: int, proposal_id: int) -> models.CharacterStateProposal:
    """Close an open proposal without changing the state (owner only)."""
    proposal = _owned_proposal(db, user_id, proposal_id)
    if proposal.status != PROPOSED:
        raise CharacterStateError(f"this proposal was already {proposal.status}")
    proposal.status = REJECTED
    proposal.decided_at = utcnow()
    db.commit()
    db.refresh(proposal)
    return proposal


#: Largest history page the API serves; older rows are reached with the ``before_id`` cursor.
MAX_HISTORY_PAGE = 100


def list_history(
    db: Session, user_id: int, limit: int = 50, before_id: int | None = None
) -> list[models.CharacterStateChange]:
    """Return up to ``limit`` of the owner's audit rows, newest first, older than ``before_id`` when given."""
    query = db.query(models.CharacterStateChange).filter(models.CharacterStateChange.user_id == user_id)
    if before_id is not None:
        query = query.filter(models.CharacterStateChange.id < before_id)
    return query.order_by(models.CharacterStateChange.id.desc()).limit(limit).all()


def history_page(
    db: Session, user_id: int, limit: int = 50, before_id: int | None = None
) -> tuple[list[models.CharacterStateChange], int | None]:
    """Return one page of history and the cursor for the next (older) page, or ``None`` at the end."""
    limit = max(1, min(limit, MAX_HISTORY_PAGE))
    rows = list_history(db, user_id, limit + 1, before_id)
    return rows[:limit], (rows[limit - 1].id if len(rows) > limit else None)


def list_proposals(db: Session, user_id: int, status: str | None = None) -> list[models.CharacterStateProposal]:
    """Return the owner's proposals, newest first, optionally of one status."""
    query = db.query(models.CharacterStateProposal).filter(models.CharacterStateProposal.user_id == user_id)
    if status is not None:
        query = query.filter(models.CharacterStateProposal.status == status)
    return query.order_by(models.CharacterStateProposal.id.desc()).limit(100).all()


def state_view(db: Session, user_id: int) -> dict[str, Any]:
    """Return the current state with its envelope, defaults and revision (API and tool shape)."""
    envelope = get_envelope(db, user_id)
    record = _record(db, user_id)
    return {
        "state": current_values(db, user_id),
        "defaults": defaults(envelope),
        "envelope": envelope.model_dump(),
        "revision": record.revision if record is not None else 0,
        "updated_at": record.updated_at if record is not None else None,
    }


def runtime_state(db: Session, user_id: int, constitution: CharacterConstitution) -> CharacterState:
    """Return the persisted state as the ``CharacterState`` every system prompt renders."""
    record = _record(db, user_id)
    return CharacterState(
        constitution_id=constitution.constitution_id,
        constitution_version=constitution.version,
        adaptation_version=record.revision if record is not None else 0,
        trait_values=tuple((trait.name, trait.baseline) for trait in constitution.traits),
        **current_values(db, user_id),
    )


def prompt_character(db: Session, user_id: int) -> tuple[CharacterConstitution, CharacterState]:
    """Return the owner's constitution and persisted state, as every system-prompt surface renders them."""
    constitution = get_character_constitution(db, user_id)
    return constitution, runtime_state(db, user_id, constitution)


def delete_for_user(db: Session, user_id: int) -> None:
    """Remove the owner's state, proposals and audit rows (the caller commits); SQLite enforces no FK."""
    for model in (models.CharacterStateChange, models.CharacterStateProposal, models.CharacterStateRecord):
        db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)


__all__ = [
    "CharacterStateError",
    "CharacterStateNotFoundError",
    "MAX_PENDING_PROPOSALS",
    "approve_proposal",
    "create_proposal",
    "current_values",
    "delete_for_user",
    "envelope_violations",
    "get_envelope",
    "history_page",
    "list_history",
    "list_proposals",
    "prompt_character",
    "reject_proposal",
    "reset_state",
    "rollback_state",
    "runtime_state",
    "set_envelope",
    "set_state",
    "state_view",
]
