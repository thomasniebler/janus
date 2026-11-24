"""Read-only views across the knowledge stack: a wiki page's provenance and the topic/entity graph (JAN-334).

Both are built only from links the knowledge layer already persists, never
inferred at read time:

- a page's ``citations`` (``[n]`` -> ``chunk_id`` or ``fact_id``, written by
  ``wiki_synthesis.cite``),
- ``KnowledgeMention`` (chunk -> entity) and ``KnowledgeRelation`` (entity ->
  entity, JAN-243),
- ``TopicEvidence`` (topic -> email / calendar event / memory fact), joined to
  the ``KnowledgeSource`` built from that email or event.

The one exception is the uncited part of a page's ``facts`` list: the facts
the next synthesis would use (``wiki_synthesis.page_facts``, which also matches
fact text against the subject's name). They carry no ``citations`` and the UI
labels them "not cited yet"; they are never presented as a source of the text.

Every function takes the requesting ``user_id`` and only reads that user's
rows; another user's page raises ``wiki_service.WikiError`` (a 404 upstream).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from janus import models
from janus.services import knowledge_retrieval, wiki_service, wiki_synthesis

#: Characters of a cited passage returned for drill-down.
EXCERPT_CHARS = 4_000
#: Default and largest number of topics and entities in the graph.
GRAPH_TOPICS = 30
GRAPH_ENTITIES = 60
MAX_GRAPH_NODES = 150

_EVIDENCE_SOURCE_TYPES = {
    models.TopicEvidenceSource.EMAIL: models.KnowledgeSourceType.EMAIL.value,
    models.TopicEvidenceSource.CALENDAR_EVENT: models.KnowledgeSourceType.CALENDAR_EVENT.value,
}


def _fact_ref(fact: models.AgentMemoryFact) -> dict:
    return {
        "id": fact.id,
        "summary": fact.summary,
        "detail": fact.detail,
        "status": fact.status.value if fact.status is not None else None,
        "source": fact.source.value if fact.source is not None else None,
        "created_at": fact.created_at,
        "updated_at": fact.updated_at,
    }


def _wiki_page_ids(db: Session, user_id: int, subject_type: str, subject_ids: set[int]) -> dict[int, int]:
    if not subject_ids:
        return {}
    page = models.WikiPage
    rows = db.execute(
        select(page.subject_id, page.id).where(
            page.user_id == user_id, page.subject_type == subject_type, page.subject_id.in_(subject_ids)
        )
    ).all()
    return {subject_id: page_id for subject_id, page_id in rows}


def _subject(db: Session, page: models.WikiPage) -> dict:
    title, available = page.title, False
    if page.subject_id is not None:
        model = models.TrackedTopic if page.subject_type == wiki_service.TOPIC else models.KnowledgeEntity
        row = db.get(model, page.subject_id)
        if row is not None and row.user_id == page.user_id:
            title, available = (row.title if page.subject_type == wiki_service.TOPIC else row.name), True
    return {"type": page.subject_type, "id": page.subject_id, "title": title, "available": available}


def page_provenance_for_user(db: Session, user_id: int, page_id: int) -> dict:
    """What one of the user's pages is built from: each citation resolved, the entities and facts behind it.

    ``citations`` follow the page's ``[n]`` markers. A passage citation carries
    the live chunk text (``excerpt``) and its source; a fact citation the live
    memory fact. ``available`` is false when the cited row is gone since the
    page was written (the snapshot ``title``/``date`` still describe it).
    ``entities`` are the entities mentioned in the cited passages (plus the
    page's own entity); ``facts`` are the cited facts followed by the facts the
    next synthesis would use, each with the markers citing it.
    """
    page = wiki_service.get_page(db, user_id, page_id)
    citations = [citation for citation in page.citations or [] if isinstance(citation, dict)]
    chunk_ids = {citation["chunk_id"] for citation in citations if citation.get("chunk_id") is not None}
    fact_ids = {citation["fact_id"] for citation in citations if citation.get("fact_id") is not None}

    chunk = models.KnowledgeChunk
    chunks = (
        {row.id: row for row in db.scalars(select(chunk).where(chunk.user_id == user_id, chunk.id.in_(chunk_ids)))}
        if chunk_ids
        else {}
    )
    source = models.KnowledgeSource
    source_ids = {row.source_id for row in chunks.values()}
    sources = (
        {row.id: row for row in db.scalars(select(source).where(source.user_id == user_id, source.id.in_(source_ids)))}
        if source_ids
        else {}
    )
    fact = models.AgentMemoryFact
    facts = (
        {row.id: row for row in db.scalars(select(fact).where(fact.user_id == user_id, fact.id.in_(fact_ids)))}
        if fact_ids
        else {}
    )
    mention = models.KnowledgeMention
    mentioned: dict[int, list[int]] = defaultdict(list)
    if chunks:
        for chunk_id, entity_id in db.execute(
            select(mention.chunk_id, mention.entity_id)
            .where(mention.user_id == user_id, mention.chunk_id.in_(set(chunks)))
            .order_by(mention.id)
        ).all():
            mentioned[chunk_id].append(entity_id)

    resolved, entity_citations, fact_citations = [], defaultdict(list), defaultdict(list)
    for citation in sorted(citations, key=lambda item: item.get("n") or 0):
        number = citation.get("n")
        base = {
            "n": number,
            "title": citation.get("title"),
            "date": citation.get("date"),
            "source_type": citation.get("source_type"),
            "chunk_id": citation.get("chunk_id"),
            "fact_id": citation.get("fact_id"),
            "source": None,
            "excerpt": None,
            "fact": None,
            "entity_ids": [],
        }
        if citation.get("fact_id") is not None:
            row = facts.get(citation["fact_id"])
            base |= {"kind": "fact", "available": row is not None, "fact": _fact_ref(row) if row else None}
            if row is not None:
                fact_citations[row.id].append(number)
        else:
            row = chunks.get(citation.get("chunk_id"))
            origin = sources.get(row.source_id) if row is not None else None
            base |= {
                "kind": "passage",
                "available": row is not None,
                "excerpt": row.text[:EXCERPT_CHARS] if row is not None else None,
                "source": knowledge_retrieval.source_ref(origin) if origin is not None else None,
                "entity_ids": list(dict.fromkeys(mentioned.get(row.id, []))) if row is not None else [],
            }
            for entity_id in base["entity_ids"]:
                entity_citations[entity_id].append(number)
        resolved.append(base)

    subject = _subject(db, page)
    entity_ids = set(entity_citations)
    if subject["type"] == wiki_service.ENTITY and subject["available"]:
        entity_ids.add(subject["id"])
    entity = models.KnowledgeEntity
    entity_rows = (
        db.scalars(select(entity).where(entity.user_id == user_id, entity.id.in_(entity_ids))).all()
        if entity_ids
        else []
    )
    pages = _wiki_page_ids(db, user_id, wiki_service.ENTITY, {row.id for row in entity_rows})
    entities = sorted(
        (
            {
                "id": row.id,
                "name": row.name,
                "type": row.entity_type,
                "citations": sorted(set(entity_citations.get(row.id, []))),
                "is_subject": subject["type"] == wiki_service.ENTITY and row.id == subject["id"],
                "wiki_page_id": pages.get(row.id) if pages.get(row.id) != page.id else None,
            }
            for row in entity_rows
        ),
        key=lambda item: (not item["is_subject"], -len(item["citations"]), item["name"].lower()),
    )

    linked = [*facts.values(), *(row for row in wiki_synthesis.page_facts(db, page) if row.id not in facts)]
    fact_list = [
        _fact_ref(row) | {"citations": sorted(set(fact_citations.get(row.id, [])))}
        for row in sorted(linked, key=lambda row: (row.id not in fact_citations, min(fact_citations.get(row.id, [0]))))
    ]
    return {
        "page_id": page.id,
        "subject": subject,
        "citations": resolved,
        "entities": entities,
        "facts": fact_list,
    }


# --- Graph ------------------------------------------------------------------------


def _topic_entity_weights(db: Session, user_id: int, topic_ids: list[int]) -> dict[tuple[int, int], int]:
    """``(topic_id, entity_id) -> number of passages linking them``: evidence sources and the topic page's citations.

    Evidence passages are counted in SQL (one row per pair, never one per
    passage). A passage the topic page cites counts once more only when it is
    not already a passage of one of that topic's evidence sources. A passage
    cited by several topic pages links each of them.
    """
    weights: dict[tuple[int, int], int] = defaultdict(int)
    if not topic_ids:
        return weights
    evidence, source = models.TopicEvidence, models.KnowledgeSource
    chunk, mention = models.KnowledgeChunk, models.KnowledgeMention
    for evidence_type, source_type in _EVIDENCE_SOURCE_TYPES.items():
        rows = db.execute(
            select(evidence.topic_id, mention.entity_id, func.count(func.distinct(chunk.id)))
            .join(
                source,
                (source.owner_id == evidence.source_id)
                & (source.source_type == source_type)
                & (source.user_id == user_id),
            )
            .join(chunk, chunk.source_id == source.id)
            .join(mention, mention.chunk_id == chunk.id)
            .where(evidence.topic_id.in_(topic_ids), evidence.source_type == evidence_type)
            .group_by(evidence.topic_id, mention.entity_id)
        ).all()
        for topic_id, entity_id, count in rows:
            weights[(topic_id, entity_id)] += int(count)

    page = models.WikiPage
    cited: dict[int, set[int]] = defaultdict(set)
    for topic_id, citations in db.execute(
        select(page.subject_id, page.citations).where(
            page.user_id == user_id, page.subject_type == wiki_service.TOPIC, page.subject_id.in_(topic_ids)
        )
    ).all():
        for citation in citations or []:
            if isinstance(citation, dict) and citation.get("chunk_id") is not None:
                cited[citation["chunk_id"]].add(topic_id)
    if not cited:
        return weights
    # Cited passages that already count as evidence of the citing topic.
    already: set[tuple[int, int]] = set()
    for evidence_type, source_type in _EVIDENCE_SOURCE_TYPES.items():
        already.update(
            db.execute(
                select(evidence.topic_id, chunk.id)
                .join(
                    source,
                    (source.owner_id == evidence.source_id)
                    & (source.source_type == source_type)
                    & (source.user_id == user_id),
                )
                .join(chunk, chunk.source_id == source.id)
                .where(
                    evidence.topic_id.in_(topic_ids),
                    evidence.source_type == evidence_type,
                    chunk.id.in_(set(cited)),
                )
            )
            .tuples()
            .all()
        )
    for chunk_id, entity_id in db.execute(
        select(mention.chunk_id, mention.entity_id).where(mention.user_id == user_id, mention.chunk_id.in_(set(cited)))
    ).all():
        for topic_id in cited[chunk_id]:
            if (topic_id, chunk_id) not in already:
                weights[(topic_id, entity_id)] += 1
    return weights


def _mention_counts(db: Session, user_id: int, entity_ids: set[int]) -> dict[int, int]:
    if not entity_ids:
        return {}
    mention = models.KnowledgeMention
    return {
        entity_id: int(count)
        for entity_id, count in db.execute(
            select(mention.entity_id, func.count(mention.id))
            .where(mention.user_id == user_id, mention.entity_id.in_(entity_ids))
            .group_by(mention.entity_id)
        ).all()
    }


def graph_for_user(db: Session, user_id: int, *, topics: int = GRAPH_TOPICS, entities: int = GRAPH_ENTITIES) -> dict:
    """The user's active or pinned topics, their most connected entities, and the persisted links between them.

    Edges are ``evidence`` (a topic's evidence sources or cited passages mention
    the entity; ``weight`` is the number of such passages) and ``relation`` (an
    extracted ``KnowledgeRelation``; ``label`` is its predicate). Entities
    linked to a shown topic come first, then the most mentioned ones, up to
    ``entities``. Nodes name their wiki page when they have one. Every query
    is aggregated or limited, so the cost does not grow with the number of
    passages behind the topics.
    """
    topic = models.TrackedTopic
    topic_rows = db.scalars(
        select(topic)
        .where(topic.user_id == user_id, or_(topic.status == models.TopicStatus.ACTIVE, topic.pinned.is_(True)))
        .order_by(topic.pinned.desc(), topic.last_seen_at.desc(), topic.id.desc())
        .limit(topics)
    ).all()
    links = _topic_entity_weights(db, user_id, [row.id for row in topic_rows])

    mention, entity = models.KnowledgeMention, models.KnowledgeEntity
    linked_weight: dict[int, int] = defaultdict(int)
    for (_topic_id, entity_id), weight in links.items():
        linked_weight[entity_id] += weight
    counts = _mention_counts(db, user_id, set(linked_weight))
    ranked = sorted(
        linked_weight, key=lambda entity_id: (-linked_weight[entity_id], -counts.get(entity_id, 0), entity_id)
    )
    if len(ranked) < entities:
        mentions = func.count(mention.id)
        query = (
            select(mention.entity_id, mentions)
            .where(mention.user_id == user_id)
            .group_by(mention.entity_id)
            .order_by(mentions.desc(), mention.entity_id)
            .limit(entities)
        )
        if linked_weight:
            query = query.where(mention.entity_id.not_in(set(linked_weight)))
        for entity_id, count in db.execute(query).all():
            counts[entity_id] = int(count)
            ranked.append(entity_id)
    chosen = ranked[:entities]
    entity_rows = (
        {row.id: row for row in db.scalars(select(entity).where(entity.user_id == user_id, entity.id.in_(chosen)))}
        if chosen
        else {}
    )
    topic_pages = _wiki_page_ids(db, user_id, wiki_service.TOPIC, {row.id for row in topic_rows})
    entity_pages = _wiki_page_ids(db, user_id, wiki_service.ENTITY, set(entity_rows))

    nodes = [
        {
            "id": f"topic:{row.id}",
            "kind": "topic",
            "ref_id": row.id,
            "label": row.title,
            "entity_type": None,
            "mentions": 0,
            "wiki_page_id": topic_pages.get(row.id),
        }
        for row in topic_rows
    ]
    nodes += [
        {
            "id": f"entity:{entity_id}",
            "kind": "entity",
            "ref_id": entity_id,
            "label": entity_rows[entity_id].name,
            "entity_type": entity_rows[entity_id].entity_type,
            "mentions": int(counts.get(entity_id, 0)),
            "wiki_page_id": entity_pages.get(entity_id),
        }
        for entity_id in chosen
        if entity_id in entity_rows
    ]
    edges = [
        {
            "source": f"topic:{topic_id}",
            "target": f"entity:{entity_id}",
            "kind": "evidence",
            "label": None,
            "weight": weight,
        }
        for (topic_id, entity_id), weight in sorted(links.items())
        if entity_id in entity_rows
    ]
    if entity_rows:
        relation = models.KnowledgeRelation
        for row in db.scalars(
            select(relation)
            .where(
                relation.user_id == user_id,
                relation.subject_id.in_(set(entity_rows)),
                relation.object_id.in_(set(entity_rows)),
            )
            .order_by(relation.id)
        ).all():
            edges.append(
                {
                    "source": f"entity:{row.subject_id}",
                    "target": f"entity:{row.object_id}",
                    "kind": "relation",
                    "label": row.predicate,
                    "weight": 1,
                }
            )
    return {"nodes": nodes, "edges": edges}
