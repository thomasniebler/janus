"""Tests for stable Janus character constitution and bounded adaptation."""

import pytest

from janus.character import CharacterAdaptationProposal, CharacterConstitution, CharacterRuntime, CharacterTrait


def _constitution() -> CharacterConstitution:
    """Build a small constitution for character tests."""
    return CharacterConstitution(
        constitution_id="janus-default",
        version=1,
        name="Janus",
        identity="A curious, candid personal operating companion.",
        core_values=("Be useful", "Be honest"),
        non_negotiables=("Never invent tool results", "Never self-authorize permissions"),
        traits=(
            CharacterTrait("initiative", 0.5, max_delta=0.1, description="willingness to act"),
            CharacterTrait("playfulness", 0.3, max_delta=0.2, description="lightness of expression"),
        ),
    )


def test_character_prompt_keeps_constitution_and_adaptive_traits_distinct():
    """Rendered prompts contain both stable rules and current tendencies."""
    runtime = CharacterRuntime(_constitution())

    prompt = runtime.constitution.render_system_prompt(runtime.initial_state())

    assert "Never self-authorize permissions" in prompt
    assert "initiative: 0.50" in prompt
    assert "Adaptive tendencies influence style" in prompt


def test_brain_adaptation_is_bounded_and_cannot_add_unknown_authority():
    """Self-proposed changes are constrained to known adaptive dimensions."""
    runtime = CharacterRuntime(_constitution())
    state = runtime.initial_state()

    decision = runtime.apply(
        state,
        CharacterAdaptationProposal(
            changes=(("initiative", 1.0), ("permissions", 1.0), ("playfulness", -1.0)),
            rationale="Try a more proactive mode",
        ),
    )

    values = dict(decision.state.trait_values)
    assert values["initiative"] == 0.6
    assert values["playfulness"] == 0.3
    assert decision.state.adaptation_version == 1
    assert [(denial.dimension, denial.reason) for denial in decision.denials] == [
        ("initiative", "bounded_delta"),
        ("permissions", "unknown_dimension"),
        ("playfulness", "invalid_value"),
    ]


def test_character_state_must_match_its_constitution():
    """Adaptation state cannot be applied across constitution versions."""
    runtime = CharacterRuntime(_constitution())
    state = runtime.initial_state()
    foreign = state.__class__("other", state.constitution_version, 0, state.trait_values)

    with pytest.raises(ValueError, match="does not match constitution"):
        runtime.apply(foreign, CharacterAdaptationProposal((("initiative", 0.6),), "test"))
