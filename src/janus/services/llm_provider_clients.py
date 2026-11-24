"""Chat clients for the GitHub Models and Azure OpenAI providers (JAN-340).

``LLMService`` resolves the provider and model name, then hands the rest to the
builder registered here in :data:`BUILDERS`. Each builder reads its settings
(a draft ``settings_override`` value wins over the stored column), raises
``ValueError`` with a clear message when required configuration is missing, and
returns a LangChain chat model.

Azure OpenAI ``entra`` mode authenticates with the host's Azure identity
(``DefaultAzureCredential``: the ``az login`` session locally, a managed
identity on a host). The credential is created lazily and shared by every client in
the process. On the v1 route a client build may ask it for a token, to look up the
deployment's model (``services/azure_deployment_models.py``). That lookup runs in a
background thread when the build happens on an event loop, and a failure only means
routing by the deployment name. A missing ``az login`` therefore never fails startup, a
settings save or a client build; it surfaces as :class:`AzureCredentialError` when a
chat request is made. The token call is blocking (an IMDS probe, an ``az`` subprocess),
so async clients run it in a worker thread and keep the event loop free.

This module also holds the provider tables other modules share: :data:`PROVIDER_LABELS`
and :data:`MODEL_COLUMNS`, and :func:`resolved_value`, the draft-over-stored lookup.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langchain_openai.chat_models.base import _RESPONSES_API_ONLY_PREFIXES

from janus import config, event_logger, models, security

GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"
DEFAULT_GITHUB_MODELS_MODEL = "openai/gpt-4.1-mini"
#: The v1 data plane (``{endpoint}/openai/v1``) is the default route (JAN-356). A dated api-version
#: (``YYYY-MM-DD`` or ``YYYY-MM-DD-preview``) selects the legacy deployments route instead.
DEFAULT_AZURE_OPENAI_API_VERSION = "preview"
#: api-version setting -> the ``api-version`` query the v1 route sends; any other (dated) value
#: selects the legacy deployments route (JAN-356, JAN-357).
AZURE_V1_API_VERSIONS = {"preview": "preview", "latest": "latest", "v1": "preview", "": "preview"}

AZURE_AUTH_ENTRA = "entra"
AZURE_AUTH_API_KEY = "api_key"
AZURE_AUTH_MODES = (AZURE_AUTH_ENTRA, AZURE_AUTH_API_KEY)
#: Entra ID scope for Azure OpenAI / Azure AI Foundry (Cognitive Services) data-plane calls.
AZURE_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
#: The data-plane API version that still lists a resource's deployments (``GET /openai/deployments``).
AZURE_DEPLOYMENTS_API_VERSION = "2023-03-15-preview"
#: Shared by the settings model catalog and the Azure deployment-model lookup (JAN-357): the
#: timeout of one listing or lookup request, and how long an answer is cached.
LOOKUP_TIMEOUT_SECONDS = 5.0
LOOKUP_CACHE_TTL_SECONDS = 300.0


def new_lookup_client() -> httpx.Client:
    """An outbound client for listings and lookups: short timeout, no redirects (they could leak auth headers)."""
    return httpx.Client(timeout=LOOKUP_TIMEOUT_SECONDS, follow_redirects=False)


#: OpenAI's own API base URL; an explicit ``openai_base_url`` equal to it counts as "none" (JAN-354).
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def responses_only_model(model_name: str | None) -> bool:
    """Whether a model only serves the Responses API (the ``*-pro`` family, gpt-5.6-sol, any ``codex``).

    Reuses langchain-openai's own list, so it stays in step with the client's routing. A publisher
    prefix (``openai/gpt-5-pro`` on GitHub Models or OpenRouter) is ignored.
    """
    name = (model_name or "").strip().lower().rsplit("/", 1)[-1]
    return bool(name) and (name.startswith(_RESPONSES_API_ONLY_PREFIXES) or "codex" in name)


def _responses_only_error(provider: models.LLMProvider, model_name: str, remedy: str) -> ValueError:
    return _missing(
        provider,
        f"{model_name} only works with the OpenAI Responses API, which Janus does not use for "
        f"{PROVIDER_LABELS[provider]} here. Pick another model, or {remedy}.",
    )


def is_native_openai(base_url: object) -> bool:
    """Whether an ``openai_base_url`` (empty, or OpenAI's own host) means OpenAI itself rather than a proxy."""
    url = str(base_url or "").strip()
    return not url or urlparse(url).hostname == urlparse(DEFAULT_OPENAI_BASE_URL).hostname


def azure_v1_api_version(api_version: object) -> str | None:
    """The ``api-version`` query the v1 route sends for a setting, or ``None`` for a dated version.

    ``preview``, ``v1`` and empty send ``preview``, ``latest`` sends ``latest``; a dated api-version
    (``YYYY-MM-DD`` or ``YYYY-MM-DD-preview``) selects the legacy deployments route (JAN-356).
    """
    return AZURE_V1_API_VERSIONS.get(str(api_version or "").strip().lower())


def azure_v1_route(api_version: object) -> bool:
    """Whether Azure OpenAI is reached through the v1 endpoint rather than the legacy deployments route."""
    return azure_v1_api_version(api_version) is not None


def _azure_preview_version(api_version: str) -> bool:
    """Whether an Azure api-version can serve ``/openai/responses`` (``preview`` or a ``YYYY-MM-DD-preview``)."""
    version = api_version.strip().lower()
    return version == "preview" or version.endswith("-preview")


#: Human labels of every provider, for log lines and error messages.
PROVIDER_LABELS: dict[models.LLMProvider, str] = {
    models.LLMProvider.OLLAMA: "Ollama",
    models.LLMProvider.OPENAI: "OpenAI",
    models.LLMProvider.OPENROUTER: "OpenRouter",
    models.LLMProvider.GEMINI: "Gemini",
    models.LLMProvider.ANTHROPIC: "Anthropic",
    models.LLMProvider.GITHUB_MODELS: "GitHub Models",
    models.LLMProvider.AZURE_OPENAI: "Azure OpenAI",
}

#: The settings column holding the model of each provider built here (Azure: the deployment).
MODEL_COLUMNS: dict[models.LLMProvider, str] = {
    models.LLMProvider.GITHUB_MODELS: "github_models_model",
    models.LLMProvider.AZURE_OPENAI: "azure_openai_deployment",
}

_token_provider: Callable[[], str] | None = None
_token_provider_lock = threading.Lock()
#: How long a failed Entra sign-in is remembered, so repeated lookups (a debounced settings
#: field, every chat turn) do not each run the whole DefaultAzureCredential chain again.
ENTRA_FAILURE_TTL_SECONDS = 60.0
_last_failure: tuple[float, AzureCredentialError] | None = None


class AzureCredentialError(ValueError):
    """No Azure identity could issue a token for Azure OpenAI in ``entra`` mode."""


def resolved_value(override: dict[str, object] | None, key: str, fallback):
    """Returns a draft override value when sent, otherwise ``fallback`` (the stored value)."""
    if override and key in override:
        return override[key]
    return fallback


def _value(settings: models.Settings, override: dict[str, object] | None, key: str):
    """A draft override value when sent, otherwise the stored column."""
    return resolved_value(override, key, getattr(settings, key))


def _secret(settings: models.Settings, override: dict[str, object] | None, key: str) -> str | None:
    """A draft secret when sent, otherwise the decrypted stored one."""
    if override and key in override:
        return str(override[key] or "") or None
    stored = getattr(settings, key)
    return security.security_service.decrypt(stored) if stored else None


def _missing(provider: models.LLMProvider, message: str) -> ValueError:
    event_logger.log(models.EventType.ERROR, "LLMService", message, {"llm_provider": provider.value})
    return ValueError(message)


def azure_entra_token() -> str:
    """Returns an Entra ID bearer token for Azure OpenAI, creating the shared credential on first use.

    ``get_bearer_token_provider`` caches the token and refreshes it before expiry. A failure is
    remembered for :data:`ENTRA_FAILURE_TTL_SECONDS` and re-raised at once, without the lock.

    Raises:
        AzureCredentialError: When no Azure identity is available (for example no ``az login``).
    """
    global _token_provider, _last_failure
    failure = _last_failure
    if failure is not None and time.monotonic() - failure[0] < ENTRA_FAILURE_TTL_SECONDS:
        raise failure[1]
    try:
        with _token_provider_lock:
            if _token_provider is None:
                # Imported lazily: azure-identity is only needed in ``entra`` mode.
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider

                _token_provider = get_bearer_token_provider(DefaultAzureCredential(), AZURE_COGNITIVE_SERVICES_SCOPE)
            provider = _token_provider
        token = provider()
    except Exception as error:
        detail = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
        wrapped = AzureCredentialError(
            "Azure OpenAI Entra ID sign-in failed: no Azure identity could issue a token. "
            "Run `az login` on the Janus host (or give it a managed identity with the "
            "'Cognitive Services OpenAI User' role), or switch Azure OpenAI to API key auth. "
            f"Detail: {detail}"
        )
        _last_failure = (time.monotonic(), wrapped)
        raise wrapped from error
    _last_failure = None
    return token


def azure_auth_headers(api_key: str | None) -> dict[str, str]:
    """``api-key`` in API key mode, else an Entra ID bearer token; raises :class:`AzureCredentialError`.

    Callers check :func:`is_entra_endpoint` first: the token must only go to Azure OpenAI hosts.
    """
    return {"api-key": api_key} if api_key else {"Authorization": f"Bearer {azure_entra_token()}"}


async def azure_entra_token_async() -> str:
    """:func:`azure_entra_token` for the async client, run in a worker thread.

    Without it ``AsyncAzureOpenAI`` would call the blocking sync provider on the event loop,
    stalling every other request while the credential probes IMDS or runs ``az``.
    """
    return await asyncio.to_thread(azure_entra_token)


def reset_azure_credential() -> None:
    """Drops the shared Azure credential and any remembered failure (used by tests)."""
    global _token_provider, _last_failure
    with _token_provider_lock:
        _token_provider = None
        _last_failure = None


def build_github_models(
    settings: models.Settings, override: dict[str, object] | None, model_name: str, extra: dict[str, object]
) -> BaseChatModel:
    """GitHub Models: the OpenAI client against the GitHub Models inference endpoint, a GitHub token as key."""
    provider = models.LLMProvider.GITHUB_MODELS
    api_key = _secret(settings, override, "github_models_api_key")
    if not api_key:
        raise _missing(provider, "GitHub Models token not found in settings.")
    base_url = _value(settings, override, "github_models_base_url") or GITHUB_MODELS_BASE_URL
    if responses_only_model(model_name):
        raise _responses_only_error(provider, model_name, "use OpenAI without a base URL")
    return ChatOpenAI(
        model=model_name,
        # No `temperature` (JAN-352): reasoning models reject non-default values.
        api_key=api_key,
        base_url=str(base_url),
        # GitHub Models only serves Chat Completions; langchain-openai would otherwise call
        # /responses for some model names, e.g. gpt-6* with tools bound (JAN-354).
        use_responses_api=False,
        **extra,
    )


#: Hosts that may use plain ``http://`` when the e2e test seam allows it (JAN-349).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_azure_endpoint(endpoint: str) -> str:
    """Returns the Azure OpenAI resource root (``https://host``) or raises ``ValueError`` saying why not.

    Azure always serves HTTPS; refusing anything else keeps a key or Entra token off plain HTTP.
    Only the e2e test seam (``JANUS_AZURE_OPENAI_ALLOW_LOOPBACK_HTTP``) admits ``http://`` on
    loopback hosts. Janus appends ``/openai/v1`` itself (or, on the legacy route a dated api-version
    selects, the SDK appends ``/openai/deployments/<deployment>/...``), so a pasted Target URI or a
    ``.../openai/v1/`` URL is refused instead of failing later with an unexplained 404.
    """
    parsed = urlparse(endpoint.strip())
    allow_loopback = config.settings.azure_openai_allow_loopback_http
    loopback = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS and allow_loopback
    if (parsed.scheme != "https" and not loopback) or not parsed.netloc:
        raise ValueError("Azure OpenAI endpoint must be an https:// URL.")
    if parsed.path.strip("/") or parsed.query or parsed.fragment:
        raise ValueError(
            "Azure OpenAI endpoint must be the resource root without a path or query, "
            f"for example {parsed.scheme}://{parsed.netloc}."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


#: Host suffixes of Azure OpenAI / AI Foundry data planes that may receive an Entra ID token.
ENTRA_HOST_SUFFIXES = (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
ENTRA_HOST_MESSAGE = (
    "Entra ID sign-in is only sent to Azure OpenAI endpoints (*.openai.azure.com, "
    "*.cognitiveservices.azure.com, *.services.ai.azure.com); use API key auth for other endpoints."
)


def is_entra_endpoint(endpoint: str) -> bool:
    """Whether ``endpoint`` (a normalized resource root) is an Azure host the host identity's token may go to.

    The token grants the host identity's Cognitive Services access, so a half-typed or foreign
    hostname must never receive it. Loopback hosts pass only with the e2e test seam.
    """
    host = (urlparse(endpoint).hostname or "").lower()
    if host in LOOPBACK_HOSTS:
        return config.settings.azure_openai_allow_loopback_http
    return host.endswith(ENTRA_HOST_SUFFIXES)


def _azure_endpoint(settings: models.Settings, override: dict[str, object] | None) -> str:
    provider = models.LLMProvider.AZURE_OPENAI
    endpoint = str(_value(settings, override, "azure_openai_endpoint") or "").strip()
    if not endpoint:
        raise _missing(provider, "Azure OpenAI endpoint not found in settings.")
    try:
        return normalize_azure_endpoint(endpoint)
    except ValueError as error:
        raise _missing(provider, str(error)) from error


def build_azure_openai(
    settings: models.Settings, override: dict[str, object] | None, model_name: str, extra: dict[str, object]
) -> BaseChatModel:
    """Azure OpenAI / Azure AI Foundry; ``model_name`` is the deployment name.

    The default is the v1 endpoint (:func:`_build_azure_v1`); a dated api-version keeps the legacy
    ``AzureChatOpenAI`` deployments route below (JAN-356). The endpoint, key and Entra host are
    validated once here for both routes (JAN-357).
    """
    provider = models.LLMProvider.AZURE_OPENAI
    endpoint = _azure_endpoint(settings, override)
    if not model_name:
        raise _missing(provider, "Azure OpenAI deployment name not found in settings.")
    api_version = str(_value(settings, override, "azure_openai_api_version") or DEFAULT_AZURE_OPENAI_API_VERSION)
    auth_mode = str(_value(settings, override, "azure_openai_auth_mode") or AZURE_AUTH_ENTRA)
    if auth_mode not in AZURE_AUTH_MODES:
        raise _missing(provider, f"Azure OpenAI auth mode must be one of: {', '.join(AZURE_AUTH_MODES)}.")
    api_key: str | None = None
    if auth_mode == AZURE_AUTH_API_KEY:
        api_key = _secret(settings, override, "azure_openai_api_key")
        if not api_key:
            raise _missing(provider, "Azure OpenAI API key not found in settings.")
    elif not is_entra_endpoint(endpoint):
        raise _missing(provider, ENTRA_HOST_MESSAGE)
    query = azure_v1_api_version(api_version)
    if query is not None:
        return _build_azure_v1(endpoint, model_name, api_key, query, extra)
    # Chat Completions on the deployments route works on every api-version, while /openai/responses
    # needs a preview one. Responses-only deployments therefore need a preview version, and then
    # keep langchain-openai's routing; everything else is forced to Chat Completions (JAN-354).
    responses_only = responses_only_model(model_name)
    if responses_only and not _azure_preview_version(api_version):
        raise _responses_only_error(provider, model_name, "set the Azure API version to a preview version")
    routing: dict[str, object] = {} if responses_only else {"use_responses_api": False}
    credentials: dict[str, object] = (
        {"api_key": api_key}
        if api_key
        else {"azure_ad_token_provider": azure_entra_token, "azure_ad_async_token_provider": azure_entra_token_async}
    )
    return AzureChatOpenAI(
        azure_endpoint=endpoint,
        azure_deployment=model_name,
        model=model_name,
        api_version=api_version,
        # No `temperature` (JAN-352): reasoning deployments such as gpt-6-luna reject non-default values.
        **routing,
        **credentials,
        **extra,
    )


def _responses_model(model: str) -> bool:
    """Models that need the Responses API for tools (gpt-6, JAN-356) or for everything (Responses-only)."""
    name = model.strip().lower().rsplit("/", 1)[-1]
    return name.startswith("gpt-6") or responses_only_model(name)


class _DropBearer(httpx.BaseTransport):
    """Sends an Azure API key once: a request carrying ``api-key`` loses the client's placeholder bearer."""

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "api-key" in request.headers:
            request.headers.pop("authorization", None)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class _AsyncDropBearer(httpx.AsyncBaseTransport):
    """The async twin of :class:`_DropBearer`."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "api-key" in request.headers:
            request.headers.pop("authorization", None)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _azure_transport() -> httpx.BaseTransport:
    """The network transport under an API-key v1 client (replaced in tests)."""
    return httpx.HTTPTransport()


def _azure_async_transport() -> httpx.AsyncBaseTransport:
    """The async network transport under an API-key v1 client (replaced in tests)."""
    return httpx.AsyncHTTPTransport()


def _build_azure_v1(
    endpoint: str, deployment: str, api_key: str | None, query: str, extra: dict[str, object]
) -> BaseChatModel:
    """The v1 endpoint (``{endpoint}/openai/v1``) through the plain OpenAI client (JAN-356, JAN-357).

    It serves both Chat Completions and the Responses API. The choice follows the deployment's
    underlying model (:func:`deployment_model`), not its name: gpt-6 and Responses-only models use
    ``/responses``, the only place Azure accepts function tools together with reasoning; other
    models (Kimi-K2.6, ...) use ``/chat/completions``. If the model cannot be read, langchain-openai
    decides from the deployment name. ``AzureChatOpenAI`` cannot be used here: with a ``base_url``
    the Azure SDK client rewrites ``/chat/completions`` to ``/deployments/<model>/...``, which 404s
    on v1. Entra mode passes the token function as a callable key (a refreshed bearer token); API
    key mode sends the key only in the ``api-key`` header.
    """
    # Imported here: the lookup module builds on this one.
    from janus.services.azure_deployment_models import deployment_model

    model = deployment_model(endpoint, deployment, api_key)
    routing: dict[str, object] = {} if model is None else {"use_responses_api": _responses_model(model)}
    credentials: dict[str, object] = {"api_key": azure_entra_token}
    if api_key:
        # The OpenAI client always builds an Authorization header from its key; the key goes only
        # in ``api-key``, and this client's own transports drop the placeholder bearer (JAN-357).
        credentials = {
            "api_key": "unused",
            "default_headers": {"api-key": api_key},
            "http_client": httpx.Client(transport=_DropBearer(_azure_transport())),
            "http_async_client": httpx.AsyncClient(transport=_AsyncDropBearer(_azure_async_transport())),
        }
    return ChatOpenAI(
        model=deployment,
        base_url=f"{endpoint}/openai/v1",
        default_query={"api-version": query},
        # No `temperature` (JAN-352): reasoning deployments such as gpt-6-luna reject non-default values.
        **routing,
        **{**extra, **credentials},
    )


#: Provider -> builder(settings, settings_override, model_name, extra client kwargs).
BUILDERS: dict[
    models.LLMProvider,
    Callable[[models.Settings, dict[str, object] | None, str, dict[str, object]], BaseChatModel],
] = {
    models.LLMProvider.GITHUB_MODELS: build_github_models,
    models.LLMProvider.AZURE_OPENAI: build_azure_openai,
}
