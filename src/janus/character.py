"""Stable Janus character constitution and bounded adaptive state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from janus.prompt_injection import character_focus_block

#: Typed adaptive dimensions (JAN-301). Each value set is closed; the owner's
#: envelope (``services/character_state_service.py``) narrows it further.
TONES: tuple[str, ...] = ("neutral", "warm", "formal", "direct", "playful")
VERBOSITY_LEVELS: tuple[str, ...] = ("brief", "balanced", "detailed")
PROACTIVITY_LEVELS: tuple[str, ...] = ("reactive", "balanced", "proactive")
STATE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "tone": TONES,
    "verbosity": VERBOSITY_LEVELS,
    "proactivity": PROACTIVITY_LEVELS,
}
#: ``current_focus`` is free text (possibly proposed by Janus), so it is capped and rendered as data.
FOCUS_MAX_CHARS = 200

_DIMENSION_HINTS = {
    "tone": "how your replies sound",
    "verbosity": "how long your replies are",
    "proactivity": "how readily you suggest next steps unasked",
}


@dataclass(frozen=True)
class CharacterTrait:
    """One numeric tendency that may be adapted within a bounded range."""

    name: str
    baseline: float
    max_delta: float = 0.1
    description: str = ""

    def __post_init__(self) -> None:
        """Validate normalized trait bounds."""
        if not self.name.strip():
            raise ValueError("character trait name must not be blank")
        if not 0.0 <= self.baseline <= 1.0:
            raise ValueError("character trait baseline must be between 0 and 1")
        if not 0.0 <= self.max_delta <= 1.0:
            raise ValueError("character trait max_delta must be between 0 and 1")


@dataclass(frozen=True)
class CharacterConstitution:
    """Operator-controlled identity and non-negotiable character boundaries."""

    constitution_id: str
    version: int
    name: str
    identity: str
    core_values: tuple[str, ...]
    non_negotiables: tuple[str, ...]
    traits: tuple[CharacterTrait, ...]
    exploration_rate: float = 0.0
    #: Operator-written free text appended after the structured rules.
    additional_instructions: str = ""

    def __post_init__(self) -> None:
        """Validate stable identity, trait uniqueness, and exploration bounds."""
        if not self.constitution_id.strip():
            raise ValueError("constitution_id must not be blank")
        if self.version < 1:
            raise ValueError("constitution version must be at least 1")
        if not self.name.strip() or not self.identity.strip():
            raise ValueError("character name and identity must not be blank")
        if not self.core_values or not self.non_negotiables:
            raise ValueError("character constitution requires values and non-negotiables")
        if any(not value.strip() for value in (*self.core_values, *self.non_negotiables)):
            raise ValueError("character values and non-negotiables must not be blank")
        if len({trait.name for trait in self.traits}) != len(self.traits):
            raise ValueError("character trait names must be unique")
        if not 0.0 <= self.exploration_rate <= 1.0:
            raise ValueError("exploration_rate must be between 0 and 1")

    def render_system_prompt(self, state: CharacterState | None = None) -> str:
        """Render stable character rules plus bounded adaptive tendencies."""
        values = "\n".join(f"- {value}" for value in self.core_values)
        boundaries = "\n".join(f"- {rule}" for rule in self.non_negotiables)
        if state is not None:
            if state.constitution_id != self.constitution_id or state.constitution_version != self.version:
                raise ValueError("character state does not match constitution")
            trait_values = dict(state.trait_values)
            if set(trait_values) != {trait.name for trait in self.traits}:
                raise ValueError("character state traits do not match constitution")
        else:
            trait_values = {trait.name: trait.baseline for trait in self.traits}
        tendencies = "\n".join(
            f"- {trait.name}: {trait_values[trait.name]:.2f}{(' — ' + trait.description) if trait.description else ''}"
            for trait in self.traits
        )
        adaptive_state = _render_adaptive_state(state)
        additional = (
            f"## Additional instructions\n{self.additional_instructions.strip()}\n\n"
            if self.additional_instructions.strip()
            else ""
        )
        return (
            f"# Character constitution: {self.name}\n\n"
            f"{self.identity}\n\n"
            "## Core values\n"
            f"{values}\n\n"
            "## Non-negotiables\n"
            f"{boundaries}\n\n"
            f"{additional}"
            "## Adaptive tendencies\n"
            f"{tendencies}\n\n"
            f"{adaptive_state}"
            "Adaptive tendencies influence style and initiative only. They never "
            "override the constitution, safety rules, authorization, or user controls."
        )


@dataclass(frozen=True)
class CharacterState:
    """Versioned adaptive values associated with one constitution."""

    constitution_id: str
    constitution_version: int
    adaptation_version: int
    trait_values: tuple[tuple[str, float], ...]
    #: Typed adaptive dimensions (JAN-301); ``None`` leaves the dimension unrendered.
    tone: str | None = None
    verbosity: str | None = None
    proactivity: str | None = None
    #: Short, untrusted free text; rendered only inside a ``<character_focus>`` data block.
    current_focus: str | None = None

    def __post_init__(self) -> None:
        """Validate the state identity, normalized values, and typed dimensions."""
        for dimension, options in STATE_DIMENSIONS.items():
            value = getattr(self, dimension)
            if value is not None and value not in options:
                raise ValueError(f"character state {dimension} must be one of: {', '.join(options)}")
        if self.current_focus is not None and len(self.current_focus) > FOCUS_MAX_CHARS:
            raise ValueError(f"character state current_focus must be at most {FOCUS_MAX_CHARS} characters")
        if not self.constitution_id.strip():
            raise ValueError("character state constitution_id must not be blank")
        if self.constitution_version < 1 or self.adaptation_version < 0:
            raise ValueError("character state versions are invalid")
        if any(not name.strip() or not 0.0 <= value <= 1.0 for name, value in self.trait_values):
            raise ValueError("character state trait values must be normalized")
        if len({name for name, _value in self.trait_values}) != len(self.trait_values):
            raise ValueError("character state trait names must be unique")


def _render_adaptive_state(state: CharacterState | None) -> str:
    """Render the owner-bounded typed state (JAN-301); the focus only as a data block."""
    if state is None:
        return ""
    lines = [
        f"- {dimension}: {getattr(state, dimension)} ({_DIMENSION_HINTS[dimension]})"
        for dimension in STATE_DIMENSIONS
        if getattr(state, dimension) is not None
    ]
    focus = (state.current_focus or "").strip()
    if not lines and not focus:
        return ""
    body = "\n".join(lines)
    if focus:
        body = f"{body}\n\n{character_focus_block(focus)}" if body else character_focus_block(focus)
    return f"## Current adaptive state\n{body}\n\n"


AdaptationSource = Literal["brain", "exploration"]


@dataclass(frozen=True)
class CharacterAdaptationProposal:
    """A requested change to adaptive tendencies, never to authority."""

    changes: tuple[tuple[str, float], ...]
    rationale: str
    source: AdaptationSource = "brain"

    def __post_init__(self) -> None:
        """Reject ambiguous proposals before applying them."""
        if not self.rationale.strip():
            raise ValueError("character adaptation rationale must not be blank")
        names = [name for name, _value in self.changes]
        if any(not name.strip() for name in names):
            raise ValueError("character adaptation dimensions must not be blank")
        if len(names) != len(set(names)):
            raise ValueError("character adaptation dimensions must be unique")


@dataclass(frozen=True)
class CharacterAdaptationDenial:
    """Auditable reason why an adaptation request was constrained."""

    dimension: str
    reason: str


@dataclass(frozen=True)
class CharacterAdaptationDecision:
    """Result of applying a proposal to adaptive character state."""

    state: CharacterState
    accepted_dimensions: tuple[str, ...] = ()
    denials: tuple[CharacterAdaptationDenial, ...] = ()


class CharacterRuntime:
    """Apply bounded character adaptations and optional controlled exploration."""

    def __init__(self, constitution: CharacterConstitution) -> None:
        """Create an adapter for one immutable constitution."""
        self.constitution = constitution
        self._traits = {trait.name: trait for trait in constitution.traits}

    def initial_state(self) -> CharacterState:
        """Return the constitution's baseline adaptive state."""
        return CharacterState(
            constitution_id=self.constitution.constitution_id,
            constitution_version=self.constitution.version,
            adaptation_version=0,
            trait_values=tuple((trait.name, trait.baseline) for trait in self.constitution.traits),
        )

    def apply(self, state: CharacterState, proposal: CharacterAdaptationProposal) -> CharacterAdaptationDecision:
        """Apply only known, adaptable, delta-bounded trait changes."""
        self._require_state(state)
        current = dict(state.trait_values)
        accepted: list[str] = []
        denials: list[CharacterAdaptationDenial] = []
        for dimension, requested in proposal.changes:
            trait = self._traits.get(dimension)
            if trait is None:
                denials.append(CharacterAdaptationDenial(dimension, "unknown_dimension"))
                continue
            if not 0.0 <= requested <= 1.0:
                denials.append(CharacterAdaptationDenial(dimension, "invalid_value"))
                continue
            bounded = max(current[dimension] - trait.max_delta, min(current[dimension] + trait.max_delta, requested))
            current[dimension] = bounded
            accepted.append(dimension)
            if bounded != requested:
                denials.append(CharacterAdaptationDenial(dimension, "bounded_delta"))

        next_state = state
        if accepted:
            next_state = replace(
                state,
                adaptation_version=state.adaptation_version + 1,
                trait_values=tuple((trait.name, current[trait.name]) for trait in self.constitution.traits),
            )
        return CharacterAdaptationDecision(next_state, tuple(accepted), tuple(denials))

    def explore(self, state: CharacterState, random_source: Callable[[], float]) -> CharacterAdaptationDecision:
        """Optionally make one bounded, self-directed style change.

        The injected source makes this behavior testable and auditable. The
        constitution controls the exploration rate and every resulting change
        still passes through the same bounded adaptation path.
        """
        self._require_state(state)
        chance = self._random_value(random_source)
        if chance >= self.constitution.exploration_rate or not self._traits:
            return CharacterAdaptationDecision(state)
        dimension_roll = self._random_value(random_source)
        direction_roll = self._random_value(random_source)
        trait_names = tuple(self._traits)
        dimension = trait_names[min(int(dimension_roll * len(trait_names)), len(trait_names) - 1)]
        current = dict(state.trait_values)[dimension]
        trait = self._traits[dimension]
        target = current + (trait.max_delta if direction_roll >= 0.5 else -trait.max_delta)
        proposal = CharacterAdaptationProposal(
            ((dimension, max(0.0, min(1.0, target))),), "bounded self-directed exploration", "exploration"
        )
        return self.apply(state, proposal)

    def _require_state(self, state: CharacterState) -> None:
        if (
            state.constitution_id != self.constitution.constitution_id
            or state.constitution_version != self.constitution.version
        ):
            raise ValueError("character state does not match constitution")

    @staticmethod
    def _random_value(random_source: Callable[[], float]) -> float:
        value = random_source()
        if not 0.0 <= value < 1.0:
            raise ValueError("character exploration source must return values in [0, 1)")
        return value


__all__ = [
    "CharacterAdaptationDecision",
    "CharacterAdaptationDenial",
    "CharacterAdaptationProposal",
    "CharacterConstitution",
    "CharacterRuntime",
    "CharacterState",
    "CharacterTrait",
    "FOCUS_MAX_CHARS",
    "PROACTIVITY_LEVELS",
    "STATE_DIMENSIONS",
    "TONES",
    "VERBOSITY_LEVELS",
]
