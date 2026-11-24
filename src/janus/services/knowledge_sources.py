"""Ingests Janus data into knowledge sources and chunks (JAN-242).

Two entry points feed ``knowledge_sources``:

- **Staging.** ``imap_service`` stages email attachments and the upload
  endpoint stages files: supported documents become ``pending`` sources that
  hold their bytes in ``staged_content`` until parsed, and everything else is
  recorded as ``skipped`` with the reason. Staging stops (sources are skipped
  with "staging backlog full") once ``STAGING_LIMIT_BYTES`` of bytes wait.
- **Discovery.** ``run_tick`` asks ``knowledge_discovery`` for mail, chat
  conversations, WhatsApp chats, planner tasks, and calendar events that have
  no source yet or changed since their source was built, so every write path
  is covered without ORM hooks.

Processing turns each source's text into chunks (``knowledge_chunking``) and
keeps every chunk whose ordinal and text are unchanged, so only new text is
re-embedded by the knowledge-index sweep (chunks are ``owner_type="chunk"``).
Sources whose owner row is gone, and mail later filed as SPAM or IGNORE, are
deleted with their chunks and embeddings. SQLite does not enforce the
foreign-key cascades here, so every delete is explicit.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from janus import models
from janus.services import knowledge_chunking, knowledge_discovery, knowledge_graph, knowledge_parsing
from janus.time_utils import utcnow

logger = logging.getLogger(__name__)

SourceType = models.KnowledgeSourceType
Status = models.KnowledgeSourceStatus

#: Most bytes that may wait in ``staged_content`` before new files are skipped.
STAGING_LIMIT_BYTES = 200 * 1024 * 1024
#: Most sources processed per worker tick.
PROCESS_BATCH = 50
#: Parse attempts before a staged document is marked failed and its bytes dropped.
MAX_PARSE_ATTEMPTS = 2

#: ``knowledge_embeddings.owner_type`` of chunk vectors.
CHUNK_OWNER_TYPE = "chunk"


@dataclass
class TickResult:
    """What one ingestion pass did."""

    processed: int = 0
    failed: int = 0
    deleted: int = 0
    errors: list[str] = field(default_factory=list)


# --- Staging -------------------------------------------------------------------


def _staged_bytes(db: Session) -> int:
    return int(
        db.query(func.coalesce(func.sum(models.KnowledgeSource.size_bytes), 0))
        .filter(
            models.KnowledgeSource.status == Status.PENDING.value,
            models.KnowledgeSource.staged_content.is_not(None),
        )
        .scalar()
    )


def stage_document(
    db: Session,
    *,
    user_id: int,
    source_type: SourceType,
    owner_id: int | None,
    part: int,
    filename: str | None,
    mime_type: str | None,
    data: bytes,
) -> models.KnowledgeSource:
    """Records one attachment or upload: ``pending`` with its bytes, or ``skipped`` with the reason.

    Does not commit. Re-staging the same (user, type, owner, part) returns the
    existing source untouched, so an IMAP re-sync never duplicates attachments.
    """
    if owner_id is not None:
        existing = (
            db.query(models.KnowledgeSource)
            .filter_by(user_id=user_id, source_type=source_type.value, owner_id=owner_id, part=part)
            .first()
        )
        if existing is not None:
            return existing
    check = knowledge_parsing.check_document(filename, mime_type, len(data))
    source = models.KnowledgeSource(
        user_id=user_id,
        source_type=source_type.value,
        owner_id=owner_id,
        part=part,
        title=filename or "(unnamed file)",
        mime_type=check.mime_type or mime_type,
        size_bytes=len(data),
    )
    if not check.accepted:
        source.status, source.status_detail = Status.SKIPPED.value, check.reason
    elif _staged_bytes(db) + len(data) > STAGING_LIMIT_BYTES:
        source.status, source.status_detail = Status.SKIPPED.value, "staging backlog full"
    else:
        source.status, source.staged_content = Status.PENDING.value, data
    db.add(source)
    db.flush()
    return source


def create_upload(
    db: Session, *, user_id: int, filename: str | None, mime_type: str | None, data: bytes
) -> models.KnowledgeSource:
    """Stages an uploaded file and parses it right away, so the caller sees the outcome; commits."""
    source = stage_document(
        db,
        user_id=user_id,
        source_type=SourceType.UPLOAD,
        owner_id=None,
        part=0,
        filename=filename,
        mime_type=mime_type,
        data=data,
    )
    if source.status == Status.PENDING.value:
        _process_staged(db, source)
    db.commit()
    db.refresh(source)
    return source


# --- Chunks --------------------------------------------------------------------


def _delete_chunk_embeddings(db: Session, chunk_ids: Iterable[int]) -> None:
    """Deletes the chunks' vectors and what the graph built on them (JAN-243)."""
    ids = list(chunk_ids)
    knowledge_graph.forget_chunks(db, ids)
    if ids:
        db.execute(
            delete(models.KnowledgeEmbedding).where(
                models.KnowledgeEmbedding.owner_type == CHUNK_OWNER_TYPE,
                models.KnowledgeEmbedding.owner_id.in_(ids),
            )
        )


def _apply_chunks(db: Session, source: models.KnowledgeSource, text: str, blocks: Callable[[str], list[str]]) -> None:
    """Replaces the source's chunks, keeping each chunk whose ordinal and text are unchanged."""
    chunks = knowledge_chunking.pack(blocks(text))
    existing = {
        chunk.ordinal: chunk
        for chunk in db.query(models.KnowledgeChunk).filter(models.KnowledgeChunk.source_id == source.id)
    }
    for ordinal, chunk in enumerate(chunks):
        row = existing.pop(ordinal, None)
        if row is not None and row.content_hash == chunk.content_hash:
            continue
        if row is None:
            row = models.KnowledgeChunk(source_id=source.id, user_id=source.user_id, ordinal=ordinal)
            db.add(row)
        else:
            knowledge_graph.forget_chunks(db, [row.id])
        row.text = chunk.text
        row.content_hash = chunk.content_hash
        row.char_count = len(chunk.text)
        row.token_estimate = chunk.token_estimate
    if existing:
        _delete_chunk_embeddings(db, (row.id for row in existing.values()))
        db.execute(delete(models.KnowledgeChunk).where(models.KnowledgeChunk.id.in_([r.id for r in existing.values()])))


def with_chunk_counts(db: Session, sources: list[models.KnowledgeSource]) -> list[tuple[models.KnowledgeSource, int]]:
    """Pairs each source with its number of chunks (one query)."""
    counts = dict(
        db.query(models.KnowledgeChunk.source_id, func.count(models.KnowledgeChunk.id))
        .filter(models.KnowledgeChunk.source_id.in_([source.id for source in sources]))
        .group_by(models.KnowledgeChunk.source_id)
        .all()
    )
    return [(source, counts.get(source.id, 0)) for source in sources]


def list_sources_for_user(
    db: Session,
    user_id: int,
    *,
    source_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[tuple[models.KnowledgeSource, int]]:
    """Lists ``user_id``'s sources, newest first, each paired with its chunk count."""
    query = db.query(models.KnowledgeSource).filter(models.KnowledgeSource.user_id == user_id)
    if source_type is not None:
        query = query.filter(models.KnowledgeSource.source_type == source_type)
    if status is not None:
        query = query.filter(models.KnowledgeSource.status == status)
    query = query.order_by(models.KnowledgeSource.created_at.desc(), models.KnowledgeSource.id.desc())
    return with_chunk_counts(db, query.offset(offset).limit(limit).all())


def get_source_for_user(db: Session, source_id: int, user_id: int) -> models.KnowledgeSource | None:
    """Returns one source owned by ``user_id``; ``None`` if missing or foreign (JAN-288)."""
    return (
        db.query(models.KnowledgeSource)
        .filter(models.KnowledgeSource.id == source_id, models.KnowledgeSource.user_id == user_id)
        .first()
    )


def delete_source_for_user(db: Session, source_id: int, user_id: int) -> bool:
    """Deletes one of ``user_id``'s sources with its chunks and vectors and commits; ``False`` if missing or foreign."""
    source = get_source_for_user(db, source_id, user_id)
    if source is None:
        return False
    delete_source(db, source)
    db.commit()
    return True


def delete_source(db: Session, source: models.KnowledgeSource) -> None:
    """Deletes a source with its chunks and their embeddings; does not commit."""
    chunk_ids = list(db.scalars(select(models.KnowledgeChunk.id).where(models.KnowledgeChunk.source_id == source.id)))
    _delete_chunk_embeddings(db, chunk_ids)
    db.execute(delete(models.KnowledgeChunk).where(models.KnowledgeChunk.source_id == source.id))
    db.delete(source)


# --- Processing ----------------------------------------------------------------


def _process_staged(db: Session, source: models.KnowledgeSource) -> bool:
    """Parses a staged document into chunks; returns whether it succeeded. Does not commit."""
    source.attempts = (source.attempts or 0) + 1
    try:
        check = knowledge_parsing.check_document(source.title, source.mime_type, len(source.staged_content or b""))
        if not check.accepted:
            raise knowledge_parsing.DocumentParseError(check.reason or "unsupported document")
        text = knowledge_parsing.parse_document(source.staged_content, check.extension)
    except knowledge_parsing.DocumentParseError as exc:
        source.status_detail = str(exc)
        if source.attempts >= MAX_PARSE_ATTEMPTS:
            source.status, source.staged_content = Status.FAILED.value, None
        return False
    db.flush()
    _apply_chunks(db, source, text, knowledge_chunking.document_blocks)
    source.content_hash = knowledge_discovery.content_hash(text)
    source.status, source.status_detail, source.staged_content = Status.READY.value, None, None
    source.processed_at = utcnow()
    return True


def _process_owner(db: Session, source_type: SourceType, owner: knowledge_discovery.OwnerText) -> None:
    """Creates or refreshes the source built from one owner row, then its chunks. Does not commit."""
    source = (
        db.query(models.KnowledgeSource)
        .filter_by(user_id=owner.user_id, source_type=source_type.value, owner_id=owner.owner_id, part=0)
        .first()
    )
    if source is None:
        source = models.KnowledgeSource(
            user_id=owner.user_id, source_type=source_type.value, owner_id=owner.owner_id, part=0
        )
        db.add(source)
        db.flush()
    source.title = owner.title[:500]
    source.mime_type = "text/plain"
    source.size_bytes = len(owner.text.encode("utf-8"))
    source.source_updated_at = owner.updated_at
    digest = knowledge_discovery.content_hash(owner.text)
    if source.content_hash != digest or source.status != Status.READY.value:
        _apply_chunks(db, source, owner.text, owner.blocks)
        source.content_hash = digest
    source.status, source.status_detail = Status.READY.value, None
    source.processed_at = utcnow()


# --- Worker entry points -------------------------------------------------------


def run_tick(db: Session, *, limit: int = PROCESS_BATCH) -> TickResult:
    """One bounded ingestion pass: staged documents, then changed owners, then cleanup. Commits."""
    result = TickResult()
    for source in knowledge_discovery.staged_sources(db, limit):
        if _process_staged(db, source):
            result.processed += 1
        else:
            result.failed += 1
        db.commit()

    for source_type, discover in knowledge_discovery.DISCOVERERS:
        remaining = limit - result.processed - result.failed
        if remaining <= 0:
            break
        try:
            for owner in discover(db, remaining):
                _process_owner(db, source_type, owner)
                result.processed += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            result.errors.append(f"{source_type.value}: {type(exc).__name__}: {exc}")
            logger.exception("Knowledge ingestion of %s failed", source_type.value)

    for orphan in knowledge_discovery.orphans(db):
        delete_source(db, orphan)
        result.deleted += 1
    db.commit()
    return result


def backfill(db: Session) -> TickResult:
    """Runs ``run_tick`` until nothing is left to process (``janus knowledge backfill``)."""
    total = TickResult()
    while True:
        tick = run_tick(db)
        total.processed += tick.processed
        total.failed += tick.failed
        total.deleted += tick.deleted
        total.errors += tick.errors
        if tick.errors or (tick.processed == 0 and tick.failed == 0):
            return total
