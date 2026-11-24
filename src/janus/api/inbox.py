"""Action inbox and device-token endpoints.

``GET /api/inbox`` and the inbox actions accept either the web session or a device token (``Authorization:
Bearer jdt_...``); a device token is refused everywhere else. Token management needs the web session.
"""

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import inbox as inbox_schemas
from janus.services.device_token_service import DeviceTokenError, DeviceTokenService
from janus.services.inbox_service import InboxError, InboxService

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


async def get_inbox_user(
    db: Session = Depends(database.get_db),
    token: security.AuthToken | None = Depends(security.get_token),
    authorization: str | None = Header(None),
) -> models.User:
    """The web session's user, or the owner of a valid device token, or 401.

    A valid web session wins; when it is missing or invalid (an expired cookie, say), a device token in the
    ``Authorization`` header is tried next.
    """
    user = await security.get_user_or_none(db=db, token=token)
    if user is None and authorization and authorization.startswith("Bearer "):
        user = DeviceTokenService(db).authenticate(authorization.removeprefix("Bearer "))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


@router.get("", response_model=inbox_schemas.InboxResponse)
def get_inbox(
    request: Request,
    response: Response,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(get_inbox_user),
):
    """The owner's open inbox, newest first. Send ``If-None-Match`` with the last ETag to poll cheaply."""
    payload = inbox_schemas.InboxResponse(items=InboxService(db).list_items(user))
    etag = 'W/"' + hashlib.sha256(payload.model_dump_json().encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return payload


@router.post("/tokens", response_model=inbox_schemas.DeviceTokenCreated, status_code=status.HTTP_201_CREATED)
def create_device_token(
    payload: inbox_schemas.DeviceTokenCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Create a device token (web session only). The secret is returned once."""
    try:
        record, raw = DeviceTokenService(db).create_token(user.id, payload.name)
    except DeviceTokenError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return inbox_schemas.DeviceTokenCreated(
        id=record.id,
        name=record.name,
        token_prefix=record.token_prefix,
        created_at=record.created_at,
        last_used_at=record.last_used_at,
        token=raw,
    )


@router.get("/tokens", response_model=list[inbox_schemas.DeviceTokenInfo])
def list_device_tokens(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """The owner's active device tokens, without their secrets (web session only)."""
    return DeviceTokenService(db).list_tokens(user.id)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device_token(
    token_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Revoke a device token (web session only)."""
    if not DeviceTokenService(db).revoke_token(user.id, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device token not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{kind}/{item_id}/{action}", status_code=status.HTTP_204_NO_CONTENT)
def act_on_inbox_item(
    kind: str,
    item_id: int,
    action: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(get_inbox_user),
):
    """Run a low-risk action (``mark_read`` or ``dismiss``) on one inbox item."""
    try:
        InboxService(db).act(user, kind, item_id, action)
    except InboxError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Inbox item not found.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
