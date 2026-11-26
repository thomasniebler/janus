"""Applies per-provider LLM settings updates to the singleton settings row.

Each provider stores its own model, API key (Fernet-encrypted at rest) and,
where it has one, base URL, so users can switch providers without re-entering
values. This keeps the field-by-field update rules in one table instead of a
long chain of ``if`` blocks in the settings route.
"""

from __future__ import annotations

from janus import config, models, security
from janus.llm_service import DEFAULT_ANTHROPIC_MODEL, get_active_model_name
from janus.services import embedding_service, image_generation, llm_provider_clients, vector_index
from janus.trigger_run_settings import validate_model_choice

#: Fields copied verbatim when present in the update.
_PLAIN_FIELDS = (
    "ollama_model",
    "ollama_base_url",
    "openai_model",
    "openrouter_model",
    "gemini_model",
    "anthropic_model",
    "github_models_model",
    "azure_openai_deployment",
    # ``SettingsUpdate`` restricts it to "entra" | "api_key".
    "azure_openai_auth_mode",
)

#: Optional URL overrides: an empty string clears the override (stored as NULL).
_NULLABLE_FIELDS = (
    "openai_base_url",
    "openrouter_base_url",
    "anthropic_base_url",
    "github_models_base_url",
    "azure_openai_endpoint",
    "azure_openai_api_version",
)

#: Secrets: encrypted before storage; an empty string clears the stored key.
_SECRET_FIELDS = (
    "ollama_api_key",
    "openai_api_key",
    "openrouter_api_key",
    "gemini_api_key",
    "anthropic_api_key",
    "github_models_api_key",
    "azure_openai_api_key",
)

#: Where the legacy provider-agnostic ``llm_model`` field lands for each provider.
_MODEL_COLUMN_BY_PROVIDER = {
    models.LLMProvider.OLLAMA: "ollama_model",
    models.LLMProvider.OPENAI: "openai_model",
    models.LLMProvider.OPENROUTER: "openrouter_model",
    models.LLMProvider.GEMINI: "gemini_model",
    models.LLMProvider.ANTHROPIC: "anthropic_model",
    **llm_provider_clients.MODEL_COLUMNS,
}


def apply_llm_settings_update(settings: models.Settings, update: object) -> None:
    """Copies the LLM provider fields that are set (not ``None``) on ``update`` onto ``settings``.

    ``update`` is a ``SettingsUpdate``-shaped object. ``llm_provider`` should already
    be applied so the legacy ``llm_model`` field routes to the active provider; it is
    applied last so it wins over a provider-specific model sent in the same request.
    """
    for field in _PLAIN_FIELDS:
        value = getattr(update, field, None)
        if value is not None:
            setattr(settings, field, value)
    for field in _NULLABLE_FIELDS:
        value = getattr(update, field, None)
        if value is not None:
            setattr(settings, field, value or None)
    for field in _SECRET_FIELDS:
        value = getattr(update, field, None)
        if value is not None:
            setattr(settings, field, security.security_service.encrypt(value) if value else None)

    llm_model = getattr(update, "llm_model", None)
    if llm_model is not None:
        column = _MODEL_COLUMN_BY_PROVIDER.get(settings.llm_provider, "gemini_model")
        setattr(settings, column, llm_model)


def provider_settings_payload(settings: models.Settings) -> dict[str, object]:
    """The per-provider LLM fields of the settings response; secrets only as ``*_configured`` flags."""
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": get_active_model_name(settings),
        "ollama_model": settings.ollama_model or "llama3:latest",
        "ollama_base_url": settings.ollama_base_url or "http://localhost:11434",
        "ollama_api_key_configured": bool(settings.ollama_api_key),
        "openai_model": settings.openai_model or "gpt-4o-mini",
        "openai_base_url": settings.openai_base_url,
        "openai_api_key_configured": bool(settings.openai_api_key),
        "openrouter_model": settings.openrouter_model or "openrouter/auto",
        "openrouter_base_url": settings.openrouter_base_url or "https://openrouter.ai/api/v1",
        "openrouter_api_key_configured": bool(settings.openrouter_api_key),
        "gemini_model": settings.gemini_model or "gemini-2.5-flash",
        "gemini_api_key_configured": bool(settings.gemini_api_key),
        "anthropic_model": settings.anthropic_model or DEFAULT_ANTHROPIC_MODEL,
        "anthropic_base_url": settings.anthropic_base_url,
        "anthropic_api_key_configured": bool(settings.anthropic_api_key),
        "github_models_model": settings.github_models_model or llm_provider_clients.DEFAULT_GITHUB_MODELS_MODEL,
        "github_models_base_url": settings.github_models_base_url,
        "github_models_api_key_configured": bool(settings.github_models_api_key),
        "azure_openai_endpoint": settings.azure_openai_endpoint,
        "azure_openai_deployment": settings.azure_openai_deployment,
        "azure_openai_api_version": settings.azure_openai_api_version
        or llm_provider_clients.DEFAULT_AZURE_OPENAI_API_VERSION,
        "azure_openai_auth_mode": settings.azure_openai_auth_mode or llm_provider_clients.AZURE_AUTH_ENTRA,
        "azure_openai_api_key_configured": bool(settings.azure_openai_api_key),
    }


def apply_surface_model_update(settings: models.Settings, update: object) -> None:
    """Applies the chat/utility model choices explicitly sent on ``update`` (JAN-233).

    A pair is validated together, with the stored value filling in the half that
    was not sent; ``None`` or an empty string resets a field to the default
    backend. Raises ``TriggerDefinitionError`` for an unknown provider.
    """
    fields_set = getattr(update, "model_fields_set", set())
    for purpose in ("chat", "utility"):
        provider_field, model_field = f"{purpose}_llm_provider", f"{purpose}_llm_model"
        if provider_field not in fields_set and model_field not in fields_set:
            continue
        source = {
            field: getattr(update if field in fields_set else settings, field)
            for field in (provider_field, model_field)
        }
        choice = validate_model_choice(source[provider_field], source[model_field])
        setattr(settings, provider_field, choice["llm_provider"])
        setattr(settings, model_field, choice["llm_model"])


def apply_embedding_update(settings: models.Settings, update: object) -> None:
    """Applies the embedding provider/model and similarity cutoff sent on ``update`` (JAN-241).

    Provider and model are validated together, with the stored value filling in
    the half that was not sent; an empty provider turns embeddings off (and
    clears the model), a provider without a model gets its suggested model.
    Raises ``EmbeddingConfigError`` for an unsupported provider.
    Apply after ``apply_surface_model_update`` so one ``except`` covers both.
    """
    fields_set = getattr(update, "model_fields_set", set())
    if "embedding_provider" in fields_set or "embedding_model" in fields_set:
        provider = update.embedding_provider if "embedding_provider" in fields_set else settings.embedding_provider
        model = update.embedding_model if "embedding_model" in fields_set else settings.embedding_model
        if "embedding_provider" in fields_set and "embedding_model" not in fields_set:
            # A provider switch without a model must not keep the old provider's model.
            model = None if provider != settings.embedding_provider else model
        choice = embedding_service.normalize_embedding_choice(provider, model)
        settings.embedding_provider = choice.provider if choice else None
        settings.embedding_model = choice.model if choice else None
    min_similarity = getattr(update, "embedding_min_similarity", None)
    if min_similarity is not None:
        settings.embedding_min_similarity = min_similarity


def embedding_settings_payload(settings: models.Settings) -> dict[str, object]:
    """The embedding fields of the settings response."""
    return {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_min_similarity": settings.embedding_min_similarity,
        "embedding_default_models": embedding_service.DEFAULT_EMBEDDING_MODELS,
        "vector_search_available": config.settings.database_url.startswith("sqlite")
        and vector_index.probe_capabilities().vector_search,
    }


def apply_image_update(settings: models.Settings, update: object) -> None:
    """Applies the image-generation provider/model sent on ``update`` (JAN-253).

    Same rules as :func:`apply_embedding_update`: validated together with the
    stored half, an empty provider turns image generation off (and clears the
    model), and a provider switch without a model resets it to the default.
    Raises ``ImageConfigError`` for a provider that cannot generate images.
    """
    fields_set = getattr(update, "model_fields_set", set())
    if "image_provider" not in fields_set and "image_model" not in fields_set:
        return
    provider = update.image_provider if "image_provider" in fields_set else settings.image_provider
    model = update.image_model if "image_model" in fields_set else settings.image_model
    if "image_provider" in fields_set and "image_model" not in fields_set:
        model = None if provider != settings.image_provider else model
    choice = image_generation.normalize_image_choice(provider, model)
    settings.image_provider = choice.provider if choice else None
    settings.image_model = choice.model if choice else None


def image_settings_payload(settings: models.Settings) -> dict[str, object]:
    """The image-generation fields of the settings response (JAN-253)."""
    return {
        "image_provider": settings.image_provider,
        "image_model": settings.image_model,
        "image_default_models": image_generation.DEFAULT_IMAGE_MODELS,
        "image_storage_limit_mb": config.settings.image_storage_limit_mb,
    }
