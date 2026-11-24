"""Schemas for agent skills."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class SkillCreate(BaseModel):
    """Schema for creating a skill."""

    name: str = Field(min_length=1, max_length=100, pattern=_SKILL_NAME_PATTERN)
    description: str
    license: str | None = None
    content: str = Field(min_length=1)


class SkillUpdate(BaseModel):
    """Schema for updating a skill."""

    name: str | None = Field(default=None, min_length=1, max_length=100, pattern=_SKILL_NAME_PATTERN)
    description: str | None = None
    license: str | None = None
    content: str | None = Field(default=None, min_length=1)


class SkillSummary(BaseModel):
    """Summary schema for listing skills."""

    id: int
    name: str
    description: str
    license: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SkillDetail(SkillSummary):
    """Detailed schema for reading a single skill."""

    content: str


class RemoteSkillSummary(BaseModel):
    """Normalized summary of a remotely discoverable skill."""

    remote_id: str
    name: str
    description: str = ""
    license: str | None = None
    content: str | None = None
    source_url: str | None = None


class SkillImportRequest(BaseModel):
    """Payload for importing a remote skill into local persisted skills."""

    remote_id: str = Field(min_length=1)
    name: str | None = None
    description: str | None = None
    license: str | None = None
    content: str | None = None


class SkillDesignAssistRequest(BaseModel):
    """Input payload for LLM-assisted skill authoring guidance."""

    goal: str = Field(min_length=1, max_length=2000)
    draft_name: str | None = Field(default=None, max_length=200)
    draft_description: str | None = Field(default=None, max_length=500)
    draft_content: str | None = Field(default=None, max_length=8000)


class SkillDesignAssistResponse(BaseModel):
    """Structured LLM output for manual skill design support."""

    suggested_name: str
    suggested_description: str
    suggested_content: str
    guidance: str
    follow_up_questions: list[str] = Field(default_factory=list)
