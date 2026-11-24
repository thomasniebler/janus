"""Keeps memory facts and tracked topics searchable by meaning and keyword (JAN-241).

Three pieces:

- An ORM ``after_flush`` hook keeps the FTS5 keyword index in sync with every
  fact/topic insert, update, and delete, whatever code path writes them, and
  drops the owner's embedding row on delete. It never calls the network.
- ``embed_owner()`` (inline, after a service commit) and ``sweep()`` (the
  ``knowledge-index`` worker and ``janus knowledge reembed``) compute vectors
  with the configured embedding model and store them in ``knowledge_embeddings``.
  A row is stale when missing, embedded with another model, or older than its
  owner's ``updated_at``; an unchanged text hash skips the provider call.
- ``search_ids()`` fuses vector and keyword hits with reciprocal-rank fusion.
  It returns ``None`` when no keyword index exists (PostgreSQL), so callers
  keep their substring search.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, delete, event, inspect, or_, select
from sqlalchemy.orm import Session

from janus import database, models
from janus.services import embedding_service, knowledge_sources, vector_index
from janus.time_utils import utcnow

logger = logging.getLogger(__name__)

FACT = "fact"
TOPIC = "topic"
CHUNK = "chunk"
ENTITY = "entity"
#: Owner types kept in sync by the keyword index (the flush hook).
OWNER_TYPES = (FACT, TOPIC)
#: Owner types the sweep embeds; chunks are phase 2 source text (JAN-242), entities the phase 3 graph (JAN-243).
EMBEDDED_TYPES = (FACT, TOPIC, CHUNK, ENTITY)

#: Texts per ``embed_documents`` call.
SWEEP_BATCH_SIZE = 32
#: Batches per owner type per sweep, so one tick stays bounded.
SWEEP_MAX_BATCHES = 10
#: Seconds between worker sweeps.
SWEEP_INTERVAL_SECONDS = 60
#: Reciprocal-rank-fusion constant.
RRF_K = 60
#: Most matches a query may gain from vector similarity alone (no keyword hit).
#: Cosine orders candidates but is no relevance test, so an unrelated query
#: must not pull in every row it happens to be closest to (JAN-245).
VECTOR_ONLY_LIMIT = 5

_OWNER_MODELS: dict[str, Any] = {
    FACT: models.AgentMemoryFact,
    TOPIC: models.TrackedTopic,
    CHUNK: models.KnowledgeChunk,
    ENTITY: models.KnowledgeEntity,
}
_TEXT_FIELDS = {
    FACT: ("summary", "detail"),
    TOPIC: ("title", "summary", "marker_keywords"),
    CHUNK: ("text",),
    ENTITY: ("name", "description"),
}


def substring_filter(owner_type: str, query: str):
    """``ILIKE`` match of the whole query or any word of 3+ characters, over the owner's text fields.

    The keyword ranking wherever FTS5 is missing: PostgreSQL, and the
    production Python, whose bundled SQLite lacks FTS5.
    """
    owner_model = _OWNER_MODELS[owner_type]
    terms = {query, *[part for part in query.split() if len(part) >= 3]}
    return or_(*(getattr(owner_model, name).ilike(f"%{term}%") for term in terms for name in _TEXT_FIELDS[owner_type]))


def owner_text(owner_type: str, owner: Any) -> str:
    """The text that is embedded and keyword-indexed for one fact or topic."""
    parts = (getattr(owner, name) for name in _TEXT_FIELDS[owner_type])
    return " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner_type_of(obj: object) -> str | None:
    if isinstance(obj, models.AgentMemoryFact):
        return FACT
    if isinstance(obj, models.TrackedTopic):
        return TOPIC
    return None


# --- Keyword sync (flush hook) -------------------------------------------------


@event.listens_for(Session, "after_flush")
def _sync_keyword_index(session: Session, _flush_context) -> None:
    """Mirrors fact/topic text changes into the keyword index inside the same transaction."""
    upserts: dict[str, list[tuple[int, int, str]]] = {owner_type: [] for owner_type in OWNER_TYPES}
    deletes: dict[str, list[int]] = {owner_type: [] for owner_type in OWNER_TYPES}
    for obj in session.new:
        owner_type = _owner_type_of(obj)
        if owner_type is not None and obj.id is not None:
            upserts[owner_type].append((obj.id, obj.user_id, owner_text(owner_type, obj)))
    for obj in session.dirty:
        owner_type = _owner_type_of(obj)
        if owner_type is None:
            continue
        state = inspect(obj)
        if any(state.attrs[name].history.has_changes() for name in _TEXT_FIELDS[owner_type]):
            upserts[owner_type].append((obj.id, obj.user_id, owner_text(owner_type, obj)))
    for obj in session.deleted:
        owner_type = _owner_type_of(obj)
        if owner_type is not None and obj.id is not None:
            deletes[owner_type].append(obj.id)

    if not any(upserts.values()) and not any(deletes.values()):
        return

    connection = session.connection()
    for owner_type, owner_ids in deletes.items():
        if owner_ids:
            connection.execute(
                delete(models.KnowledgeEmbedding.__table__).where(
                    models.KnowledgeEmbedding.owner_type == owner_type,
                    models.KnowledgeEmbedding.owner_id.in_(owner_ids),
                )
            )

    keyword_index = vector_index.get_keyword_index(session)
    if keyword_index is None:
        return
    try:
        for owner_type in OWNER_TYPES:
            keyword_index.delete(session, owner_type, deletes[owner_type])
            keyword_index.upsert(session, owner_type, upserts[owner_type])
    except Exception:
        # A keyword-index failure must not fail the user's write; the next
        # process start rebuilds a missing table, and search degrades to
        # vector-only hits for the affected rows until then.
        logger.exception("Failed to update the knowledge keyword index")


# --- Embedding -----------------------------------------------------------------


@dataclass
class SweepResult:
    """Outcome of one sweep."""

    embedded: int = 0
    refreshed: int = 0
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class _WorkerState:
    last_run_at: datetime | None = None
    last_error: str | None = None
    last_embedded: int = 0


_worker_state = _WorkerState()
_worker_state_lock = threading.Lock()


def _settings(db: Session) -> models.Settings | None:
    return db.query(models.Settings).first()


def _embedding_target(db: Session):
    """Returns ``(settings, choice, index)`` when embedding can run, else a reason string."""
    settings = _settings(db)
    choice = embedding_service.configured_choice(settings)
    if choice is None:
        return "no embedding model is configured"
    index = vector_index.get_vector_index(db)
    if index is None:
        return "vector search is unavailable on this database"
    return settings, choice, index


def _stale_owners(db: Session, owner_type: str, model_key: str, limit: int, owner_ids: list[int] | None = None):
    owner_model = _OWNER_MODELS[owner_type]
    embedding = models.KnowledgeEmbedding
    query = (
        db.query(owner_model, embedding)
        .outerjoin(embedding, and_(embedding.owner_type == owner_type, embedding.owner_id == owner_model.id))
        .filter(
            or_(
                embedding.id.is_(None),
                embedding.model != model_key,
                embedding.source_updated_at.is_(None),
                embedding.source_updated_at != owner_model.updated_at,
            )
        )
    )
    if owner_ids is not None:
        query = query.filter(owner_model.id.in_(owner_ids))
    return query.order_by(owner_model.id).limit(limit).all()


def _embed_batch(db: Session, settings, choice, owner_type: str, rows, result: SweepResult) -> None:
    """Embeds one batch of stale owners and upserts their vectors; commits."""
    pending = []
    for owner, existing in rows:
        body = owner_text(owner_type, owner)
        digest = _content_hash(body)
        if existing is not None and existing.model == choice.key and existing.content_hash == digest:
            existing.source_updated_at = owner.updated_at
            result.refreshed += 1
        else:
            pending.append((owner, existing, body, digest))
    if pending:
        client = embedding_service.build_embeddings(settings, choice)
        vectors = client.embed_documents([embedding_service.prepare_text(body) for _, _, body, _ in pending])
        now = utcnow()
        for (owner, existing, _body, digest), vector in zip(pending, vectors, strict=True):
            row = existing or models.KnowledgeEmbedding(owner_type=owner_type, owner_id=owner.id)
            row.user_id = owner.user_id
            row.model = choice.key
            row.dimensions = len(vector)
            row.content_hash = digest
            row.source_updated_at = owner.updated_at
            row.vector = vector_index.serialize_vector(list(vector))
            row.embedded_at = now
            if existing is None:
                db.add(row)
            result.embedded += 1
    db.commit()


def embed_owner(db: Session, owner_type: str, owner_id: int) -> bool:
    """Best-effort inline embedding of one just-committed fact or topic.

    Returns whether a vector is now current. Failures are logged and leave the
    row for the next sweep; they never propagate to the caller.
    """
    try:
        target = _embedding_target(db)
        if isinstance(target, str):
            return False
        settings, choice, _index = target
        rows = _stale_owners(db, owner_type, choice.key, 1, owner_ids=[owner_id])
        if rows:
            _embed_batch(db, settings, choice, owner_type, rows, SweepResult())
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Inline embedding of %s %s failed: %s", owner_type, owner_id, exc)
        return False


def sweep(
    db: Session,
    *,
    batch_size: int = SWEEP_BATCH_SIZE,
    max_batches: int | None = SWEEP_MAX_BATCHES,
    force: bool = False,
) -> SweepResult:
    """Embeds missing and stale facts/topics in batches; ``force`` re-embeds everything.

    ``max_batches=None`` runs until nothing is stale (the CLI). A provider
    error stops the sweep and is reported in ``SweepResult.errors``.
    """
    result = SweepResult()
    target = _embedding_target(db)
    if isinstance(target, str):
        result.skipped_reason = target
        return result
    settings, choice, _index = target
    if force:
        db.query(models.KnowledgeEmbedding).update(
            {models.KnowledgeEmbedding.content_hash: "", models.KnowledgeEmbedding.source_updated_at: None}
        )
        db.commit()
    for owner_type in EMBEDDED_TYPES:
        batches = 0
        while max_batches is None or batches < max_batches:
            rows = _stale_owners(db, owner_type, choice.key, batch_size)
            if not rows:
                break
            try:
                _embed_batch(db, settings, choice, owner_type, rows, result)
            except Exception as exc:
                db.rollback()
                result.errors.append(f"{type(exc).__name__}: {exc}")
                return result
            batches += 1
    return result


def run_worker_sweep() -> SweepResult:
    """One worker tick: ingest new/changed sources (JAN-242), then embed; record the outcome for status."""
    db = database.SessionLocal()
    try:
        ingestion = knowledge_sources.run_tick(db)
        result = sweep(db)
    finally:
        db.close()
    errors = ingestion.errors + result.errors
    with _worker_state_lock:
        previous_error = _worker_state.last_error
        _worker_state.last_run_at = utcnow()
        _worker_state.last_embedded = result.embedded
        _worker_state.last_error = errors[0] if errors else None
    if errors and errors[0] != previous_error:
        logger.warning("Knowledge index tick failed: %s", errors[0])
    return result


def knowledge_index_loop() -> None:
    """Supervised worker loop: sweep, then sleep."""
    while True:
        try:
            run_worker_sweep()
        except Exception:
            logger.exception("Knowledge index sweep crashed")
        time.sleep(SWEEP_INTERVAL_SECONDS)


# --- Search --------------------------------------------------------------------


def reciprocal_rank_fusion(rankings: list[list[int]]) -> list[int]:
    """Owner ids ordered by summed ``1 / (RRF_K + rank)`` over the rankings (JAN-241)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, owner_id in enumerate(ranking):
            scores[owner_id] = scores.get(owner_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda owner_id: scores[owner_id], reverse=True)


def _cap_vector_only(vector_ids: list[int], keyword_ids: list[int]) -> list[int]:
    """Keeps vector hits that also matched by keyword, plus at most ``VECTOR_ONLY_LIMIT`` others."""
    keyword_set = set(keyword_ids)
    kept: list[int] = []
    vector_only = 0
    for owner_id in vector_ids:
        if owner_id in keyword_set:
            kept.append(owner_id)
        elif vector_only < VECTOR_ONLY_LIMIT:
            kept.append(owner_id)
            vector_only += 1
    return kept


def search_ids(
    db: Session, user_id: int, owner_type: str, query: str | None, candidates: Select | None = None
) -> list[int] | None:
    """Returns owner ids ranked by fused vector + keyword relevance, within ``candidates``.

    ``candidates`` is a ``SELECT`` of the owner ids the caller's metadata
    filters (status, source, ...) allow: both rankings only run inside it,
    so filters are applied before similarity, never after. At most
    ``VECTOR_ONLY_LIMIT`` results come from vector similarity alone.

    The keyword ranking is FTS5 (BM25) where available and otherwise a
    substring (``ILIKE``) match, newest first, so exact words are always
    found. A failing query embedding degrades to keyword-only ranking.
    ``None`` means there is no query; the caller lists unranked.
    """
    if not query:
        return None
    vector_ids: list[int] | None = None
    target = _embedding_target(db)
    if not isinstance(target, str):
        settings, choice, index = target
        try:
            client = embedding_service.build_embeddings(settings, choice)
            query_vector = client.embed_query(embedding_service.prepare_text(query))
            hits = index.search(
                db,
                user_id=user_id,
                owner_type=owner_type,
                model=choice.key,
                query_vector=list(query_vector),
                min_similarity=settings.embedding_min_similarity,
                candidates=candidates,
            )
            vector_ids = [owner_id for owner_id, _similarity in hits]
        except Exception as exc:
            logger.warning("Semantic %s search fell back to keyword search: %s", owner_type, exc)
    # FTS tables only cover facts and topics; chunks and entities always use substring matching.
    keyword_index = vector_index.get_keyword_index(db) if owner_type in OWNER_TYPES else None
    if keyword_index is not None:
        keyword_ids = keyword_index.search(
            db, user_id=user_id, owner_type=owner_type, query=query, candidates=candidates
        )
    else:
        owner_model = _OWNER_MODELS[owner_type]
        substring = (
            select(owner_model.id)
            .where(owner_model.user_id == user_id, substring_filter(owner_type, query))
            .order_by(owner_model.updated_at.desc())
            .limit(vector_index.MAX_CANDIDATES)
        )
        if candidates is not None:
            substring = substring.where(owner_model.id.in_(candidates))
        keyword_ids = list(db.execute(substring).scalars())
    rankings = [keyword_ids]
    if vector_ids is not None:
        rankings.insert(0, _cap_vector_only(vector_ids, keyword_ids))
    return reciprocal_rank_fusion(rankings)


def order_by_rank(items: list, ranked_ids: list[int], key) -> list:
    """Sorts ``items`` (already filtered to ``ranked_ids``) by their position in ``ranked_ids``."""
    rank = {owner_id: position for position, owner_id in enumerate(ranked_ids)}
    return sorted(items, key=lambda item: rank[key(item)])


# --- Status --------------------------------------------------------------------


def status_snapshot(db: Session) -> dict[str, Any]:
    """Capabilities, configuration, coverage, and last worker outcome for system status."""
    capabilities = vector_index.probe_capabilities()
    is_sqlite = db.get_bind().dialect.name == "sqlite"
    choice = embedding_service.configured_choice(_settings(db))
    # Status is visible to every signed-in user, so only report whether any
    # row is still pending, never per-user or instance-wide counts.
    pending = choice is not None and any(_stale_owners(db, owner_type, choice.key, 1) for owner_type in EMBEDDED_TYPES)
    with _worker_state_lock:
        worker = {
            "last_run_at": _worker_state.last_run_at,
            "last_error": _worker_state.last_error,
            "last_embedded": _worker_state.last_embedded,
        }
    return {
        "dialect": db.get_bind().dialect.name,
        "vector_search_available": is_sqlite and capabilities.vector_search,
        "keyword_index_available": is_sqlite and capabilities.fts5,
        "capability_error": capabilities.error,
        "sqlite_vec_version": capabilities.sqlite_vec_version,
        "embedding_model": choice.key if choice else None,
        "pending": pending,
        "worker": worker,
    }
