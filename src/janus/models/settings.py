"""Per-user system and LLM settings."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
)

from janus import database
from janus.models.enums import ConfidenceLevel, LLMProvider, TopicExtractionScope


class Settings(database.Base):
    """Database model for application settings."""

    __tablename__ = "settings"
    id = Column(Integer, primary_key=True, index=True)
    initial_sync_email_limit = Column(Integer, default=100, nullable=False)
    llm_provider = Column(Enum(LLMProvider), default=LLMProvider.OLLAMA)
    llm_model = Column(String, default="llama3:latest")
    ollama_model = Column(String, default="llama3:latest")
    ollama_base_url = Column(String, default="http://localhost:11434")
    ollama_api_key = Column(String, nullable=True)  # Encrypted at rest
    openai_model = Column(String, default="gpt-4o-mini")
    openai_api_key = Column(String, nullable=True)
    openai_base_url = Column(String, nullable=True)
    openrouter_model = Column(String, default="openrouter/auto")
    openrouter_api_key = Column(String, nullable=True)
    openrouter_base_url = Column(String, default="https://openrouter.ai/api/v1")
    gemini_model = Column(String, default="gemini-2.5-flash")
    gemini_api_key = Column(String, nullable=True)
    anthropic_model = Column(String, default="claude-opus-5")
    anthropic_api_key = Column(String, nullable=True)  # Encrypted at rest
    anthropic_base_url = Column(String, nullable=True)  # Optional override; defaults to https://api.anthropic.com
    # GitHub Models (JAN-340): OpenAI-compatible, authenticated with a GitHub token.
    github_models_model = Column(String, default="openai/gpt-4.1-mini")
    github_models_api_key = Column(String, nullable=True)  # Encrypted at rest
    github_models_base_url = Column(String, nullable=True)  # Optional; defaults to https://models.github.ai/inference
    # Azure OpenAI / Azure AI Foundry (JAN-340). ``entra`` auth uses the host's Azure
    # identity (``az login`` locally, managed identity on a host) instead of a key.
    azure_openai_endpoint = Column(String, nullable=True)
    azure_openai_deployment = Column(String, nullable=True)
    azure_openai_api_version = Column(String, default="preview")  # JAN-356: v1 endpoint; a date = legacy route
    azure_openai_api_key = Column(String, nullable=True)  # Encrypted at rest; only used in ``api_key`` mode
    azure_openai_auth_mode = Column(String, default="entra")  # "entra" | "api_key"
    # Optional per-surface model overrides (JAN-233); ``None`` = the default
    # provider/model above. Chat uses ``chat_*``; conversation titles and memory
    # suggestions use ``utility_*``.
    chat_llm_provider = Column(String, nullable=True)
    chat_llm_model = Column(String, nullable=True)
    utility_llm_provider = Column(String, nullable=True)
    utility_llm_model = Column(String, nullable=True)
    llm_status = Column(String, nullable=True)
    llm_status_checked_at = Column(DateTime, nullable=True)
    llm_status_message = Column(String, nullable=True)
    github_token = Column(String, nullable=True)  # Encrypted at rest
    github_default_repo = Column(String, nullable=True)  # e.g. "owner/repo"
    linear_api_key = Column(String, nullable=True)  # Encrypted at rest
    linear_default_team_id = Column(String, nullable=True)  # Team key ("ENG") or UUID; keys resolved at call time
    # Optional agent web search (JAN-211). ``None`` provider = disabled.
    web_search_provider = Column(String, nullable=True)  # "brave" | "searxng"
    web_search_api_key = Column(String, nullable=True)  # Encrypted at rest (Brave)
    web_search_base_url = Column(String, nullable=True)  # Self-hosted SearXNG instance URL
    topic_tracking_available = Column(Boolean, default=True, server_default="1", nullable=False)
    topic_tracking_default_enabled = Column(Boolean, default=True, server_default="1", nullable=False)
    topic_tracking_default_scope = Column(
        Enum(TopicExtractionScope),
        default=TopicExtractionScope.IMPORTANT_AND_DIGEST,
        nullable=False,
    )
    topic_tracking_default_min_confidence = Column(
        Enum(ConfidenceLevel),
        default=ConfidenceLevel.MEDIUM,
        nullable=False,
    )
    topic_tracking_default_min_confirmation_mentions = Column(Integer, default=2, server_default="2", nullable=False)
    topic_tracking_max_active_topics = Column(Integer, default=20, server_default="20", nullable=False)
    topic_tracking_max_context_topics = Column(Integer, default=5, server_default="5", nullable=False)
    topic_tracking_review_required = Column(Boolean, default=True, server_default="1", nullable=False)
    # Semantic search (JAN-241): ``None`` provider = off; reuses that provider's chat credentials.
    embedding_provider = Column(String, nullable=True)  # "ollama" | "openai" | "gemini"
    embedding_model = Column(String, nullable=True)
    embedding_min_similarity = Column(Float, default=0.5, server_default="0.5", nullable=False)
    # Chat image generation (JAN-253): ``None`` provider = off; reuses that provider's chat credentials.
    image_provider = Column(String, nullable=True)  # "openai" | "gemini" | "openrouter"
    image_model = Column(String, nullable=True)
