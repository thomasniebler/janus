"""Authenticated, owner-scoped access to chat-generated images (JAN-253).

``GET /api/images/{image_id}`` is the only URL an ``image`` UI block may point
at. ``DELETE /api/images/{image_id}`` (JAN-359) removes one of the owner's images
and its file, which frees its share of the storage allowance; it is the clean-up
path for images an unattended run stored without a conversation. Ownership and
path confinement live in ``services/image_store.py``; a foreign, missing,
malformed, or unsafe id all answer the same ``404``.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.services import image_store

router = APIRouter(prefix="/api/images", tags=["images"])

#: Never cached: on a shared browser profile a cached copy would outlive the
#: owner's logout and the image's deletion with its conversation.
_CACHE_CONTROL = "no-store"


@router.get("/{image_id}", response_class=FileResponse)
def get_image(
    image_id: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Return one of the current user's generated images."""
    found = image_store.get_image_for_user(db, image_id, user.id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found.")
    image, path = found
    return FileResponse(
        path,
        media_type=image.mime_type,
        filename=f"janus-image-{image.id}{path.suffix}",
        content_disposition_type="inline",
        headers={"Cache-Control": _CACHE_CONTROL, "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: str,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Delete one of the current user's generated images (row and file)."""
    if not image_store.delete_image_for_user(db, image_id, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found.")
