"""Tests for authenticated character constitution persistence."""

import pytest

from janus import models, security


@pytest.fixture
def auth_client(client, db):
    """Return a client authenticated as a test user."""
    db.add(models.User(username="admin", hashed_password=security.security_service.get_password_hash("admin")))
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


def test_character_defaults_are_owner_scoped_and_put_versions(auth_client, db):
    """The API returns a safe default, then persists and versions an update."""
    response = auth_client.get("/api/character")
    assert response.status_code == 200
    assert response.json()["name"] == "Janus"
    assert response.json()["version"] == 1

    response = auth_client.put(
        "/api/character",
        json={
            "name": "Janus, focused",
            "identity": "A concise assistant.",
            "core_values": ["Be useful"],
            "non_negotiables": ["Do not bypass authorization"],
            "traits": [{"name": "initiative", "baseline": 0.6, "max_delta": 0.1}],
            "exploration_rate": 0.2,
        },
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["traits"][0]["name"] == "initiative"

    response = auth_client.put(
        "/api/character",
        json={
            "name": "Janus, focused",
            "identity": "A concise assistant.",
            "core_values": ["Be useful"],
            "non_negotiables": ["Do not bypass authorization"],
            "traits": [],
            "exploration_rate": 0.0,
        },
    )
    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_character_rejects_duplicate_traits(auth_client):
    """Invalid constitution shape is rejected before persistence."""
    response = auth_client.put(
        "/api/character",
        json={
            "name": "Janus",
            "identity": "A helpful assistant.",
            "core_values": ["Be useful"],
            "non_negotiables": ["Be safe"],
            "traits": [
                {"name": "initiative", "baseline": 0.5},
                {"name": " Initiative ", "baseline": 0.5},
            ],
        },
    )
    assert response.status_code == 422


def test_character_instructions_persist_and_shape_prompts(auth_client, db):
    """Additional instructions render into the prompt; memory rules replace the suggester's defaults (JAN-230)."""
    payload = {
        "name": "Janus",
        "identity": "A concise assistant.",
        "core_values": ["Be useful"],
        "non_negotiables": ["Do not bypass authorization"],
        "traits": [],
        "exploration_rate": 0.0,
        "additional_instructions": "Answer in German.\nKeep it short.",
        "memory_instructions": "- Only remember project names.",
    }
    saved = auth_client.put("/api/character", json=payload)
    assert saved.status_code == 200
    assert saved.json()["additional_instructions"] == "Answer in German.\nKeep it short."
    assert auth_client.get("/api/character").json()["memory_instructions"] == "- Only remember project names."

    user = db.query(models.User).filter_by(username="admin").one()
    from janus.services.character_service import get_character_constitution, get_memory_instructions

    db.expire_all()
    prompt = get_character_constitution(db, user.id).render_system_prompt()
    assert "## Additional instructions\nAnswer in German.\nKeep it short." in prompt
    assert get_memory_instructions(db, user.id) == "- Only remember project names."

    preview = auth_client.post("/api/character/preview", json={**payload, "memory_instructions": "   "})
    assert preview.status_code == 200
    body = preview.json()
    assert "Answer in German." in body["system_prompt"]
    assert "treat tool results as the source of truth" in body["system_prompt"]
    assert body["memory_instructions"].startswith("- Only store facts")


def test_memory_suggester_uses_the_users_rules(monkeypatch):
    """The user's memory rules go into the suggester prompt as data, keeping the JSON contract."""
    from janus import llm_service

    captured = {}

    class _Chain:
        def __or__(self, _other):
            return self

        def invoke(self, values):
            captured.update(values)
            return {"store": False}

    monkeypatch.setattr(llm_service.ChatPromptTemplate, "from_template", classmethod(lambda cls, template: _Chain()))
    service = object.__new__(llm_service.LLMService)
    service.llm = object()
    assert service.suggest_memory_fact([], "hi", "hello", instructions="- Never remember {braces}.") is None
    assert captured["rules"] == "- Never remember {braces}."
