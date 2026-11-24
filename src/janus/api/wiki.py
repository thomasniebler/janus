"""Wiki pages about topics and entities: list, read, edit, review proposals, and synthesis settings (JAN-244)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import wiki as wiki_schemas
from janus.services import knowledge_provenance, wiki_service, wiki_synthesis

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


@router.get("/pages", response_model=list[wiki_schemas.WikiPageSummary])
def list_pages(
    subject_type: str | None = Query(default=None, pattern="^(topic|entity)$"),
    subject_id: int | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """The caller's pages, most recently updated first, with their pending proposal counts."""
    rows = wiki_service.list_pages(db, user.id, subject_type=subject_type, subject_id=subject_id, query=q)
    return [wiki_schemas.WikiPageSummary.from_row(page, pending) for page, pending in rows]


@router.get("/pages/{page_id}", response_model=wiki_schemas.WikiPageDetail)
def get_page(
    page_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """One page with its citations, pending proposals, and published history."""
    try:
        page = wiki_service.get_page(db, user.id, page_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return wiki_schemas.WikiPageDetail.from_page(page, wiki_service.revisions(db, page))


@router.get("/pages/{page_id}/provenance", response_model=wiki_schemas.WikiPageProvenance)
def get_page_provenance(
    page_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """What one page is built from: each ``[n]`` resolved to its passage or memory fact, plus entities and facts."""
    try:
        return knowledge_provenance.page_provenance_for_user(db, user.id, page_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/pages", response_model=wiki_schemas.WikiPageDetail, status_code=status.HTTP_201_CREATED)
def create_page(
    payload: wiki_schemas.WikiPageCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Creates the page about a topic or entity (or returns the existing one); synthesis writes it."""
    try:
        page = wiki_service.create_page(db, user.id, payload.subject_type, payload.subject_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    db.commit()
    return wiki_schemas.WikiPageDetail.from_page(page, wiki_service.revisions(db, page))


@router.put("/pages/{page_id}", response_model=wiki_schemas.WikiPageDetail)
def edit_page(
    page_id: int,
    payload: wiki_schemas.WikiPageEdit,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Publishes the caller's edit; from now on Janus proposes rewrites instead of overwriting."""
    try:
        page = wiki_service.edit_page(db, user.id, page_id, payload.body, title=payload.title)
    except wiki_service.WikiError as error:
        code = status.HTTP_404_NOT_FOUND if "not found" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(status_code=code, detail=str(error)) from error
    return wiki_schemas.WikiPageDetail.from_page(page, wiki_service.revisions(db, page))


@router.post("/revisions/{revision_id}/accept", response_model=wiki_schemas.WikiPageDetail)
def accept_revision(
    revision_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Publishes a pending proposal."""
    try:
        page = wiki_service.accept(db, user.id, revision_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return wiki_schemas.WikiPageDetail.from_page(page, wiki_service.revisions(db, page))


@router.post("/revisions/{revision_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_revision(
    revision_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Dismisses a pending proposal; the page stays as it is."""
    try:
        wiki_service.dismiss(db, user.id, revision_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/revisions/{revision_id}/restore", response_model=wiki_schemas.WikiPageDetail)
def restore_revision(
    revision_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Publishes a copy of an earlier revision as the caller's edit."""
    try:
        page = wiki_service.restore(db, user.id, revision_id)
    except wiki_service.WikiError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return wiki_schemas.WikiPageDetail.from_page(page, wiki_service.revisions(db, page))


@router.get("/synthesis", response_model=wiki_schemas.WikiSynthesisStatus)
def get_synthesis(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Whether wiki synthesis runs, with which model, and the shared monthly budget's usage."""
    return wiki_synthesis.status(db, user.id, wiki_synthesis.builtin_definition(db, user.id))


@router.put("/synthesis", response_model=wiki_schemas.WikiSynthesisStatus)
def update_synthesis(
    payload: wiki_schemas.WikiSynthesisUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Turns wiki synthesis on or off."""
    return wiki_synthesis.update_settings(db, user.id, enabled=payload.enabled)
