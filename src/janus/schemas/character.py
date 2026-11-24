"""Schemas for user-owned character constitutions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CharacterTrait(BaseModel):
    """One bounded adaptive tendency."""

    name: str = Field(min_length=1, max_length=80)
    baseline: float = Field(ge=0.0, le=1.0)
    max_delta: float = Field(default=0.1, ge=0.0, le=1.0)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """Reject blank names and normalize surrounding whitespace."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("trait name must not be blank")
        return normalized


class CharacterProfilePayload(BaseModel):
    """Validated user-editable constitution fields."""

    name: str = Field(min_length=1, max_length=120)
    identity: str = Field(min_length=1, max_length=5000)
    core_values: list[str] = Field(min_length=1, max_length=12)
    non_negotiables: list[str] = Field(min_length=1, max_length=12)
    traits: list[CharacterTrait] = Field(default_factory=list, max_length=20)
    exploration_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    #: Free-text instructions appended to the rendered constitution (JAN-230).
    additional_instructions: str | None = Field(default=None, max_length=10000)
    #: Replaces the built-in rules for which chat facts get remembered (JAN-230).
    memory_instructions: str | None = Field(default=None, max_length=5000)

    @field_validator("additional_instructions", "memory_instructions")
    @classmethod
    def optional_text_blank_to_none(cls, value: str | None) -> str | None:
        """Keep free text verbatim (line breaks matter) but store blank as unset."""
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("name", "identity", mode="before")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        """Normalize required text and reject blank values."""
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("core_values", "non_negotiables")
    @classmethod
    def normalize_rules(cls, values: list[str]) -> list[str]:
        """Trim constitution rules while preserving their order."""
        normalized = [" ".join(value.split()) for value in values]
        if any(not value for value in normalized):
            raise ValueError("constitution values must not be blank")
        return normalized

    @model_validator(mode="after")
    def trait_names_must_be_unique(self) -> "CharacterProfilePayload":
        """Reject duplicate adaptive tendency names."""
        names = [trait.name.casefold() for trait in self.traits]
        if len(names) != len(set(names)):
            raise ValueError("character trait names must be unique")
        return self


class CharacterPromptPreview(BaseModel):
    """What the runtime would actually send for a (saved or draft) character."""

    system_prompt: str
    memory_instructions: str


class CharacterProfileResponse(CharacterProfilePayload):
    """Serialized character constitution returned to its owner."""

    model_config = ConfigDict(from_attributes=True)

    constitution_id: str
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
