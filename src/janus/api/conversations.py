"""REST API router for conversation management."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services.conversation_service import ConversationService

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

#: Upper bound on the delegations list ``offset`` (JAN-208); far beyond any real conversation.
MAX_DELEGATIONS_OFFSET = 1_000_000


# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------


class ToolCallResponse(BaseModel):
    """One tool call of an assistant message (JAN-249).

    ``args`` are redacted (a JSON value, or cut JSON text when too long);
    ``result_preview`` is untrusted plain text for display only.
    """

    model_config = ConfigDict(from_attributes=True)

    call_id: str
    tool_id: str | None
    tool_name: str
    phase: str
    args: Any = None
    args_truncated: bool
    result_preview: str | None
    result_truncated: bool
    duration_ms: int | None
    #: The user's chat approval decision (JAN-254) for a confirmation-required call, else ``None``.
    approval: str | None = None


class UIBlockResponse(BaseModel):
    """One UI block of an assistant message (JAN-252).

    ``payload`` is the validated block exactly as streamed in ``__UI_BLOCK__``
    (``id``, ``version``, ``type`` and the type's fields); it is model output,
    rendered as text by the client.
    """

    model_config = ConfigDict(from_attributes=True)

    block_id: str
    block_type: str
    schema_version: int
    payload: dict[str, Any]


class MessageResponse(BaseModel):
    """Response schema for a single conversation message."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime
    tool_calls: list[ToolCallResponse] = []
    ui_blocks: list[UIBlockResponse] = []


class ConversationSummary(BaseModel):
    """Summary response for a conversation (no messages)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime
    #: ``user``, or ``janus`` for a conversation an unattended run started (JAN-319).
    origin: str = "user"
    #: ``True`` for a conversation Janus started that its owner has not opened yet (JAN-319).
    unread: bool = False


class ConversationDetail(ConversationSummary):
    """Detail response for a conversation including its messages."""

    messages: list[MessageResponse]


class DelegationResponse(BaseModel):
    """One delegated child task spawned from a conversation (JAN-208).

    ``objective``, ``result`` and ``error`` are model-produced or model-derived
    text, rendered as plain text by the client. The private claim token and the
    internal parent/child task ids are deliberately not exposed.
    """

    model_config = ConfigDict(from_attributes=True)

    delegation_id: str
    status: models.RuntimeDelegationStatus
    objective: str
    requested_tool_ids: list[str]
    allowed_tool_ids: list[str]
    result: str | None
    error: str | None
    depth: int
    created_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None


class DelegationPage(BaseModel):
    """One newest-first page of a conversation's delegated tasks plus pagination metadata."""

    items: list[DelegationResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class ConversationCreate(BaseModel):
    """Request schema for creating a new conversation."""

    title: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[ConversationSummary])
def list_conversations(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns all conversations belonging to the current user, newest first."""
    return ConversationService(db).list_conversations_for_user(user.id)


@router.post("/", response_model=ConversationSummary, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Creates a new empty conversation for the current user."""
    return ConversationService(db).create_conversation_for_user(user.id, title=payload.title)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns a conversation with all of its messages; opening one Janus started marks it read (JAN-319)."""
    service = ConversationService(db)
    conversation = service.get_conversation_for_user(conversation_id, user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    service.mark_read_for_user(conversation)
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Deletes a conversation and all its messages."""
    if not ConversationService(db).delete_conversation_for_user(conversation_id, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


@router.get("/{conversation_id}/delegations", response_model=DelegationPage)
def list_conversation_delegations(
    conversation_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    # Bounded so an out-of-range value answers 422 instead of overflowing SQLite's integer (500).
    offset: int = Query(default=0, ge=0, le=MAX_DELEGATIONS_OFFSET),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns one newest-first page of the tasks Janus delegated from this conversation (JAN-208).

    Another user's conversation answers 404, the same as a missing one.
    """
    page = ConversationService(db).list_delegations_for_user(conversation_id, user.id, limit=limit, offset=offset)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    rows, total = page
    return DelegationPage(
        items=[DelegationResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )
