"""LLM model discovery and connection testing for the AI settings (JAN-215, JAN-222).

Lives beside the planner settings routes (same ``/api/planner`` prefix) but in
its own module so the planner router stays within its size budget.
"""

import logging

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.llm_service import LLMService
from janus.schemas import planner as planner_schemas
from janus.services import llm_model_catalog

router = APIRouter(prefix="/api/planner", tags=["planner"])
logger = logging.getLogger(__name__)


@router.post("/llm-models", response_model=planner_schemas.LLMModelListResponse)
def list_llm_models(
    payload: planner_schemas.LLMModelListRequest,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Lists the models a provider offers, using draft credentials without saving them.

    Provider failures (missing key, network error, non-2xx) are returned as
    ``{"models": [], "error": "..."}`` with HTTP 200 so the settings form can
    fall back to manual model entry.
    """
    settings = db.query(models.Settings).first() or models.Settings()
    try:
        credentials = llm_model_catalog.resolve_credentials(
            settings, payload.provider, payload.api_key, payload.base_url, payload.auth_mode
        )
        options = llm_model_catalog.list_models(payload.provider, credentials)
    except llm_model_catalog.ModelCatalogError as exc:
        return planner_schemas.LLMModelListResponse(models=[], error=str(exc))
    except Exception:
        logger.exception("Unexpected failure listing %s models", payload.provider.value)
        return planner_schemas.LLMModelListResponse(models=[], error="Could not load the model list.")
    return planner_schemas.LLMModelListResponse(
        models=[planner_schemas.LLMModelOption(id=option.id, label=option.label) for option in options]
    )


_STORED_KEY_WITHHELD_MESSAGE = (
    "Enter the API key to test this base URL: the stored key is only sent to the base URL it was saved with."
)


@router.post("/test-llm")
def test_llm(
    settings_data: planner_schemas.SettingsUpdate | None = Body(default=None),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Tests the connection to the LLM provider with draft settings, admin only like saving them."""
    try:
        overrides = settings_data.model_dump(exclude_none=True) if settings_data else None
        stored = db.query(models.Settings).first() or models.Settings()
        if overrides and llm_model_catalog.draft_withholds_stored_key(stored, overrides):
            return {"status": "error", "message": _STORED_KEY_WITHHELD_MESSAGE}
        service = LLMService(settings_override=overrides)
        success, message = service.test_connection()
        return {"status": "success" if success else "error", "message": message}
    except Exception as e:
        return {"status": "error", "message": str(e)}
