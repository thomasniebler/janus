"""Email API router for Janus."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from janus import builtin_triggers, database, event_logger, models, processor, security
from janus.graph_mail_service import GraphMailMonitor
from janus.imap_service import IMAPMonitor
from janus.schemas import email as email_schemas
from janus.services import email_service, oauth_connection_service
from janus.services.whatsapp_service import phone_to_jid

router = APIRouter(prefix="/api/emails", tags=["emails"])


def _ensure_settings(db: Session) -> models.Settings:
    """Returns the singleton settings row, creating it if needed."""
    settings = db.query(models.Settings).first()
    if settings is None:
        settings = models.Settings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def _serialize_mail_ingestion_settings(settings: models.Settings) -> email_schemas.MailIngestionSettings:
    """Build the mail-ingestion settings payload.

    Classifier guidance comes from the user's character constitution (JAN-201).
    """
    return email_schemas.MailIngestionSettings(initial_sync_email_limit=settings.initial_sync_email_limit)


@router.get("/accounts", response_model=list[email_schemas.EmailAccount])
async def get_accounts(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Returns email accounts owned by the current user."""
    service = email_service.EmailService(db)
    return service.get_accounts(user_id=user.id)


@router.post("/accounts", response_model=email_schemas.EmailAccount)
async def add_account(
    account_data: email_schemas.EmailAccountCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Adds a new email account owned by the current user."""
    service = email_service.EmailService(db)
    return service.add_account(account_data.model_dump(), user_id=user.id)


@router.patch("/accounts/{account_id}", response_model=email_schemas.EmailAccount)
async def update_account(
    account_id: int,
    account_data: email_schemas.EmailAccountUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Updates an existing email account owned by the current user."""
    service = email_service.EmailService(db)
    account = service.update_account_for_user(account_id, user.id, account_data.model_dump(exclude_none=True))
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Removes an email account owned by the current user."""
    service = email_service.EmailService(db)
    if not service.delete_account_for_user(account_id, user.id):
        raise HTTPException(status_code=404, detail="Account not found")
    return {"status": "success"}


@router.post("/accounts/{account_id}/test")
async def test_account_connection(
    account_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Tests the connection to an email account's IMAP server."""
    service = email_service.EmailService(db)
    account = service.get_account_for_user(account_id, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    try:
        monitor = (
            GraphMailMonitor(account)
            if account.auth_type == oauth_connection_service.MICROSOFT
            else IMAPMonitor(account)
        )
        # The test's connect/logout events belong to the account's owner (JAN-272).
        with event_logger.owned_by(account.user_id):
            # Both transports block (sockets, token refresh), so keep them off the event loop.
            success, message = await run_in_threadpool(monitor.test_connection)
        return {"status": "success" if success else "error", "message": message}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/rules", response_model=list[email_schemas.Rule])
async def get_rules(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Returns prioritization rules owned by the current user."""
    service = email_service.EmailService(db)
    return service.get_rules(user_id=user.id)


@router.get("/notifications/preferences", response_model=email_schemas.NotificationPreference)
async def get_notification_preferences(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns notification preferences for the authenticated user."""
    service = email_service.EmailService(db)
    return service.get_notification_preference(user)


@router.patch("/notifications/preferences", response_model=email_schemas.NotificationPreference)
async def update_notification_preferences(
    preference_data: email_schemas.NotificationPreferenceUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Updates notification preferences for the authenticated user."""
    service = email_service.EmailService(db)
    updates = preference_data.model_dump(exclude_none=True)
    current = service.get_notification_preference(user)
    start = updates.get("secretary_active_start_hour", current.secretary_active_start_hour)
    end = updates.get("secretary_active_end_hour", current.secretary_active_end_hour)
    if start >= end:
        raise HTTPException(status_code=422, detail="The secretary's active hours must end after they start.")
    if "secretary_whatsapp_number" in updates:
        number = updates["secretary_whatsapp_number"].strip()
        try:
            # Stored as +<digits>, the form the WhatsApp send path validates; blank clears it.
            updates["secretary_whatsapp_number"] = "+" + phone_to_jid(number).split("@", 1)[0] if number else None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    preference = service.update_notification_preference(user, updates)
    if {"daily_digest_enabled", "daily_digest_hour", "daily_digest_minute"} & updates.keys():
        # The digest itself is the built-in Afternoon digest trigger (JAN-231);
        # keep its schedule in step with these legacy preference fields.
        builtin_triggers.sync_digest_schedule(db, user.id)
    return preference


@router.get("/settings", response_model=email_schemas.MailIngestionSettings)
async def get_mail_ingestion_settings(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns settings specific to mail ingestion and classification."""
    return _serialize_mail_ingestion_settings(_ensure_settings(db))


@router.patch("/settings", response_model=email_schemas.MailIngestionSettings)
async def update_mail_ingestion_settings(
    settings_data: email_schemas.MailIngestionSettingsUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.admin_required),
):
    """Updates the instance-wide mail ingestion settings (admin only, like ``PATCH /api/planner/settings``, JAN-222)."""
    settings = _ensure_settings(db)

    if settings_data.initial_sync_email_limit is not None:
        settings.initial_sync_email_limit = settings_data.initial_sync_email_limit

    db.commit()
    db.refresh(settings)
    return _serialize_mail_ingestion_settings(settings)


@router.post("/rules", response_model=email_schemas.Rule)
async def add_rule(
    rule_data: email_schemas.RuleCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Adds a new prioritization rule."""
    if not processor.validate_regex(rule_data.pattern):
        raise HTTPException(status_code=400, detail="Invalid regex pattern")

    service = email_service.EmailService(db)
    return service.add_rule(rule_data.model_dump(), user_id=user.id)


@router.patch("/rules/{rule_id}", response_model=email_schemas.Rule)
async def update_rule(
    rule_id: int,
    rule_data: email_schemas.RuleUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Updates a rule (e.g. order or active status)."""
    service = email_service.EmailService(db)
    rule = service.update_rule_for_user(rule_id, user.id, order=rule_data.order, is_active=rule_data.is_active)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Removes a prioritization rule owned by the current user."""
    service = email_service.EmailService(db)
    if not service.delete_rule_for_user(rule_id, user.id):
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "success"}


@router.get("/", response_model=list[email_schemas.EmailRecord])
async def list_emails(
    limit: int = 100,
    priority: models.Priority | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Lists recent emails from the current user's accounts.

    Without ``priority`` SPAM is left out; ``priority=spam`` lists the spam folder.
    """
    service = email_service.EmailService(db)
    emails = service.list_emails_for_user(user.id, limit=limit, priority=priority)
    return service.serialize_emails(emails, user=user)


@router.patch("/{email_id}/priority", response_model=email_schemas.EmailRecord)
async def update_priority(
    email_id: int,
    priority: models.Priority,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Updates the priority of an email owned by the current user."""
    service = email_service.EmailService(db)
    email = service.update_email_priority_for_user(email_id, priority, user)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email
