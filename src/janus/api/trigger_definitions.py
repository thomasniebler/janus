"""Authenticated CRUD for saved trigger definitions and their run history (JAN-228).

Also the review side of agent-proposed automations (JAN-235): the proposal
list, approve, and reject routes are the *only* code that writes a proposal's
approval fields, and they always act as the authenticated owner.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import builtin_triggers, database, models, security, trigger_events
from janus import trigger_definition_service as definitions
from janus import trigger_proposal_service as proposals
from janus.plan_state import load_plan_outcomes
from janus.schemas.runtime_triggers import (
    RuntimeTriggerResponse,
    TriggerDefinitionCreate,
    TriggerDefinitionResponse,
    TriggerDefinitionUpdate,
    TriggerProposalApprove,
    TriggerProposalCount,
    TriggerProposalReject,
    TriggerProposalResponse,
    TriggerSubsystemResponse,
)

router = APIRouter(prefix="/api/runtime/definitions", tags=["runtime-triggers"])


def _get_or_404(db: Session, user: models.User, definition_id: int) -> models.TriggerDefinition:
    definition = definitions.get_owned_definition(db, user.id, definition_id)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trigger definition not found")
    return definition


def _unprocessable(error: definitions.TriggerDefinitionError) -> HTTPException:
    if isinstance(error, definitions.TriggerDefinitionConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))


def with_outcomes(db: Session, owner_id: int, records: list[models.RuntimeTrigger]) -> list[RuntimeTriggerResponse]:
    """Attach each run's persisted plan outcome (result / step error) to its response."""
    outcomes = load_plan_outcomes(db, owner_id, [record.task_id for record in records])
    definition_ids = {record.definition_id for record in records if record.definition_id is not None}
    linked = (
        {
            definition.id: definition
            for definition in db.scalars(
                select(models.TriggerDefinition).where(
                    models.TriggerDefinition.id.in_(definition_ids),
                    models.TriggerDefinition.user_id == owner_id,
                )
            )
        }
        if definition_ids
        else {}
    )
    responses = []
    for record in records:
        update: dict[str, object] = {}
        outcome = outcomes.get(record.task_id)
        if outcome is not None:
            update.update(result=outcome.result, step_error=outcome.error)
        definition = linked.get(record.definition_id) if record.definition_id is not None else None
        if definition is not None:
            # ``model_copy`` skips validation, so convert the raw ``origin`` column to the
            # schema's enum here; a plain string makes Pydantic warn on every
            # serialization (JAN-294).
            update.update(
                definition_name=definition.name,
                definition_origin=models.TriggerDefinitionOrigin(definition.origin),
            )
        response = RuntimeTriggerResponse.model_validate(record)
        responses.append(response.model_copy(update=update) if update else response)
    return responses


@router.get("/event-catalog", response_model=list[TriggerSubsystemResponse])
def get_event_catalog(_user: models.User = Depends(security.get_current_user)):
    """List the subsystems that publish events and the events each one publishes (JAN-210).

    This is the only set of values an event definition's ``event_type`` accepts.
    """
    return trigger_events.catalog()


@router.get("/proposals", response_model=list[TriggerProposalResponse])
def list_proposals(
    proposal_status: models.TriggerProposalStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List the automations Janus suggested, newest first, with their review data."""
    rows = proposals.list_proposals(
        db, user.id, status=proposal_status.value if proposal_status is not None else None, limit=limit
    )
    return [
        TriggerProposalResponse.model_validate({**TriggerDefinitionResponse.model_validate(row).model_dump(), **review})
        for row, review in zip(rows, proposals.review_data(db, user.id, rows), strict=True)
    ]


@router.get("/proposals/count", response_model=TriggerProposalCount)
def count_proposals(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return how many suggested automations await review (navigation badge)."""
    return {"open": proposals.count_open_proposals(db, user.id)}


@router.post("/{definition_id}/approve", response_model=TriggerDefinitionResponse)
def approve_proposal(
    definition_id: int,
    body: TriggerProposalApprove,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Approve a suggested automation as the owner, with optional edits; 409 unless it is still proposed."""
    definition = _get_or_404(db, user, definition_id)
    if not proposals.is_proposal(definition):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggested automation not found")
    edits = body.model_dump(exclude_unset=True, exclude={"enable"})
    if edits.get("kind") is not None:
        edits["kind"] = edits["kind"].value
    try:
        return proposals.approve_proposal(db, definition, user.id, edits=edits, enable=body.enable)
    except definitions.TriggerDefinitionError as error:
        raise _unprocessable(error) from error


@router.post("/{definition_id}/reject", response_model=TriggerDefinitionResponse)
def reject_proposal(
    definition_id: int,
    body: TriggerProposalReject | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Reject a suggested automation; Janus sees the reason and will not propose it again for 30 days."""
    definition = _get_or_404(db, user, definition_id)
    if not proposals.is_proposal(definition):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggested automation not found")
    try:
        return proposals.reject_proposal(db, definition, reason=body.reason if body else None)
    except definitions.TriggerDefinitionError as error:
        raise _unprocessable(error) from error


@router.get("/", response_model=list[TriggerDefinitionResponse])
def list_definitions(
    timezone: str | None = Query(default=None, description="The browser's IANA timezone, used for seeding."),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List the current user's trigger definitions, built-ins first (seeding any missing built-in).

    Suggested automations that are not approved are listed by ``/proposals`` instead.
    """
    builtin_triggers.ensure_builtin_definitions(db, user.id)
    builtin_triggers.adopt_client_timezone(db, user.id, timezone)
    return definitions.list_owned_definitions(db, user.id)


@router.post("/", response_model=TriggerDefinitionResponse, status_code=status.HTTP_201_CREATED)
def create_definition(
    body: TriggerDefinitionCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a user-authored definition."""
    try:
        return definitions.create_definition(
            db,
            user.id,
            origin=models.TriggerDefinitionOrigin.USER.value,
            enabled=body.enabled,
            name=body.name,
            objective=body.objective,
            tool_ids=body.tool_ids,
            kind=body.kind.value,
            schedule=body.schedule.model_dump() if body.schedule else None,
            timezone=body.timezone,
            event_type=body.event_type,
            event_filter=[condition.model_dump() for condition in body.event_filter or []] or None,
            # Left out: the event's default limit (JAN-363); an explicit null means no limit.
            **({"max_runs_per_hour": body.max_runs_per_hour} if "max_runs_per_hour" in body.model_fields_set else {}),
            llm_provider=body.llm_provider,
            llm_model=body.llm_model,
            max_tool_calls=body.max_tool_calls,
            max_run_seconds=body.max_run_seconds,
        )
    except definitions.TriggerDefinitionError as error:
        raise _unprocessable(error) from error


@router.patch("/{definition_id}", response_model=TriggerDefinitionResponse)
def update_definition(
    definition_id: int,
    body: TriggerDefinitionUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update a definition. Editing a definition does not change who authored it.

    A suggested automation awaiting review answers 409 (approve or reject it
    instead); the owner's edit of an approved one re-records its approval.
    """
    definition = _get_or_404(db, user, definition_id)
    changes = body.model_dump(exclude_unset=True)
    if "kind" in changes and changes["kind"] is not None:
        changes["kind"] = changes["kind"].value
    try:
        return definitions.update_definition(db, definition, actor_user_id=user.id, **changes)
    except definitions.TriggerDefinitionError as error:
        raise _unprocessable(error) from error


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_definition(
    definition_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Delete a definition (built-ins can only be disabled)."""
    definition = _get_or_404(db, user, definition_id)
    try:
        definitions.delete_definition(db, definition)
    except definitions.TriggerDefinitionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/{definition_id}/run", response_model=RuntimeTriggerResponse, status_code=status.HTTP_202_ACCEPTED)
def run_definition(
    definition_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Queue an immediate run of a definition, whatever its activation kind."""
    definition = _get_or_404(db, user, definition_id)
    try:
        return definitions.run_definition_now(db, definition)
    except definitions.TriggerDefinitionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/{definition_id}/runs", response_model=list[RuntimeTriggerResponse])
def list_definition_runs(
    definition_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """List the newest runs of one definition, with their outcomes."""
    definition = _get_or_404(db, user, definition_id)
    records = list(
        db.scalars(
            select(models.RuntimeTrigger)
            .where(
                models.RuntimeTrigger.user_id == user.id,
                models.RuntimeTrigger.definition_id == definition.id,
            )
            .order_by(models.RuntimeTrigger.created_at.desc(), models.RuntimeTrigger.id.desc())
            .limit(limit)
        )
    )
    return with_outcomes(db, user.id, records)
