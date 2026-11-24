"""Schemas for the bounded adaptive character state (JAN-301)."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from janus.character import FOCUS_MAX_CHARS

Tone = Literal["neutral", "warm", "formal", "direct", "playful"]
Verbosity = Literal["brief", "balanced", "detailed"]
Proactivity = Literal["reactive", "balanced", "proactive"]


def clean_focus(value: str | None) -> str | None:
    """Collapse the focus to one trimmed line; blank means unset. Length is checked by the caller."""
    if value is None:
        return None
    collapsed = " ".join("".join(ch for ch in value if ch.isprintable() or ch.isspace()).split())
    return collapsed or None


def _check_dimension(default: str, allowed: Sequence[str]) -> None:
    """Keep the allowed list unique and the default inside it."""
    if len(set(allowed)) != len(allowed):
        raise ValueError("allowed values must be unique")
    if default not in allowed:
        raise ValueError("the default must be one of the allowed values")


class ToneDimension(BaseModel):
    """Allowed tones and the default tone."""

    default: Tone
    allowed: list[Tone] = Field(min_length=1)

    @model_validator(mode="after")
    def default_is_allowed(self) -> "ToneDimension":
        """Reject a default outside the allowed values."""
        _check_dimension(self.default, self.allowed)
        return self


class VerbosityDimension(BaseModel):
    """Allowed verbosity levels and the default."""

    default: Verbosity
    allowed: list[Verbosity] = Field(min_length=1)

    @model_validator(mode="after")
    def default_is_allowed(self) -> "VerbosityDimension":
        """Reject a default outside the allowed values."""
        _check_dimension(self.default, self.allowed)
        return self


class ProactivityDimension(BaseModel):
    """Allowed proactivity levels and the default."""

    default: Proactivity
    allowed: list[Proactivity] = Field(min_length=1)

    @model_validator(mode="after")
    def default_is_allowed(self) -> "ProactivityDimension":
        """Reject a default outside the allowed values."""
        _check_dimension(self.default, self.allowed)
        return self


class CharacterStateEnvelope(BaseModel):
    """Owner-set bounds and defaults of the adaptive state, stored with the character profile."""

    tone: ToneDimension = ToneDimension(default="neutral", allowed=["neutral", "warm", "formal", "direct", "playful"])
    verbosity: VerbosityDimension = VerbosityDimension(default="balanced", allowed=["brief", "balanced", "detailed"])
    proactivity: ProactivityDimension = ProactivityDimension(
        default="balanced", allowed=["reactive", "balanced", "proactive"]
    )
    #: Whether a ``current_focus`` may be set at all.
    focus_allowed: bool = True


class CharacterStateValues(BaseModel):
    """A complete adaptive state."""

    tone: Tone
    verbosity: Verbosity
    proactivity: Proactivity
    current_focus: str | None = Field(default=None, max_length=FOCUS_MAX_CHARS)

    @field_validator("current_focus", mode="before")
    @classmethod
    def normalize_focus(cls, value: object) -> object:
        """One trimmed line without control characters; blank means unset."""
        return clean_focus(value) if isinstance(value, str) else value


class CharacterStateRollback(BaseModel):
    """Restore the state an earlier audit row produced."""

    change_id: int


class CharacterStateChangeResponse(BaseModel):
    """One audit row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    revision: int
    actor: Literal["owner", "janus"]
    action: Literal["edit", "proposal", "rollback", "reset", "envelope"]
    chat_turn_id: str | None = None
    proposal_id: int | None = None
    rollback_of_id: int | None = None
    previous_state: CharacterStateValues
    new_state: CharacterStateValues
    created_at: datetime


class CharacterStateHistoryPage(BaseModel):
    """One page of the audit trail, newest first; ``next_before_id`` fetches the next older page."""

    items: list[CharacterStateChangeResponse]
    next_before_id: int | None = None


class CharacterStateProposalResponse(BaseModel):
    """One proposal Janus made in chat."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: Literal["proposed", "approved", "rejected"]
    changes: dict[str, str | None]
    rationale: str
    chat_turn_id: str | None = None
    conversation_id: int | None = None
    created_at: datetime
    decided_at: datetime | None = None


class CharacterStateResponse(BaseModel):
    """The owner's current state, its envelope and defaults."""

    state: CharacterStateValues
    defaults: CharacterStateValues
    envelope: CharacterStateEnvelope
    revision: int
    updated_at: datetime | None = None
    focus_max_chars: int = FOCUS_MAX_CHARS
