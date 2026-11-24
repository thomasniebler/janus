"""Schemas for knowledge sources and uploads (JAN-242)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeSource(BaseModel):
    """One ingested source; its staged bytes and chunk text are never returned."""

    id: int
    source_type: str
    owner_id: int | None = None
    part: int
    title: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    status: str
    status_detail: str | None = None
    chunk_count: int = 0
    processed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_source(cls, source: object, chunk_count: int) -> "KnowledgeSource":
        """Builds the response for one ``models.KnowledgeSource`` row and its chunk count."""
        return cls.model_validate(source).model_copy(update={"chunk_count": chunk_count})


class KnowledgeExtractionStatus(BaseModel):
    """Knowledge extraction settings and this month's usage (JAN-243)."""

    enabled: bool
    definition_id: int | None = None
    model: str
    is_cloud: bool
    monthly_token_budget: int
    period: str
    tokens_used: int
    calls: int
    pending_chunks: int


class KnowledgeExtractionUpdate(BaseModel):
    """Turns extraction on or off and sets the monthly token budget."""

    enabled: bool | None = None
    monthly_token_budget: int | None = Field(default=None, ge=0, le=1_000_000_000)


class KnowledgeEntity(BaseModel):
    """One entity with its mention and relation counts."""

    id: int
    entity_type: str
    name: str
    aliases: list[str] = []
    description: str | None = None
    mention_count: int = 0
    relation_count: int = 0

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, entity: object, mention_count: int = 0, relation_count: int = 0) -> "KnowledgeEntity":
        """Builds the response for one ``models.KnowledgeEntity`` and its counts."""
        return cls.model_validate(entity).model_copy(
            update={"mention_count": mention_count, "relation_count": relation_count}
        )


class KnowledgeMergeSuggestion(BaseModel):
    """A proposed merge of ``duplicate`` into ``entity``."""

    id: int
    reason: str
    created_at: datetime
    entity: KnowledgeEntity
    duplicate: KnowledgeEntity


class KnowledgeEntityRef(BaseModel):
    """An entity named in a search hit or relation."""

    id: int
    name: str
    type: str


class KnowledgeSourceRef(BaseModel):
    """The source a hit or mention comes from."""

    id: int
    type: str
    title: str | None = None
    owner_id: int | None = None
    date: str | None = None


class KnowledgeSearchHit(BaseModel):
    """One retrieved passage with its source and why it matched."""

    chunk_id: int
    text: str
    source: KnowledgeSourceRef
    entities: list[KnowledgeEntityRef] = []
    matched_by: list[str] = []


class KnowledgeSearchResult(BaseModel):
    """Ranked passages plus the entities the search started from."""

    hits: list[KnowledgeSearchHit]
    entities: list[KnowledgeEntityRef]


class KnowledgeRelationRef(BaseModel):
    """One relation of an entity, seen from that entity."""

    id: int
    direction: str
    predicate: str
    other: KnowledgeEntityRef
    confidence: str
    supporting_chunks: int


class KnowledgeEntitySource(KnowledgeSourceRef):
    """A source mentioning an entity, with how many of its chunks do."""

    mentions: int


class KnowledgeEntityDetail(KnowledgeEntityRef):
    """An entity with its relations and the sources mentioning it most."""

    aliases: list[str] = []
    description: str | None = None
    relations: list[KnowledgeRelationRef]
    sources: list[KnowledgeEntitySource]


class KnowledgeGraphNode(BaseModel):
    """A topic or entity in the knowledge graph view (JAN-334)."""

    id: str
    kind: str
    ref_id: int
    label: str
    entity_type: str | None = None
    mentions: int = 0
    wiki_page_id: int | None = None


class KnowledgeGraphEdge(BaseModel):
    """A persisted link: topic evidence mentioning an entity, or an extracted relation between entities."""

    source: str
    target: str
    kind: str
    label: str | None = None
    weight: int = 1


class KnowledgeGraph(BaseModel):
    """The caller's active topics, their most connected entities, and the links between them."""

    nodes: list[KnowledgeGraphNode]
    edges: list[KnowledgeGraphEdge]
