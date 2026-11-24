"""WhatsApp API router for Janus."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import whatsapp as whatsapp_schemas
from janus.services.whatsapp_service import WhatsAppService
from janus.services.whatsapp_setup_service import WhatsAppSetupService

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.get("/accounts", response_model=list[whatsapp_schemas.WhatsAppAccount])
def list_accounts(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Return WhatsApp accounts for the current user."""
    service = WhatsAppService(db)
    return [service.serialize_account(account) for account in service.list_accounts(user)]


@router.post("/accounts", response_model=whatsapp_schemas.WhatsAppAccount)
def create_account(
    payload: whatsapp_schemas.WhatsAppAccountCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a WhatsApp account."""
    service = WhatsAppService(db)
    return service.serialize_account(service.create_account(user, payload.model_dump()))


@router.post("/setup-sessions", response_model=whatsapp_schemas.WhatsAppSetupSession)
def create_setup_session(
    payload: whatsapp_schemas.WhatsAppSetupSessionCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a user-scoped WhatsApp setup session."""
    service = WhatsAppSetupService(db)
    try:
        session = service.create_setup_session(user, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.serialize_setup_session(session)


@router.get("/setup-sessions/{session_id}", response_model=whatsapp_schemas.WhatsAppSetupSession)
def get_setup_session(
    session_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one WhatsApp setup session for the current user."""
    service = WhatsAppSetupService(db)
    session = service.get_setup_session_for_user(session_id, user)
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp setup session not found")
    return service.serialize_setup_session(session)


@router.post("/setup-sessions/{session_id}/start-pairing", response_model=whatsapp_schemas.WhatsAppSetupSession)
def start_pairing(
    session_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Start live Neonize pairing for a setup session."""
    service = WhatsAppSetupService(db)
    session = service.get_setup_session_for_user(session_id, user)
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp setup session not found")
    try:
        session = service.start_pairing(session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize_setup_session(session)


@router.post("/setup-sessions/{session_id}/cancel", response_model=whatsapp_schemas.WhatsAppSetupSession)
def cancel_setup_session(
    session_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Cancel one setup session."""
    service = WhatsAppSetupService(db)
    session = service.get_setup_session_for_user(session_id, user)
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp setup session not found")
    return service.serialize_setup_session(service.cancel_setup_session(session))


@router.post("/setup-sessions/{session_id}/fallback-import", response_model=whatsapp_schemas.WhatsAppSetupSession)
def fallback_import(
    session_id: int,
    payload: whatsapp_schemas.WhatsAppSetupSessionFallbackImport,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Attach fallback session material to a setup session."""
    service = WhatsAppSetupService(db)
    session = service.get_setup_session_for_user(session_id, user)
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp setup session not found")
    try:
        session = service.import_fallback_session(
            session,
            session_data=payload.session_data,
            session_database_path=payload.session_database_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return service.serialize_setup_session(session)


@router.post("/setup-sessions/{session_id}/complete", response_model=whatsapp_schemas.WhatsAppSetupCompleteResult)
def complete_setup_session(
    session_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Finalize a setup session into a durable WhatsApp account and sync it."""
    service = WhatsAppSetupService(db)
    session = service.get_setup_session_for_user(session_id, user)
    if session is None:
        raise HTTPException(status_code=404, detail="WhatsApp setup session not found")
    try:
        return service.complete_setup_session(user, session)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/accounts/{account_id}", response_model=whatsapp_schemas.WhatsAppAccount)
def update_account(
    account_id: int,
    payload: whatsapp_schemas.WhatsAppAccountUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update one WhatsApp account."""
    service = WhatsAppService(db)
    account = service.update_account_for_user(account_id, user, payload.model_dump(exclude_unset=True))
    if account is None:
        raise HTTPException(status_code=404, detail="WhatsApp account not found")
    return service.serialize_account(account)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Delete one WhatsApp account."""
    if not WhatsAppService(db).delete_account_for_user(account_id, user):
        raise HTTPException(status_code=404, detail="WhatsApp account not found")
    return {"status": "success"}


@router.post("/accounts/{account_id}/test")
def test_connection(
    account_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Run a best-effort Neonize readiness check."""
    service = WhatsAppService(db)
    account = service.get_account_for_user(account_id, user)
    if account is None:
        raise HTTPException(status_code=404, detail="WhatsApp account not found")
    ok, message = service.test_connection(account)
    return {"status": "success" if ok else "error", "message": message}


@router.post("/accounts/{account_id}/sync", response_model=whatsapp_schemas.WhatsAppSyncResult)
def restart_connector(
    account_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Restart the live Neonize connector for one account (best-effort connectivity refresh)."""
    service = WhatsAppService(db)
    account = service.get_account_for_user(account_id, user)
    if account is None:
        raise HTTPException(status_code=404, detail="WhatsApp account not found")
    try:
        return service.restart_connector(account)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conversations", response_model=list[whatsapp_schemas.WhatsAppConversationSummary])
def list_conversations(
    limit: int = 50,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return recent WhatsApp conversations."""
    return WhatsAppService(db).list_conversations(user, limit=limit)


@router.get("/messages", response_model=list[whatsapp_schemas.WhatsAppMessage])
def list_messages(
    conversation_id: int | None = None,
    days: int = 3,
    limit: int = 100,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return recent WhatsApp messages."""
    return WhatsAppService(db).list_messages(user, conversation_id=conversation_id, days=days, limit=limit)


@router.patch("/messages/{message_id}/priority", response_model=whatsapp_schemas.WhatsAppMessage)
def update_message_priority(
    message_id: int,
    payload: whatsapp_schemas.WhatsAppPriorityUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Update one WhatsApp message priority similarly to email handling."""
    message = WhatsAppService(db).update_message_priority(user, message_id, payload.priority)
    if message is None:
        raise HTTPException(status_code=404, detail="WhatsApp message not found")
    return message
