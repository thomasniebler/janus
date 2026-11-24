"""Prompt/skills content is sourced from the character constitution and the flat skill library (JAN-201)."""

from janus import agent_runtime, models, security
from janus.schemas.character import CharacterProfilePayload, CharacterTrait
from janus.services.character_service import DEFAULT_CHARACTER, get_character_constitution, save_character_profile
from janus.services.dashboard_service import DashboardService


def _make_user(db, username: str) -> models.User:
    user = models.User(
        username=username,
        hashed_password=security.security_service.get_password_hash("secret"),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _save_pilot(db, user_id: int) -> None:
    save_character_profile(
        db,
        user_id,
        CharacterProfilePayload(
            name="Pilot",
            identity="Speaks plainly and double-checks sources.",
            core_values=["Be accurate"],
            non_negotiables=["Never guess dates"],
            traits=[CharacterTrait(name="initiative", baseline=0.7, max_delta=0.2, description="acts early")],
        ),
    )


def test_constitution_defaults_to_the_safe_default_character(db):
    """A user without a saved profile gets the same safe default GET /api/character shows."""
    user = _make_user(db, "default-character")

    constitution = get_character_constitution(db, user.id)

    assert constitution.constitution_id == f"user-{user.id}-character"
    assert constitution.version == 1
    assert constitution.name == DEFAULT_CHARACTER.name
    assert constitution.core_values == tuple(DEFAULT_CHARACTER.core_values)
    assert constitution.traits == ()


def test_constitution_reflects_the_persisted_profile_including_traits(db):
    """The runtime constitution mirrors the persisted profile, traits included."""
    user = _make_user(db, "persisted-character")
    _save_pilot(db, user.id)

    constitution = get_character_constitution(db, user.id)

    assert constitution.name == "Pilot"
    assert constitution.non_negotiables == ("Never guess dates",)
    assert [(t.name, t.baseline, t.max_delta, t.description) for t in constitution.traits] == [
        ("initiative", 0.7, 0.2, "acts early")
    ]
    assert "- initiative: 0.70 — acts early" in constitution.render_system_prompt()


def test_constitution_is_per_user(db):
    """One user's character never leaks into another user's prompt."""
    first = _make_user(db, "first-character-owner")
    second = _make_user(db, "second-character-owner")
    _save_pilot(db, first.id)

    assert get_character_constitution(db, first.id).name == "Pilot"
    assert get_character_constitution(db, second.id).name == DEFAULT_CHARACTER.name


def test_janus_system_prompt_uses_character_and_the_whole_skill_library(db):
    """Chat prompt = character + every stored skill."""
    user = _make_user(db, "prompt-owner")
    _save_pilot(db, user.id)
    db.add_all(
        [
            models.AgentSkill(name="Attached", description="d", content="Attached content."),
            models.AgentSkill(name="Unattached", description="d", content="Unattached content."),
        ]
    )
    db.commit()

    prompt = agent_runtime.build_janus_system_prompt(
        get_character_constitution(db, user.id),
        agent_runtime.load_skill_library(db),
    )

    assert prompt.startswith("# Character constitution: Pilot")
    assert "## Attached\n\nAttached content." in prompt
    assert "## Unattached\n\nUnattached content." in prompt
    assert "--- Tool Availability ---" in prompt


def test_briefing_context_sources_character(db, monkeypatch):
    """The briefing persona comes from the character constitution."""
    user = _make_user(db, "briefing-owner")
    _save_pilot(db, user.id)
    db.add(models.DashboardWidgetPreference(user_id=user.id, widget_key=models.DashboardWidgetKey.BRIEFING))
    db.commit()
    monkeypatch.setattr(
        "janus.services.dashboard_service.planner_service.PlannerService.get_briefing_payload",
        lambda self, **_kwargs: {"summary": "Today's plan", "topic_ids": [], "active_topics": []},
    )

    payload = DashboardService(db).get_briefing_generation_context(user)

    assert str(payload["character_prompt"]).startswith("# Character constitution: Pilot")
    assert "agent_system_prompt" not in payload
    assert "agent_default_model" not in payload
