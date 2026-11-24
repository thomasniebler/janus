"""Storage of chat-generated images (JAN-253).

Files, never SQLite blobs: ``<image_storage_dir>/<user_id>/<image_id>.<ext>``
with one ``generated_images`` row each (``models_images.GeneratedImage``).
This module owns writing them (:func:`store_image`), the per-user cap
(:func:`ensure_capacity`), the owner-scoped lookup the endpoint uses
(:func:`get_image_for_user`, which also confines every path to the images
directory), linking a turn's images to its assistant message
(:func:`attach_turn_images`), and removing files when their conversation is
deleted (:func:`conversation_image_paths` + :func:`delete_files`).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from janus import config, models
from janus.models_images import GeneratedImage
from janus.services.image_generation import SniffedImage

logger = logging.getLogger(__name__)

IMAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
IMAGE_URL_PREFIX = "/api/images/"


class ImageStorageFull(RuntimeError):
    """The user's images already use (or this image would exceed) their storage allowance."""


#: One lock per user serializes the cap check and the write of a new image, so two
#: chat turns of the same user cannot both pass the check (Janus runs as a single
#: process; see "Single-instance deployment constraint" in configuration.md).
_user_locks: dict[int, threading.Lock] = {}
_user_locks_guard = threading.Lock()


def _user_lock(user_id: int) -> threading.Lock:
    with _user_locks_guard:
        return _user_locks.setdefault(user_id, threading.Lock())


def _full_message(used: int, limit: int) -> str:
    return (
        f"image storage is full ({used // (1024 * 1024)} of {limit // (1024 * 1024)} MB used); "
        "delete conversations with images to free space"
    )


def images_root() -> Path:
    """Return the directory every generated image lives under."""
    return Path(config.settings.image_storage_dir)


def limit_bytes() -> int:
    """Return the per-user storage allowance in bytes."""
    return int(config.settings.image_storage_limit_mb) * 1024 * 1024


def new_image_id() -> str:
    """Return a fresh public image id (32 URL-safe characters)."""
    return secrets.token_urlsafe(24)


def image_url(image_id: str) -> str:
    """Return the same-origin URL an ``image`` block uses for ``image_id``."""
    return f"{IMAGE_URL_PREFIX}{image_id}"


def usage_bytes(db: Session, user_id: int) -> int:
    """Return the total size of ``user_id``'s stored images."""
    total = (
        db.query(func.coalesce(func.sum(GeneratedImage.size_bytes), 0))
        .filter(GeneratedImage.user_id == user_id)
        .scalar()
    )
    return int(total or 0)


def ensure_capacity(db: Session, user_id: int) -> None:
    """Raise :class:`ImageStorageFull` when ``user_id`` is at or over the allowance.

    A cheap pre-check before a provider is paid; :func:`store_image` checks again,
    with the new image's size, under the user's lock.
    """
    used, limit = usage_bytes(db, user_id), limit_bytes()
    if used >= limit:
        raise ImageStorageFull(_full_message(used, limit))


def resolve_path(relative: str) -> Path | None:
    """Return the absolute file for a stored relative ``path``, or ``None`` if it leaves the images dir."""
    root = images_root().resolve()
    candidate = (root / relative).resolve()
    if candidate == root or not candidate.is_relative_to(root):
        return None
    return candidate


def store_image(
    db: Session,
    *,
    user_id: int,
    conversation_id: int | None,
    prompt: str,
    provider: str,
    model: str,
    data: bytes,
    sniffed: SniffedImage,
) -> GeneratedImage:
    """Write ``data`` to the user's image folder and commit its metadata row.

    Raises :class:`ImageStorageFull` (and writes nothing) when the user's stored
    images plus ``data`` would exceed the allowance; the check, the write, and the
    commit run under the user's lock so concurrent turns cannot overshoot it.
    """
    with _user_lock(user_id):
        used, limit = usage_bytes(db, user_id), limit_bytes()
        if used + len(data) > limit:
            raise ImageStorageFull(_full_message(used, limit))
        return _write_image(
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            prompt=prompt,
            provider=provider,
            model=model,
            data=data,
            sniffed=sniffed,
        )


def _write_image(
    db: Session,
    *,
    user_id: int,
    conversation_id: int | None,
    prompt: str,
    provider: str,
    model: str,
    data: bytes,
    sniffed: SniffedImage,
) -> GeneratedImage:
    image_id = new_image_id()
    relative = f"{user_id}/{image_id}.{sniffed.extension}"
    target = resolve_path(relative)
    if target is None:  # pragma: no cover - the id and user id cannot contain a path separator
        raise ValueError("image path escapes the images directory")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    row = GeneratedImage(
        id=image_id,
        user_id=user_id,
        conversation_id=conversation_id,
        prompt=prompt,
        provider=provider,
        model=model,
        path=relative,
        mime_type=sniffed.mime_type,
        width=sniffed.width,
        height=sniffed.height,
        size_bytes=len(data),
    )
    try:
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    return row


def get_image_for_user(db: Session, image_id: str, user_id: int) -> tuple[GeneratedImage, Path] | None:
    """Return ``user_id``'s image row and its file, or ``None`` (missing, foreign, malformed, or unsafe)."""
    if not IMAGE_ID_PATTERN.match(image_id):
        return None
    row = db.query(GeneratedImage).filter(GeneratedImage.id == image_id, GeneratedImage.user_id == user_id).first()
    if row is None:
        return None
    path = resolve_path(row.path)
    if path is None or not path.is_file():
        return None
    return row, path


def image_ids_in_blocks(blocks: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return the image ids the turn's ``image`` blocks point at, in order."""
    ids = []
    for block in blocks:
        url = block.get("url") if block.get("type") == "image" else None
        if isinstance(url, str) and url.startswith(IMAGE_URL_PREFIX):
            ids.append(url[len(IMAGE_URL_PREFIX) :])
    return list(dict.fromkeys(ids))


def attach_turn_images(
    db: Session, blocks: Iterable[Mapping[str, Any]], message: models.ConversationMessage, user_id: int
) -> int:
    """Link the not-yet-linked images of this turn's blocks to the assistant ``message``; return how many."""
    ids = image_ids_in_blocks(blocks)
    if not ids:
        return 0
    rows = (
        db.query(GeneratedImage)
        .filter(
            GeneratedImage.id.in_(ids),
            GeneratedImage.user_id == user_id,
            GeneratedImage.conversation_id == message.conversation_id,
            GeneratedImage.message_id.is_(None),
        )
        .all()
    )
    for row in rows:
        row.message = message
    return len(rows)


def conversation_image_paths(db: Session, conversation_id: int) -> list[str]:
    """Return the stored relative paths of a conversation's images (read before deleting it)."""
    rows = db.query(GeneratedImage.path).filter(GeneratedImage.conversation_id == conversation_id)
    return [path for (path,) in rows]


def delete_image_for_user(db: Session, image_id: str, user_id: int) -> bool:
    """Delete one of ``user_id``'s images (row and file); ``False`` when it is not theirs or missing (JAN-359).

    The only way to free the space of an image an unattended run stored without a
    conversation; a chat image can be deleted this way too.
    """
    if not IMAGE_ID_PATTERN.match(image_id):
        return False
    row = db.query(GeneratedImage).filter(GeneratedImage.id == image_id, GeneratedImage.user_id == user_id).first()
    if row is None:
        return False
    path = row.path
    db.delete(row)
    db.commit()
    delete_files([path])
    return True


def delete_files(paths: Iterable[str]) -> None:
    """Remove image files by stored relative path; best effort, never outside the images dir."""
    for relative in paths:
        target = resolve_path(relative)
        if target is None:
            logger.warning("Refusing to delete an image path outside the images directory")
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete a generated image file", exc_info=True)


__all__ = [
    "IMAGE_ID_PATTERN",
    "ImageStorageFull",
    "attach_turn_images",
    "conversation_image_paths",
    "delete_files",
    "ensure_capacity",
    "get_image_for_user",
    "image_ids_in_blocks",
    "image_url",
    "images_root",
    "limit_bytes",
    "resolve_path",
    "store_image",
    "usage_bytes",
]
