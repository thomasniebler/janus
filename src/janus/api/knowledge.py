"""Knowledge sources (uploads, ingested sources; JAN-242) and the graph (extraction, entities, merges; JAN-243)."""

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from janus import database, models, security
from janus.schemas import knowledge as knowledge_schemas
from janus.services import (
    knowledge_extraction,
    knowledge_graph,
    knowledge_parsing,
    knowledge_provenance,
    knowledge_retrieval,
    knowledge_sources,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_READ_CHUNK_BYTES = 1024 * 1024


@router.post("/uploads", response_model=knowledge_schemas.KnowledgeSource, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Uploads one document (PDF, Word, Excel, PowerPoint, text, CSV, HTML, mail; up to 10 MB) and parses it.

    A plain ``def`` route, so FastAPI runs the parsing in its thread pool instead of on the event loop.
    """
    data = bytearray()
    while chunk := file.file.read(_READ_CHUNK_BYTES):
        data.extend(chunk)
        if len(data) > knowledge_parsing.MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File is larger than 10 MB.")
    check = knowledge_parsing.check_document(file.filename, file.content_type, len(data))
    if not check.accepted:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Cannot ingest: {check.reason}.")
    source = knowledge_sources.create_upload(
        db, user_id=user.id, filename=file.filename, mime_type=file.content_type, data=bytes(data)
    )
    ((source, chunk_count),) = knowledge_sources.with_chunk_counts(db, [source])
    return knowledge_schemas.KnowledgeSource.from_source(source, chunk_count)


@router.get("/sources", response_model=list[knowledge_schemas.KnowledgeSource])
def list_sources(
    source_type: models.KnowledgeSourceType | None = Query(default=None),
    source_status: models.KnowledgeSourceStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Lists the caller's knowledge sources, newest first."""
    rows = knowledge_sources.list_sources_for_user(
        db,
        user.id,
        source_type=source_type.value if source_type is not None else None,
        status=source_status.value if source_status is not None else None,
        limit=limit,
        offset=offset,
    )
    return [knowledge_schemas.KnowledgeSource.from_source(source, count) for source, count in rows]


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Deletes one of the caller's sources with its chunks and embeddings.

    Sources built from mail, chats, or planner items come back on the next
    ingestion tick while their owner row exists; delete the owner instead.
    """
    if not knowledge_sources.delete_source_for_user(db, source_id, user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")


# --- Graph (JAN-243) -------------------------------------------------------------


@router.get("/extraction", response_model=knowledge_schemas.KnowledgeExtractionStatus)
def get_extraction(db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)):
    """Whether knowledge extraction runs, with which model, and this month's token usage against the budget."""
    return knowledge_extraction.status(db, user.id, knowledge_extraction.builtin_definition(db, user.id))


@router.put("/extraction", response_model=knowledge_schemas.KnowledgeExtractionStatus)
def update_extraction(
    payload: knowledge_schemas.KnowledgeExtractionUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Turns knowledge extraction on or off and sets the monthly token budget."""
    return knowledge_extraction.update_settings(
        db, user.id, enabled=payload.enabled, monthly_token_budget=payload.monthly_token_budget
    )


@router.get("/entities", response_model=list[knowledge_schemas.KnowledgeEntity])
def list_entities(
    entity_type: models.KnowledgeEntityType | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Lists the caller's entities, most mentioned first."""
    rows = knowledge_graph.list_entities(
        db, user.id, entity_type=entity_type.value if entity_type else None, query=q, limit=limit, offset=offset
    )
    return [
        knowledge_schemas.KnowledgeEntity.from_row(entity, mentions, relations) for entity, mentions, relations in rows
    ]


@router.get("/merge-suggestions", response_model=list[knowledge_schemas.KnowledgeMergeSuggestion])
def list_merge_suggestions(
    db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Open entity merge suggestions, oldest first."""
    return [
        knowledge_schemas.KnowledgeMergeSuggestion(
            id=suggestion.id,
            reason=suggestion.reason,
            created_at=suggestion.created_at,
            entity=knowledge_schemas.KnowledgeEntity.from_row(entity),
            duplicate=knowledge_schemas.KnowledgeEntity.from_row(duplicate),
        )
        for suggestion, entity, duplicate in knowledge_graph.pending_suggestions(db, user.id)
    ]


@router.post("/merge-suggestions/{suggestion_id}/accept", response_model=knowledge_schemas.KnowledgeEntity)
def accept_merge_suggestion(
    suggestion_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Merges the duplicate into the suggested entity and returns the merged entity."""
    try:
        entity = knowledge_graph.accept_suggestion(db, user.id, suggestion_id)
    except knowledge_graph.MergeError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return knowledge_schemas.KnowledgeEntity.from_row(entity)


@router.post("/merge-suggestions/{suggestion_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_merge_suggestion(
    suggestion_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """Keeps both entities; the pair is not suggested again."""
    try:
        knowledge_graph.reject_suggestion(db, user.id, suggestion_id)
    except knowledge_graph.MergeError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/search", response_model=knowledge_schemas.KnowledgeSearchResult)
def search_knowledge(
    q: str = Query(min_length=1, max_length=500),
    source_type: list[models.KnowledgeSourceType] | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    entity_id: list[int] | None = Query(default=None),
    limit: int = Query(default=knowledge_retrieval.DEFAULT_LIMIT, ge=1, le=knowledge_retrieval.MAX_LIMIT),
    hops: int = Query(default=1, ge=0, le=knowledge_retrieval.MAX_HOPS),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Hybrid search over the caller's ingested sources: filters first, then meaning, words, and the graph."""
    result = knowledge_retrieval.search(
        db,
        user.id,
        q,
        source_types=[kind.value for kind in source_type] if source_type else None,
        since=since,
        until=until,
        entity_ids=entity_id,
        limit=limit,
        hops=hops,
    )
    return knowledge_schemas.KnowledgeSearchResult.model_validate(
        {"hits": [asdict(hit) for hit in result.hits], "entities": result.entities}
    )


@router.get("/graph", response_model=knowledge_schemas.KnowledgeGraph)
def get_graph(
    topics: int = Query(default=knowledge_provenance.GRAPH_TOPICS, ge=0, le=knowledge_provenance.MAX_GRAPH_NODES),
    entities: int = Query(default=knowledge_provenance.GRAPH_ENTITIES, ge=0, le=knowledge_provenance.MAX_GRAPH_NODES),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """The caller's active or pinned topics, their most connected entities, and the persisted links between them."""
    return knowledge_provenance.graph_for_user(db, user.id, topics=topics, entities=entities)


@router.get("/entities/{entity_id}", response_model=knowledge_schemas.KnowledgeEntityDetail)
def get_entity(
    entity_id: int, db: Session = Depends(database.get_db), user: models.User = Depends(security.get_current_user)
):
    """One of the caller's entities with its relations and the sources mentioning it most."""
    detail = knowledge_retrieval.entity_detail(db, user.id, entity_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    return detail
