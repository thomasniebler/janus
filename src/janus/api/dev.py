"""Serves the built Storybook static site under an admin-gated developer route."""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse

from janus import models, security

router = APIRouter(prefix="/dev/storybook", tags=["dev"])

STORYBOOK_DIR = Path(__file__).resolve().parents[3] / "frontend" / "storybook-static"


def _resolve_storybook_file(fallback: str) -> Path:
    safe_path = os.path.normpath(fallback).lstrip(os.sep)
    if ".." in safe_path or safe_path.startswith(os.sep):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")

    file_path = STORYBOOK_DIR / safe_path
    if file_path.is_file():
        return file_path

    index_path = STORYBOOK_DIR / "index.html"
    if index_path.is_file():
        return index_path

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Storybook build not found. Run 'npm run build-storybook' in the frontend directory.",
    )


@router.get("")
async def serve_storybook_root(user: models.User = Depends(security.admin_required)):
    """Redirects to the trailing-slash root (admin-only).

    Storybook's built index.html references its assets with relative (`./...`) URLs, which
    resolve against `/dev/` rather than `/dev/storybook/` without the trailing slash --
    breaking every asset request. Redirecting fixes the browser's base path first.
    """
    return RedirectResponse(url="/dev/storybook/")


@router.get("/{fallback:path}")
async def serve_storybook(fallback: str, user: models.User = Depends(security.admin_required)):
    """Serves a file from the built Storybook static site, or its index for client-side routes (admin-only)."""
    return FileResponse(_resolve_storybook_file(fallback))
