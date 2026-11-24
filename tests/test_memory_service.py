"""Unit tests for MemoryService and the /api/memory endpoints."""

from datetime import datetime, timedelta

import pytest

from janus import models, security, time_utils
from janus.services.memory_service import MemoryService

# db, client, and auth_client fixtures come from conftest.py.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db, username: str = "alice") -> models.User:
    """Create and persist a test user, returning it."""
    hashed_pw = security.security_service.get_password_hash("secret")
    user = models.User(username=username, hashed_password=hashed_pw, is_admin=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_fact(
    db,
    user_id: int,
    *,
    summary: str = "test fact",
    detail: str | None = None,
) -> models.AgentMemoryFact:
    """Create and persist a memory fact directly, returning it."""
    svc = MemoryService(db)
    return svc.create_fact(user_id=user_id, summary=summary, detail=detail)


def _naive(dt: datetime) -> datetime:
    """Strip timezone info to match SQLite's naive datetime storage format."""
    return dt.replace(tzinfo=None) if dt is not None else dt


# ---------------------------------------------------------------------------
# MemoryService.create_fact
# ---------------------------------------------------------------------------


def test_create_fact_stores_and_returns_fact(db):
    """create_fact persists a new memory fact in the database."""
    user = _make_user(db)
    svc = MemoryService(db)
    fact = svc.create_fact(user_id=user.id, summary="Prefers dark mode")
    assert fact.id is not None
    assert fact.summary == "Prefers dark mode"
    assert fact.source == models.AgentMemorySource.USER
    assert fact.status == models.AgentMemoryStatus.ACTIVE


def test_create_fact_blank_summary_raises(db):
    """create_fact raises ValueError when summary is blank or whitespace-only."""
    user = _make_user(db)
    svc = MemoryService(db)
    with pytest.raises(ValueError, match="summary"):
        svc.create_fact(user_id=user.id, summary="   ")


def test_create_fact_empty_summary_raises(db):
    """create_fact raises ValueError when summary is an empty string."""
    user = _make_user(db)
    svc = MemoryService(db)
    with pytest.raises(ValueError):
        svc.create_fact(user_id=user.id, summary="")


def test_create_fact_deduplicates_existing_active_fact(db):
    """Creating a fact with the same summary updates the existing record."""
    user = _make_user(db)
    svc = MemoryService(db)
    first = svc.create_fact(user_id=user.id, summary="I drink tea", detail="green tea")
    second = svc.create_fact(user_id=user.id, summary="I drink tea", detail="black tea now")
    assert first.id == second.id
    assert second.detail == "black tea now"


def test_create_fact_deduplicates_ignoring_case(db):
    """The summary match that refreshes an existing fact ignores case."""
    user = _make_user(db)
    svc = MemoryService(db)
    first = svc.create_fact(user_id=user.id, summary="I drink tea")
    second = svc.create_fact(user_id=user.id, summary="I DRINK TEA")
    assert first.id == second.id


@pytest.mark.parametrize(
    ("existing", "new"),
    [
        ("Budget is 50 percent spent", "Budget is 50% spent"),
        ("Room abc is booked", "Room a_c is booked"),
        ("Path C:/x is shared", "Path C:\\x is shared"),
    ],
)
def test_create_fact_treats_like_wildcards_as_text(db, existing, new):
    """Regression (JAN-224): `%`, `_` and a backslash in a summary are text, not LIKE wildcards.

    The dedupe lookup used the new summary as an ``ILIKE`` pattern, so saving
    "Budget is 50% spent" silently refreshed "Budget is 50 percent spent"
    instead of storing a second fact.
    """
    user = _make_user(db)
    svc = MemoryService(db)
    first = svc.create_fact(user_id=user.id, summary=existing, detail="original")
    second = svc.create_fact(user_id=user.id, summary=new, detail="new")
    assert second.id != first.id
    db.refresh(first)
    assert first.summary == existing
    assert first.detail == "original"
    assert second.summary == new
    # The same wildcard summary again is still deduplicated.
    assert svc.create_fact(user_id=user.id, summary=new).id == second.id


# ---------------------------------------------------------------------------
# MemoryService.list_facts — updated_at must not change on read
# ---------------------------------------------------------------------------


def test_list_facts_touch_accessed_does_not_change_updated_at(db):
    """Touching last_accessed_at must NOT bump updated_at on the fact row."""
    user = _make_user(db)
    fact = _make_fact(db, user.id, summary="remember timezone preference")

    original_updated_at = _naive(fact.updated_at)

    # Expire the identity-map entry so the next access hits the DB.
    db.expire(fact)

    svc = MemoryService(db)
    results = svc.list_facts(user.id, touch_accessed=True)
    assert len(results) == 1

    db.expire(results[0])
    db.refresh(results[0])

    assert _naive(results[0].updated_at) == original_updated_at, (
        "list_facts() must not bump updated_at when updating last_accessed_at"
    )


def test_list_facts_touch_accessed_updates_last_accessed_at(db):
    """Touching last_accessed_at sets the field to a recent timestamp."""
    user = _make_user(db)
    fact = _make_fact(db, user.id, summary="user's working hours")

    # Manually push last_accessed_at into the past so the update is visible.
    old_ts = _naive(time_utils.utcnow()) - timedelta(hours=1)
    fact.last_accessed_at = old_ts
    db.commit()

    svc = MemoryService(db)
    results = svc.list_facts(user.id, touch_accessed=True)
    assert len(results) == 1

    db.expire(results[0])
    db.refresh(results[0])
    assert _naive(results[0].last_accessed_at) > old_ts


def test_list_facts_no_touch_does_not_update_last_accessed_at(db):
    """When touch_accessed=False, last_accessed_at is not modified."""
    user = _make_user(db)
    fact = _make_fact(db, user.id, summary="user's language preference")

    old_ts = _naive(time_utils.utcnow()) - timedelta(hours=2)
    fact.last_accessed_at = old_ts
    db.commit()

    svc = MemoryService(db)
    svc.list_facts(user.id, touch_accessed=False)

    db.expire(fact)
    db.refresh(fact)
    assert _naive(fact.last_accessed_at) == old_ts


# ---------------------------------------------------------------------------
# /api/memory POST — blank summary returns 422
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_client(client, db):
    """Returns an authenticated TestClient for API tests."""
    _make_user(db, username="bob")
    response = client.post("/api/auth/login", json={"username": "bob", "password": "secret"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_create_memory_fact_blank_summary_returns_422(auth_client):
    """POST /api/memory/ with a blank summary returns 422 Unprocessable Entity."""
    response = auth_client.post("/api/memory/", json={"summary": "   "})
    assert response.status_code == 422


def test_create_memory_fact_empty_summary_returns_422(auth_client):
    """POST /api/memory/ with an empty string summary returns 422."""
    response = auth_client.post("/api/memory/", json={"summary": ""})
    assert response.status_code == 422


def test_create_memory_fact_valid_summary_returns_201(auth_client):
    """POST /api/memory/ with a valid summary returns 201 and the created fact."""
    response = auth_client.post("/api/memory/", json={"summary": "Prefers bullet-point summaries"})
    assert response.status_code == 201
    data = response.json()
    assert data["summary"] == "Prefers bullet-point summaries"
    assert data["status"] == "active"
    assert data["source"] == "user"


# ---------------------------------------------------------------------------
# MemoryService._run_compaction — source_ids resilience to string IDs
# ---------------------------------------------------------------------------


def test_compaction_source_ids_accepts_string_integers(db, testing_session_local):
    """_run_compaction handles string IDs from LLM responses alongside plain ints."""
    from unittest.mock import MagicMock, patch

    from janus.services.memory_service import MemoryService

    user = _make_user(db, username="compact_user")
    svc = MemoryService(db)
    fact_a = svc.create_fact(user_id=user.id, summary="Fact A")
    fact_b = svc.create_fact(user_id=user.id, summary="Fact B")

    # Simulate an LLM returning source_ids as strings instead of ints.
    fake_suggestions = [
        {
            "source_ids": [str(fact_a.id), str(fact_b.id)],
            "summary": "Combined fact",
            "detail": None,
        }
    ]

    run = models.AgentMemoryCompactionRun(user_id=user.id)
    db.add(run)
    db.commit()
    db.refresh(run)
    run_id = run.id
    fact_a_id = fact_a.id
    fact_b_id = fact_b.id
    user_id = user.id

    with patch("janus.services.memory_service.llm_service") as mock_llm_svc:
        mock_llm_instance = MagicMock()
        mock_llm_instance.suggest_memory_compactions.return_value = fake_suggestions
        mock_llm_svc.build_llm_service.return_value = mock_llm_instance

        with patch("janus.services.memory_service.database.SessionLocal", testing_session_local):
            # _run_compaction creates and closes its own session via SessionLocal();
            # we patch SessionLocal to the test factory so it shares the same DB.
            MemoryService._run_compaction(run_id)

    # Verify results using the caller's test session.
    refreshed_a = db.get(models.AgentMemoryFact, fact_a_id)
    refreshed_b = db.get(models.AgentMemoryFact, fact_b_id)
    db.expire(refreshed_a)
    db.expire(refreshed_b)
    db.refresh(refreshed_a)
    db.refresh(refreshed_b)
    assert refreshed_a.status == models.AgentMemoryStatus.ARCHIVED
    assert refreshed_b.status == models.AgentMemoryStatus.ARCHIVED
    merged = (
        db.query(models.AgentMemoryFact)
        .filter(
            models.AgentMemoryFact.user_id == user_id,
            models.AgentMemoryFact.summary == "Combined fact",
        )
        .first()
    )
    assert merged is not None
    assert merged.status == models.AgentMemoryStatus.ACTIVE


# ---------------------------------------------------------------------------
# /api/memory/{id} PATCH — edit a fact (JAN-224)
# ---------------------------------------------------------------------------


def test_update_memory_fact_edits_summary_and_detail(auth_client):
    """PATCH edits the summary and detail; an omitted field stays; an empty detail clears it."""
    created = auth_client.post("/api/memory/", json={"summary": "Prefers tea", "detail": "green"}).json()

    response = auth_client.patch(
        f"/api/memory/{created['id']}", json={"summary": "Prefers  coffee ", "detail": "black"}
    )
    assert response.status_code == 200
    assert response.json()["summary"] == "Prefers coffee"
    assert response.json()["detail"] == "black"

    response = auth_client.patch(f"/api/memory/{created['id']}", json={"detail": "oat milk"})
    assert response.status_code == 200
    assert response.json()["summary"] == "Prefers coffee"
    assert response.json()["detail"] == "oat milk"

    response = auth_client.patch(f"/api/memory/{created['id']}", json={"detail": ""})
    assert response.status_code == 200
    assert response.json()["detail"] is None

    listed = auth_client.get("/api/memory/").json()
    assert [(fact["id"], fact["summary"]) for fact in listed] == [(created["id"], "Prefers coffee")]


@pytest.mark.parametrize("payload", [{"summary": "   "}, {"summary": ""}, {}])
def test_update_memory_fact_rejects_blank_or_empty_edits(auth_client, payload):
    """A blank summary, or an edit that changes nothing, is a 422 and leaves the fact alone."""
    created = auth_client.post("/api/memory/", json={"summary": "Keeps a paper diary"}).json()
    response = auth_client.patch(f"/api/memory/{created['id']}", json=payload)
    assert response.status_code == 422
    assert auth_client.get("/api/memory/").json()[0]["summary"] == "Keeps a paper diary"


def test_update_memory_fact_missing_or_foreign_returns_404(auth_client, db):
    """Another user's fact and a missing id answer the same 404."""
    other = _make_user(db, username="carol")
    foreign = MemoryService(db).create_fact(user_id=other.id, summary="Carol's secret")

    for fact_id in (foreign.id, 999_999):
        response = auth_client.patch(f"/api/memory/{fact_id}", json={"summary": "hijacked"})
        assert response.status_code == 404
        assert response.json()["detail"] == "Memory fact not found"
    db.refresh(foreign)
    assert foreign.summary == "Carol's secret"
