"""Tests for per-provider LLM model listing (JAN-215)."""

import httpx
import pytest

from janus import models, security
from janus.services import llm_model_catalog as catalog
from janus.services.llm_model_catalog import CatalogCredentials, ModelCatalogError

#: Providers whose listing needs only a key and a URL; Azure OpenAI (endpoint and auth mode, JAN-349)
#: is covered in test_llm_provider_clients.py.
LISTABLE_PROVIDERS = [provider for provider in models.LLMProvider if provider != models.LLMProvider.AZURE_OPENAI]


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    catalog.clear_cache()
    yield
    catalog.clear_cache()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _recording_client(response: httpx.Response, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return _client(handler)


# --- success paths, one per provider ---------------------------------------


def test_openai_lists_chat_models_and_filters_non_chat_ids():
    """OpenAI listings drop embedding/audio/image/moderation ids and send a bearer key."""
    seen: list[httpx.Request] = []
    payload = {
        "data": [
            {"id": "gpt-4o-mini"},
            {"id": "text-embedding-3-small"},
            {"id": "whisper-1"},
            {"id": "tts-1"},
            {"id": "dall-e-3"},
            {"id": "omni-moderation-latest"},
            {"id": "gpt-4.1"},
        ]
    }
    client = _recording_client(httpx.Response(200, json=payload), seen)

    options = catalog.list_models(models.LLMProvider.OPENAI, CatalogCredentials("sk-test", None), client=client)

    assert [option.id for option in options] == ["gpt-4.1", "gpt-4o-mini"]
    assert str(seen[0].url) == "https://api.openai.com/v1/models"
    assert seen[0].headers["authorization"] == "Bearer sk-test"


def test_openai_uses_custom_base_url():
    """An OpenAI-compatible base URL override is honoured (trailing slash tolerated)."""
    seen: list[httpx.Request] = []
    client = _recording_client(httpx.Response(200, json={"data": [{"id": "local-model"}]}), seen)

    catalog.list_models(
        models.LLMProvider.OPENAI, CatalogCredentials("sk-test", "http://vllm.internal:8000/v1/"), client=client
    )

    assert str(seen[0].url) == "http://vllm.internal:8000/v1/models"


def test_openrouter_lists_models_without_key():
    """OpenRouter's public listing works without a key and uses the display name as label."""
    seen: list[httpx.Request] = []
    payload = {"data": [{"id": "openai/gpt-4o-mini", "name": "OpenAI: GPT-4o-mini"}, {"id": "openrouter/auto"}]}
    client = _recording_client(httpx.Response(200, json=payload), seen)

    options = catalog.list_models(models.LLMProvider.OPENROUTER, CatalogCredentials(None, None), client=client)

    assert [(option.id, option.label) for option in options] == [
        ("openai/gpt-4o-mini", "OpenAI: GPT-4o-mini"),
        ("openrouter/auto", "openrouter/auto"),
    ]
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/models"
    assert "authorization" not in seen[0].headers


def test_gemini_lists_generate_content_models_with_header_key():
    """Gemini keeps only generateContent models and sends the key as a header, not a query param."""
    seen: list[httpx.Request] = []
    payload = {
        "models": [
            {
                "name": "models/gemini-2.5-flash",
                "displayName": "Gemini 2.5 Flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    client = _recording_client(httpx.Response(200, json=payload), seen)

    options = catalog.list_models(models.LLMProvider.GEMINI, CatalogCredentials("AIza-test", None), client=client)

    assert [(option.id, option.label) for option in options] == [("gemini-2.5-flash", "Gemini 2.5 Flash")]
    assert seen[0].url.path == "/v1beta/models"
    assert "key" not in seen[0].url.params
    assert seen[0].headers["x-goog-api-key"] == "AIza-test"


def test_anthropic_lists_models_with_version_header():
    """Anthropic listings send x-api-key plus the anthropic-version header."""
    seen: list[httpx.Request] = []
    payload = {
        "data": [
            {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5", "type": "model"},
            {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "type": "model"},
        ],
        "has_more": False,
    }
    client = _recording_client(httpx.Response(200, json=payload), seen)

    options = catalog.list_models(models.LLMProvider.ANTHROPIC, CatalogCredentials("sk-ant-test", None), client=client)

    assert [(option.id, option.label) for option in options] == [
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
    ]
    assert seen[0].url.path == "/v1/models"
    assert seen[0].url.host == "api.anthropic.com"
    assert seen[0].headers["x-api-key"] == "sk-ant-test"
    assert seen[0].headers["anthropic-version"] == catalog.ANTHROPIC_API_VERSION


def test_ollama_lists_local_tags():
    """Ollama models come from the local /api/tags endpoint."""
    seen: list[httpx.Request] = []
    payload = {"models": [{"name": "llama3:latest"}, {"name": "gemma3:27b"}]}
    client = _recording_client(httpx.Response(200, json=payload), seen)

    options = catalog.list_models(
        models.LLMProvider.OLLAMA, CatalogCredentials(None, "http://ollama:11434"), client=client
    )

    assert [option.id for option in options] == ["gemma3:27b", "llama3:latest"]
    assert str(seen[0].url) == "http://ollama:11434/api/tags"


def test_ollama_cloud_normalizes_api_suffix_and_sends_bearer():
    """Ollama Cloud's /api base URL is normalized and the key is sent as a bearer token."""
    seen: list[httpx.Request] = []
    client = _recording_client(httpx.Response(200, json={"models": [{"name": "gpt-oss:120b"}]}), seen)

    catalog.list_models(
        models.LLMProvider.OLLAMA, CatalogCredentials("ollama-key", "https://ollama.com/api"), client=client
    )

    assert str(seen[0].url) == "https://ollama.com/api/tags"
    assert seen[0].headers["authorization"] == "Bearer ollama-key"


# --- failure paths ---------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        models.LLMProvider.OPENAI,
        models.LLMProvider.GEMINI,
        models.LLMProvider.ANTHROPIC,
        models.LLMProvider.GITHUB_MODELS,
    ],
)
def test_key_required_providers_fail_without_calling_out(provider):
    """Providers that need a key fail fast with a helpful message and no request."""

    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("no request expected")

    with pytest.raises(ModelCatalogError, match="API key"):
        catalog.list_models(provider, CatalogCredentials(None, None), client=_client(handler))


@pytest.mark.parametrize("provider", LISTABLE_PROVIDERS)
def test_http_error_is_reported_without_the_key(provider):
    """Non-2xx responses become a short error that never echoes the key."""
    client = _client(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(ModelCatalogError) as excinfo:
        catalog.list_models(provider, CatalogCredentials("secret-key", None), client=client)

    assert "HTTP 500" in str(excinfo.value)
    assert "secret-key" not in str(excinfo.value)


@pytest.mark.parametrize("provider", LISTABLE_PROVIDERS)
def test_auth_rejection_has_clear_message(provider):
    """401/403 responses tell the user the key was rejected."""
    client = _client(lambda request: httpx.Response(401, json={"error": "invalid"}))

    with pytest.raises(ModelCatalogError, match="rejected the API key"):
        catalog.list_models(provider, CatalogCredentials("bad-key", None), client=client)


@pytest.mark.parametrize("provider", LISTABLE_PROVIDERS)
def test_network_error_is_reported(provider):
    """Connection failures become a short 'could not reach' error."""

    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ModelCatalogError, match="Could not reach"):
        catalog.list_models(provider, CatalogCredentials("key", None), client=_client(handler))


def test_unexpected_payload_shape_is_reported():
    """A malformed provider payload becomes a catalog error, not a crash."""
    client = _client(lambda request: httpx.Response(200, json={"unexpected": True}))

    with pytest.raises(ModelCatalogError, match="unexpected"):
        catalog.list_models(models.LLMProvider.ANTHROPIC, CatalogCredentials("key", None), client=client)


def test_non_http_base_url_is_rejected():
    """Only http(s) base URLs are fetched."""
    with pytest.raises(ModelCatalogError, match="http"):
        catalog.list_models(models.LLMProvider.OLLAMA, CatalogCredentials(None, "file:///etc/passwd"))


# --- cache -----------------------------------------------------------------


def test_successful_listing_is_cached_until_ttl_expires():
    """Successful listings are cached per provider/key/base URL for the TTL."""
    seen: list[httpx.Request] = []
    client = _recording_client(httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}]}), seen)
    clock = [1000.0]
    credentials = CatalogCredentials("sk-ant", None)

    catalog.list_models(models.LLMProvider.ANTHROPIC, credentials, client=client, now=lambda: clock[0])
    catalog.list_models(models.LLMProvider.ANTHROPIC, credentials, client=client, now=lambda: clock[0])
    assert len(seen) == 1

    # A different key is a different cache entry.
    catalog.list_models(
        models.LLMProvider.ANTHROPIC, CatalogCredentials("other", None), client=client, now=lambda: clock[0]
    )
    assert len(seen) == 2

    clock[0] += catalog.CACHE_TTL_SECONDS + 1
    catalog.list_models(models.LLMProvider.ANTHROPIC, credentials, client=client, now=lambda: clock[0])
    assert len(seen) == 3


def test_failures_are_not_cached():
    """Errors are not cached so a fixed key or URL works on the next lookup."""
    responses = iter([httpx.Response(503), httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})])
    client = _client(lambda request: next(responses))

    with pytest.raises(ModelCatalogError):
        catalog.list_models(models.LLMProvider.OPENAI, CatalogCredentials("k", None), client=client)
    options = catalog.list_models(models.LLMProvider.OPENAI, CatalogCredentials("k", None), client=client)
    assert [option.id for option in options] == ["gpt-4o"]


# --- stored credential policy ----------------------------------------------


def _settings(**values) -> models.Settings:
    return models.Settings(**values)


def test_resolve_prefers_draft_key():
    """A draft (unsaved) key always wins over the stored key."""
    settings = _settings(anthropic_api_key=security.security_service.encrypt("stored"))
    credentials = catalog.resolve_credentials(settings, models.LLMProvider.ANTHROPIC, " draft ", None)
    assert credentials.api_key == "draft"


def test_resolve_uses_stored_key_for_stored_base_url():
    """The stored key is used when the draft URL matches the stored URL."""
    settings = _settings(
        openai_api_key=security.security_service.encrypt("stored"),
        openai_base_url="https://proxy.example/v1",
    )
    credentials = catalog.resolve_credentials(settings, models.LLMProvider.OPENAI, None, "https://proxy.example/v1/")
    assert credentials.api_key == "stored"
    assert credentials.stored_key_withheld is False


def test_resolve_treats_empty_draft_url_as_provider_default():
    """An empty draft URL means the provider default, which matches a stored default URL."""
    settings = _settings(
        openrouter_api_key=security.security_service.encrypt("stored"),
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    credentials = catalog.resolve_credentials(settings, models.LLMProvider.OPENROUTER, "", "")
    assert credentials.api_key == "stored"
    assert credentials.base_url is None


def test_resolve_withholds_stored_key_from_a_different_base_url():
    """The stored key is never sent to a draft base URL that differs from the stored one."""
    settings = _settings(anthropic_api_key=security.security_service.encrypt("stored"))
    credentials = catalog.resolve_credentials(settings, models.LLMProvider.ANTHROPIC, None, "https://attacker.example")
    assert credentials.api_key is None
    assert credentials.stored_key_withheld is True

    with pytest.raises(ModelCatalogError, match="custom base URL"):
        catalog.list_models(models.LLMProvider.ANTHROPIC, credentials)
