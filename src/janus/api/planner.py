"""Planner API router for Janus."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.llm_service import get_active_model_name
from janus.schemas import planner as planner_schemas
from janus.services import (
    embedding_service,
    image_generation,
    llm_settings,
    planner_account_service,
)
from janus.services.local_planner_service import LocalPlannerService
from janus.trigger_definition_service import TriggerDefinitionError

router = APIRouter(prefix="/api/planner", tags=["planner"])
logger = logging.getLogger(__name__)


def _ensure_settings(db: Session) -> models.Settings:
    """Returns the singleton settings row, creating it if needed."""
    settings = db.query(models.Settings).first()
    if not settings:
        settings = models.Settings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _serialize_settings(settings: models.Settings) -> planner_schemas.Settings:
    """Builds the API-safe settings payload, excluding raw secret values."""
    return planner_schemas.Settings(
        initial_sync_email_limit=settings.initial_sync_email_limit,
        chat_llm_provider=settings.chat_llm_provider,
        chat_llm_model=settings.chat_llm_model,
        utility_llm_provider=settings.utility_llm_provider,
        utility_llm_model=settings.utility_llm_model,
        github_token_configured=bool(settings.github_token),
        github_default_repo=settings.github_default_repo,
        topic_tracking_available=settings.topic_tracking_available,
        topic_tracking_default_enabled=settings.topic_tracking_default_enabled,
        topic_tracking_default_scope=settings.topic_tracking_default_scope.value,
        topic_tracking_default_min_confidence=settings.topic_tracking_default_min_confidence.value,
        topic_tracking_default_min_confirmation_mentions=settings.topic_tracking_default_min_confirmation_mentions,
        topic_tracking_max_active_topics=settings.topic_tracking_max_active_topics,
        topic_tracking_max_context_topics=settings.topic_tracking_max_context_topics,
        topic_tracking_review_required=settings.topic_tracking_review_required,
        **llm_settings.provider_settings_payload(settings),
        **llm_settings.embedding_settings_payload(settings),
        **llm_settings.image_settings_payload(settings),
    )


def _sync_active_model(settings: models.Settings) -> None:
    """Keeps the legacy llm_model column aligned with the active provider."""
    settings.llm_model = get_active_model_name(settings)


@router.get("/accounts", response_model=list[planner_schemas.PlannerAccount])
def get_planners(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Returns planner integrations for the current user."""
    return planner_account_service.list_accounts_for_user(db, user.id)


@router.post("/accounts", response_model=planner_schemas.PlannerAccount)
def add_planner(
    planner_data: planner_schemas.PlannerAccountCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Adds a new planner integration for the current user."""
    encrypted_token = security.security_service.encrypt(planner_data.token)

    new_account = models.PlannerAccount(
        name=planner_data.name,
        provider=planner_data.provider,
        token=encrypted_token,
        api_url=planner_data.api_url,
        user_id=user.id,
    )
    db.add(new_account)
    db.commit()
    db.refresh(new_account)
    return new_account


@router.get("/events", response_model=list[planner_schemas.PlannerEventItem])
def get_events(
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return unified local and imported planner events for the requested range."""
    return LocalPlannerService(db).list_events(user.id, start=start, end=end, include_imported=True)


@router.get("/tasks", response_model=list[planner_schemas.PlannerTaskItem])
def get_tasks(
    include_completed: bool = False,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return unified local and imported planner tasks for the current user."""
    return LocalPlannerService(db).list_tasks(user.id, include_completed=include_completed, include_imported=True)


@router.get("/local/tasks", response_model=list[planner_schemas.PlannerTaskItem])
def get_local_tasks(
    include_completed: bool = False,
    root_only: bool = False,
    task_status: models.TaskStatus | None = Query(default=None, alias="status"),
    urgency: models.TaskUrgency | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return Janus-owned planner tasks only."""
    return LocalPlannerService(db).list_tasks(
        user.id,
        include_completed=include_completed,
        include_imported=False,
        root_only=root_only,
        status=task_status,
        urgency=urgency,
        due_before=due_before,
        due_after=due_after,
    )


@router.get("/local/tasks/tree", response_model=list[planner_schemas.PlannerTaskTreeItem])
def get_local_task_tree(
    include_completed: bool = False,
    task_status: models.TaskStatus | None = Query(default=None, alias="status"),
    urgency: models.TaskUrgency | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return the nested local task tree for the current user."""
    return LocalPlannerService(db).list_local_task_tree(
        user.id,
        include_completed=include_completed,
        status=task_status,
        urgency=urgency,
    )


@router.post("/local/tasks", response_model=planner_schemas.PlannerTaskItem)
def create_local_task(
    payload: planner_schemas.LocalPlannerTaskCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a Janus-owned planner task."""
    try:
        return LocalPlannerService(db).create_task(user.id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.patch("/local/tasks/{task_id}", response_model=planner_schemas.PlannerTaskItem)
def update_local_task(
    task_id: int,
    payload: planner_schemas.LocalPlannerTaskUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update a Janus-owned planner task."""
    try:
        updated = LocalPlannerService(db).update_task(user.id, task_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local planner task not found.")
    return updated


@router.post("/local/tasks/{task_id}/complete", response_model=planner_schemas.PlannerTaskItem)
def complete_local_task(
    task_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Complete exactly one local task without cascading to children."""
    updated = LocalPlannerService(db).complete_task(user.id, task_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local planner task not found.")
    return updated


@router.post("/local/tasks/{task_id}/move", response_model=planner_schemas.PlannerTaskItem)
def move_local_task(
    task_id: int,
    payload: planner_schemas.LocalPlannerTaskMove,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Move a local task to another owned parent or to the root."""
    try:
        moved = LocalPlannerService(db).move_task(
            user.id,
            task_id,
            parent_task_id=payload.parent_task_id,
            sort_order=payload.sort_order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if moved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local planner task not found.")
    return moved


@router.get("/local/events", response_model=list[planner_schemas.PlannerEventItem])
def get_local_events(
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return Janus-owned planner events only."""
    return LocalPlannerService(db).list_events(user.id, start=start, end=end, include_imported=False)


@router.post("/local/events", response_model=planner_schemas.PlannerEventItem)
def create_local_event(
    payload: planner_schemas.LocalPlannerEventCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a Janus-owned planner event."""
    return LocalPlannerService(db).create_event(user.id, payload.model_dump())


@router.patch("/local/events/{event_id}", response_model=planner_schemas.PlannerEventItem)
def update_local_event(
    event_id: int,
    payload: planner_schemas.LocalPlannerEventUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update a Janus-owned planner event."""
    updated = LocalPlannerService(db).update_event(user.id, event_id, payload.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local planner event not found.")
    return updated


@router.get("/suggestions", response_model=list[planner_schemas.PlannerSuggestionItem])
def list_planner_suggestions(
    include_applied: bool = False,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return planner suggestions awaiting review."""
    suggestion_status = None if include_applied else models.PlannerSuggestionStatus.PENDING
    return LocalPlannerService(db).list_suggestions(user.id, status=suggestion_status)


@router.patch("/suggestions/{suggestion_id}", response_model=planner_schemas.PlannerSuggestionItem)
def update_planner_suggestion(
    suggestion_id: int,
    payload: planner_schemas.PlannerSuggestionUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Edit a planner suggestion before review."""
    updated = LocalPlannerService(db).update_suggestion(user.id, suggestion_id, payload.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planner suggestion not found.")
    return updated


@router.post("/suggestions/{suggestion_id}/approve", response_model=planner_schemas.PlannerSuggestionItem)
def approve_planner_suggestion(
    suggestion_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Approve and apply a planner suggestion."""
    updated = LocalPlannerService(db).approve_suggestion(user.id, suggestion_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planner suggestion not found.")
    return updated


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=planner_schemas.PlannerSuggestionItem)
def dismiss_planner_suggestion(
    suggestion_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Dismiss a planner suggestion without mutating planner items."""
    updated = LocalPlannerService(db).dismiss_suggestion(user.id, suggestion_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Planner suggestion not found.")
    return updated


@router.get("/settings", response_model=planner_schemas.Settings)
def get_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns application settings."""
    return _serialize_settings(_ensure_settings(db))


@router.patch("/settings", response_model=planner_schemas.Settings)
def update_settings(  # noqa: C901
    settings_data: planner_schemas.SettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Updates application settings."""
    settings = _ensure_settings(db)

    if settings_data.initial_sync_email_limit is not None:
        settings.initial_sync_email_limit = settings_data.initial_sync_email_limit
    if settings_data.llm_provider is not None:
        settings.llm_provider = settings_data.llm_provider
    llm_settings.apply_llm_settings_update(settings, settings_data)
    try:
        llm_settings.apply_surface_model_update(settings, settings_data)
        llm_settings.apply_embedding_update(settings, settings_data)
        llm_settings.apply_image_update(settings, settings_data)
    except (TriggerDefinitionError, embedding_service.EmbeddingConfigError, image_generation.ImageConfigError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if settings_data.github_token is not None:
        settings.github_token = (
            security.security_service.encrypt(settings_data.github_token) if settings_data.github_token else None
        )
    if settings_data.github_default_repo is not None:
        settings.github_default_repo = settings_data.github_default_repo or None
    if settings_data.topic_tracking_available is not None:
        settings.topic_tracking_available = settings_data.topic_tracking_available
    if settings_data.topic_tracking_default_enabled is not None:
        settings.topic_tracking_default_enabled = settings_data.topic_tracking_default_enabled
    if settings_data.topic_tracking_default_scope is not None:
        try:
            settings.topic_tracking_default_scope = models.TopicExtractionScope(
                settings_data.topic_tracking_default_scope
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "topic_tracking_default_scope must be one of: important_only, important_and_digest, all_non_spam"
                ),
            ) from exc
    if settings_data.topic_tracking_default_min_confidence is not None:
        try:
            settings.topic_tracking_default_min_confidence = models.ConfidenceLevel(
                settings_data.topic_tracking_default_min_confidence
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="topic_tracking_default_min_confidence must be one of: high, medium, low",
            ) from exc
    if settings_data.topic_tracking_default_min_confirmation_mentions is not None:
        settings.topic_tracking_default_min_confirmation_mentions = (
            settings_data.topic_tracking_default_min_confirmation_mentions
        )
    if settings_data.topic_tracking_max_active_topics is not None:
        settings.topic_tracking_max_active_topics = settings_data.topic_tracking_max_active_topics
    if settings_data.topic_tracking_max_context_topics is not None:
        settings.topic_tracking_max_context_topics = settings_data.topic_tracking_max_context_topics
    if settings_data.topic_tracking_review_required is not None:
        settings.topic_tracking_review_required = settings_data.topic_tracking_review_required

    _sync_active_model(settings)

    db.commit()
    db.refresh(settings)
    return _serialize_settings(settings)
