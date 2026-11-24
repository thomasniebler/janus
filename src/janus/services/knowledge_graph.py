"""The knowledge graph: entities, mentions, relations, and merge review (JAN-243).

``apply_extraction`` writes one batch of extraction output. Entities are merged
automatically only on an exact normalized name or alias match within one
type; a similar name (string ratio or contained words) only queues a
``KnowledgeMergeSuggestion`` for the user, because similarity scores are not
reliable enough to merge on (see the spec's "Filter first, then rank").
``forget_chunks`` removes what a deleted or rewritten chunk supported, so the
graph never outlives its sources. SQLite does not enforce the foreign-key
cascades, so every delete here is explicit.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from janus import models
from janus.time_utils import utcnow

EntityType = models.KnowledgeEntityType
SuggestionStatus = models.KnowledgeMergeSuggestionStatus

#: Accepted extraction confidences; "low" items are dropped before they get here.
CONFIDENCES = ("high", "medium")
#: String-similarity ratio at which a new entity is proposed as a duplicate of an existing one.
SIMILAR_NAME_RATIO = 0.85
#: Most merge suggestions queued for one new entity.
MAX_SUGGESTIONS_PER_ENTITY = 3

_PREDICATE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Casefolded name with accents, punctuation, and repeated whitespace collapsed."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii") or name
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def normalize_predicate(predicate: str) -> str:
    """Lowercase ``snake_case`` predicate of at most 64 characters (empty when unusable)."""
    return _PREDICATE.sub("_", predicate.casefold()).strip("_")[:64]


def similar_names(first: str, second: str) -> str | None:
    """Why two normalized names look like the same entity, or ``None``."""
    if not first or not second or first == second:
        return None
    shorter, longer = sorted((first.split(), second.split()), key=len)
    if shorter and len(shorter) < len(longer) and all(word in longer for word in shorter):
        return "one name contains the other"
    if SequenceMatcher(None, first, second).ratio() >= SIMILAR_NAME_RATIO:
        return "similar spelling"
    return None


# --- Entity resolution -----------------------------------------------------------


@dataclass
class ApplyResult:
    """What one ``apply_extraction`` call wrote."""

    entities_created: int = 0
    relations_created: int = 0
    suggestions_created: int = 0
    mentions: int = 0


@dataclass
class EntityResolver:
    """Resolves extracted names to one user's entities, loading each type's names once."""

    db: Session
    user_id: int
    _names: dict[str, dict[str, int]] = field(default_factory=dict)

    def _index(self, entity_type: str) -> dict[str, int]:
        if entity_type not in self._names:
            index: dict[str, int] = {}
            entity = models.KnowledgeEntity
            rows = self.db.execute(
                select(entity.id, entity.normalized_name, entity.aliases)
                .where(entity.user_id == self.user_id)
                .where(entity.entity_type == entity_type)
            )
            for entity_id, normalized, aliases in rows:
                index.setdefault(normalized, entity_id)
                for alias in aliases or []:
                    index.setdefault(normalize_name(alias), entity_id)
            self._names[entity_type] = index
        return self._names[entity_type]

    def resolve(
        self, name: str, entity_type: str, aliases: Iterable[str], description: str | None, result: ApplyResult
    ) -> models.KnowledgeEntity:
        """The existing entity with this name or alias, else a new one (queuing merge suggestions)."""
        index = self._index(entity_type)
        written = [name, *aliases]
        keys = [key for key in (normalize_name(value) for value in written) if key]
        entity_id = next((index[key] for key in keys if key in index), None)
        if entity_id is not None:
            entity = self.db.get(models.KnowledgeEntity, entity_id)
            known = {entity.normalized_name, *(normalize_name(alias) for alias in entity.aliases or [])}
            new_aliases = [value for value in written if normalize_name(value) not in known]
            if new_aliases:
                entity.aliases = [*(entity.aliases or []), *dict.fromkeys(new_aliases)]
            if description and not entity.description:
                entity.description = description
        else:
            entity = models.KnowledgeEntity(
                user_id=self.user_id,
                entity_type=entity_type,
                name=name[:200],
                normalized_name=keys[0][:200],
                aliases=list(dict.fromkeys(value for value in aliases if normalize_name(value) != keys[0])),
                description=description,
            )
            self.db.add(entity)
            self.db.flush()
            result.entities_created += 1
            result.suggestions_created += self._suggest_merges(entity, index)
        for key in keys:
            index.setdefault(key, entity.id)
        return entity

    def _suggest_merges(self, entity: models.KnowledgeEntity, index: dict[str, int]) -> int:
        candidates: dict[int, str] = {}
        for key, other_id in index.items():
            reason = similar_names(entity.normalized_name, key)
            if reason and other_id != entity.id and other_id not in candidates:
                candidates[other_id] = reason
            if len(candidates) >= MAX_SUGGESTIONS_PER_ENTITY:
                break
        created = 0
        for other_id, reason in candidates.items():
            if _suggestion_exists(self.db, other_id, entity.id):
                continue
            self.db.add(
                models.KnowledgeMergeSuggestion(
                    user_id=self.user_id, entity_id=other_id, duplicate_id=entity.id, reason=reason
                )
            )
            created += 1
        return created


def _suggestion_exists(db: Session, first: int, second: int) -> bool:
    suggestion = models.KnowledgeMergeSuggestion
    return (
        db.scalar(
            select(func.count(suggestion.id)).where(
                or_(
                    (suggestion.entity_id == first) & (suggestion.duplicate_id == second),
                    (suggestion.entity_id == second) & (suggestion.duplicate_id == first),
                )
            )
        )
        > 0
    )


# --- Writing extraction output ---------------------------------------------------


def apply_extraction(
    db: Session,
    user_id: int,
    chunks: list[models.KnowledgeChunk],
    output: dict,
    resolver: EntityResolver,
    *,
    live_ids: set[int] | None = None,
) -> ApplyResult:
    """Writes one batch's validated extraction output; chunks are numbered from 1 in the prompt. Does not commit.

    ``live_ids`` names the chunks still unchanged since the prompt was built; output about any other chunk
    (rewritten or deleted meanwhile) is ignored, and those chunks stay pending.
    """
    result = ApplyResult()
    live = [chunk for chunk in chunks if live_ids is None or chunk.id in live_ids]
    forget_chunks(db, [chunk.id for chunk in live])
    by_number = {number: chunk for number, chunk in enumerate(chunks, start=1) if chunk in live}
    resolved: dict[str, models.KnowledgeEntity] = {}
    for item in _objects(output.get("entities")):
        # The prompt HTML-escapes chunk text, so names may come back escaped ("AT&amp;T").
        entity_type, name = item.get("type"), html.unescape(str(item.get("name") or "")).strip()
        if entity_type not in {kind.value for kind in EntityType} or not normalize_name(name):
            continue
        if item.get("confidence") not in CONFIDENCES:
            continue
        # An entity is only kept with a mention in a (still unchanged) chunk of this batch.
        numbers = item.get("chunks") if isinstance(item.get("chunks"), list) else []
        mentioned = [by_number[number] for number in numbers if isinstance(number, int) and number in by_number]
        if not mentioned:
            continue
        raw_aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        aliases = [html.unescape(str(alias)).strip()[:200] for alias in raw_aliases if str(alias).strip()]
        description = html.unescape(str(item.get("description") or "")).strip()[:500] or None
        entity = resolver.resolve(name[:200], entity_type, aliases, description, result)
        for key in (name, *aliases):
            resolved[normalize_name(key)] = entity
        for chunk in dict.fromkeys(mentioned):
            if _add_mention(db, user_id, chunk.id, entity.id):
                result.mentions += 1
    for item in _objects(output.get("relations")):
        subject = resolved.get(normalize_name(html.unescape(str(item.get("subject") or ""))))
        target = resolved.get(normalize_name(html.unescape(str(item.get("object") or ""))))
        predicate = normalize_predicate(str(item.get("predicate") or ""))
        chunk = by_number.get(item.get("chunk")) if isinstance(item.get("chunk"), int) else None
        if subject is None or target is None or subject.id == target.id or not predicate or chunk is None:
            continue
        if item.get("confidence") not in CONFIDENCES:
            continue
        result.relations_created += _add_relation(db, user_id, subject.id, predicate, target.id, chunk, item)
    for chunk in live:
        chunk.extracted_hash = chunk.content_hash
    return result


def _objects(value: object) -> list[dict]:
    """The dict items of a model-returned list; anything else is ignored."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _add_mention(db: Session, user_id: int, chunk_id: int, entity_id: int) -> bool:
    mention = models.KnowledgeMention
    if db.scalar(select(mention.id).where(mention.chunk_id == chunk_id, mention.entity_id == entity_id)):
        return False
    db.add(mention(user_id=user_id, chunk_id=chunk_id, entity_id=entity_id))
    db.flush()
    return True


def _add_relation(
    db: Session, user_id: int, subject_id: int, predicate: str, object_id: int, chunk: models.KnowledgeChunk, item: dict
) -> int:
    relation_model = models.KnowledgeRelation
    relation = db.scalar(
        select(relation_model).where(
            relation_model.subject_id == subject_id,
            relation_model.predicate == predicate,
            relation_model.object_id == object_id,
        )
    )
    created = 0
    if relation is None:
        relation = relation_model(
            user_id=user_id,
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            confidence=item["confidence"],
            valid_from=utcnow(),
        )
        db.add(relation)
        db.flush()
        created = 1
    elif item["confidence"] == "high":
        relation.confidence = "high"
    evidence = models.KnowledgeRelationEvidence
    if not db.scalar(select(evidence.id).where(evidence.relation_id == relation.id, evidence.chunk_id == chunk.id)):
        db.add(evidence(relation_id=relation.id, chunk_id=chunk.id))
        db.flush()
    return created


# --- Cleanup ---------------------------------------------------------------------


def forget_chunks(db: Session, chunk_ids: Iterable[int]) -> None:
    """Removes the mentions and evidence of these chunks, then relations and entities left without support."""
    ids = list(chunk_ids)
    if not ids:
        return
    mention, evidence = models.KnowledgeMention, models.KnowledgeRelationEvidence
    entity_ids = set(db.scalars(select(mention.entity_id).where(mention.chunk_id.in_(ids))))
    relation_ids = set(db.scalars(select(evidence.relation_id).where(evidence.chunk_id.in_(ids))))
    if not entity_ids and not relation_ids:
        return
    db.execute(delete(mention).where(mention.chunk_id.in_(ids)))
    db.execute(delete(evidence).where(evidence.chunk_id.in_(ids)))
    relation_model = models.KnowledgeRelation
    for relation in db.scalars(select(relation_model).where(relation_model.id.in_(relation_ids))):
        entity_ids.update((relation.subject_id, relation.object_id))
        if not db.scalar(select(evidence.id).where(evidence.relation_id == relation.id).limit(1)):
            db.delete(relation)
    db.flush()
    _prune_entities(db, entity_ids)


def _prune_entities(db: Session, entity_ids: set[int]) -> None:
    """Deletes the given entities that have no mentions and take part in no relation."""
    mention, relation = models.KnowledgeMention, models.KnowledgeRelation
    for entity_id in entity_ids:
        referenced = db.scalar(select(mention.id).where(mention.entity_id == entity_id).limit(1)) or db.scalar(
            select(relation.id).where(or_(relation.subject_id == entity_id, relation.object_id == entity_id)).limit(1)
        )
        if not referenced:
            _delete_entity(db, entity_id)
    db.flush()


def _delete_entity(db: Session, entity_id: int) -> None:
    embedding = models.KnowledgeEmbedding
    db.execute(delete(embedding).where(embedding.owner_type == "entity", embedding.owner_id == entity_id))
    suggestion = models.KnowledgeMergeSuggestion
    db.execute(delete(suggestion).where(or_(suggestion.entity_id == entity_id, suggestion.duplicate_id == entity_id)))
    db.execute(delete(models.KnowledgeEntity).where(models.KnowledgeEntity.id == entity_id))


# --- Merge review ----------------------------------------------------------------


class MergeError(ValueError):
    """A merge suggestion that does not exist for the user or is already resolved."""


def _pending_suggestion(db: Session, user_id: int, suggestion_id: int) -> models.KnowledgeMergeSuggestion:
    suggestion = db.get(models.KnowledgeMergeSuggestion, suggestion_id)
    if suggestion is None or suggestion.user_id != user_id:
        raise MergeError("Merge suggestion not found")
    if suggestion.status != SuggestionStatus.PENDING.value:
        raise MergeError("Merge suggestion is already resolved")
    return suggestion


def reject_suggestion(db: Session, user_id: int, suggestion_id: int) -> None:
    """Keeps both entities; the pair is never suggested again. Commits."""
    suggestion = _pending_suggestion(db, user_id, suggestion_id)
    suggestion.status, suggestion.resolved_at = SuggestionStatus.REJECTED.value, utcnow()
    db.commit()


def accept_suggestion(db: Session, user_id: int, suggestion_id: int) -> models.KnowledgeEntity:
    """Merges the duplicate into the suggested entity: mentions, relations, evidence, and names. Commits.

    The duplicate is deleted together with every suggestion naming it, this one included.
    """
    suggestion = _pending_suggestion(db, user_id, suggestion_id)
    survivor = db.get(models.KnowledgeEntity, suggestion.entity_id)
    duplicate = db.get(models.KnowledgeEntity, suggestion.duplicate_id)
    mention = models.KnowledgeMention
    for row in db.scalars(select(mention).where(mention.entity_id == duplicate.id)).all():
        _add_mention(db, user_id, row.chunk_id, survivor.id)
        db.delete(row)
    relation_model, evidence = models.KnowledgeRelation, models.KnowledgeRelationEvidence
    touching = or_(relation_model.subject_id == duplicate.id, relation_model.object_id == duplicate.id)
    for relation in db.scalars(select(relation_model).where(touching)).all():
        subject_id = survivor.id if relation.subject_id == duplicate.id else relation.subject_id
        object_id = survivor.id if relation.object_id == duplicate.id else relation.object_id
        chunk_ids = list(db.scalars(select(evidence.chunk_id).where(evidence.relation_id == relation.id)))
        db.execute(delete(evidence).where(evidence.relation_id == relation.id))
        db.delete(relation)
        db.flush()
        if subject_id == object_id:
            continue
        for chunk in db.scalars(select(models.KnowledgeChunk).where(models.KnowledgeChunk.id.in_(chunk_ids))):
            _add_relation(
                db, user_id, subject_id, relation.predicate, object_id, chunk, {"confidence": relation.confidence}
            )
    known = {survivor.normalized_name, *(normalize_name(alias) for alias in survivor.aliases or [])}
    names = [value for value in (duplicate.name, *(duplicate.aliases or [])) if normalize_name(value) not in known]
    survivor.aliases = [*(survivor.aliases or []), *dict.fromkeys(names)]
    survivor.description = survivor.description or duplicate.description
    db.flush()
    _delete_entity(db, duplicate.id)
    db.commit()
    db.refresh(survivor)
    return survivor


# --- Listing ---------------------------------------------------------------------


def list_entities(
    db: Session, user_id: int, *, entity_type: str | None, query: str | None, limit: int, offset: int
) -> list[tuple[models.KnowledgeEntity, int, int]]:
    """The user's entities with mention and relation counts, most mentioned first."""
    entity, mention, relation = models.KnowledgeEntity, models.KnowledgeMention, models.KnowledgeRelation
    mentions = select(func.count(mention.id)).where(mention.entity_id == entity.id).correlate(entity).scalar_subquery()
    relations = (
        select(func.count(relation.id))
        .where(or_(relation.subject_id == entity.id, relation.object_id == entity.id))
        .correlate(entity)
        .scalar_subquery()
    )
    statement = select(entity, mentions, relations).where(entity.user_id == user_id)
    if entity_type:
        statement = statement.where(entity.entity_type == entity_type)
    if query and normalize_name(query):
        statement = statement.where(entity.normalized_name.contains(normalize_name(query)))
    statement = statement.order_by(mentions.desc(), entity.name).limit(limit).offset(offset)
    return [(row[0], int(row[1] or 0), int(row[2] or 0)) for row in db.execute(statement)]


def pending_suggestions(
    db: Session, user_id: int
) -> list[tuple[models.KnowledgeMergeSuggestion, models.KnowledgeEntity, models.KnowledgeEntity]]:
    """Open merge suggestions with both entities, oldest first."""
    suggestion = models.KnowledgeMergeSuggestion
    rows = db.scalars(
        select(suggestion)
        .where(suggestion.user_id == user_id, suggestion.status == SuggestionStatus.PENDING.value)
        .order_by(suggestion.id)
    ).all()
    return [
        (row, db.get(models.KnowledgeEntity, row.entity_id), db.get(models.KnowledgeEntity, row.duplicate_id))
        for row in rows
    ]
