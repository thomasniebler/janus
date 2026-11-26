"""Planner and status schemas for Janus API."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from janus.models import (
    BriefingEmailWindow,
    ConfidenceLevel,
    DashboardWidgetKey,
    LLMProvider,
    PlannerItemKind,
    PlannerItemSource,
    PlannerSuggestionStatus,
    PlannerSuggestionType,
    Provider,
    TaskStatus,
    TaskUrgency,
)
from janus.schemas.email import EmailRecord
from janus.schemas.topics import TopicSummary
from janus.schemas.whatsapp import WhatsAppMessage


def normalize_prompt_override(value: object) -> object:
    """Converts blank prompt override strings into None."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


class PlannerAccountBase(BaseModel):
    """Base planner account schema."""

    name: str
    provider: Provider
    api_url: str | None = None
    is_active: bool = True


class PlannerAccountCreate(PlannerAccountBase):
    """Schema for creating a planner account."""

    token: str


class PlannerAccount(PlannerAccountBase):
    """Schema for a planner account."""

    id: int
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class GoogleCalendarOption(BaseModel):
    """One calendar visible to the connected Google account."""

    id: str
    summary: str
    primary: bool = False
    access_role: str
    selected: bool


class GoogleCalendarSelectionUpdate(BaseModel):
    """Payload to update which Google calendars Janus syncs."""

    calendar_ids: list[str] = Field(min_length=1)


class CalendarEvent(BaseModel):
    """Schema for a calendar event."""

    id: int
    title: str
    start_time: datetime
    end_time: datetime
    location: str | None = None
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TaskRecord(BaseModel):
    """Schema for a task record."""

    id: int
    title: str
    status: str
    due_date: datetime | None = None
    priority: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PlannerTaskItem(BaseModel):
    """Unified planner task returned to the frontend."""

    id: int
    kind: PlannerItemKind = PlannerItemKind.TASK
    source: PlannerItemSource
    source_label: str
    read_only: bool
    title: str
    description: str | None = None
    status: str
    urgency: TaskUrgency = TaskUrgency.NORMAL
    parent_task_id: int | None = None
    sort_order: int | None = None
    child_count: int = 0
    open_child_count: int = 0
    priority: str | None = None
    due_at: datetime | None = None
    scheduled_for: datetime | None = None
    completed_at: datetime | None = None
    confidence: ConfidenceLevel | None = None
    external_id: str | None = None
    planner_account_id: int | None = None


class PlannerTaskTreeItem(PlannerTaskItem):
    """A local task with recursively nested local children."""

    children: list["PlannerTaskTreeItem"] = Field(default_factory=list)


class DesktopSummary(BaseModel):
    """Compact aggregate payload for desktop status clients."""

    open_tasks: int
    unread_emails: int | None = None


class PlannerEventItem(BaseModel):
    """Unified planner event returned to the frontend."""

    id: int
    kind: PlannerItemKind = PlannerItemKind.EVENT
    source: PlannerItemSource
    source_label: str
    read_only: bool
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    location: str | None = None
    all_day: bool = False
    confidence: ConfidenceLevel | None = None
    external_id: str | None = None
    planner_account_id: int | None = None


class LocalPlannerTaskCreate(BaseModel):
    """Payload for creating a Janus-owned planner task."""

    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO
    urgency: TaskUrgency = TaskUrgency.NORMAL
    parent_task_id: int | None = None
    sort_order: int | None = None
    priority: str | None = None
    due_at: datetime | None = None
    scheduled_for: datetime | None = None


class LocalPlannerTaskUpdate(BaseModel):
    """Payload for updating a Janus-owned planner task."""

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    urgency: TaskUrgency | None = None
    parent_task_id: int | None = None
    sort_order: int | None = None
    priority: str | None = None
    due_at: datetime | None = None
    scheduled_for: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> Self:
        """Reject empty task update payloads."""
        if not self.model_fields_set:
            raise ValueError("at least one task field must be provided")
        return self


class LocalPlannerTaskMove(BaseModel):
    """Payload for moving a local task within the user's task tree."""

    parent_task_id: int | None = None
    sort_order: int | None = None


PlannerTaskTreeItem.model_rebuild()


class LocalPlannerEventCreate(BaseModel):
    """Payload for creating a Janus-owned planner event."""

    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    location: str | None = None
    all_day: bool = False


class LocalPlannerEventUpdate(BaseModel):
    """Payload for updating a Janus-owned planner event."""

    title: str | None = None
    description: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    all_day: bool | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> Self:
        """Reject empty event update payloads."""
        if not self.model_fields_set:
            raise ValueError("at least one event field must be provided")
        return self


class PlannerSuggestionItem(BaseModel):
    """Reviewable planner suggestion exposed to the dashboard."""

    id: int
    suggestion_type: PlannerSuggestionType
    status: PlannerSuggestionStatus
    source: PlannerItemSource
    confidence: ConfidenceLevel | None = None
    reason: str | None = None
    dedupe_key: str | None = None
    title: str | None = None
    description: str | None = None
    task_status: TaskStatus | None = None
    priority: str | None = None
    due_at: datetime | None = None
    scheduled_for: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    all_day: bool = False
    source_email_id: int | None = None
    source_email_subject: str | None = None
    source_email_sender: str | None = None
    source_topic_id: int | None = None
    source_topic_title: str | None = None
    target_task_id: int | None = None
    target_event_id: int | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlannerSuggestionUpdate(BaseModel):
    """Editable fields for a pending planner suggestion."""

    title: str | None = None
    description: str | None = None
    task_status: TaskStatus | None = None
    priority: str | None = None
    due_at: datetime | None = None
    scheduled_for: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str | None = None
    all_day: bool | None = None
    reason: str | None = None
    dedupe_key: str | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> Self:
        """Reject empty suggestion edit payloads."""
        if not self.model_fields_set:
            raise ValueError("at least one suggestion field must be provided")
        return self


class PlannerSuggestionBulkDismiss(BaseModel):
    """Dismiss many pending suggestions at once: by ids, or every pending one (``all_pending``)."""

    suggestion_ids: list[int] | None = None
    all_pending: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        """Require ids or an explicit all_pending, never an empty request."""
        if not self.suggestion_ids and not self.all_pending:
            raise ValueError("give suggestion_ids or set all_pending")
        return self


class PlannerSuggestionBulkResult(BaseModel):
    """What a bulk dismissal changed."""

    dismissed_ids: list[int]
    #: Duplicate cleanup only: how many duplicate groups were found, and the suggestion kept in each.
    groups: int = 0
    kept_ids: list[int] = []


class PlannerSuggestionDuplicateSummary(BaseModel):
    """How many pending suggestions duplicate another one."""

    groups: int
    duplicates: int


#: How Janus authenticates to Azure OpenAI: the host's Entra ID identity or a stored key (JAN-340).
AzureOpenAIAuthMode = Literal["entra", "api_key"]


class Settings(BaseModel):
    """Schema for application settings."""

    initial_sync_email_limit: int
    llm_provider: LLMProvider
    llm_model: str
    ollama_model: str
    ollama_base_url: str
    ollama_api_key_configured: bool = False
    openai_model: str
    openai_base_url: str | None = None
    openai_api_key_configured: bool = False
    openrouter_model: str
    openrouter_base_url: str | None = None
    openrouter_api_key_configured: bool = False
    gemini_model: str
    gemini_api_key_configured: bool = False
    anthropic_model: str
    anthropic_base_url: str | None = None
    anthropic_api_key_configured: bool = False
    #: GitHub Models and Azure OpenAI (JAN-340).
    github_models_model: str = "openai/gpt-4.1-mini"
    github_models_base_url: str | None = None
    github_models_api_key_configured: bool = False
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "preview"
    azure_openai_auth_mode: AzureOpenAIAuthMode = "entra"
    azure_openai_api_key_configured: bool = False
    #: Optional per-surface model overrides (JAN-233); ``None`` = default model.
    chat_llm_provider: str | None = None
    chat_llm_model: str | None = None
    utility_llm_provider: str | None = None
    utility_llm_model: str | None = None
    github_token_configured: bool = False
    github_default_repo: str | None = None
    topic_tracking_available: bool = True
    topic_tracking_default_enabled: bool = True
    topic_tracking_default_scope: str
    topic_tracking_default_min_confidence: str
    topic_tracking_default_min_confirmation_mentions: int
    topic_tracking_max_active_topics: int
    topic_tracking_max_context_topics: int
    topic_tracking_review_required: bool = True
    #: Semantic search (JAN-241); ``None`` provider = embeddings off.
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_min_similarity: float = 0.5
    embedding_default_models: dict[str, str] = Field(default_factory=dict)
    vector_search_available: bool = False
    #: Chat image generation (JAN-253); ``None`` provider = off.
    image_provider: str | None = None
    image_model: str | None = None
    image_default_models: dict[str, str] = Field(default_factory=dict)
    image_storage_limit_mb: int = 200

    model_config = ConfigDict(from_attributes=True)


class SettingsUpdate(BaseModel):
    """Schema for updating application settings."""

    initial_sync_email_limit: int | None = Field(default=None, ge=1)
    llm_provider: LLMProvider | None = None
    llm_model: str | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None
    ollama_api_key: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openrouter_model: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str | None = None
    gemini_model: str | None = None
    gemini_api_key: str | None = None
    anthropic_model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    #: GitHub Models and Azure OpenAI (JAN-340); an empty base URL/endpoint/API version clears it.
    github_models_model: str | None = None
    github_models_api_key: str | None = None
    github_models_base_url: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_auth_mode: AzureOpenAIAuthMode | None = None
    #: Send ``null``/empty to reset a surface to the default model.
    chat_llm_provider: str | None = None
    chat_llm_model: str | None = None
    utility_llm_provider: str | None = None
    utility_llm_model: str | None = None
    github_token: str | None = None
    github_default_repo: str | None = None
    topic_tracking_available: bool | None = None
    topic_tracking_default_enabled: bool | None = None
    topic_tracking_default_scope: str | None = None
    topic_tracking_default_min_confidence: str | None = None
    topic_tracking_default_min_confirmation_mentions: int | None = Field(default=None, ge=1, le=10)
    topic_tracking_max_active_topics: int | None = Field(default=None, ge=1, le=100)
    topic_tracking_max_context_topics: int | None = Field(default=None, ge=1, le=25)
    topic_tracking_review_required: bool | None = None
    #: Send ``null``/empty ``embedding_provider`` to turn embeddings off.
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Send ``null``/empty ``image_provider`` to turn image generation off (JAN-253).
    image_provider: str | None = None
    image_model: str | None = None


class LLMModelListRequest(BaseModel):
    """Draft provider credentials used to list selectable models (JAN-215).

    ``api_key``/``base_url`` are unsaved draft values from the settings form;
    omitted values fall back to the stored settings. The body (not the query
    string) carries them so keys never appear in URLs or access logs.
    """

    provider: LLMProvider
    api_key: str | None = None
    base_url: str | None = None
    #: Azure OpenAI only (JAN-349): the draft auth mode; omitted means the stored one.
    auth_mode: AzureOpenAIAuthMode | None = None


class LLMModelOption(BaseModel):
    """One selectable model returned by a provider."""

    id: str
    label: str


class LLMModelListResponse(BaseModel):
    """Normalized model listing; ``error`` is set (and ``models`` empty) on failure."""

    models: list[LLMModelOption] = []
    error: str | None = None


class DashboardData(BaseModel):
    """Schema for the main dashboard data."""

    important_emails: list[EmailRecord]
    whatsapp_messages: list[WhatsAppMessage] = []
    all_emails: list[EmailRecord] = []
    events: list[PlannerEventItem]
    tasks: list[PlannerTaskTreeItem]
    topics: list[TopicSummary] = []
    now: datetime


class DashboardWidgetPreference(BaseModel):
    """Schema for dashboard widget preference state."""

    widget_key: DashboardWidgetKey
    is_visible: bool
    is_collapsed: bool
    prompt_override: str | None = None
    briefing_email_window: BriefingEmailWindow | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("prompt_override", mode="before")
    @classmethod
    def normalize_prompt_override_value(cls, value: object) -> object:
        """Ensure blank persisted prompt overrides are exposed as None."""
        return normalize_prompt_override(value)


class DashboardWidgetPreferenceUpdate(BaseModel):
    """Schema for updating a single dashboard widget preference."""

    widget_key: DashboardWidgetKey
    is_visible: bool | None = None
    is_collapsed: bool | None = None
    prompt_override: str | None = None
    briefing_email_window: BriefingEmailWindow | None = None

    @field_validator("prompt_override", mode="before")
    @classmethod
    def normalize_prompt_override_value(cls, value: object) -> object:
        """Ensure blank updates are treated as requests to use the default prompt."""
        return normalize_prompt_override(value)

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> Self:
        """Require at least one mutable field."""
        if (
            self.is_visible is None
            and self.is_collapsed is None
            and "prompt_override" not in self.model_fields_set
            and "briefing_email_window" not in self.model_fields_set
        ):
            raise ValueError(
                "at least one of is_visible, is_collapsed, prompt_override, or briefing_email_window must be provided"
            )
        return self


class DashboardWidgetPreferenceUpdateRequest(BaseModel):
    """Batch update payload for dashboard widget preferences."""

    widgets: list[DashboardWidgetPreferenceUpdate]
