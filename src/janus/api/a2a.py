"""Authenticated management API for the user's remote A2A agents (JAN-404)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import a2a as schemas
from janus.services.a2a_client import A2AError
from janus.services.a2a_service import A2AService

router = APIRouter(prefix="/api/tools/a2a-agents", tags=["tools"])


def _response(agent: models.A2AAgent) -> schemas.A2AAgentResponse:
    card = agent.card or {}
    return schemas.A2AAgentResponse(
        id=agent.id,
        name=agent.name,
        base_url=agent.base_url,
        enabled=agent.enabled,
        card_name=card.get("name"),
        card_description=card.get("description"),
        protocol_version=card.get("protocol_version"),
        skills=[schemas.A2AAgentSkill(**skill) for skill in card.get("skills") or []],
        rpc_url=agent.rpc_url,
        token_configured=bool(agent.encrypted_token),
        last_error=agent.last_error,
        last_checked_at=agent.last_checked_at,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _get_or_404(db: Session, user: models.User, agent_id: int) -> models.A2AAgent:
    agent = A2AService(db).get_agent(user, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote agent not found")
    return agent


@router.get("", response_model=list[schemas.A2AAgentResponse])
def list_agents(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
) -> list[schemas.A2AAgentResponse]:
    """List the user's remote agents."""
    return [_response(agent) for agent in A2AService(db).list_agents(user)]


@router.post("", response_model=schemas.A2AAgentResponse, status_code=status.HTTP_201_CREATED)
def create_agent(
    payload: schemas.A2AAgentCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.A2AAgentResponse:
    """Register a remote agent (switched off) and fetch its card; a failed fetch shows in ``last_error``."""
    token = payload.token.get_secret_value() if payload.token else None
    try:
        agent = A2AService(db).create_agent(user, name=payload.name, base_url=payload.base_url, token=token)
    except A2AError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return _response(agent)


@router.patch("/{agent_id}", response_model=schemas.A2AAgentResponse)
def update_agent(
    agent_id: int,
    payload: schemas.A2AAgentUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.A2AAgentResponse:
    """Rename, move, switch on/off, or change the token of a remote agent."""
    agent = _get_or_404(db, user, agent_id)
    if payload.token is not None and payload.clear_token:
        raise HTTPException(status_code=422, detail="Send either a new token or clear_token, not both.")
    changes = payload.model_dump(exclude_unset=True, exclude={"token", "clear_token"})
    if payload.token is not None:
        changes["token"] = payload.token.get_secret_value()
    elif payload.clear_token:
        changes["token"] = None
    try:
        A2AService(db).update_agent(agent, changes)
    except A2AError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return _response(agent)


@router.post("/{agent_id}/refresh", response_model=schemas.A2AAgentResponse)
def refresh_agent(
    agent_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.A2AAgentResponse:
    """Fetch the agent card again; a failure switches the agent off and shows in ``last_error``."""
    agent = A2AService(db).refresh(_get_or_404(db, user, agent_id))
    db.commit()
    return _response(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    agent_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> Response:
    """Delete a remote agent and its tool policy."""
    A2AService(db).delete_agent(_get_or_404(db, user, agent_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
