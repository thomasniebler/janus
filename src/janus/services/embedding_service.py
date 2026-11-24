"""Builds the configured LangChain ``Embeddings`` provider (JAN-241).

Embeddings are off until an admin sets ``Settings.embedding_provider``. The
provider reuses the stored, Fernet-encrypted API key and base URL of the chat
provider with the same name, so there is no second credential to manage.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_ollama import OllamaEmbeddings
from langchain_openai import OpenAIEmbeddings

from janus import llm_service, models, security

#: Providers that can embed, and the model suggested when none is set.
DEFAULT_EMBEDDING_MODELS = {
    models.LLMProvider.OLLAMA.value: "nomic-embed-text",
    models.LLMProvider.OPENAI.value: "text-embedding-3-small",
    models.LLMProvider.GEMINI.value: "gemini-embedding-001",
}

#: Per-request timeout for embedding calls, in seconds.
EMBEDDING_TIMEOUT_SECONDS = 30

#: Texts are truncated to this many characters before embedding; facts and
#: topics are short, so this only guards against pathological input.
MAX_EMBEDDING_INPUT_CHARS = 8000


class EmbeddingConfigError(ValueError):
    """Raised when the configured embedding provider cannot be built."""


@dataclass(frozen=True)
class EmbeddingChoice:
    """The configured embedding provider and model."""

    provider: str
    model: str

    @property
    def key(self) -> str:
        """The ``provider/model`` string stored on every vector row."""
        return f"{self.provider}/{self.model}"


def normalize_embedding_choice(provider: str | None, model: str | None) -> EmbeddingChoice | None:
    """Validates a provider/model pair; ``None``/empty provider disables embeddings.

    A provider without a model gets that provider's suggested model. Raises
    ``EmbeddingConfigError`` for a provider that cannot embed.
    """
    cleaned_provider = (provider or "").strip().lower()
    if not cleaned_provider:
        return None
    if cleaned_provider not in DEFAULT_EMBEDDING_MODELS:
        supported = ", ".join(sorted(DEFAULT_EMBEDDING_MODELS))
        raise EmbeddingConfigError(f"embedding_provider must be one of: {supported} (or empty to disable)")
    cleaned_model = (model or "").strip() or DEFAULT_EMBEDDING_MODELS[cleaned_provider]
    return EmbeddingChoice(provider=cleaned_provider, model=cleaned_model)


def configured_choice(settings: models.Settings | None) -> EmbeddingChoice | None:
    """Returns the stored embedding choice, or ``None`` when embeddings are off or invalid."""
    if settings is None:
        return None
    try:
        return normalize_embedding_choice(settings.embedding_provider, settings.embedding_model)
    except EmbeddingConfigError:
        return None


def _decrypt(value: str | None) -> str | None:
    return security.security_service.decrypt(value) if value else None


def build_embeddings(settings: models.Settings, choice: EmbeddingChoice) -> Embeddings:
    """Builds the LangChain ``Embeddings`` client for ``choice`` from stored credentials."""
    if choice.provider == models.LLMProvider.OPENAI.value:
        api_key = _decrypt(settings.openai_api_key)
        if not api_key:
            raise EmbeddingConfigError("OpenAI embeddings need an OpenAI API key in the LLM settings.")
        return OpenAIEmbeddings(
            model=choice.model,
            api_key=api_key,
            base_url=settings.openai_base_url or None,
            request_timeout=EMBEDDING_TIMEOUT_SECONDS,
            max_retries=1,
            # Skips tiktoken's token-length pre-check, which downloads its
            # encoding files on first use and breaks offline installs.
            check_embedding_ctx_length=False,
        )
    if choice.provider == models.LLMProvider.GEMINI.value:
        api_key = _decrypt(settings.gemini_api_key)
        if not api_key:
            raise EmbeddingConfigError("Google embeddings need a Gemini API key in the LLM settings.")
        model_name = choice.model if choice.model.startswith("models/") else f"models/{choice.model}"
        return GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=api_key,
            request_options={"timeout": EMBEDDING_TIMEOUT_SECONDS},
        )
    if choice.provider == models.LLMProvider.OLLAMA.value:
        client_kwargs = {
            **llm_service._ollama_client_kwargs(_decrypt(settings.ollama_api_key)),
            "timeout": EMBEDDING_TIMEOUT_SECONDS,
        }
        return OllamaEmbeddings(
            model=choice.model,
            base_url=llm_service._normalize_ollama_base_url(settings.ollama_base_url or "http://localhost:11434"),
            client_kwargs=client_kwargs,
        )
    raise EmbeddingConfigError(f"unsupported embedding provider: {choice.provider}")


def prepare_text(text: str) -> str:
    """Truncates one text to the embedding input limit."""
    return text[:MAX_EMBEDDING_INPUT_CHARS]
