"""ORM models of the knowledge layer (JAN-239); re-exported from ``janus.models``.

Kept apart from the ``janus.models`` package so the knowledge tables live together as the
layer grows (sources and chunks in phase 2, the graph and wiki later).
"""

import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)

from janus import database
from janus.time_utils import utcnow


class KnowledgeEmbedding(database.Base):
    """One float32 embedding of a fact, topic, or chunk; ``services.knowledge_index`` owns the lifecycle."""

    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", name="uq_knowledge_embeddings_owner"),
        Index("ix_knowledge_embeddings_search", "user_id", "owner_type", "model"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(Integer, nullable=False)
    model = Column(String, nullable=False)
    dimensions = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    source_updated_at = Column(DateTime, nullable=True)
    vector = Column(LargeBinary, nullable=False)
    embedded_at = Column(DateTime, default=utcnow, nullable=False)


class KnowledgeSourceType(str, enum.Enum):
    """Kinds of ingested sources (JAN-242)."""

    EMAIL = "email"
    EMAIL_ATTACHMENT = "email_attachment"
    CONVERSATION = "conversation"
    WHATSAPP_CONVERSATION = "whatsapp_conversation"
    CALENDAR_EVENT = "calendar_event"
    PLANNER_TASK = "planner_task"
    UPLOAD = "upload"


class KnowledgeSourceStatus(str, enum.Enum):
    """Ingestion state of one source."""

    PENDING = "pending"
    READY = "ready"
    SKIPPED = "skipped"
    FAILED = "failed"


class KnowledgeSource(database.Base):
    """One ingested item; its text lives in ``KnowledgeChunk`` rows (JAN-242).

    ``owner_id`` is the id of the row the source was built from (an email, a
    conversation, ...) and ``part`` tells attachments of one email apart.
    ``staged_content`` holds attachment/upload bytes only until they are parsed.
    """

    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "source_type", "owner_id", "part", name="uq_knowledge_sources_owner"),
        Index("ix_knowledge_sources_status", "status", "source_type"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Plain strings (validated in code) so new source types need no enum migration.
    source_type = Column(String(32), nullable=False)
    owner_id = Column(Integer, nullable=True)
    part = Column(Integer, nullable=False, default=0, server_default="0")
    title = Column(String, nullable=True)
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    content_hash = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default=KnowledgeSourceStatus.PENDING.value)
    status_detail = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    staged_content = Column(LargeBinary, nullable=True)
    source_updated_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeChunk(database.Base):
    """One ordered text chunk of a source; embedded as ``owner_type="chunk"``."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunks_ordinal"),)

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    char_count = Column(Integer, nullable=False)
    token_estimate = Column(Integer, nullable=False)
    #: ``content_hash`` of the text last run through entity extraction (JAN-243); pending while it differs.
    extracted_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --- Graph (JAN-243) -------------------------------------------------------------


class KnowledgeEntityType(str, enum.Enum):
    """Kinds of extracted entities."""

    PERSON = "person"
    ORGANIZATION = "organization"
    PROJECT = "project"
    PLACE = "place"
    PRODUCT = "product"
    CONCEPT = "concept"


class KnowledgeEntity(database.Base):
    """One canonical person, organization, project, place, product, or concept."""

    __tablename__ = "knowledge_entities"
    __table_args__ = (UniqueConstraint("user_id", "entity_type", "normalized_name", name="uq_knowledge_entities_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = Column(String(16), nullable=False)
    name = Column(String(200), nullable=False)
    normalized_name = Column(String(200), nullable=False)
    #: Other names seen for the entity, as written.
    aliases = Column(JSON, nullable=False, default=list)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeMention(database.Base):
    """A chunk that mentions an entity (the provenance edge)."""

    __tablename__ = "knowledge_mentions"
    __table_args__ = (UniqueConstraint("chunk_id", "entity_id", name="uq_knowledge_mentions_pair"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id = Column(Integer, ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)


class KnowledgeRelation(database.Base):
    """``subject --predicate--> object`` between two entities of one user."""

    __tablename__ = "knowledge_relations"
    __table_args__ = (UniqueConstraint("subject_id", "predicate", "object_id", name="uq_knowledge_relations_triple"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id = Column(Integer, ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    predicate = Column(String(64), nullable=False)
    object_id = Column(Integer, ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    confidence = Column(String(8), nullable=False, default="medium")
    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class KnowledgeRelationEvidence(database.Base):
    """A chunk supporting a relation."""

    __tablename__ = "knowledge_relation_evidence"
    __table_args__ = (UniqueConstraint("relation_id", "chunk_id", name="uq_knowledge_relation_evidence_pair"),)

    id = Column(Integer, primary_key=True, index=True)
    relation_id = Column(Integer, ForeignKey("knowledge_relations.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id = Column(Integer, ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False, index=True)


class KnowledgeMergeSuggestionStatus(str, enum.Enum):
    """Review state of a proposed entity merge."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class KnowledgeMergeSuggestion(database.Base):
    """A proposed merge of ``duplicate`` into ``entity`` awaiting the user's decision."""

    __tablename__ = "knowledge_merge_suggestions"
    __table_args__ = (UniqueConstraint("entity_id", "duplicate_id", name="uq_knowledge_merge_suggestions_pair"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False)
    duplicate_id = Column(Integer, ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False)
    reason = Column(String(200), nullable=False)
    status = Column(String(16), nullable=False, default=KnowledgeMergeSuggestionStatus.PENDING.value)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)


class KnowledgeLlmUsage(database.Base):
    """Tokens one user's knowledge extraction used in one month (``period`` = ``YYYY-MM``)."""

    __tablename__ = "knowledge_llm_usage"
    __table_args__ = (UniqueConstraint("user_id", "period", name="uq_knowledge_llm_usage_period"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    period = Column(String(7), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    calls = Column(Integer, nullable=False, default=0, server_default="0")


#: Default monthly token budget for knowledge extraction.
DEFAULT_MONTHLY_TOKEN_BUDGET = 2_000_000


class KnowledgePreference(database.Base):
    """Per-user knowledge settings; whether extraction runs is the built-in trigger's ``enabled`` flag."""

    __tablename__ = "knowledge_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    monthly_token_budget = Column(
        Integer, nullable=False, default=DEFAULT_MONTHLY_TOKEN_BUDGET, server_default=str(DEFAULT_MONTHLY_TOKEN_BUDGET)
    )
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# --- Wiki (JAN-244) --------------------------------------------------------------


class WikiPage(database.Base):
    """One wiki page about a tracked topic or an entity; ``body`` is the published Markdown."""

    __tablename__ = "wiki_pages"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_wiki_pages_slug"),
        Index("ix_wiki_pages_subject", "user_id", "subject_type", "subject_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    slug = Column(String(200), nullable=False)
    title = Column(String(300), nullable=False)
    #: ``topic`` or ``entity``; ``subject_id`` is cleared when a user-edited page outlives its subject.
    subject_type = Column(String(16), nullable=False)
    subject_id = Column(Integer, nullable=True)
    body = Column(Text, nullable=False, default="", server_default="")
    #: ``[{n, chunk_id, source_id, source_type, title, date}]`` snapshots for the ``[n]`` markers in ``body``;
    #: a marker citing a memory fact has ``fact_id`` and ``source_type="memory_fact"`` instead (JAN-334).
    citations = Column(JSON, nullable=False, default=list)
    #: Hash of the inputs the published body was written from; stale when the current inputs differ.
    input_hash = Column(String(64), nullable=True)
    synthesized_at = Column(DateTime, nullable=True)
    #: When synthesis last compared the page with its inputs; the staleness scan starts with the oldest.
    checked_at = Column(DateTime, nullable=True)
    user_edited = Column(Boolean, nullable=False, default=False, server_default="0")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class WikiPageRevision(database.Base):
    """One version of a page: published (history) or proposed (awaiting review)."""

    __tablename__ = "wiki_page_revisions"
    __table_args__ = (Index("ix_wiki_page_revisions_page", "page_id", "kind", "status"),)

    id = Column(Integer, primary_key=True, index=True)
    page_id = Column(Integer, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    citations = Column(JSON, nullable=False, default=list)
    #: ``janus`` (synthesis), ``user`` (an edit or restore), or ``agent`` (``propose_wiki_edit``).
    author = Column(String(16), nullable=False)
    #: ``published`` or ``proposed``.
    kind = Column(String(16), nullable=False)
    #: For proposals: ``pending``, ``accepted``, or ``dismissed``; ``None`` for published revisions.
    status = Column(String(16), nullable=True)
    note = Column(String(500), nullable=True)
    input_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
