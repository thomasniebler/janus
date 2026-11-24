"""Hybrid retrieval over knowledge chunks with graph expansion, and entity detail (JAN-243).

``search`` filters first and ranks second (see the spec's "Filter first, then
rank"): metadata filters (source types, source dates, entities) bound the
candidate chunks before any similarity is computed. Two rankings are fused
with reciprocal-rank fusion:

- **direct**: ``knowledge_index.search_ids`` over the candidate chunks
  (vector similarity capped at ``VECTOR_ONLY_LIMIT`` meaning-only hits, plus
  substring matches);
- **graph**: candidate chunks mentioning the entities the query matches and
  their neighbours one or two relation hops away (an ``entity_ids`` filter
  only narrows the candidates; it seeds nothing).

Nothing is sent to a chat model here; only the query is embedded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from janus import models
from janus.services import knowledge_index
from janus.services.knowledge_graph import normalize_name

#: Default and most results per search.
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
#: Seed entities taken from the query ranking.
MAX_QUERY_SEEDS = 5
#: Neighbours added per expansion hop, and the most hops.
MAX_NEIGHBOURS_PER_HOP = 20
MAX_HOPS = 2
#: Chunks considered by the graph ranking.
GRAPH_CANDIDATES = 50
#: Characters of chunk text returned per hit.
SNIPPET_CHARS = 1_200
#: Relations and sources returned by ``entity_detail``.
DETAIL_RELATIONS = 50
DETAIL_SOURCES = 10


@dataclass
class Hit:
    """One retrieved chunk with its source and why it matched."""

    chunk_id: int
    text: str
    source: dict
    entities: list[dict] = field(default_factory=list)
    matched_by: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    """Ranked hits plus the entities the search started from."""

    hits: list[Hit]
    entities: list[dict]


def to_naive_utc(value: datetime | None) -> datetime | None:
    """The database's naive-UTC form of a timestamp; offset-aware values are converted, not just stripped."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _source_date():
    source = models.KnowledgeSource
    return func.coalesce(source.source_updated_at, source.created_at)


def _entity_ref(entity: models.KnowledgeEntity) -> dict:
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type}


def source_ref(source: models.KnowledgeSource) -> dict:
    """The id, type, title, owner, and date of a source, as returned with hits and citations."""
    when = source.source_updated_at or source.created_at
    return {
        "id": source.id,
        "type": source.source_type,
        "title": source.title,
        "owner_id": source.owner_id,
        "date": when.isoformat() if when else None,
    }


def candidate_chunks(
    user_id: int,
    *,
    source_types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    entity_ids: list[int] | None = None,
) -> Select:
    """``SELECT`` of the chunk ids the filters allow, for ranking to run inside."""
    chunk, source = models.KnowledgeChunk, models.KnowledgeSource
    when = _source_date()
    since, until = to_naive_utc(since), to_naive_utc(until)
    statement = (
        select(chunk.id)
        .join(source, source.id == chunk.source_id)
        .where(chunk.user_id == user_id, source.status == models.KnowledgeSourceStatus.READY.value)
    )
    if source_types:
        statement = statement.where(source.source_type.in_(source_types))
    if since is not None:
        statement = statement.where(when >= since)
    if until is not None:
        statement = statement.where(when <= until)
    if entity_ids:
        mention = models.KnowledgeMention
        statement = statement.where(chunk.id.in_(select(mention.chunk_id).where(mention.entity_id.in_(entity_ids))))
    return statement


def resolve_entities(db: Session, user_id: int, reference: str | int | None) -> list[int]:
    """Entity ids for an id or a name (exact normalized name or alias first, then contained in the name)."""
    if reference is None or reference == "":
        return []
    entity = models.KnowledgeEntity
    if isinstance(reference, int) or str(reference).isdigit():
        found = db.scalar(select(entity.id).where(entity.user_id == user_id, entity.id == int(reference)))
        return [found] if found else []
    key = normalize_name(str(reference))
    if not key:
        return []
    exact = list(db.scalars(select(entity.id).where(entity.user_id == user_id, entity.normalized_name == key)))
    if exact:
        return exact
    aliased = [
        entity_id
        for entity_id, aliases in db.execute(select(entity.id, entity.aliases).where(entity.user_id == user_id))
        if key in {normalize_name(alias) for alias in aliases or []}
    ]
    if aliased:
        return aliased
    return list(
        db.scalars(select(entity.id).where(entity.user_id == user_id, entity.normalized_name.contains(key)).limit(10))
    )


def _neighbours(db: Session, entity_ids: set[int], hops: int) -> dict[int, int]:
    """Entities reachable within ``hops`` relation steps, mapped to the entity they were reached from."""
    relation = models.KnowledgeRelation
    reached: dict[int, int] = {}
    frontier = set(entity_ids)
    for _ in range(max(0, min(hops, MAX_HOPS))):
        if not frontier:
            break
        rows = db.execute(
            select(relation.subject_id, relation.object_id).where(
                or_(relation.subject_id.in_(frontier), relation.object_id.in_(frontier))
            )
        ).all()
        found: dict[int, int] = {}
        for subject_id, object_id in rows:
            for near, far in ((subject_id, object_id), (object_id, subject_id)):
                if near in frontier and far not in entity_ids and far not in reached and far not in found:
                    found[far] = reached.get(near, near)
            if len(found) >= MAX_NEIGHBOURS_PER_HOP:
                break
        reached.update(found)
        frontier = set(found)
    return reached


def _graph_ranking(db: Session, candidates: Select, entity_ids: set[int]) -> list[int]:
    """Candidate chunks mentioning any of the entities: more of them first, then newer sources."""
    if not entity_ids:
        return []
    mention, chunk, source = models.KnowledgeMention, models.KnowledgeChunk, models.KnowledgeSource
    matches = func.count(mention.entity_id.distinct())
    rows = db.execute(
        select(mention.chunk_id)
        .join(chunk, chunk.id == mention.chunk_id)
        .join(source, source.id == chunk.source_id)
        .where(mention.entity_id.in_(entity_ids), mention.chunk_id.in_(candidates))
        .group_by(mention.chunk_id, source.id)
        .order_by(matches.desc(), _source_date().desc(), source.id.desc())
        .limit(GRAPH_CANDIDATES)
    )
    return [chunk_id for (chunk_id,) in rows]


def search(
    db: Session,
    user_id: int,
    query: str,
    *,
    source_types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    entity_ids: list[int] | None = None,
    limit: int = DEFAULT_LIMIT,
    hops: int = 1,
) -> SearchResult:
    """Filtered hybrid search over the user's chunks (direct + graph), with source references."""
    limit = max(1, min(limit, MAX_LIMIT))
    candidates = candidate_chunks(user_id, source_types=source_types, since=since, until=until, entity_ids=entity_ids)
    direct = knowledge_index.search_ids(db, user_id, knowledge_index.CHUNK, query, candidates) or []
    entity = models.KnowledgeEntity
    user_entities = select(entity.id).where(entity.user_id == user_id)
    query_seeds = (knowledge_index.search_ids(db, user_id, knowledge_index.ENTITY, query, user_entities) or [])[
        :MAX_QUERY_SEEDS
    ]
    # ``entity_ids`` only narrows the candidates; seeding the graph with it would return every passage
    # mentioning the entity whatever the query says.
    seeds = set(query_seeds)
    reached = _neighbours(db, seeds, hops)
    graph = _graph_ranking(db, candidates, seeds | set(reached))
    fused = knowledge_index.reciprocal_rank_fusion([ranking for ranking in (direct, graph) if ranking])[:limit]

    chunks = {
        row.id: row for row in db.scalars(select(models.KnowledgeChunk).where(models.KnowledgeChunk.id.in_(fused)))
    }
    sources = {
        row.id: row
        for row in db.scalars(
            select(models.KnowledgeSource).where(
                models.KnowledgeSource.id.in_({chunk.source_id for chunk in chunks.values()})
            )
        )
    }
    mentioned: dict[int, list[models.KnowledgeEntity]] = {}
    for chunk_id, row in db.execute(
        select(models.KnowledgeMention.chunk_id, entity)
        .join(entity, entity.id == models.KnowledgeMention.entity_id)
        .where(models.KnowledgeMention.chunk_id.in_(fused))
    ):
        mentioned.setdefault(chunk_id, []).append(row)
    linked = seeds | set(reached)
    direct_set, graph_set = set(direct), set(graph)
    hits = []
    for chunk_id in fused:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            continue
        matched_by = ["query"] if chunk_id in direct_set else []
        if chunk_id in graph_set:
            via = [row.name for row in mentioned.get(chunk_id, []) if row.id in linked]
            matched_by.append("graph via " + ", ".join(via[:3]) if via else "graph")
        hits.append(
            Hit(
                chunk_id=chunk.id,
                text=chunk.text[:SNIPPET_CHARS],
                source=source_ref(sources[chunk.source_id]),
                entities=[_entity_ref(row) for row in mentioned.get(chunk_id, [])],
                matched_by=matched_by,
            )
        )
    seed_rows = db.scalars(select(entity).where(entity.id.in_(seeds))).all() if seeds else []
    return SearchResult(hits=hits, entities=[_entity_ref(row) for row in seed_rows])


def entity_detail(db: Session, user_id: int, entity_id: int) -> dict | None:
    """One entity with its relations (both directions) and the sources mentioning it most."""
    entity = db.get(models.KnowledgeEntity, entity_id)
    if entity is None or entity.user_id != user_id:
        return None
    relation, evidence = models.KnowledgeRelation, models.KnowledgeRelationEvidence
    support = (
        select(func.count(evidence.id)).where(evidence.relation_id == relation.id).correlate(relation).scalar_subquery()
    )
    rows = db.execute(
        select(relation, support)
        .where(or_(relation.subject_id == entity.id, relation.object_id == entity.id))
        .order_by(support.desc(), relation.id)
        .limit(DETAIL_RELATIONS)
    ).all()
    other_ids = {row.object_id if row.subject_id == entity.id else row.subject_id for row, _ in rows}
    others = {
        row.id: row
        for row in db.scalars(select(models.KnowledgeEntity).where(models.KnowledgeEntity.id.in_(other_ids)))
    }
    relations = []
    for row, supporting in rows:
        outgoing = row.subject_id == entity.id
        other = others.get(row.object_id if outgoing else row.subject_id)
        if other is None:
            continue
        relations.append(
            {
                "id": row.id,
                "direction": "outgoing" if outgoing else "incoming",
                "predicate": row.predicate,
                "other": _entity_ref(other),
                "confidence": row.confidence,
                "supporting_chunks": int(supporting or 0),
            }
        )
    mention, chunk, source = models.KnowledgeMention, models.KnowledgeChunk, models.KnowledgeSource
    mentions = func.count(mention.id).label("mentions")
    source_rows = db.execute(
        select(source, mentions)
        .join(chunk, chunk.source_id == source.id)
        .join(mention, mention.chunk_id == chunk.id)
        .where(mention.entity_id == entity.id)
        .group_by(source.id)
        .order_by(mentions.desc(), _source_date().desc(), source.id.desc())
        .limit(DETAIL_SOURCES)
    ).all()
    return {
        **_entity_ref(entity),
        "aliases": list(entity.aliases or []),
        "description": entity.description,
        "relations": relations,
        "sources": [{**source_ref(row), "mentions": int(count)} for row, count in source_rows],
    }
