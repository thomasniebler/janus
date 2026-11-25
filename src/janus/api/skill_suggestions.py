"""Skill suggestions from repeated tool sequences in the user's chats (JAN-407)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import skill_proposer

router = APIRouter(prefix="/api/skills/suggestions", tags=["skills"])


class SkillSuggestionResponse(BaseModel):
    """A repeated tool sequence the user could turn into a skill."""

    key: str
    tools: list[str]
    turns: int
    examples: list[str]
    #: The goal to hand to ``POST /api/skills/design-assist``.
    brief: str


class SkillSuggestionDismiss(BaseModel):
    """Hide one suggestion for a while."""

    key: str = Field(min_length=1, max_length=32)


@router.get("", response_model=list[SkillSuggestionResponse])
def list_suggestions(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
) -> list[SkillSuggestionResponse]:
    """The user's current suggestions (computed on request, no model call)."""
    return [
        SkillSuggestionResponse(key=s.key, tools=list(s.tools), turns=s.turns, examples=s.examples, brief=s.brief)
        for s in skill_proposer.suggestions(db, user.id)
    ]


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_suggestion(
    payload: SkillSuggestionDismiss,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> Response:
    """Hide a suggestion for 30 days."""
    skill_proposer.dismiss(db, user.id, payload.key)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
