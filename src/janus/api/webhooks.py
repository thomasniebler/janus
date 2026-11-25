"""Inbound webhooks (JAN-406): management for the signed-in user, and the public delivery URL."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import webhooks as schemas
from janus.services import webhook_service

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

DELIVERY_PATH = "/api/webhooks/in/{key}"


def _response(endpoint: models.WebhookEndpoint) -> schemas.WebhookResponse:
    return schemas.WebhookResponse(
        id=endpoint.id,
        name=endpoint.name,
        key_prefix=endpoint.key_prefix,
        enabled=endpoint.enabled,
        received_count=endpoint.received_count or 0,
        last_received_at=endpoint.last_received_at,
        created_at=endpoint.created_at,
    )


def _get_or_404(db: Session, user: models.User, endpoint_id: int) -> models.WebhookEndpoint:
    endpoint = webhook_service.get_endpoint(db, user, endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    return endpoint


@router.get("", response_model=list[schemas.WebhookResponse])
def list_webhooks(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
) -> list[schemas.WebhookResponse]:
    """List the user's webhooks."""
    return [_response(endpoint) for endpoint in webhook_service.list_endpoints(db, user)]


@router.post("", response_model=schemas.WebhookSecretResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(
    payload: schemas.WebhookCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.WebhookSecretResponse:
    """Create a webhook; the response is the only time its URL path and secret are shown."""
    try:
        created = webhook_service.create(db, user, payload.name)
    except webhook_service.WebhookError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    return schemas.WebhookSecretResponse(
        webhook=_response(created.endpoint), path=DELIVERY_PATH.format(key=created.key), secret=created.secret
    )


@router.patch("/{endpoint_id}", response_model=schemas.WebhookResponse)
def update_webhook(
    endpoint_id: int,
    payload: schemas.WebhookUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.WebhookResponse:
    """Rename a webhook or switch it on/off."""
    endpoint = _get_or_404(db, user, endpoint_id)
    try:
        webhook_service.update(endpoint, name=payload.name, enabled=payload.enabled)
    except webhook_service.WebhookError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    return _response(endpoint)


@router.post("/{endpoint_id}/rotate-secret", response_model=schemas.WebhookSecretResponse)
def rotate_webhook_secret(
    endpoint_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> schemas.WebhookSecretResponse:
    """Replace the secret; the old one stops working at once. The new one is shown only now."""
    endpoint = _get_or_404(db, user, endpoint_id)
    secret = webhook_service.rotate_secret(endpoint)
    db.commit()
    return schemas.WebhookSecretResponse(webhook=_response(endpoint), secret=secret)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    endpoint_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
) -> Response:
    """Delete a webhook; its URL stops working at once."""
    webhook_service.delete(db, _get_or_404(db, user, endpoint_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/in/{key}", response_model=schemas.WebhookDeliveryResponse, status_code=status.HTTP_202_ACCEPTED)
async def deliver(
    key: str, request: Request, db: Session = Depends(database.get_db)
) -> schemas.WebhookDeliveryResponse:
    """Public delivery URL: authenticated by the HMAC signature, not a session (see ``webhook_service``)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > webhook_service.MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="the body is too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > webhook_service.MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="the body is too large")
    try:
        runs = webhook_service.receive(db, key, bytes(body), dict(request.headers))
    except webhook_service.WebhookError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    db.commit()
    return schemas.WebhookDeliveryResponse(accepted=True, runs=runs)
