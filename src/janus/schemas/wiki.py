"""Schemas for wiki pages, revisions, and synthesis settings (JAN-244)."""

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    # Type-only: the schemas must not import the ORM layer at runtime.
    from janus.models_knowledge import WikiPageRevision


class WikiCitation(BaseModel):
    """The source behind one ``[n]`` marker, as it was when the page was written."""

    n: int
    chunk_id: int | None = None
    #: Set instead of ``chunk_id`` when the marker cites a memory fact (JAN-334).
    fact_id: int | None = None
    source_id: int | None = None
    source_type: str | None = None
    title: str | None = None
    date: str | None = None


class WikiPageSummary(BaseModel):
    """A page in the list."""

    id: int
    slug: str
    title: str
    subject_type: str
    subject_id: int | None = None
    user_edited: bool
    synthesized_at: datetime | None = None
    updated_at: datetime
    pending_proposals: int = 0

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_row(cls, page: object, pending: int = 0) -> "WikiPageSummary":
        """Builds the list item for one ``models.WikiPage``."""
        return cls.model_validate(page).model_copy(update={"pending_proposals": pending})


class WikiRevision(BaseModel):
    """One published or proposed version of a page."""

    id: int
    body: str
    citations: list[WikiCitation] = []
    author: str
    kind: str
    status: str | None = None
    note: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WikiPageDetail(WikiPageSummary):
    """A page with its text, citations, pending proposals, and history."""

    body: str
    citations: list[WikiCitation] = []
    proposals: list[WikiRevision] = []
    revisions: list[WikiRevision] = []

    @classmethod
    def from_page(cls, page: object, revisions: list["WikiPageRevision"]) -> "WikiPageDetail":
        """Builds the detail for one page from its revisions (newest first)."""
        proposals = [row for row in revisions if row.kind == "proposed" and row.status == "pending"]
        published = [row for row in revisions if row.kind == "published"]
        return cls.model_validate(page).model_copy(
            update={
                "pending_proposals": len(proposals),
                "proposals": [WikiRevision.model_validate(row) for row in proposals],
                "revisions": [WikiRevision.model_validate(row) for row in published],
            }
        )


class WikiPageCreate(BaseModel):
    """Creates the page about a topic or entity."""

    subject_type: str = Field(pattern="^(topic|entity)$")
    subject_id: int


class WikiPageEdit(BaseModel):
    """A user edit of a page."""

    body: str = Field(min_length=1, max_length=50_000)
    title: str | None = Field(default=None, max_length=300)


class WikiSynthesisStatus(BaseModel):
    """Wiki synthesis switch, model, and the shared knowledge budget's usage."""

    enabled: bool
    definition_id: int | None = None
    model: str
    is_cloud: bool
    monthly_token_budget: int
    period: str
    tokens_used: int


class WikiSynthesisUpdate(BaseModel):
    """Turns wiki synthesis on or off."""

    enabled: bool


# --- Provenance (JAN-334) --------------------------------------------------------


class ProvenanceSourceRef(BaseModel):
    """The source a cited passage belongs to."""

    id: int
    type: str
    title: str | None = None
    owner_id: int | None = None
    date: str | None = None


class ProvenanceFact(BaseModel):
    """A memory fact behind a page, with the ``[n]`` markers citing it."""

    id: int
    summary: str
    detail: str | None = None
    status: str | None = None
    source: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    citations: list[int] = []


class ProvenanceCitation(BaseModel):
    """One ``[n]`` marker resolved to the passage or memory fact it cites, as it is now."""

    n: int
    kind: str
    available: bool
    title: str | None = None
    date: str | None = None
    source_type: str | None = None
    chunk_id: int | None = None
    fact_id: int | None = None
    source: ProvenanceSourceRef | None = None
    excerpt: str | None = None
    fact: ProvenanceFact | None = None
    entity_ids: list[int] = []


class ProvenanceEntity(BaseModel):
    """An entity mentioned in the cited passages (or the page's own entity)."""

    id: int
    name: str
    type: str
    citations: list[int] = []
    is_subject: bool = False
    wiki_page_id: int | None = None


class ProvenanceSubject(BaseModel):
    """The topic or entity a page is about; ``available`` is false once it is gone."""

    type: str
    id: int | None = None
    title: str
    available: bool


class WikiPageProvenance(BaseModel):
    """What a page is built from: resolved citations, the entities in them, and the facts behind the page."""

    page_id: int
    subject: ProvenanceSubject
    citations: list[ProvenanceCitation]
    entities: list[ProvenanceEntity]
    facts: list[ProvenanceFact]
