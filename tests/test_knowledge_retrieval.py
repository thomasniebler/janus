"""Tests for hybrid knowledge retrieval, graph expansion, entity detail, tools, and API (JAN-243, part 3b)."""

from datetime import datetime, timedelta

import pytest

from janus import models, runtime_adapter, security, tool_policy
from janus.agent_toolboxes.knowledge import UNTRUSTED_NOTE, build_knowledge_tools
from janus.agent_toolboxes.shared import ToolContext
from janus.services import embedding_service, knowledge_index
from janus.services import knowledge_retrieval as retrieval
from janus.services.knowledge_graph import normalize_name

Source = models.KnowledgeSourceType


def _user(db, username: str = "alice") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _chunk(db, user, text, *, source_type=Source.EMAIL, title=None, when=None) -> models.KnowledgeChunk:
    source = models.KnowledgeSource(
        user_id=user.id,
        source_type=source_type.value,
        title=title or text[:30],
        status=models.KnowledgeSourceStatus.READY.value,
        source_updated_at=when,
    )
    db.add(source)
    db.flush()
    chunk = models.KnowledgeChunk(
        source_id=source.id,
        user_id=user.id,
        ordinal=0,
        text=text,
        content_hash=f"h{source.id}",
        char_count=len(text),
        token_estimate=len(text) // 4,
    )
    db.add(chunk)
    db.commit()
    return chunk


def _entity(db, user, name, kind="person", description=None, aliases=()) -> models.KnowledgeEntity:
    entity = models.KnowledgeEntity(
        user_id=user.id,
        entity_type=kind,
        name=name,
        normalized_name=normalize_name(name),
        aliases=list(aliases),
        description=description,
    )
    db.add(entity)
    db.commit()
    return entity


def _mention(db, chunk, entity):
    db.add(models.KnowledgeMention(user_id=chunk.user_id, chunk_id=chunk.id, entity_id=entity.id))
    db.commit()


def _relate(db, subject, predicate, target, *chunks):
    relation = models.KnowledgeRelation(
        user_id=subject.user_id, subject_id=subject.id, predicate=predicate, object_id=target.id, confidence="high"
    )
    db.add(relation)
    db.flush()
    for chunk in chunks:
        db.add(models.KnowledgeRelationEvidence(relation_id=relation.id, chunk_id=chunk.id))
    db.commit()
    return relation


@pytest.fixture
def world(db):
    """Dana works at Northwind; Northwind runs Apollo; a third chunk is unrelated."""
    user = _user(db)
    now = datetime(2026, 9, 20)
    dana_chunk = _chunk(db, user, "Dana Whitfield starts at Northwind on Monday.", title="Welcome Dana", when=now)
    apollo_chunk = _chunk(
        db,
        user,
        "The Apollo migration kicks off in October; budget approved.",
        source_type=Source.UPLOAD,
        title="apollo-plan.pdf",
        when=now - timedelta(days=30),
    )
    unrelated = _chunk(db, user, "Lunch menu for Friday: soup.", title="Lunch", when=now)
    dana = _entity(db, user, "Dana Whitfield", aliases=["Dana"], description="New project lead.")
    northwind = _entity(db, user, "Northwind", "organization")
    apollo = _entity(db, user, "Apollo migration", "project")
    _mention(db, dana_chunk, dana)
    _mention(db, dana_chunk, northwind)
    _mention(db, apollo_chunk, apollo)
    _relate(db, dana, "works_at", northwind, dana_chunk)
    _relate(db, northwind, "runs", apollo, apollo_chunk)
    return {
        "user": user,
        "chunks": (dana_chunk, apollo_chunk, unrelated),
        "entities": {"dana": dana, "northwind": northwind, "apollo": apollo},
    }


# --- Search ----------------------------------------------------------------------


def test_direct_hits_carry_sources_entities_and_reasons(db, world):
    """A word match returns the chunk with its source, its entities, and "query" as the reason."""
    result = retrieval.search(db, world["user"].id, "Monday", hops=0)
    (hit,) = result.hits
    assert hit.matched_by == ["query"]
    assert (hit.source["title"], hit.source["type"], hit.source["date"]) == (
        "Welcome Dana",
        "email",
        "2026-09-20T00:00:00",
    )
    assert {entity["name"] for entity in hit.entities} == {"Dana Whitfield", "Northwind"}


def test_graph_expansion_finds_passages_about_related_entities(db, world):
    """Asking about Dana reaches the Apollo plan through Northwind, one or two hops away."""
    user_id = world["user"].id
    no_graph = retrieval.search(db, user_id, "Dana Whitfield", hops=0)
    assert [hit.source["title"] for hit in no_graph.hits] == ["Welcome Dana"]

    two_hops = retrieval.search(db, user_id, "Dana Whitfield", hops=2)
    titles = [hit.source["title"] for hit in two_hops.hits]
    assert titles[0] == "Welcome Dana" and "apollo-plan.pdf" in titles and "Lunch" not in titles
    apollo_hit = next(hit for hit in two_hops.hits if hit.source["title"] == "apollo-plan.pdf")
    assert apollo_hit.matched_by == ["graph via Apollo migration"]
    assert [entity["name"] for entity in two_hops.entities] == ["Dana Whitfield"]

    one_hop = retrieval.search(db, user_id, "Dana Whitfield", hops=1)
    assert "apollo-plan.pdf" not in [hit.source["title"] for hit in one_hop.hits]


def test_filters_bound_both_rankings(db, world):
    """Source type, dates, and entity filters apply before ranking, to direct and graph hits alike."""
    user_id = world["user"].id
    uploads = retrieval.search(db, user_id, "Dana Whitfield", source_types=["upload"], hops=2)
    assert [hit.source["title"] for hit in uploads.hits] == ["apollo-plan.pdf"]
    recent = retrieval.search(db, user_id, "Dana Whitfield", since=datetime(2026, 9, 1), hops=2)
    assert "apollo-plan.pdf" not in [hit.source["title"] for hit in recent.hits]
    apollo_only = retrieval.search(db, user_id, "budget", entity_ids=[world["entities"]["apollo"].id], hops=0)
    assert [hit.source["title"] for hit in apollo_only.hits] == ["apollo-plan.pdf"]
    assert retrieval.search(db, user_id, "Monday", entity_ids=[world["entities"]["apollo"].id]).hits == []


def test_offset_timestamps_are_converted_to_utc(db, world):
    """``since`` with an offset is compared as the same instant in UTC, not as local wall-clock time."""
    user_id = world["user"].id
    # Welcome Dana is stored at 2026-09-20 00:00 UTC, i.e. 02:00 at +02:00.
    at_plus_two = datetime.fromisoformat("2026-09-20T01:30:00+02:00")  # 23:30 UTC the day before
    assert retrieval.to_naive_utc(at_plus_two) == datetime(2026, 9, 19, 23, 30)
    found = retrieval.search(db, user_id, "Monday", since=at_plus_two, hops=0)
    assert [hit.source["title"] for hit in found.hits] == ["Welcome Dana"]
    later = datetime.fromisoformat("2026-09-20T03:00:00+02:00")  # 01:00 UTC, after the source
    assert retrieval.search(db, user_id, "Monday", since=later, hops=0).hits == []


def test_ties_are_broken_by_source_date_not_id(db, world):
    """Among equally matching sources, the most recently dated wins, even with an older id."""
    user = world["user"]
    apollo = world["entities"]["apollo"]
    old_id_new_date = _chunk(db, user, "Apollo status update.", title="old id, new date", when=datetime(2026, 9, 27))
    new_id_old_date = _chunk(db, user, "Apollo kickoff notes.", title="new id, old date", when=datetime(2026, 1, 1))
    _mention(db, old_id_new_date, apollo)
    _mention(db, new_id_old_date, apollo)
    detail = retrieval.entity_detail(db, user.id, apollo.id)
    titles = [source["title"] for source in detail["sources"]]
    assert titles.index("old id, new date") < titles.index("new id, old date")
    graph = retrieval._graph_ranking(db, retrieval.candidate_chunks(user.id), {apollo.id})
    assert graph.index(old_id_new_date.id) < graph.index(new_id_old_date.id)


def test_search_is_owner_scoped_and_bounded(db, world):
    """Another user's chunks and entities never appear; the limit is clamped."""
    bob = _user(db, "bob")
    _chunk(db, bob, "Dana Whitfield is Bob's cousin.")
    result = retrieval.search(db, world["user"].id, "Dana Whitfield", hops=2, limit=500)
    assert "Dana Whitfield is Bob's cousin." not in [hit.text for hit in result.hits]
    assert retrieval.search(db, bob.id, "Northwind", hops=2).entities == []
    for index in range(30):
        _chunk(db, world["user"], f"Monday note {index}")
    assert len(retrieval.search(db, world["user"].id, "Monday", limit=500).hits) == retrieval.MAX_LIMIT


def test_vector_only_hits_stay_capped_and_entities_are_embedded(db, world, monkeypatch):
    """With embeddings on, entities are embedded and similarity alone adds at most VECTOR_ONLY_LIMIT chunks."""

    class Same:
        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    settings = db.query(models.Settings).first() or models.Settings()
    settings.embedding_provider, settings.embedding_model, settings.embedding_min_similarity = "ollama", "fake", 0.5
    db.add(settings)
    db.commit()
    monkeypatch.setattr(embedding_service, "build_embeddings", lambda settings, choice: Same())
    for index in range(10):
        _chunk(db, world["user"], f"filler text {index}")
    knowledge_index.sweep(db)
    embedded = {row.owner_type for row in db.query(models.KnowledgeEmbedding)}
    if not embedded:
        pytest.skip("sqlite-vec is not available here")
    assert {"chunk", "entity"} <= embedded

    result = retrieval.search(db, world["user"].id, "zzz nothing matches", hops=0, limit=20)
    assert len(result.hits) == knowledge_index.VECTOR_ONLY_LIMIT
    assert len(result.entities) <= retrieval.MAX_QUERY_SEEDS


def test_deleting_an_entity_deletes_its_vector(db, world):
    """Pruned or merged entities do not leave orphaned embeddings behind."""
    from janus.services import knowledge_graph

    northwind = world["entities"]["northwind"]
    db.add(
        models.KnowledgeEmbedding(
            user_id=world["user"].id,
            owner_type="entity",
            owner_id=northwind.id,
            model="m",
            dimensions=1,
            content_hash="x",
            vector=b"\x00\x00\x80?",
        )
    )
    db.commit()
    knowledge_graph._delete_entity(db, northwind.id)
    db.commit()
    assert db.query(models.KnowledgeEmbedding).count() == 0


# --- Entities --------------------------------------------------------------------


def test_entity_detail_and_resolution(db, world):
    """Detail lists relations both ways with support counts, and sources; names resolve by alias or part."""
    user_id = world["user"].id
    northwind = world["entities"]["northwind"]
    detail = retrieval.entity_detail(db, user_id, northwind.id)
    relations = {
        (r["direction"], r["predicate"], r["other"]["name"], r["supporting_chunks"]) for r in detail["relations"]
    }
    assert relations == {("incoming", "works_at", "Dana Whitfield", 1), ("outgoing", "runs", "Apollo migration", 1)}
    assert [(source["title"], source["mentions"]) for source in detail["sources"]] == [("Welcome Dana", 1)]
    assert retrieval.entity_detail(db, _user(db, "eve").id, northwind.id) is None

    dana = world["entities"]["dana"]
    assert retrieval.resolve_entities(db, user_id, "dana") == [dana.id]
    assert retrieval.resolve_entities(db, user_id, "whitfield") == [dana.id]
    assert retrieval.resolve_entities(db, user_id, str(dana.id)) == [dana.id]
    assert retrieval.resolve_entities(db, user_id, "nobody") == []


# --- Tools -----------------------------------------------------------------------


def _tools(db, user):
    context = ToolContext(db=db, user=user)
    return {tool.name: tool for tool in build_knowledge_tools(context)}


def test_tools_search_and_look_up_entities(db, world):
    """The tools return cited hits and entity details, labelled as untrusted content."""
    tools = _tools(db, world["user"])
    result = tools["search_knowledge"].invoke({"query": "budget", "entity": "Apollo"})
    assert result["note"] == UNTRUSTED_NOTE
    assert [hit["source"]["title"] for hit in result["hits"]] == ["apollo-plan.pdf"]
    assert tools["search_knowledge"].invoke({"query": "x", "entity": "Nobody"})["hits"] == []
    with pytest.raises(ValueError):
        tools["search_knowledge"].invoke({"query": "x", "source_types": ["fax"]})
    with pytest.raises(ValueError):
        tools["search_knowledge"].invoke({"query": "x", "since": "last week"})
    since = tools["search_knowledge"].invoke({"query": "Dana Whitfield", "since": "2026-09-01T02:00:00+02:00"})
    assert [hit["source"]["title"] for hit in since["hits"]] == ["Welcome Dana"]

    entity = tools["get_entity"].invoke({"entity": "Northwind"})
    assert entity["name"] == "Northwind" and len(entity["relations"]) == 2
    assert "No entity matches" in tools["get_entity"].invoke({"entity": "Nobody"})["message"]


def test_knowledge_tools_are_read_only_and_reachable_where_intended():
    """The toolbox never needs confirmation and is allowlisted for chat and the unattended read modes."""
    entry = next(e for e in runtime_adapter.DEFAULT_TOOL_REGISTRY.entries() if e.id == "knowledge")
    assert (entry.operation, entry.confirmation, entry.risk_level) == ("read", "never", "low")
    for mode in ("chat", "briefing", "digest"):
        assert "knowledge" in runtime_adapter.TASK_MODE_ALLOWED_TOOL_IDS[mode], mode
    assert "knowledge" not in runtime_adapter.TASK_MODE_ALLOWED_TOOL_IDS["ingestion"]
    # Unattended runs get it by default, and delegated children may read it too.
    assert tool_policy.default_unattended_allowed(entry)
    assert "knowledge" in tool_policy.delegable_tool_ids(frozenset({"knowledge"}), [entry])


# --- API -------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """An authenticated client for user ``bob``."""
    _user(db, "bob")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_search_and_entity_api(auth_client, db):
    """The search endpoint returns cited hits with filters; entity detail is owner-scoped."""
    bob = db.query(models.User).filter_by(username="bob").one()
    chunk = _chunk(db, bob, "Quarterly numbers from Globex are in.", title="Q3 numbers", when=datetime(2026, 9, 1))
    globex = _entity(db, bob, "Globex", "organization")
    _mention(db, chunk, globex)
    other = _entity(db, _user(db, "carol"), "Initech", "organization")

    result = auth_client.get("/api/knowledge/search", params={"q": "Globex", "hops": 2}).json()
    assert [hit["source"]["title"] for hit in result["hits"]] == ["Q3 numbers"]
    assert result["entities"] == [{"id": globex.id, "name": "Globex", "type": "organization"}]
    filtered = auth_client.get("/api/knowledge/search", params={"q": "Globex", "source_type": "upload"}).json()
    assert filtered["hits"] == []
    assert auth_client.get("/api/knowledge/search", params={"q": ""}).status_code == 422
    assert auth_client.get("/api/knowledge/search", params={"q": "x", "hops": 3}).status_code == 422

    detail = auth_client.get(f"/api/knowledge/entities/{globex.id}").json()
    assert detail["name"] == "Globex" and detail["sources"][0]["title"] == "Q3 numbers"
    assert auth_client.get(f"/api/knowledge/entities/{other.id}").status_code == 404
