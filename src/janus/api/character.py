"""REST API router for the authenticated user's character constitution and adaptive state."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from janus import agent_runtime, database, models, security
from janus.schemas import character as character_schemas
from janus.schemas import character_state as state_schemas
from janus.services import character_state_service as state_service
from janus.services.character_service import (
    DEFAULT_CHARACTER,
    DEFAULT_MEMORY_INSTRUCTIONS,
    constitution_from_payload,
    get_character_profile,
    save_character_profile,
)

router = APIRouter(prefix="/api/character", tags=["character"])


def _response_from_profile(profile: models.CharacterProfile) -> character_schemas.CharacterProfileResponse:
    """Convert a database profile into the public schema."""
    return character_schemas.CharacterProfileResponse(
        constitution_id=profile.constitution_id,
        version=profile.version,
        name=profile.name,
        identity=profile.identity,
        core_values=profile.core_values,
        non_negotiables=profile.non_negotiables,
        traits=profile.traits,
        exploration_rate=profile.exploration_rate,
        additional_instructions=profile.additional_instructions,
        memory_instructions=profile.memory_instructions,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("", response_model=character_schemas.CharacterProfileResponse)
def get_character(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the user's persisted constitution or the safe default."""
    profile = get_character_profile(db, user.id)
    if profile is None:
        return character_schemas.CharacterProfileResponse(
            constitution_id=f"user-{user.id}-character",
            version=1,
            **DEFAULT_CHARACTER.model_dump(),
        )
    return _response_from_profile(profile)


@router.put("", response_model=character_schemas.CharacterProfileResponse)
def update_character(
    payload: character_schemas.CharacterProfilePayload,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Persist a new version of the user's constitution."""
    return _response_from_profile(save_character_profile(db, user.id, payload))


@router.post("/preview", response_model=character_schemas.CharacterPromptPreview)
def preview_character_prompt(
    payload: character_schemas.CharacterProfilePayload,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Render the exact system prompt chat and every trigger run would use for this (draft) character."""
    constitution = constitution_from_payload(user.id, payload)
    return character_schemas.CharacterPromptPreview(
        system_prompt=agent_runtime.build_janus_system_prompt(
            constitution,
            agent_runtime.load_skill_library(db),
            # The saved adaptive state (JAN-301), exactly as the runtime renders it.
            character_state=state_service.runtime_state(db, user.id, constitution),
        ),
        memory_instructions=payload.memory_instructions or DEFAULT_MEMORY_INSTRUCTIONS,
    )


# --- Adaptive character state (JAN-301): owner-only; Janus can only propose from chat. ---


@contextmanager
def _state_errors() -> Iterator[None]:
    """Map refused changes to 409 and another owner's (or a missing) row to 404."""
    try:
        yield
    except state_service.CharacterStateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except state_service.CharacterStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/state", response_model=state_schemas.CharacterStateResponse)
def get_character_state(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the current adaptive state, its envelope, defaults and revision."""
    return state_service.state_view(db, user.id)


@router.put("/state", response_model=state_schemas.CharacterStateResponse)
def update_character_state(
    payload: state_schemas.CharacterStateValues,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Apply an owner edit directly; values outside the envelope are refused (409)."""
    with _state_errors():
        state_service.set_state(db, user.id, payload.model_dump())
    return state_service.state_view(db, user.id)


@router.put("/state/envelope", response_model=state_schemas.CharacterStateResponse)
def update_character_state_envelope(
    payload: state_schemas.CharacterStateEnvelope,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Store the owner's bounds and defaults; the current state is pulled back inside them."""
    state_service.set_envelope(db, user.id, payload)
    return state_service.state_view(db, user.id)


@router.post("/state/reset", response_model=state_schemas.CharacterStateResponse)
def reset_character_state(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Restore the envelope defaults."""
    state_service.reset_state(db, user.id)
    return state_service.state_view(db, user.id)


@router.post("/state/rollback", response_model=state_schemas.CharacterStateResponse)
def rollback_character_state(
    payload: state_schemas.CharacterStateRollback,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Restore the state an earlier change produced (404 for another owner's change, 409 outside the envelope)."""
    with _state_errors():
        state_service.rollback_state(db, user.id, payload.change_id)
    return state_service.state_view(db, user.id)


@router.get("/state/history", response_model=state_schemas.CharacterStateHistoryPage)
def list_character_state_history(
    before_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=state_service.MAX_HISTORY_PAGE),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one page of the owner's audit rows, newest first; pass ``next_before_id`` back for older ones."""
    items, next_before_id = state_service.history_page(db, user.id, limit, before_id)
    return {"items": items, "next_before_id": next_before_id}


@router.get("/state/proposals", response_model=list[state_schemas.CharacterStateProposalResponse])
def list_character_state_proposals(
    status: Literal["proposed", "approved", "rejected"] | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the proposals Janus made in chat, newest first."""
    return state_service.list_proposals(db, user.id, status)


@router.post("/state/proposals/{proposal_id}/approve", response_model=state_schemas.CharacterStateResponse)
def approve_character_state_proposal(
    proposal_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Apply an open proposal; refused (409) when it no longer fits the envelope or was already decided."""
    with _state_errors():
        state_service.approve_proposal(db, user.id, proposal_id)
    return state_service.state_view(db, user.id)


@router.post("/state/proposals/{proposal_id}/reject", response_model=state_schemas.CharacterStateProposalResponse)
def reject_character_state_proposal(
    proposal_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Close an open proposal without changing the state."""
    with _state_errors():
        return state_service.reject_proposal(db, user.id, proposal_id)
