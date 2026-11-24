"""Tests for semantic memory/topic search (JAN-241): keyword index, embeddings, fallbacks."""

import re
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from janus import cli, database, models, security, status_service
from janus.main import api
from janus.services import embedding_service, knowledge_index, vector_index
from janus.services.memory_service import MemoryService
from janus.services.topic_service import TopicService

#: Words that share a "meaning" dimension in the fake embedding space.
_CONCEPTS = {
    "tea": 0,
    "coffee": 0,
    "beverage": 0,
    "drink": 0,
    "deadline": 1,
    "release": 1,
    "schedule": 1,
    "kitchen": 2,
    "renovation": 2,
    "contractor": 2,
}


class FakeEmbeddings:
    """Deterministic bag-of-concepts embeddings: related words map to the same axis."""

    def __init__(self, dimensions: int = 4):
        """Records calls; ``fail`` makes every call raise like a provider outage."""
        self.dimensions = dimensions
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []
        self.fail = False

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().replace(".", " ").split():
            axis = _CONCEPTS.get(word)
            if axis is not None:
                vector[axis] += 1.0
        vector[self.dimensions - 1] += 0.01  # never the zero vector
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embeds a batch of documents."""
        if self.fail:
            raise RuntimeError("embedding provider down")
        self.document_calls.append(list(texts))
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embeds one search query."""
        if self.fail:
            raise RuntimeError("embedding provider down")
        self.query_calls.append(text)
        return self._embed(text)


def _user(db, username: str = "alice") -> models.User:
    user = models.User(username=username, hashed_password=security.security_service.get_password_hash("secret"))
    db.add(user)
    db.commit()
    return user


def _configure(db, provider: str | None = "ollama", model: str | None = "fake-embed", min_similarity: float = 0.5):
    settings = db.query(models.Settings).first() or models.Settings()
    settings.embedding_provider = provider
    settings.embedding_model = model
    settings.embedding_min_similarity = min_similarity
    db.add(settings)
    db.commit()
    return settings


@pytest.fixture
def fake_embeddings(monkeypatch):
    """Replaces the configured provider with ``FakeEmbeddings``."""
    fake = FakeEmbeddings()
    monkeypatch.setattr(embedding_service, "build_embeddings", lambda settings, choice: fake)
    return fake


def _summaries(facts) -> list[str]:
    return [fact.summary for fact in facts]


# --- Keyword index -------------------------------------------------------------


def test_keyword_index_follows_create_update_and_delete(db):
    """The flush hook keeps FTS5 in sync with every write, without embeddings configured."""
    user = _user(db)
    service = MemoryService(db)
    tea = service.create_fact(user_id=user.id, summary="Prefers green tea in the morning")
    deadline = service.create_fact(user_id=user.id, summary="Janus release", detail="deadline in October")

    assert _summaries(service.list_facts(user.id, query="tea")) == ["Prefers green tea in the morning"]
    assert _summaries(service.list_facts(user.id, query="deadlines")) == []  # no stemming, prefix only
    assert _summaries(service.list_facts(user.id, query="dead")) == ["Janus release"]

    service.update_fact(user_id=user.id, fact_id=tea.id, summary="Prefers coffee")
    assert service.list_facts(user.id, query="tea") == []
    assert _summaries(service.list_facts(user.id, query="coffee")) == ["Prefers coffee"]

    service.delete_fact(user_id=user.id, fact_id=deadline.id)
    assert service.list_facts(user.id, query="october") == []


def test_keyword_search_is_owner_scoped(db):
    """One user's facts never match another user's search."""
    alice, bob = _user(db, "alice"), _user(db, "bob")
    service = MemoryService(db)
    service.create_fact(user_id=alice.id, summary="Alice likes tea")
    assert service.list_facts(bob.id, query="tea") == []


def test_keyword_table_backfills_existing_rows(db):
    """A missing FTS table is rebuilt from the owner table on first use (upgrade path)."""
    user = _user(db)
    MemoryService(db).create_fact(user_id=user.id, summary="Parking permit expires in May")
    vector_index.drop_keyword_tables(db)
    db.commit()

    assert _summaries(MemoryService(db).list_facts(user.id, query="permit")) == ["Parking permit expires in May"]


def test_search_lists_active_facts_before_archived(db):
    """Archived hits follow active ones when archived facts are included."""
    user = _user(db)
    service = MemoryService(db)
    first = service.create_fact(user_id=user.id, summary="Tea supplier one")
    second = service.create_fact(user_id=user.id, summary="Tea supplier two")
    merged, _ids = service.group_facts(user_id=user.id, fact_ids=[first.id, second.id], summary="Tea suppliers")

    active_only = service.list_facts(user.id, query="tea")
    assert [fact.id for fact in active_only] == [merged.id]
    with_archived = service.list_facts(user.id, query="tea", include_archived=True)
    assert with_archived[0].id == merged.id
    assert {fact.id for fact in with_archived[1:]} == {first.id, second.id}


def test_vector_search_runs_without_keyword_index(db, fake_embeddings, monkeypatch):
    """SQLite without FTS5 but with sqlite-vec still ranks by meaning (review finding on #429)."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    service.create_fact(user_id=user.id, summary="Kitchen renovation")
    monkeypatch.setattr(vector_index, "get_keyword_index", lambda db: None)

    assert _summaries(service.list_facts(user.id, query="beverage")) == ["Prefers green tea"]
    fake_embeddings.fail = True  # no ranking at all -> substring fallback
    assert _summaries(service.list_facts(user.id, query="renov")) == ["Kitchen renovation"]


def test_production_shape_keeps_substring_matches(db, fake_embeddings, monkeypatch):
    """sqlite-vec but no FTS5 (the Uberspace Python): substring hits are fused with vector hits, not dropped."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    service.create_fact(user_id=user.id, summary="Kitchen renovation")
    monkeypatch.setattr(vector_index, "get_keyword_index", lambda db: None)

    # "renov" is no word the embedder knows, so only the substring leg can find it.
    assert _summaries(service.list_facts(user.id, query="renov")) == ["Kitchen renovation"]
    assert _summaries(service.list_facts(user.id, query="beverage")) == ["Prefers green tea"]


def test_substring_search_without_keyword_index(db, monkeypatch):
    """Without FTS5 (e.g. PostgreSQL), search keeps the old ILIKE substring match."""
    user = _user(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Upcoming meeting with the landlord")
    monkeypatch.setattr(vector_index, "get_keyword_index", lambda db: None)

    assert _summaries(service.list_facts(user.id, query="eeting")) == ["Upcoming meeting with the landlord"]


# --- Embeddings and semantic search ----------------------------------------------


def test_semantic_search_finds_facts_without_shared_words(db, fake_embeddings):
    """With a model configured, a query matches by meaning and the cutoff drops unrelated facts."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea in the morning")
    service.create_fact(user_id=user.id, summary="Kitchen renovation starts Monday")

    results = service.list_facts(user.id, query="favourite beverage")
    assert _summaries(results) == ["Prefers green tea in the morning"]
    rows = db.query(models.KnowledgeEmbedding).all()
    assert {row.model for row in rows} == {"ollama/fake-embed"}
    assert {row.dimensions for row in rows} == {4}


def test_fused_ranking_prefers_items_matching_both_ways(db, fake_embeddings):
    """A fact that matches by keyword and by meaning outranks one matching only by meaning."""
    user = _user(db)
    _configure(db, min_similarity=0.1)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Coffee beans from the market")
    service.create_fact(user_id=user.id, summary="Tea from the market")

    assert _summaries(service.list_facts(user.id, query="tea"))[0] == "Tea from the market"


def test_query_embedding_failure_falls_back_to_keywords(db, fake_embeddings):
    """A provider outage at search time degrades to keyword search instead of failing."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    fake_embeddings.fail = True

    assert _summaries(service.list_facts(user.id, query="tea")) == ["Prefers green tea"]
    assert service.list_facts(user.id, query="beverage") == []


def test_inline_failure_leaves_the_fact_for_the_sweep(db, fake_embeddings):
    """A failed inline embedding never fails the write; the sweep embeds it later."""
    user = _user(db)
    _configure(db)
    fake_embeddings.fail = True
    fact = MemoryService(db).create_fact(user_id=user.id, summary="Prefers green tea")
    assert fact.id is not None
    assert db.query(models.KnowledgeEmbedding).count() == 0

    fake_embeddings.fail = False
    result = knowledge_index.sweep(db)
    assert (result.embedded, result.errors) == (1, [])
    assert db.query(models.KnowledgeEmbedding).count() == 1


def test_sweep_batches_and_reembeds_after_model_change(db, fake_embeddings):
    """The sweep embeds in batches, skips unchanged text, and re-embeds on a model change."""
    user = _user(db)
    for index in range(5):
        db.add(models.AgentMemoryFact(user_id=user.id, summary=f"Tea note {index}"))
    db.commit()
    _configure(db)

    first = knowledge_index.sweep(db, batch_size=2, max_batches=None)
    assert first.embedded == 5
    assert [len(call) for call in fake_embeddings.document_calls] == [2, 2, 1]
    assert knowledge_index.sweep(db).embedded == 0

    fact = db.query(models.AgentMemoryFact).first()
    fact.status = models.AgentMemoryStatus.ARCHIVED  # bumps updated_at, same text
    db.commit()
    unchanged = knowledge_index.sweep(db)
    assert (unchanged.embedded, unchanged.refreshed) == (0, 1)

    _configure(db, model="fake-embed-v2")
    assert knowledge_index.sweep(db, max_batches=None).embedded == 5
    assert {row.model for row in db.query(models.KnowledgeEmbedding)} == {"ollama/fake-embed-v2"}


def test_sweep_force_reembeds_everything(db, fake_embeddings):
    """``force`` recomputes current vectors too."""
    user = _user(db)
    _configure(db)
    MemoryService(db).create_fact(user_id=user.id, summary="Tea")
    assert knowledge_index.sweep(db, force=True).embedded == 1


def test_sweep_reports_provider_errors(db, fake_embeddings):
    """A provider error stops the sweep and is reported."""
    user = _user(db)
    db.add(models.AgentMemoryFact(user_id=user.id, summary="Tea"))
    db.commit()
    _configure(db)
    fake_embeddings.fail = True

    result = knowledge_index.sweep(db)
    assert result.embedded == 0
    assert result.errors == ["RuntimeError: embedding provider down"]


def test_deleting_a_fact_deletes_its_embedding(db, fake_embeddings):
    """The flush hook drops the embedding row with its owner."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    fact = service.create_fact(user_id=user.id, summary="Tea")
    assert db.query(models.KnowledgeEmbedding).count() == 1

    service.delete_fact(user_id=user.id, fact_id=fact.id)
    assert db.query(models.KnowledgeEmbedding).count() == 0


def test_semantic_topic_search(db, fake_embeddings):
    """Topics are embedded on create/update and searched by meaning."""
    user = _user(db)
    _configure(db)
    service = TopicService(db)
    kitchen = service.create_topic(user_id=user.id, title="Home project", summary="Kitchen renovation quotes")
    service.create_topic(user_id=user.id, title="Q4 planning", summary="Release schedule")

    assert [topic["title"] for topic in service.list_topics(user.id, query="contractor")] == ["Home project"]

    service.update_topic(user_id=user.id, topic_id=kitchen.id, summary="Buying a drink machine")
    assert [topic["title"] for topic in service.list_topics(user.id, query="beverage")] == ["Home project"]
    assert service.list_topics(user.id, query="contractor") == []


def test_no_embeddings_when_vector_search_is_unavailable(db, fake_embeddings, monkeypatch):
    """Without extension loading, nothing is embedded and search stays keyword-only."""
    monkeypatch.setattr(
        vector_index,
        "_capabilities",
        vector_index.SearchCapabilities(
            load_extension=False, sqlite_vec=False, sqlite_vec_version=None, fts5=True, error="no extensions"
        ),
    )
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")

    assert db.query(models.KnowledgeEmbedding).count() == 0
    assert fake_embeddings.document_calls == []
    assert knowledge_index.sweep(db).skipped_reason == "vector search is unavailable on this database"
    assert _summaries(service.list_facts(user.id, query="tea")) == ["Prefers green tea"]
    assert service.list_facts(user.id, query="beverage") == []

    component = status_service._knowledge_component_status(db)
    assert component["status"] == status_service.STATUS_DEGRADED
    assert "no extensions" in component["message"]


def test_status_reports_embeddings_off_as_operational(db):
    """Embeddings off is a valid configuration, not a degradation."""
    _configure(db, provider=None, model=None)
    component = status_service._knowledge_component_status(db)
    assert component["status"] == status_service.STATUS_OPERATIONAL
    assert component["subtitle"] == "embeddings off"


def test_status_reports_pending_embeddings_without_counts(db, fake_embeddings):
    """Status says whether embedding is still pending, never how many rows exist."""
    user = _user(db)
    db.add(models.AgentMemoryFact(user_id=user.id, summary="Tea"))
    db.commit()
    _configure(db)
    pending = status_service._knowledge_component_status(db)
    assert pending["status"] == status_service.STATUS_OPERATIONAL
    assert "still being embedded" in pending["message"]
    assert re.search(r"\b\d+\b", pending["message"]) is None  # no counts ("FTS5" is fine)

    knowledge_index.sweep(db)
    done = status_service._knowledge_component_status(db)
    assert "still being embedded" not in done["message"]


def test_capability_probe_in_this_environment():
    """CI's Python can load sqlite-vec and has FTS5 (production is checked at startup)."""
    capabilities = vector_index.probe_capabilities()
    assert capabilities.vector_search, capabilities.error
    assert capabilities.fts5


def test_fts_match_expression_quotes_every_token():
    """User text never reaches FTS5 as query syntax."""
    assert vector_index.fts_match_expression('tea OR "x" NEAR(') == '"tea"* OR "or"* OR "x"* OR "near"*'
    assert vector_index.fts_match_expression("!!!") is None


# --- Configuration -------------------------------------------------------------


def test_normalize_embedding_choice():
    """Empty disables, a bare provider gets its suggested model, unknown providers fail."""
    assert embedding_service.normalize_embedding_choice(None, "x") is None
    assert embedding_service.normalize_embedding_choice(" OpenAI ", "") == embedding_service.EmbeddingChoice(
        "openai", "text-embedding-3-small"
    )
    with pytest.raises(embedding_service.EmbeddingConfigError):
        embedding_service.normalize_embedding_choice("anthropic", None)


def test_build_embeddings_requires_the_provider_key(db):
    """Cloud providers reuse the stored chat key and fail clearly without one."""
    settings = _configure(db, provider="openai", model="text-embedding-3-small")
    with pytest.raises(embedding_service.EmbeddingConfigError):
        embedding_service.build_embeddings(settings, embedding_service.configured_choice(settings))
    settings.openai_api_key = security.security_service.encrypt("sk-test")
    client = embedding_service.build_embeddings(settings, embedding_service.configured_choice(settings))
    assert client.model == "text-embedding-3-small"


def test_settings_api_configures_embeddings(client, db):
    """PATCH stores provider/model/cutoff; a provider switch resets the model; empty disables."""
    user = _user(db)
    api.dependency_overrides[security.admin_required] = lambda: SimpleNamespace(id=user.id, is_admin=True)
    try:
        body = client.patch(
            "/api/planner/settings", json={"embedding_provider": "ollama", "embedding_min_similarity": 0.7}
        ).json()
        assert (body["embedding_provider"], body["embedding_model"]) == ("ollama", "nomic-embed-text")
        assert body["embedding_min_similarity"] == 0.7
        assert body["embedding_default_models"]["gemini"] == "gemini-embedding-001"
        assert body["vector_search_available"] is True

        body = client.patch("/api/planner/settings", json={"embedding_model": "mxbai-embed-large"}).json()
        assert body["embedding_model"] == "mxbai-embed-large"
        body = client.patch("/api/planner/settings", json={"embedding_provider": "openai"}).json()
        assert body["embedding_model"] == "text-embedding-3-small"

        assert client.patch("/api/planner/settings", json={"embedding_provider": "anthropic"}).status_code == 422
        assert client.patch("/api/planner/settings", json={"embedding_min_similarity": 2}).status_code == 422

        body = client.patch("/api/planner/settings", json={"embedding_provider": ""}).json()
        assert (body["embedding_provider"], body["embedding_model"]) == (None, None)
    finally:
        api.dependency_overrides.pop(security.admin_required, None)


def test_cli_reembed(db, fake_embeddings, monkeypatch, testing_session_local):
    """``janus knowledge reembed`` sweeps until done and reports the count."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    user = _user(db)
    db.add(models.AgentMemoryFact(user_id=user.id, summary="Tea"))
    db.commit()
    runner = CliRunner()

    skipped = runner.invoke(cli.app, ["knowledge", "reembed"])
    assert "Skipped: no embedding model is configured" in skipped.output

    _configure(db)
    result = runner.invoke(cli.app, ["knowledge", "reembed", "--force"])
    assert result.exit_code == 0, result.output
    assert "Embedded 1" in result.output


# --- Filter first, then rank (JAN-245) -------------------------------------------


def test_vector_only_matches_are_capped(db, fake_embeddings):
    """Cosine alone may add at most VECTOR_ONLY_LIMIT results; keyword matches are not capped."""
    user = _user(db)
    _configure(db, min_similarity=0.0)
    service = MemoryService(db)
    for index in range(8):
        service.create_fact(user_id=user.id, summary=f"Tea note {index}")

    by_meaning = service.list_facts(user.id, query="beverage")
    assert len(by_meaning) == knowledge_index.VECTOR_ONLY_LIMIT
    assert len(service.list_facts(user.id, query="tea")) == 8


def test_metadata_filters_apply_before_ranking(db, fake_embeddings):
    """Archived near-duplicates cannot use up the vector-only slots of an active-only search."""
    user = _user(db)
    _configure(db, min_similarity=0.0)
    service = MemoryService(db)
    for index in range(knowledge_index.VECTOR_ONLY_LIMIT + 2):
        db.add(
            models.AgentMemoryFact(
                user_id=user.id, summary=f"Tea tea tea {index}", status=models.AgentMemoryStatus.ARCHIVED
            )
        )
    db.commit()
    knowledge_index.sweep(db)
    # Weaker match than every archived fact, so post-filtering would lose it.
    service.create_fact(user_id=user.id, summary="Coffee in the kitchen")

    assert _summaries(service.list_facts(user.id, query="beverage")) == ["Coffee in the kitchen"]


def test_source_filter_narrows_search_and_listing(db, fake_embeddings):
    """``source`` keeps only facts from one origin, for searches and plain listings."""
    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    service.create_fact(user_id=user.id, summary="Drinks coffee daily", source=models.AgentMemorySource.AGENT)

    agent_only = service.list_facts(user.id, query="beverage", source=models.AgentMemorySource.AGENT)
    assert _summaries(agent_only) == ["Drinks coffee daily"]
    user_only = service.list_facts(user.id, source=models.AgentMemorySource.USER)
    assert _summaries(user_only) == ["Prefers green tea"]


def test_search_facts_tool_accepts_a_source(db, fake_embeddings):
    """The agent's ``search_facts`` tool forwards ``source``; unknown values are rejected."""
    from janus.agent_toolboxes.memory import build_memory_tools

    user = _user(db)
    _configure(db)
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    service.create_fact(user_id=user.id, summary="Drinks coffee daily", source=models.AgentMemorySource.AGENT)
    tools = {tool.name: tool for tool in build_memory_tools(SimpleNamespace(db=db, user=user))}

    results = tools["search_facts"].invoke({"query": "beverage", "source": "user"})
    assert [row["summary"] for row in results] == ["Prefers green tea"]
    with pytest.raises(ValueError):
        tools["search_facts"].invoke({"query": "beverage", "source": "rumour"})


def test_memory_api_source_filter(client, db):
    """``GET /api/memory/?source=`` filters by origin and rejects unknown values."""
    user = _user(db, "bob")
    service = MemoryService(db)
    service.create_fact(user_id=user.id, summary="Prefers green tea")
    service.create_fact(user_id=user.id, summary="Drinks coffee daily", source=models.AgentMemorySource.AGENT)
    response = client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]

    agent = client.get("/api/memory/", params={"source": "agent"})
    assert [row["summary"] for row in agent.json()] == ["Drinks coffee daily"]
    assert client.get("/api/memory/", params={"source": "rumour"}).status_code == 422
