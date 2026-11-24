"""Schemas for tracked-topic API payloads."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from janus import models


class TopicSummary(BaseModel):
    """Compact tracked-topic payload used in lists and summaries."""

    id: int
    title: str
    summary: str | None = None
    marker_keywords: list[str] = []
    status: models.TopicStatus
    pinned: bool
    user_confirmed: bool
    origin: models.TopicOrigin
    confidence: models.ConfidenceLevel | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    snoozed_until: datetime | None = None
    last_briefed_at: datetime | None = None
    last_digested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    evidence_count: int = 0


class TopicEvidenceResponse(BaseModel):
    """One evidence row linked to a tracked topic."""

    id: int
    source_type: models.TopicEvidenceSource
    source_id: int | None = None
    excerpt: str | None = None
    evidence_role: models.TopicEvidenceRole
    created_at: datetime


class TopicDetail(TopicSummary):
    """Expanded tracked-topic payload with evidence rows."""

    evidence: list[TopicEvidenceResponse]


class TopicCreate(BaseModel):
    """Payload for creating a manual tracked topic."""

    title: str
    summary: str
    marker_keywords: list[str]

    @field_validator("title", "summary")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        """Reject blank required text fields."""
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("field must not be blank")
        return cleaned

    @field_validator("marker_keywords")
    @classmethod
    def marker_keywords_must_not_be_empty(cls, value: list[str]) -> list[str]:
        """Require at least one non-blank marker keyword."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for keyword in value:
            normalized = " ".join(keyword.split())
            if not normalized:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("marker_keywords must contain at least one non-blank keyword")
        return cleaned


class TopicUpdate(BaseModel):
    """Editable topic fields."""

    title: str | None = None
    summary: str | None = None
    marker_keywords: list[str] | None = None
    status: models.TopicStatus | None = None
    pinned: bool | None = None
    snoozed_until: datetime | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str | None) -> str | None:
        """Reject blank titles while allowing omitted values."""
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("marker_keywords")
    @classmethod
    def normalize_marker_keywords(cls, value: list[str] | None) -> list[str] | None:
        """Trim, deduplicate, and preserve explicit empty lists for service-level handling."""
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for keyword in value:
            normalized = " ".join(keyword.split())
            if not normalized:
                continue
            dedupe_key = normalized.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            cleaned.append(normalized)
        return cleaned

    def includes(self, field_name: str) -> bool:
        """Return whether the client explicitly provided a field."""
        return field_name in self.model_fields_set


class TopicSuggestionResponse(BaseModel):
    """Serialized topic suggestion."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    topic_id: int | None = None
    suggestion_type: models.TopicSuggestionType
    status: models.TopicSuggestionStatus
    payload_json: str | None = None
    reason: str
    confidence: models.ConfidenceLevel
    created_at: datetime
    updated_at: datetime
