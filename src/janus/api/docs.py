"""Documentation API router for Janus.

File access and path confinement live in ``services/docs_service.py``, which
the ``docs`` agent toolbox shares (JAN-297). The router serves every page under
``docs/docs`` (including ``reviews/``) plus the ``tech-spec.md`` alias, but only
to a logged-in session (JAN-299): the frontend ``/docs`` page already sits
behind the login redirect, and the tech specs and security assessments are not
meant to be public.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from janus import security
from janus.services import docs_service

router = APIRouter(prefix="/api/docs", tags=["docs"], dependencies=[Depends(security.get_current_user)])


@router.get("/nav")
async def get_navigation():
    """Parses zensical.toml to return the documentation navigation structure."""
    try:
        return docs_service.load_nav()
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="zensical.toml not found")  # noqa: B904
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))  # noqa: B904


@router.get("/{path:path}", response_class=PlainTextResponse)
async def get_doc_page(path: str):
    """Serves a Markdown documentation file."""
    try:
        return docs_service.read_doc_text(path)
    except docs_service.DocNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documentation page not found")  # noqa: B904
