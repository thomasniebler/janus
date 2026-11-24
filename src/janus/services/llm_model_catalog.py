"""Per-provider LLM model listing for the AI configuration dropdown (JAN-215).

The settings UI asks the backend which models a provider offers so the model
field can be a searchable dropdown instead of free text. Each provider exposes
its own listing endpoint; this module normalizes them to ``ModelOption`` items.

Failures are never fatal for the caller: :func:`list_models` raises
:class:`ModelCatalogError` with a short, secret-free message that the API layer
returns as ``{"models": [], "error": ...}`` so the UI can fall back to manual
entry. Successful listings are cached in-process for a few minutes, keyed by
provider, a hash of the API key, and the effective base URL.

Credential safety: a stored (encrypted) API key is only ever sent to the
provider base URL it was saved with (or the provider default). If the caller
overrides the base URL without supplying a key, the stored key is withheld so a
draft URL can never be used to exfiltrate it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from janus import models, security
from janus.llm_service import _normalize_ollama_base_url
from janus.services import llm_provider_clients
from janus.services.llm_provider_clients import AZURE_AUTH_ENTRA, GITHUB_MODELS_BASE_URL, PROVIDER_LABELS

CACHE_TTL_SECONDS = llm_provider_clients.LOOKUP_CACHE_TTL_SECONDS
MAX_CACHE_ENTRIES = 64
REQUEST_TIMEOUT_SECONDS = llm_provider_clients.LOOKUP_TIMEOUT_SECONDS
MAX_MODELS = 1000

ANTHROPIC_API_VERSION = "2023-06-01"

DEFAULT_BASE_URLS: dict[models.LLMProvider, str] = {
    models.LLMProvider.OLLAMA: "http://localhost:11434",
    models.LLMProvider.OPENAI: "https://api.openai.com/v1",
    models.LLMProvider.OPENROUTER: "https://openrouter.ai/api/v1",
    models.LLMProvider.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
    models.LLMProvider.ANTHROPIC: "https://api.anthropic.com",
    models.LLMProvider.GITHUB_MODELS: GITHUB_MODELS_BASE_URL,
}


#: Providers whose listing endpoint cannot be called without an API key.
KEY_REQUIRED_PROVIDERS = frozenset(
    {
        models.LLMProvider.OPENAI,
        models.LLMProvider.GEMINI,
        models.LLMProvider.ANTHROPIC,
        models.LLMProvider.GITHUB_MODELS,
    }
)

#: The settings field that plays the "base URL" role for the stored-key policy, where it is not ``<provider>_base_url``.
_BASE_URL_FIELDS = {models.LLMProvider.AZURE_OPENAI: "azure_openai_endpoint"}

#: OpenAI ``/v1/models`` returns every model family; these are not chat models.
_OPENAI_NON_CHAT_MARKERS = (
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "davinci",
    "babbage",
    "transcribe",
    "gpt-image",
    "realtime",
    "audio",
)


class ModelCatalogError(Exception):
    """Raised when a provider's model list cannot be fetched. The message is safe to show."""


@dataclass(frozen=True)
class ModelOption:
    """One selectable model: the id sent to the provider and a human label."""

    id: str
    label: str


@dataclass(frozen=True)
class CatalogCredentials:
    """The credentials a lookup should use after applying the stored-key policy."""

    api_key: str | None
    base_url: str | None
    stored_key_withheld: bool = False
    #: Azure OpenAI only (JAN-349): ``entra`` lists with the host's Entra ID token, not a key.
    auth_mode: str | None = None


_cache: dict[tuple[str, str, str], tuple[float, list[ModelOption]]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Drops every cached listing (used by tests and after credential changes)."""
    with _cache_lock:
        _cache.clear()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def effective_base_url(provider: models.LLMProvider, base_url: str | None) -> str:
    """Returns the URL a lookup will actually hit: the override or the provider default."""
    url = _clean(base_url) or DEFAULT_BASE_URLS.get(provider, "")
    if provider == models.LLMProvider.OLLAMA:
        url = str(_normalize_ollama_base_url(url))
    return url.rstrip("/")


def _stored_credentials(settings: models.Settings, provider: models.LLMProvider) -> tuple[str | None, str | None]:
    """Returns the (encrypted api key, base url) persisted for ``provider``."""
    if provider == models.LLMProvider.OPENAI:
        return settings.openai_api_key, settings.openai_base_url
    if provider == models.LLMProvider.OPENROUTER:
        return settings.openrouter_api_key, settings.openrouter_base_url
    if provider == models.LLMProvider.GEMINI:
        return settings.gemini_api_key, None
    if provider == models.LLMProvider.ANTHROPIC:
        return settings.anthropic_api_key, settings.anthropic_base_url
    if provider == models.LLMProvider.GITHUB_MODELS:
        return settings.github_models_api_key, settings.github_models_base_url
    if provider == models.LLMProvider.AZURE_OPENAI:
        return settings.azure_openai_api_key, settings.azure_openai_endpoint
    return settings.ollama_api_key, settings.ollama_base_url


def resolve_credentials(
    settings: models.Settings,
    provider: models.LLMProvider,
    api_key: str | None,
    base_url: str | None,
    auth_mode: str | None = None,
) -> CatalogCredentials:
    """Merges draft (unsaved) values with stored settings for a model lookup.

    A draft ``api_key`` always wins. A draft ``base_url`` of ``None`` means "use
    the stored one"; an empty string means "use the provider default". The
    stored key is only used when the effective URL matches the stored URL.
    Azure OpenAI in Entra ID mode (draft ``auth_mode``, else stored) uses no key at all,
    like its chat client (JAN-349).
    """
    stored_key_encrypted, stored_base_url = _stored_credentials(settings, provider)
    resolved_base_url = _clean(stored_base_url) if base_url is None else _clean(base_url)
    if provider == models.LLMProvider.GEMINI:
        resolved_base_url = None
    if provider == models.LLMProvider.AZURE_OPENAI:
        mode = _clean(auth_mode) or settings.azure_openai_auth_mode or AZURE_AUTH_ENTRA
        if mode == AZURE_AUTH_ENTRA:
            return CatalogCredentials(api_key=None, base_url=resolved_base_url, auth_mode=AZURE_AUTH_ENTRA)
        resolved = _resolve_key_policy(provider, stored_key_encrypted, stored_base_url, api_key, resolved_base_url)
        return dataclasses.replace(resolved, auth_mode=mode)
    return _resolve_key_policy(provider, stored_key_encrypted, stored_base_url, api_key, resolved_base_url)


def _resolve_key_policy(
    provider: models.LLMProvider,
    stored_key_encrypted: str | None,
    stored_base_url: str | None,
    api_key: str | None,
    resolved_base_url: str | None,
) -> CatalogCredentials:
    """The draft key, else the stored key if the effective URL is the one it was saved with."""
    draft_key = _clean(api_key)
    if draft_key:
        return CatalogCredentials(api_key=draft_key, base_url=resolved_base_url)
    if not stored_key_encrypted:
        return CatalogCredentials(api_key=None, base_url=resolved_base_url)

    if effective_base_url(provider, resolved_base_url) != effective_base_url(provider, stored_base_url):
        return CatalogCredentials(api_key=None, base_url=resolved_base_url, stored_key_withheld=True)
    return CatalogCredentials(
        api_key=security.security_service.decrypt(stored_key_encrypted),
        base_url=resolved_base_url,
    )


def draft_withholds_stored_key(settings: models.Settings, draft: dict) -> bool:
    """Whether draft settings would send the stored API key to a base URL it was not saved with (JAN-222).

    ``draft`` is a ``SettingsUpdate`` dump, as the connection test receives it. The
    same policy as a model lookup applies: a draft key may go anywhere, but the
    stored key only goes to its own base URL, so a draft URL cannot exfiltrate it.
    Azure OpenAI in Entra ID mode never sends the stored key, so the rule does not apply.
    """
    provider = models.LLMProvider(draft.get("llm_provider") or settings.llm_provider)
    auth_mode = None
    if provider == models.LLMProvider.AZURE_OPENAI:
        auth_mode = draft.get("azure_openai_auth_mode") or settings.azure_openai_auth_mode or AZURE_AUTH_ENTRA
        if auth_mode == AZURE_AUTH_ENTRA:
            return False
    base_url_field = _BASE_URL_FIELDS.get(provider, f"{provider.value}_base_url")
    if provider == models.LLMProvider.GEMINI or base_url_field not in draft or draft.get(f"{provider.value}_api_key"):
        return False
    # The effective (draft) Azure auth mode: with a saved Entra mode and a leftover key, a draft that
    # switches to API key mode and a new endpoint must still not get the stored key.
    return resolve_credentials(settings, provider, None, draft[base_url_field], auth_mode).stored_key_withheld


def _cache_key(provider: models.LLMProvider, api_key: str | None, base_url: str) -> tuple[str, str, str]:
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else ""
    return provider.value, key_hash, base_url


def _get_json(
    client: httpx.Client, label: str, url: str, headers: dict[str, str], params=None, *, denied: str | None = None
) -> object:
    """GETs a listing; ``denied`` replaces the default text for a 401/403."""
    try:
        response = client.get(url, headers=headers, params=params)
    except httpx.RequestError as exc:
        raise ModelCatalogError(f"Could not reach {label} to load models.") from exc
    if response.status_code in (401, 403):
        default = f"{label} rejected the API key"
        raise ModelCatalogError(f"{denied or default} (HTTP {response.status_code}).")
    if response.status_code >= 400:
        raise ModelCatalogError(f"{label} returned HTTP {response.status_code} when listing models.")
    try:
        return response.json()
    except ValueError as exc:
        raise ModelCatalogError(f"{label} returned an unexpected response when listing models.") from exc


def _items(payload: object, field: str) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get(field), list):
        raise ModelCatalogError("Provider returned an unexpected model list format.")
    return [item for item in payload[field] if isinstance(item, dict)]


def _option(model_id: object, label: object = None) -> ModelOption | None:
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    text = label if isinstance(label, str) and label.strip() else model_id
    return ModelOption(id=model_id.strip(), label=text.strip())


def _fetch_openai(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    payload = _get_json(client, "OpenAI", f"{base_url}/models", {"Authorization": f"Bearer {api_key}"})
    options = []
    for item in _items(payload, "data"):
        model_id = item.get("id")
        if isinstance(model_id, str) and any(marker in model_id.lower() for marker in _OPENAI_NON_CHAT_MARKERS):
            continue
        options.append(_option(model_id))
    return [option for option in options if option]


def _fetch_openrouter(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = _get_json(client, "OpenRouter", f"{base_url}/models", headers)
    return [option for item in _items(payload, "data") if (option := _option(item.get("id"), item.get("name")))]


def _fetch_gemini(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    # The key goes in a header, not the ``?key=`` query string, so it never lands in URL logs.
    payload = _get_json(client, "Gemini", f"{base_url}/models", {"x-goog-api-key": api_key or ""}, {"pageSize": 1000})
    options = []
    for item in _items(payload, "models"):
        methods = item.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = item.get("name")
        if isinstance(name, str):
            name = name.removeprefix("models/")
        options.append(_option(name, item.get("displayName")))
    return [option for option in options if option]


def _fetch_anthropic(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    headers = {"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_API_VERSION}
    payload = _get_json(client, "Anthropic", f"{base_url}/v1/models", headers, {"limit": 1000})
    return [option for item in _items(payload, "data") if (option := _option(item.get("id"), item.get("display_name")))]


def _fetch_github_models(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    # The catalog sits beside the inference API: https://models.github.ai/catalog/models.
    catalog_url = f"{base_url.removesuffix('/inference')}/catalog/models"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/vnd.github+json"}
    payload = _get_json(client, "GitHub Models", catalog_url, headers)
    items = payload if isinstance(payload, list) else _items(payload, "data")
    return [
        option
        for item in items
        if isinstance(item, dict) and _github_text_model(item) and (option := _option(item.get("id"), item.get("name")))
    ]


def _github_text_model(item: dict) -> bool:
    """Chat models Janus can call through Chat Completions.

    Leaves out embedding models (output ``embeddings``, not ``text``) and Responses-only models,
    which GitHub Models cannot serve that way (JAN-354).
    """
    modalities = item.get("supported_output_modalities")
    model_id = item.get("id")
    if isinstance(model_id, str) and llm_provider_clients.responses_only_model(model_id):
        return False
    return not isinstance(modalities, list) or "text" in modalities


def _azure_auth_headers(api_key: str | None, base_url: str) -> dict[str, str]:
    """The ``api-key`` header in API key mode, else an Entra ID bearer token for the host identity.

    The token is only minted for an Azure OpenAI host: the field is looked up on every debounced
    keystroke, and a half-typed or foreign hostname must never receive a live token.
    """
    if not api_key and not llm_provider_clients.is_entra_endpoint(base_url):
        raise ModelCatalogError(f"{llm_provider_clients.ENTRA_HOST_MESSAGE} Enter the deployment name.")
    try:
        return llm_provider_clients.azure_auth_headers(api_key)
    except llm_provider_clients.AzureCredentialError as exc:
        raise ModelCatalogError(
            "No Azure sign-in to list deployments: run `az login` on the Janus host (or give it a "
            "managed identity), or enter the deployment name."
        ) from exc


def _azure_chat_deployment(item: dict) -> bool:
    """Usable chat deployments only: provisioned (``succeeded``), and no embedding/audio/image model."""
    model = item.get("model")
    if item.get("status") not in (None, "succeeded"):
        return False
    return not (isinstance(model, str) and any(marker in model.lower() for marker in _OPENAI_NON_CHAT_MARKERS))


def _fetch_azure_deployments(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    """The resource's deployments (``GET /openai/deployments``), labelled ``deployment (model)`` (JAN-349)."""
    payload = _get_json(
        client,
        "Azure OpenAI",
        f"{base_url}/openai/deployments",
        _azure_auth_headers(api_key, base_url),
        {"api-version": llm_provider_clients.AZURE_DEPLOYMENTS_API_VERSION},
        denied="Azure OpenAI denied listing deployments (PermissionDenied): the identity or key cannot read "
        "this resource's deployments; enter the deployment name",
    )
    options = []
    for item in filter(_azure_chat_deployment, _items(payload, "data")):
        model = item.get("model")
        label = f"{item.get('id')} ({model})" if isinstance(model, str) and model.strip() else None
        options.append(_option(item.get("id"), label))
    return [option for option in options if option]


def _fetch_ollama(client: httpx.Client, api_key: str | None, base_url: str) -> list[ModelOption]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = _get_json(client, "Ollama", f"{base_url}/api/tags", headers)
    return [option for item in _items(payload, "models") if (option := _option(item.get("name") or item.get("model")))]


def _new_client() -> httpx.Client:
    """Builds the outbound client: short timeout, no redirects (shared with the Azure lookup, JAN-357)."""
    return llm_provider_clients.new_lookup_client()


_FETCHERS: dict[models.LLMProvider, Callable[[httpx.Client, str | None, str], list[ModelOption]]] = {
    models.LLMProvider.OPENAI: _fetch_openai,
    models.LLMProvider.OPENROUTER: _fetch_openrouter,
    models.LLMProvider.GEMINI: _fetch_gemini,
    models.LLMProvider.ANTHROPIC: _fetch_anthropic,
    models.LLMProvider.GITHUB_MODELS: _fetch_github_models,
    models.LLMProvider.AZURE_OPENAI: _fetch_azure_deployments,
    models.LLMProvider.OLLAMA: _fetch_ollama,
}


def _azure_base_url(endpoint: str | None) -> str:
    """The validated Azure resource root the chat client would use, or a catalog error saying why not."""
    if not _clean(endpoint):
        raise ModelCatalogError("Enter the Azure OpenAI endpoint to list its deployments.")
    try:
        return llm_provider_clients.normalize_azure_endpoint(endpoint or "")
    except ValueError as exc:
        raise ModelCatalogError(str(exc)) from exc


def list_models(
    provider: models.LLMProvider,
    credentials: CatalogCredentials,
    *,
    client: httpx.Client | None = None,
    now: Callable[[], float] = time.monotonic,
) -> list[ModelOption]:
    """Returns the provider's selectable models, sorted by id, using a short TTL cache.

    Raises:
        ModelCatalogError: With a short user-facing message on any failure.
    """
    label = PROVIDER_LABELS[provider]
    if provider not in _FETCHERS:
        raise ModelCatalogError(f"{label} cannot list models here; enter the model name.")
    azure = provider == models.LLMProvider.AZURE_OPENAI
    needs_key = provider in KEY_REQUIRED_PROVIDERS or (azure and credentials.auth_mode != AZURE_AUTH_ENTRA)
    if needs_key and not credentials.api_key:
        if credentials.stored_key_withheld:
            where = "deployments from a changed endpoint" if azure else "models from a custom base URL"
            raise ModelCatalogError(f"Enter the {label} API key to list {where}.")
        article = "an" if label[0].lower() in "aeiou" else "a"
        raise ModelCatalogError(f"Enter {article} {label} API key to load the model list.")

    base_url = _azure_base_url(credentials.base_url) if azure else effective_base_url(provider, credentials.base_url)
    if urlparse(base_url).scheme not in ("http", "https"):
        raise ModelCatalogError(f"The {label} base URL must start with http:// or https://.")

    # An Entra ID listing is cached apart from key listings of the same endpoint.
    cache_key = _cache_key(provider, credentials.api_key or credentials.auth_mode, base_url)
    current_time = now()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and current_time - cached[0] < CACHE_TTL_SECONDS:
            return list(cached[1])

    owns_client = client is None
    http_client = client or _new_client()
    try:
        options = _FETCHERS[provider](http_client, credentials.api_key, base_url)
    finally:
        if owns_client:
            http_client.close()

    unique = {option.id: option for option in options}
    result = sorted(unique.values(), key=lambda option: option.id.lower())[:MAX_MODELS]

    with _cache_lock:
        expired = [key for key, (stored_at, _) in _cache.items() if current_time - stored_at >= CACHE_TTL_SECONDS]
        for key in expired:
            del _cache[key]
        if len(_cache) >= MAX_CACHE_ENTRIES:
            oldest = min(_cache, key=lambda key: _cache[key][0])
            del _cache[oldest]
        _cache[cache_key] = (current_time, result)
    return list(result)
