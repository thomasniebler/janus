"""Wiki pages about tracked topics and entities: discovery, edits, proposals, and revisions (JAN-244).

Every change to a page's published ``body`` is a ``published`` revision, so
history is complete and restorable. Janus's rewrites of a page the user has
edited (``user_edited``) are filed as ``proposed`` revisions instead, and the
user accepts or dismisses them. ``wiki_synthesis`` writes the text; this
module owns the rows.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from janus import models
from janus.services.knowledge_graph import normalize_name
from janus.time_utils import utcnow

TOPIC = "topic"
ENTITY = "entity"
SUBJECT_TYPES = (TOPIC, ENTITY)

AUTHOR_JANUS, AUTHOR_USER, AUTHOR_AGENT = "janus", "user", "agent"
PUBLISHED, PROPOSED = "published", "proposed"
PENDING, ACCEPTED, DISMISSED = "pending", "accepted", "dismissed"

#: Entities mentioned in at least this many chunks get a page automatically.
MIN_ENTITY_MENTIONS = 5
#: Longest page body accepted from a user or the agent.
MAX_BODY_CHARS = 50_000


class WikiError(ValueError):
    """A page or revision that does not exist for the user, or a request that cannot apply."""


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_name(title)).strip("-")[:180] or "page"


def _unique_slug(db: Session, user_id: int, title: str) -> str:
    base = _slugify(title)
    page = models.WikiPage
    taken = set(db.scalars(select(page.slug).where(page.user_id == user_id, page.slug.like(f"{base}%"))))
    slug, counter = base, 2
    while slug in taken:
        slug, counter = f"{base}-{counter}", counter + 1
    return slug


# --- Pages -----------------------------------------------------------------------


def page_for(db: Session, user_id: int, subject_type: str, subject_id: int) -> models.WikiPage | None:
    """The user's page about one topic or entity, if any."""
    page = models.WikiPage
    return db.scalar(
        select(page).where(page.user_id == user_id, page.subject_type == subject_type, page.subject_id == subject_id)
    )


def get_page(db: Session, user_id: int, page_id: int) -> models.WikiPage:
    """One of the user's pages, or ``WikiError``."""
    page = db.get(models.WikiPage, page_id)
    if page is None or page.user_id != user_id:
        raise WikiError("Wiki page not found")
    return page


def find_page(db: Session, user_id: int, reference: str | int) -> models.WikiPage | None:
    """A page by id, slug, or title (exact normalized title first, then contained in it)."""
    page = models.WikiPage
    if isinstance(reference, int) or str(reference).isdigit():
        found = db.get(page, int(reference))
        return found if found is not None and found.user_id == user_id else None
    text = str(reference).strip()
    by_slug = db.scalar(select(page).where(page.user_id == user_id, page.slug == text))
    if by_slug is not None:
        return by_slug
    key = normalize_name(text)
    pages = db.scalars(select(page).where(page.user_id == user_id)).all()
    exact = [row for row in pages if normalize_name(row.title) == key]
    if exact:
        return exact[0]
    contained = [row for row in pages if key and key in normalize_name(row.title)]
    return contained[0] if contained else None


def list_pages(
    db: Session,
    user_id: int,
    *,
    subject_type: str | None = None,
    subject_id: int | None = None,
    query: str | None = None,
) -> list[tuple[models.WikiPage, int]]:
    """The user's pages with their number of pending proposals, most recently updated first."""
    page, revision = models.WikiPage, models.WikiPageRevision
    pending = (
        select(func.count(revision.id))
        .where(revision.page_id == page.id, revision.kind == PROPOSED, revision.status == PENDING)
        .correlate(page)
        .scalar_subquery()
    )
    statement = select(page, pending).where(page.user_id == user_id)
    if subject_type:
        statement = statement.where(page.subject_type == subject_type)
    if subject_id is not None:
        statement = statement.where(page.subject_id == subject_id)
    if query and query.strip():
        like = f"%{query.strip()}%"
        statement = statement.where(or_(page.title.ilike(like), page.slug.ilike(like), page.body.ilike(like)))
    rows = db.execute(statement.order_by(page.updated_at.desc(), page.id.desc())).all()
    return [(row[0], int(row[1] or 0)) for row in rows]


def _subject_title(db: Session, user_id: int, subject_type: str, subject_id: int) -> str | None:
    model = models.TrackedTopic if subject_type == TOPIC else models.KnowledgeEntity
    row = db.get(model, subject_id)
    if row is None or row.user_id != user_id:
        return None
    return row.title if subject_type == TOPIC else row.name


def create_page(db: Session, user_id: int, subject_type: str, subject_id: int) -> models.WikiPage:
    """The page about a topic or entity, created (empty, to be written by synthesis) if missing. Flushes."""
    if subject_type not in SUBJECT_TYPES:
        raise WikiError(f"subject_type must be one of {', '.join(SUBJECT_TYPES)}")
    existing = page_for(db, user_id, subject_type, subject_id)
    if existing is not None:
        return existing
    title = _subject_title(db, user_id, subject_type, subject_id)
    if title is None:
        raise WikiError(f"{subject_type} not found")
    page = models.WikiPage(
        user_id=user_id,
        slug=_unique_slug(db, user_id, title),
        title=title[:300],
        subject_type=subject_type,
        subject_id=subject_id,
        body="",
        citations=[],
    )
    db.add(page)
    db.flush()
    return page


def ensure_pages(db: Session, user_id: int) -> int:
    """Creates missing pages for active or pinned topics and busy entities; returns how many. Flushes."""
    topic = models.TrackedTopic
    topic_ids = db.scalars(
        select(topic.id).where(
            topic.user_id == user_id, or_(topic.status == models.TopicStatus.ACTIVE, topic.pinned.is_(True))
        )
    ).all()
    mention = models.KnowledgeMention
    entity_ids = db.scalars(
        select(mention.entity_id)
        .where(mention.user_id == user_id)
        .group_by(mention.entity_id)
        .having(func.count(mention.id) >= MIN_ENTITY_MENTIONS)
    ).all()
    page = models.WikiPage
    existing = set(db.execute(select(page.subject_type, page.subject_id).where(page.user_id == user_id)).tuples().all())
    created = 0
    for subject_type, ids in ((TOPIC, topic_ids), (ENTITY, entity_ids)):
        for subject_id in ids:
            if (subject_type, subject_id) not in existing:
                create_page(db, user_id, subject_type, subject_id)
                created += 1
    return created


def remove_orphans(db: Session, user_id: int) -> int:
    """Deletes pages whose topic or entity is gone, unless the user edited them (those lose the link). Flushes."""
    page = models.WikiPage
    removed = 0
    for row in db.scalars(select(page).where(page.user_id == user_id, page.subject_id.is_not(None))).all():
        if _subject_title(db, user_id, row.subject_type, row.subject_id) is not None:
            continue
        if row.user_edited:
            row.subject_id = None
        else:
            _delete_page(db, row)
            removed += 1
    db.flush()
    return removed


def _delete_page(db: Session, page: models.WikiPage) -> None:
    db.execute(delete(models.WikiPageRevision).where(models.WikiPageRevision.page_id == page.id))
    db.delete(page)


# --- Revisions -------------------------------------------------------------------


def revisions(db: Session, page: models.WikiPage) -> list[models.WikiPageRevision]:
    """A page's revisions, newest first."""
    revision = models.WikiPageRevision
    return list(db.scalars(select(revision).where(revision.page_id == page.id).order_by(revision.id.desc())).all())


def _clean_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise WikiError("The page text is empty")
    if len(text) > MAX_BODY_CHARS:
        raise WikiError(f"The page text is longer than {MAX_BODY_CHARS} characters")
    return text


def publish(
    db: Session,
    page: models.WikiPage,
    body: str,
    *,
    author: str,
    citations: Iterable[dict] = (),
    input_hash: str | None = None,
    note: str | None = None,
) -> models.WikiPageRevision:
    """Makes ``body`` the page's text and records it as a published revision. Does not commit."""
    citations = list(citations)
    page.body, page.citations = body, citations
    if author == AUTHOR_USER:
        page.user_edited = True
    if author == AUTHOR_JANUS:
        page.input_hash, page.synthesized_at = input_hash, utcnow()
    revision = models.WikiPageRevision(
        page_id=page.id,
        user_id=page.user_id,
        body=body,
        citations=citations,
        author=author,
        kind=PUBLISHED,
        note=note,
        input_hash=input_hash,
    )
    db.add(revision)
    db.flush()
    return revision


def propose(
    db: Session,
    page: models.WikiPage,
    body: str,
    *,
    author: str,
    citations: Iterable[dict] = (),
    input_hash: str | None = None,
    note: str | None = None,
) -> models.WikiPageRevision:
    """Files ``body`` as a pending proposal; an older pending proposal by the same author is superseded."""
    revision = models.WikiPageRevision
    for older in db.scalars(
        select(revision).where(
            revision.page_id == page.id,
            revision.kind == PROPOSED,
            revision.status == PENDING,
            revision.author == author,
        )
    ).all():
        older.status, older.resolved_at = DISMISSED, utcnow()
    if author == AUTHOR_JANUS:
        # The inputs were considered; do not rewrite again until they change.
        page.input_hash = input_hash
    row = revision(
        page_id=page.id,
        user_id=page.user_id,
        body=body,
        citations=list(citations),
        author=author,
        kind=PROPOSED,
        status=PENDING,
        note=note,
        input_hash=input_hash,
    )
    db.add(row)
    db.flush()
    return row


def clear_sourced_text(db: Session, page: models.WikiPage, input_hash: str) -> bool:
    """Drops what Janus wrote from sources a page no longer has; returns whether anything changed. Flushes.

    Used when a page's inputs changed to no passages or facts at all (its last
    source was deleted or unlinked). Janus- and agent-authored revisions go, pending
    proposals included; a page the user never edited returns to "not written".
    A user-edited page keeps its text and the user's revisions, with only the
    citations whose passage or memory fact still exists.
    """
    revision = models.WikiPageRevision
    changed = bool(page.body) and not page.user_edited
    changed |= (
        db.execute(
            delete(revision).where(revision.page_id == page.id, revision.author.in_((AUTHOR_JANUS, AUTHOR_AGENT)))
        ).rowcount
        > 0
    )
    if page.user_edited:
        cited = page.citations or []
        live = set(
            db.scalars(
                select(models.KnowledgeChunk.id).where(
                    models.KnowledgeChunk.id.in_({citation.get("chunk_id") for citation in cited})
                )
            )
        )
        live_facts = set(
            db.scalars(
                select(models.AgentMemoryFact.id).where(
                    models.AgentMemoryFact.user_id == page.user_id,
                    models.AgentMemoryFact.id.in_({citation.get("fact_id") for citation in cited}),
                )
            )
        )
        kept = [
            citation
            for citation in cited
            if citation.get("chunk_id") in live
            or (citation.get("fact_id") is not None and citation["fact_id"] in live_facts)
        ]
        changed |= len(kept) != len(page.citations or [])
        page.citations = kept
    else:
        page.body, page.citations, page.synthesized_at = "", [], None
    page.input_hash = input_hash
    db.flush()
    return changed


def edit_page(db: Session, user_id: int, page_id: int, body: str, *, title: str | None = None) -> models.WikiPage:
    """A user edit: published at once, and the page is marked ``user_edited``. Commits."""
    page = get_page(db, user_id, page_id)
    if title is not None and title.strip():
        page.title = title.strip()[:300]
    publish(db, page, _clean_body(body), author=AUTHOR_USER, citations=_kept_citations(page.citations, body))
    db.commit()
    db.refresh(page)
    return page


def agent_proposal(
    db: Session, user_id: int, reference: str | int, body: str, note: str | None
) -> models.WikiPageRevision:
    """A proposal from the agent's ``propose_wiki_edit`` tool; it keeps only citations still referenced. Commits."""
    page = find_page(db, user_id, reference)
    if page is None:
        raise WikiError(f"No wiki page matches {reference!r}")
    text = _clean_body(body)
    row = propose(db, page, text, author=AUTHOR_AGENT, citations=_kept_citations(page.citations, text), note=note)
    db.commit()
    return row


def _kept_citations(citations: Iterable[dict], body: str) -> list[dict]:
    """The citations whose ``[n]`` marker still appears in ``body``."""
    used = {int(number) for number in re.findall(r"\[(\d+)\]", body or "")}
    return [citation for citation in citations or [] if citation.get("n") in used]


def _revision_for(db: Session, user_id: int, revision_id: int) -> tuple[models.WikiPageRevision, models.WikiPage]:
    row = db.get(models.WikiPageRevision, revision_id)
    if row is None or row.user_id != user_id:
        raise WikiError("Revision not found")
    return row, db.get(models.WikiPage, row.page_id)


def accept(db: Session, user_id: int, revision_id: int) -> models.WikiPage:
    """Publishes a pending proposal. Commits."""
    row, page = _revision_for(db, user_id, revision_id)
    if row.kind != PROPOSED or row.status != PENDING:
        raise WikiError("Only pending proposals can be accepted")
    row.status, row.resolved_at = ACCEPTED, utcnow()
    publish(db, page, row.body, author=row.author, citations=row.citations, input_hash=row.input_hash, note="accepted")
    db.commit()
    db.refresh(page)
    return page


def dismiss(db: Session, user_id: int, revision_id: int) -> None:
    """Dismisses a pending proposal; the page stays as it is. Commits."""
    row, _page = _revision_for(db, user_id, revision_id)
    if row.kind != PROPOSED or row.status != PENDING:
        raise WikiError("Only pending proposals can be dismissed")
    row.status, row.resolved_at = DISMISSED, utcnow()
    db.commit()


def restore(db: Session, user_id: int, revision_id: int) -> models.WikiPage:
    """Publishes a copy of an earlier published revision as a user edit. Commits."""
    row, page = _revision_for(db, user_id, revision_id)
    if row.kind != PUBLISHED:
        raise WikiError("Only published revisions can be restored")
    publish(db, page, row.body, author=AUTHOR_USER, citations=row.citations, note=f"restored revision {row.id}")
    db.commit()
    db.refresh(page)
    return page
