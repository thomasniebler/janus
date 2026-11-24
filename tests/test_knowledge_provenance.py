"""Tests for the knowledge stack views: fact citations in wiki synthesis, page provenance, and the graph (JAN-334)."""

import pytest
from langchain_core.messages import AIMessage

from janus import models, security
from janus.services import knowledge_provenance, wiki_service, wiki_synthesis
from janus.services.knowledge_graph import normalize_name


class _FakeLlm:
    """Answers with queued page texts and records prompts."""

    def __init__(self):
        """Starts with no answers; unanswered prompts get a generic cited page."""
        self.answers: list[str] = []
        self.prompts: list[str] = []

    def invoke(self, prompt):
        """Answers the next prompt."""
        self.prompts.append(prompt)
        text = self.answers.pop(0) if self.answers else "Summary of the subject [1]."
        return AIMessage(content=text, usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60})


@pytest.fixture
def fake_llm(monkeypatch):
    """Installs a fake utility model for synthesis."""
    llm = _FakeLlm()

    class _Service:
        def __init__(self):
            self.llm = llm

    monkeypatch.setattr(wiki_synthesis.llm_service, "build_llm_service", lambda *args, **kwargs: _Service())
    return llm


def _user(db, username="alice"):
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _chunk(db, user, text, *, owner_id=None, title="Mail", source_type=models.KnowledgeSourceType.EMAIL):
    source = models.KnowledgeSource(
        user_id=user.id, source_type=source_type.value, owner_id=owner_id, title=title, status="ready"
    )
    db.add(source)
    db.flush()
    chunk = models.KnowledgeChunk(
        source_id=source.id,
        user_id=user.id,
        ordinal=0,
        text=text,
        content_hash=f"h{source.id}-{len(text)}",
        char_count=len(text),
        token_estimate=len(text) // 4,
    )
    db.add(chunk)
    db.commit()
    return chunk


def _topic(db, user, title, *, email_ids=(), fact_ids=(), status=models.TopicStatus.ACTIVE):
    topic = models.TrackedTopic(user_id=user.id, slug=normalize_name(title), title=title, status=status)
    db.add(topic)
    db.flush()
    for source_type, ids in (
        (models.TopicEvidenceSource.EMAIL, email_ids),
        (models.TopicEvidenceSource.MEMORY_FACT, fact_ids),
    ):
        for source_id in ids:
            db.add(
                models.TopicEvidence(
                    topic_id=topic.id,
                    source_type=source_type,
                    source_id=source_id,
                    evidence_role=models.TopicEvidenceRole.SUPPORTING,
                )
            )
    db.commit()
    return topic


def _entity(db, user, name, mentions=(), *, entity_type="organization", aliases=()):
    entity = models.KnowledgeEntity(
        user_id=user.id,
        entity_type=entity_type,
        name=name,
        normalized_name=normalize_name(name),
        aliases=list(aliases),
    )
    db.add(entity)
    db.flush()
    for chunk in mentions:
        db.add(models.KnowledgeMention(user_id=user.id, chunk_id=chunk.id, entity_id=entity.id))
    db.commit()
    return entity


def _fact(db, user, summary, detail=None, *, status=models.AgentMemoryStatus.ACTIVE):
    fact = models.AgentMemoryFact(user_id=user.id, summary=summary, detail=detail, status=status)
    db.add(fact)
    db.commit()
    return fact


@pytest.fixture
def world(db):
    """A topic backed by one email and one linked fact, with entities mentioned in that email."""
    user = _user(db)
    mail = _chunk(db, user, "Acme asks Dana to sign the renewal by 1 November.", owner_id=101, title="Renewal mail")
    linked = _fact(db, user, "Renewal budget is capped at 40k", "Agreed with finance in September.")
    named = _fact(db, user, "Dana prefers the Acme renewal call on Tuesdays")
    _fact(db, user, "Unrelated: the dentist is on Friday")
    _fact(db, user, "Old Acme renewal note", status=models.AgentMemoryStatus.ARCHIVED)
    topic = _topic(db, user, "Acme renewal", email_ids=[101], fact_ids=[linked.id])
    acme = _entity(db, user, "Acme", [mail])
    dana = _entity(db, user, "Dana", [mail], entity_type="person")
    db.add(models.KnowledgeRelation(user_id=user.id, subject_id=dana.id, predicate="works_with", object_id=acme.id))
    db.commit()
    return {"user": user, "mail": mail, "linked": linked, "named": named, "topic": topic, "acme": acme, "dana": dana}


def _topic_page(db, world):
    return wiki_service.create_page(db, world["user"].id, wiki_service.TOPIC, world["topic"].id)


# --- Synthesis -------------------------------------------------------------------


def test_synthesis_numbers_facts_after_passages_and_cites_them(db, world, fake_llm):
    """Linked and naming facts are numbered after the passages; their markers become fact citations."""
    page = _topic_page(db, world)
    inputs = wiki_synthesis.page_inputs(db, page)
    assert [row.id for row in inputs.facts] == [world["linked"].id, world["named"].id]
    assert [row.id for row in inputs.chunks] == [world["mail"].id]

    fake_llm.answers = ["Dana must sign by 1 November [1]. The budget is capped at 40k [2]. Calls are Tuesdays [3]."]
    wiki_synthesis.run_synthesis(db, world["user"].id, None)
    db.refresh(page)
    prompt = fake_llm.prompts[0]
    assert '<passage number="2" kind="remembered fact">' in prompt and "Renewal budget is capped at 40k" in prompt
    by_number = {citation["n"]: citation for citation in page.citations}
    assert by_number[1]["chunk_id"] == world["mail"].id
    assert by_number[2] == by_number[2] | {"fact_id": world["linked"].id, "source_type": "memory_fact"}
    assert "chunk_id" not in by_number[2]
    assert by_number[3]["fact_id"] == world["named"].id


def test_facts_alone_are_enough_to_write_a_page(db, fake_llm):
    """A page whose subject has facts but no passages is written, not cleared."""
    user = _user(db)
    fact = _fact(db, user, "Globex is our hosting provider")
    entity = _entity(db, user, "Globex")
    page = wiki_service.create_page(db, user.id, wiki_service.ENTITY, entity.id)
    db.commit()
    fake_llm.answers = ["Globex hosts our services [1]."]
    summary = wiki_synthesis.run_synthesis(db, user.id, None)
    db.refresh(page)
    assert summary.published == 1 and page.citations[0]["fact_id"] == fact.id


def test_pages_without_facts_keep_their_input_hash(db):
    """Adding fact support does not mark every existing page stale: no facts, same hash as before."""
    user = _user(db)
    chunk = _chunk(db, user, "Plain text", owner_id=5)
    inputs = wiki_synthesis._inputs(db, "Topic: X", [chunk.id])
    expected = wiki_synthesis._hash(["Topic: X", f"{chunk.id}:{chunk.content_hash}"])
    assert inputs.input_hash == expected and inputs.facts == []


def test_fact_matching_is_owner_scoped_and_escapes_wildcards(db):
    """Another user's facts never match, and ``%`` / ``_`` in a name are literal."""
    alice, bob = _user(db), _user(db, "bob")
    _fact(db, bob, "Acme renewal is bob's business")
    _fact(db, alice, "Anything at all")
    assert wiki_synthesis.subject_facts(db, alice.id, [], ["Acme renewal"]) == []
    assert wiki_synthesis.subject_facts(db, alice.id, [], ["%%%"]) == []
    bob_fact = db.query(models.AgentMemoryFact).filter_by(user_id=bob.id).one()
    assert wiki_synthesis.subject_facts(db, alice.id, [bob_fact.id], []) == []


def test_user_edited_page_keeps_fact_citations_when_passages_vanish(db, world, fake_llm):
    """Clearing sourced text keeps a user-edited page's citations whose fact still exists."""
    page = _topic_page(db, world)
    fake_llm.answers = ["Mail [1], budget [2]."]
    wiki_synthesis.run_synthesis(db, world["user"].id, None)
    wiki_service.edit_page(db, world["user"].id, page.id, "Mine: mail [1], budget [2].")
    changed = wiki_service.clear_sourced_text(db, page, "new-hash")
    assert changed
    assert [citation["n"] for citation in page.citations] == [1, 2]
    db.delete(world["linked"])
    db.delete(world["mail"])
    db.commit()
    wiki_service.clear_sourced_text(db, page, "newer-hash")
    assert page.citations == []


# --- Provenance ------------------------------------------------------------------


def test_provenance_resolves_passages_facts_and_entities(db, world, fake_llm):
    """Each marker resolves to its live passage or fact; entities come from the cited passages."""
    page = _topic_page(db, world)
    fake_llm.answers = ["Dana signs [1]. Budget [2]."]
    wiki_synthesis.run_synthesis(db, world["user"].id, None)
    result = knowledge_provenance.page_provenance_for_user(db, world["user"].id, page.id)

    assert result["subject"] == {"type": "topic", "id": world["topic"].id, "title": "Acme renewal", "available": True}
    passage, fact = result["citations"]
    assert passage["kind"] == "passage" and passage["available"]
    assert passage["excerpt"].startswith("Acme asks Dana") and passage["source"]["title"] == "Renewal mail"
    assert set(passage["entity_ids"]) == {world["acme"].id, world["dana"].id}
    assert fact["kind"] == "fact" and fact["fact"]["summary"] == "Renewal budget is capped at 40k"
    assert {row["name"]: row["citations"] for row in result["entities"]} == {"Acme": [1], "Dana": [1]}
    # The cited fact first, then the uncited one the next synthesis would use.
    assert [(row["id"], row["citations"]) for row in result["facts"]] == [
        (world["linked"].id, [2]),
        (world["named"].id, []),
    ]


def test_provenance_marks_deleted_sources_unavailable(db, world, fake_llm):
    """A cited row that is gone keeps its snapshot but is flagged unavailable."""
    page = _topic_page(db, world)
    fake_llm.answers = ["Dana signs [1]. Budget [2]."]
    wiki_synthesis.run_synthesis(db, world["user"].id, None)
    db.delete(world["linked"])
    db.delete(world["mail"])
    db.commit()
    citations = knowledge_provenance.page_provenance_for_user(db, world["user"].id, page.id)["citations"]
    assert [(row["available"], row["title"] is not None) for row in citations] == [(False, True), (False, True)]
    assert citations[0]["excerpt"] is None and citations[1]["fact"] is None


def test_provenance_ignores_citations_pointing_at_other_users_rows(db, world):
    """A forged citation to another user's chunk or fact resolves to nothing."""
    bob = _user(db, "bob")
    secret = _chunk(db, bob, "Bob's secret passage", owner_id=7)
    bob_fact = _fact(db, bob, "Bob's secret fact")
    page = _topic_page(db, world)
    page.citations = [{"n": 1, "chunk_id": secret.id}, {"n": 2, "fact_id": bob_fact.id}]
    db.commit()
    citations = knowledge_provenance.page_provenance_for_user(db, world["user"].id, page.id)["citations"]
    assert [row["available"] for row in citations] == [False, False]
    assert citations[0]["excerpt"] is None and citations[1]["fact"] is None


def test_entity_page_provenance_lists_its_own_entity(db, world):
    """An entity page lists its subject entity first, even before anything is cited."""
    page = wiki_service.create_page(db, world["user"].id, wiki_service.ENTITY, world["acme"].id)
    db.commit()
    result = knowledge_provenance.page_provenance_for_user(db, world["user"].id, page.id)
    assert [(row["name"], row["is_subject"]) for row in result["entities"]] == [("Acme", True)]
    assert [row["summary"] for row in result["facts"]] == [
        "Dana prefers the Acme renewal call on Tuesdays",
    ]


# --- Graph -----------------------------------------------------------------------


def test_graph_links_topics_to_entities_and_entities_to_each_other(db, world):
    """Topics link to entities their evidence mentions; relations link entities; wiki pages are named."""
    _entity(db, world["user"], "Lonely")  # no mentions: not shown
    page = _topic_page(db, world)
    db.commit()
    graph = knowledge_provenance.graph_for_user(db, world["user"].id)
    nodes = {node["id"]: node for node in graph["nodes"]}
    topic_id, acme_id, dana_id = (
        f"topic:{world['topic'].id}",
        f"entity:{world['acme'].id}",
        f"entity:{world['dana'].id}",
    )
    assert set(nodes) == {topic_id, acme_id, dana_id}
    assert nodes[topic_id]["wiki_page_id"] == page.id and nodes[dana_id]["entity_type"] == "person"
    edges = {(edge["source"], edge["target"], edge["kind"]): edge for edge in graph["edges"]}
    assert edges[(topic_id, acme_id, "evidence")]["weight"] == 1
    assert edges[(dana_id, acme_id, "relation")]["label"] == "works_with"


def test_graph_counts_cited_passages_and_respects_limits(db, world):
    """A topic page's cited passages link too, and the entity limit is applied."""
    other = _chunk(db, world["user"], "Initech joined the Acme deal.", owner_id=202)
    initech = _entity(db, world["user"], "Initech", [other])
    page = _topic_page(db, world)
    page.citations = [{"n": 1, "chunk_id": other.id}]
    db.commit()
    graph = knowledge_provenance.graph_for_user(db, world["user"].id)
    assert any(edge["target"] == f"entity:{initech.id}" and edge["kind"] == "evidence" for edge in graph["edges"])
    small = knowledge_provenance.graph_for_user(db, world["user"].id, topics=0, entities=1)
    assert len(small["nodes"]) == 1 and small["edges"] == []


def test_graph_links_every_topic_whose_page_cites_a_shared_passage(db, world):
    """A passage cited by two topic pages links both topics, not only the last page read."""
    shared = _chunk(db, world["user"], "Initech and Acme share the renewal.", owner_id=303)
    initech = _entity(db, world["user"], "Initech", [shared])
    second = _topic(db, world["user"], "Initech rollout")
    for topic in (world["topic"], second):
        page = wiki_service.create_page(db, world["user"].id, wiki_service.TOPIC, topic.id)
        page.citations = [{"n": 1, "chunk_id": shared.id}]
    db.commit()
    graph = knowledge_provenance.graph_for_user(db, world["user"].id)
    linked = {edge["source"] for edge in graph["edges"] if edge["target"] == f"entity:{initech.id}"}
    assert linked == {f"topic:{world['topic'].id}", f"topic:{second.id}"}


def test_graph_counts_a_cited_evidence_passage_once(db, world):
    """A passage that is both topic evidence and cited by the topic page weighs 1, not 2."""
    page = _topic_page(db, world)
    page.citations = [{"n": 1, "chunk_id": world["mail"].id}]
    db.commit()
    graph = knowledge_provenance.graph_for_user(db, world["user"].id)
    edge = next(edge for edge in graph["edges"] if edge["target"] == f"entity:{world['acme'].id}")
    assert edge["kind"] == "evidence" and edge["weight"] == 1


def test_graph_query_count_does_not_grow_with_passages(db, world):
    """The graph runs the same number of queries for 1 or 40 evidence passages (aggregated, no N+1)."""
    from sqlalchemy import event

    def statements() -> int:
        seen: list[str] = []
        bind = db.get_bind()
        listener = lambda *args: seen.append(args[2])  # noqa: E731
        event.listen(bind, "before_cursor_execute", listener)
        try:
            graph = knowledge_provenance.graph_for_user(db, world["user"].id)
        finally:
            event.remove(bind, "before_cursor_execute", listener)
        assert graph["nodes"]
        return len(seen)

    before = statements()
    acme = world["acme"]
    for index in range(40):
        chunk = _chunk(db, world["user"], f"Acme update {index}", owner_id=1000 + index)
        db.add(models.KnowledgeMention(user_id=world["user"].id, chunk_id=chunk.id, entity_id=acme.id))
        db.add(
            models.TopicEvidence(
                topic_id=world["topic"].id,
                source_type=models.TopicEvidenceSource.EMAIL,
                source_id=1000 + index,
                evidence_role=models.TopicEvidenceRole.SUPPORTING,
            )
        )
    db.commit()
    assert statements() == before
    graph = knowledge_provenance.graph_for_user(db, world["user"].id)
    edge = next(edge for edge in graph["edges"] if edge["target"] == f"entity:{acme.id}")
    assert edge["weight"] == 41


def test_graph_and_provenance_api(client, db, world, fake_llm):
    """Both endpoints answer for the owner; another user's page is a 404."""
    page = _topic_page(db, world)
    fake_llm.answers = ["Dana signs [1]."]
    wiki_synthesis.run_synthesis(db, world["user"].id, None)
    response = client.post("/api/auth/login", json={"username": "alice", "password": "secret"})
    assert response.status_code == 200
    provenance = client.get(f"/api/wiki/pages/{page.id}/provenance")
    assert provenance.status_code == 200 and provenance.json()["citations"][0]["kind"] == "passage"
    graph = client.get("/api/knowledge/graph", params={"entities": 5})
    assert graph.status_code == 200 and len(graph.json()["nodes"]) == 3
    assert client.get("/api/knowledge/graph", params={"entities": 1000}).status_code == 422
    assert client.get("/api/wiki/pages/999999/provenance").status_code == 404
