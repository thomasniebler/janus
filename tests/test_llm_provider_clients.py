"""Tests for the GitHub Models and Azure OpenAI providers (JAN-340)."""

import asyncio
import json
import sys
import threading
import types

import httpx
import pytest

from janus import database, llm_service, models, security
from janus.services import azure_deployment_models as deployments
from janus.services import llm_model_catalog as catalog
from janus.services import llm_provider_clients as clients
from janus.services import llm_settings
from janus.services.llm_model_catalog import CatalogCredentials, ModelCatalogError


@pytest.fixture
def auth_client(client, db):
    """An admin session on the test client."""
    db.add(
        models.User(
            username="admin", hashed_password=security.security_service.get_password_hash("admin"), is_admin=True
        )
    )
    db.commit()
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.headers[security.CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return client


#: The real lookup, kept before the autouse fixture stubs it out (JAN-357).
_REAL_FETCH_DEPLOYMENT_MODEL = deployments._fetch_deployment_model


@pytest.fixture(autouse=True)
def _fresh_azure_credential(monkeypatch):
    clients.reset_azure_credential()
    deployments.reset()
    catalog.clear_cache()
    # No real deployment lookups: by default the model is unknown (the deployment-name heuristic).
    monkeypatch.setattr(deployments, "_fetch_deployment_model", lambda endpoint, deployment, api_key: None)
    yield
    clients.reset_azure_credential()
    deployments.reset()
    catalog.clear_cache()


class _Recorder:
    """A stand-in chat client class that records its constructor kwargs."""

    def __init__(self):
        self.calls: list[dict[str, object]] = []
        recorder = self

        class Fake:
            def __init__(self, **kwargs):
                recorder.calls.append(kwargs)

            def invoke(self, prompt):
                return prompt

        self.cls = Fake

    @property
    def kwargs(self) -> dict[str, object]:
        assert len(self.calls) == 1
        return self.calls[0]


@pytest.fixture
def fake_openai(monkeypatch):
    """Records the ChatOpenAI kwargs the GitHub Models builder passes."""
    recorder = _Recorder()
    monkeypatch.setattr(clients, "ChatOpenAI", recorder.cls)
    return recorder


@pytest.fixture
def fake_azure(monkeypatch):
    """Records the AzureChatOpenAI kwargs the Azure builder passes."""
    recorder = _Recorder()
    monkeypatch.setattr(clients, "AzureChatOpenAI", recorder.cls)
    return recorder


def _store(db, monkeypatch, **values) -> None:
    db.add(models.Settings(**values))
    db.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db.__class__(bind=db.get_bind()))


# --- GitHub Models ----------------------------------------------------------


def test_github_models_uses_decrypted_token_default_base_url_and_default_model(db, monkeypatch, fake_openai):
    """The stored token is decrypted and sent to the GitHub Models inference endpoint."""
    _store(
        db,
        monkeypatch,
        llm_provider=models.LLMProvider.GITHUB_MODELS,
        github_models_api_key=security.security_service.encrypt("gho_secret"),
    )

    service = llm_service.LLMService()

    assert fake_openai.kwargs["api_key"] == "gho_secret"
    assert fake_openai.kwargs["base_url"] == "https://models.github.ai/inference"
    assert fake_openai.kwargs["model"] == "openai/gpt-4.1-mini"
    assert service.model_label == "github_models/openai/gpt-4.1-mini"


def test_github_models_honours_base_url_override_and_draft_values(db, monkeypatch, fake_openai):
    """Draft values (connection test) win over stored ones, including a base URL override."""
    _store(db, monkeypatch, llm_provider=models.LLMProvider.OLLAMA)

    llm_service.LLMService(
        settings_override={
            "llm_provider": "github_models",
            "github_models_model": "meta/llama-4",
            "github_models_api_key": "draft-token",
            "github_models_base_url": "https://models.example.internal/inference",
        }
    )

    assert fake_openai.kwargs["model"] == "meta/llama-4"
    assert fake_openai.kwargs["api_key"] == "draft-token"
    assert fake_openai.kwargs["base_url"] == "https://models.example.internal/inference"


def test_github_models_without_token_raises_configuration_error(db, monkeypatch, fake_openai):
    """A missing token fails like the other cloud providers, before any client is built."""
    _store(db, monkeypatch, llm_provider=models.LLMProvider.GITHUB_MODELS)

    with pytest.raises(ValueError, match="GitHub Models token not found"):
        llm_service.LLMService()
    assert fake_openai.calls == []


def test_github_models_works_as_a_per_surface_override(db, monkeypatch, fake_openai):
    """A chat/utility override to GitHub Models uses its token and the chosen model (JAN-233)."""
    _store(
        db,
        monkeypatch,
        llm_provider=models.LLMProvider.OLLAMA,
        github_models_api_key=security.security_service.encrypt("gho_secret"),
    )

    service = llm_service.build_llm_service(
        db, llm_service.ModelChoice(provider="github_models", model="openai/gpt-4.1"), purpose="chat"
    )

    assert fake_openai.kwargs["model"] == "openai/gpt-4.1"
    assert fake_openai.kwargs["api_key"] == "gho_secret"
    assert service.model_label == "github_models/openai/gpt-4.1"


# --- Azure OpenAI -----------------------------------------------------------


def _azure_settings(**overrides) -> dict[str, object]:
    """Azure settings on the legacy deployments route (a dated api-version); v1 tests pass ``preview`` (JAN-356)."""
    values: dict[str, object] = {
        "llm_provider": models.LLMProvider.AZURE_OPENAI,
        "azure_openai_endpoint": "https://example-foundry.cognitiveservices.azure.com/",
        "azure_openai_deployment": "Kimi-K2.6",
        "azure_openai_api_version": "2024-10-21",
    }
    values.update(overrides)
    return values


def test_azure_entra_mode_passes_a_token_provider_and_no_api_key(db, monkeypatch, fake_azure):
    """Entra mode (the default) authenticates with a token provider; no static key reaches the client."""
    _store(db, monkeypatch, **_azure_settings(azure_openai_api_key=security.security_service.encrypt("unused")))

    service = llm_service.LLMService()

    kwargs = fake_azure.kwargs
    assert kwargs["azure_ad_token_provider"] is clients.azure_entra_token
    assert kwargs["azure_ad_async_token_provider"] is clients.azure_entra_token_async
    assert "api_key" not in kwargs
    assert kwargs["azure_endpoint"] == "https://example-foundry.cognitiveservices.azure.com"
    assert kwargs["azure_deployment"] == "Kimi-K2.6"
    assert kwargs["api_version"] == "2024-10-21"
    assert service.model_label == "azure_openai/Kimi-K2.6"


def test_azure_api_key_mode_passes_the_decrypted_key(db, monkeypatch, fake_azure):
    """API key mode sends the stored key and no token provider."""
    _store(
        db,
        monkeypatch,
        **_azure_settings(
            azure_openai_auth_mode="api_key",
            azure_openai_api_key=security.security_service.encrypt("azure-secret"),
            azure_openai_api_version="2025-04-01-preview",
        ),
    )

    llm_service.LLMService()

    assert fake_azure.kwargs["api_key"] == "azure-secret"
    assert "azure_ad_token_provider" not in fake_azure.kwargs
    assert "azure_ad_async_token_provider" not in fake_azure.kwargs
    assert fake_azure.kwargs["api_version"] == "2025-04-01-preview"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"azure_openai_endpoint": None}, "endpoint not found"),
        ({"azure_openai_endpoint": "http://plain.example.com"}, "must be an https:// URL"),
        (
            {"azure_openai_endpoint": "https://x.cognitiveservices.azure.com/openai/v1/"},
            r"resource root without a path or query, for example https://x\.cognitiveservices\.azure\.com\.",
        ),
        (
            {"azure_openai_endpoint": "https://x.openai.azure.com/openai/deployments/d/chat/completions?api-version=1"},
            "resource root",
        ),
        ({"azure_openai_deployment": None}, "deployment name not found"),
        ({"azure_openai_auth_mode": "api_key"}, "API key not found"),
        ({"azure_openai_auth_mode": "magic"}, "auth mode must be one of"),
    ],
)
def test_azure_configuration_errors_are_clear(db, monkeypatch, fake_azure, overrides, message):
    """Missing or invalid Azure settings fail with a clear ValueError before a client is built."""
    _store(db, monkeypatch, **_azure_settings(**overrides))

    with pytest.raises(ValueError, match=message):
        llm_service.LLMService()
    assert fake_azure.calls == []


def test_azure_works_as_a_per_surface_override_with_the_model_as_deployment(db, monkeypatch, fake_azure):
    """A utility override to Azure uses the chosen model as the deployment name (JAN-233)."""
    _store(db, monkeypatch, **_azure_settings(llm_provider=models.LLMProvider.OLLAMA))

    llm_service.build_llm_service(
        db, llm_service.ModelChoice(provider="azure_openai", model="gpt-4.1-mini"), purpose="utility"
    )

    assert fake_azure.kwargs["azure_deployment"] == "gpt-4.1-mini"
    assert fake_azure.kwargs["azure_ad_token_provider"] is clients.azure_entra_token


def _install_fake_azure_identity(monkeypatch, credential_factory) -> list[str]:
    """Replaces ``azure.identity`` with a fake; returns the scopes tokens were requested for."""
    scopes: list[str] = []

    def get_bearer_token_provider(credential, scope):
        scopes.append(scope)
        return lambda: credential.token()

    module = types.ModuleType("azure.identity")
    module.DefaultAzureCredential = credential_factory
    module.get_bearer_token_provider = get_bearer_token_provider
    monkeypatch.setitem(sys.modules, "azure.identity", module)
    return scopes


def test_azure_client_construction_does_not_touch_the_credential(db, monkeypatch):
    """Building the client never creates the Azure credential, so a missing ``az login`` cannot break startup."""

    def exploding_credential():
        raise AssertionError("credential must be created lazily")

    _install_fake_azure_identity(monkeypatch, exploding_credential)
    _store(db, monkeypatch, **_azure_settings())

    service = llm_service.LLMService()

    assert service.llm.azure_ad_token_provider is clients.azure_entra_token


def test_azure_entra_token_creates_one_shared_credential_for_the_cognitive_services_scope(monkeypatch):
    """The credential is built on the first token request, then reused."""
    created: list[object] = []

    class Credential:
        def __init__(self):
            created.append(self)

        def token(self):
            return "entra-token"

    scopes = _install_fake_azure_identity(monkeypatch, Credential)

    assert clients.azure_entra_token() == "entra-token"
    assert clients.azure_entra_token() == "entra-token"
    assert len(created) == 1
    assert scopes == ["https://cognitiveservices.azure.com/.default"]


def test_azure_async_token_provider_runs_the_credential_off_the_event_loop(monkeypatch):
    """The async client's provider runs the blocking credential in a worker thread, not on the loop."""
    threads: list[threading.Thread] = []

    class Credential:
        def token(self):
            threads.append(threading.current_thread())
            return "entra-token"

    _install_fake_azure_identity(monkeypatch, Credential)

    async def call():
        return threading.current_thread(), await clients.azure_entra_token_async()

    loop_thread, token = asyncio.run(call())

    assert token == "entra-token"
    assert threads and threads[0] is not loop_thread


def test_azure_entra_token_wraps_a_missing_login_in_a_clear_error(monkeypatch):
    """No usable Azure identity surfaces as AzureCredentialError naming the fix."""

    class Credential:
        def token(self):
            raise RuntimeError("DefaultAzureCredential failed to retrieve a token\nmore detail")

    _install_fake_azure_identity(monkeypatch, Credential)

    with pytest.raises(clients.AzureCredentialError, match="az login") as excinfo:
        clients.azure_entra_token()
    assert "DefaultAzureCredential failed to retrieve a token" in str(excinfo.value)
    assert "more detail" not in str(excinfo.value)


def test_azure_connection_test_reports_the_credential_error(db, monkeypatch):
    """The settings connection test shows the Entra error instead of crashing."""

    class Credential:
        def token(self):
            raise RuntimeError("Please run 'az login' to set up an account")

    _install_fake_azure_identity(monkeypatch, Credential)
    _store(db, monkeypatch, **_azure_settings())

    service = llm_service.LLMService()  # the real AzureChatOpenAI asks for a token before any request

    ok, message = service.test_connection()

    assert ok is False
    assert "Entra ID sign-in failed" in message


# --- settings update / payload ----------------------------------------------


def test_settings_update_stores_new_provider_fields():
    """Tokens and keys are encrypted, optional URLs clear to NULL, and the legacy model routes correctly."""
    settings = models.Settings(llm_provider=models.LLMProvider.AZURE_OPENAI, github_models_base_url="https://x")
    update = types.SimpleNamespace(
        github_models_model="openai/gpt-4.1",
        github_models_api_key="gho_secret",
        github_models_base_url="",
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="azure-secret",
        azure_openai_auth_mode="api_key",
        llm_model="gpt-4o-deployment",
    )

    llm_settings.apply_llm_settings_update(settings, update)

    assert settings.github_models_model == "openai/gpt-4.1"
    assert security.security_service.decrypt(settings.github_models_api_key) == "gho_secret"
    assert settings.github_models_base_url is None
    assert settings.azure_openai_endpoint == "https://example.openai.azure.com"
    assert security.security_service.decrypt(settings.azure_openai_api_key) == "azure-secret"
    assert settings.azure_openai_auth_mode == "api_key"
    assert settings.azure_openai_deployment == "gpt-4o-deployment"


def test_system_settings_api_round_trips_github_models_and_azure(auth_client, db):
    """The settings API exposes the new fields, keeps secrets as flags, and rejects an unknown auth mode."""
    payload = auth_client.get("/api/planner/settings").json()
    assert payload["github_models_model"] == "openai/gpt-4.1-mini"
    assert payload["github_models_api_key_configured"] is False
    assert payload["azure_openai_auth_mode"] == "entra"
    assert payload["azure_openai_api_version"] == "preview"

    response = auth_client.patch(
        "/api/planner/settings",
        json={
            "llm_provider": "azure_openai",
            "azure_openai_endpoint": "https://example.openai.azure.com",
            "azure_openai_deployment": "gpt-4.1-mini",
            "azure_openai_api_key": "azure-secret",
            "github_models_api_key": "gho_secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"] == "azure_openai"
    assert payload["llm_model"] == "gpt-4.1-mini"
    assert payload["azure_openai_api_key_configured"] is True
    assert payload["github_models_api_key_configured"] is True
    assert "azure_openai_api_key" not in payload
    assert "gho_secret" not in response.text

    response = auth_client.patch("/api/planner/settings", json={"azure_openai_auth_mode": "magic"})
    assert response.status_code == 422

    response = auth_client.patch("/api/planner/settings", json={"chat_llm_provider": "github_models"})
    assert response.status_code == 200
    assert response.json()["chat_llm_provider"] == "github_models"


# --- model catalog ----------------------------------------------------------


def test_github_models_catalog_lists_models_with_bearer_token():
    """GitHub Models lists chat models from the catalog beside the inference API, without embedding models."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {"id": "openai/gpt-4.1", "name": "OpenAI GPT-4.1", "supported_output_modalities": ["text"]},
                {"id": "meta/llama"},
                {"id": "openai/text-embedding-3-small", "supported_output_modalities": ["embeddings"]},
            ],
        )

    options = catalog.list_models(
        models.LLMProvider.GITHUB_MODELS,
        CatalogCredentials("gho_secret", None),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [(option.id, option.label) for option in options] == [
        ("meta/llama", "meta/llama"),
        ("openai/gpt-4.1", "OpenAI GPT-4.1"),
    ]
    assert str(seen[0].url) == "https://models.github.ai/catalog/models"
    assert seen[0].headers["authorization"] == "Bearer gho_secret"


def test_github_models_catalog_needs_a_token():
    """Without a token the lookup fails before calling out."""
    with pytest.raises(ModelCatalogError, match="GitHub Models API key"):
        catalog.list_models(models.LLMProvider.GITHUB_MODELS, CatalogCredentials(None, None))


def _azure_client(response: httpx.Response, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


_DEPLOYMENTS = {
    "data": [
        {"id": "Kimi-K2.6", "model": "Kimi-K2.6", "status": "succeeded"},
        {"id": "chat", "model": "gpt-4.1-mini", "status": "succeeded"},
        {"id": "legacy"},
    ]
}


def test_azure_lists_deployments_with_an_api_key():
    """API key mode lists the resource's deployments with the api-key header (JAN-349)."""
    seen: list[httpx.Request] = []
    credentials = CatalogCredentials("azure-key", "https://x.openai.azure.com/", auth_mode="api_key")

    options = catalog.list_models(
        models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200, json=_DEPLOYMENTS), seen)
    )

    assert [(option.id, option.label) for option in options] == [
        ("chat", "chat (gpt-4.1-mini)"),
        ("Kimi-K2.6", "Kimi-K2.6 (Kimi-K2.6)"),
        ("legacy", "legacy"),
    ]
    assert str(seen[0].url) == "https://x.openai.azure.com/openai/deployments?api-version=2023-03-15-preview"
    assert seen[0].headers["api-key"] == "azure-key"
    assert "authorization" not in seen[0].headers


def test_azure_lists_deployments_with_an_entra_token(monkeypatch):
    """Entra ID mode sends the host identity's bearer token and no key."""

    class Credential:
        def token(self):
            return "entra-token"

    _install_fake_azure_identity(monkeypatch, Credential)
    seen: list[httpx.Request] = []
    credentials = CatalogCredentials(None, "https://x.cognitiveservices.azure.com", auth_mode="entra")

    options = catalog.list_models(
        models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200, json=_DEPLOYMENTS), seen)
    )

    assert [option.id for option in options] == ["chat", "Kimi-K2.6", "legacy"]
    assert seen[0].headers["authorization"] == "Bearer entra-token"
    assert "api-key" not in seen[0].headers


def test_azure_permission_denied_falls_back_to_manual_entry():
    """A 403 PermissionDenied says the identity cannot read deployments and asks for the name."""
    denied = httpx.Response(403, json={"error": {"code": "PermissionDenied", "message": "lacks permission"}})
    credentials = CatalogCredentials("azure-key", "https://x.openai.azure.com", auth_mode="api_key")

    with pytest.raises(ModelCatalogError, match=r"PermissionDenied.*enter the deployment name"):
        catalog.list_models(models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(denied, []))


def test_azure_listing_without_sign_in_explains_the_fix(monkeypatch):
    """No Azure identity fails before any request, naming `az login` and manual entry."""

    class Credential:
        def token(self):
            raise RuntimeError("DefaultAzureCredential failed to retrieve a token")

    _install_fake_azure_identity(monkeypatch, Credential)
    seen: list[httpx.Request] = []
    credentials = CatalogCredentials(None, "https://x.openai.azure.com", auth_mode="entra")

    with pytest.raises(ModelCatalogError, match=r"az login.*enter the deployment name"):
        catalog.list_models(
            models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200, json={}), seen)
        )
    assert seen == []


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        (None, "Enter the Azure OpenAI endpoint"),
        ("http://x.openai.azure.com", "must be an https:// URL"),
        ("https://x.openai.azure.com/openai/v1/", "resource root"),
    ],
)
def test_azure_listing_validates_the_endpoint_like_the_chat_client(endpoint, message):
    """The listing refuses the same endpoints the chat client refuses, before any request."""
    credentials = CatalogCredentials("azure-key", endpoint, auth_mode="api_key")

    with pytest.raises(ModelCatalogError, match=message):
        catalog.list_models(models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200), []))


def test_azure_api_key_mode_without_a_key_needs_one():
    """API key mode with no key (none stored, or withheld from a draft endpoint) asks for it."""
    with pytest.raises(ModelCatalogError, match="Enter an Azure OpenAI API key"):
        catalog.list_models(
            models.LLMProvider.AZURE_OPENAI, CatalogCredentials(None, "https://x.openai.azure.com", auth_mode="api_key")
        )
    withheld = CatalogCredentials(None, "https://other.openai.azure.com", stored_key_withheld=True, auth_mode="api_key")
    with pytest.raises(ModelCatalogError, match="list deployments from a changed endpoint"):
        catalog.list_models(models.LLMProvider.AZURE_OPENAI, withheld)


def test_azure_resolve_credentials_follows_the_auth_mode():
    """Entra mode never resolves a key; API key mode withholds the stored key from a draft endpoint."""
    settings = models.Settings(
        llm_provider=models.LLMProvider.AZURE_OPENAI,
        azure_openai_auth_mode="api_key",
        azure_openai_endpoint="https://mine.openai.azure.com",
        azure_openai_api_key=security.security_service.encrypt("azure-secret"),
    )
    provider = models.LLMProvider.AZURE_OPENAI

    stored = catalog.resolve_credentials(settings, provider, None, None)
    assert (stored.api_key, stored.auth_mode) == ("azure-secret", "api_key")
    draft_endpoint = catalog.resolve_credentials(settings, provider, None, "https://evil.example.com")
    assert draft_endpoint.api_key is None and draft_endpoint.stored_key_withheld
    entra = catalog.resolve_credentials(settings, provider, None, "https://other.openai.azure.com", "entra")
    assert (entra.api_key, entra.auth_mode, entra.stored_key_withheld) == (None, "entra", False)


def test_plain_http_endpoints_need_the_e2e_test_seam(monkeypatch):
    """Production accepts only https; the e2e seam admits http on loopback hosts, never elsewhere."""
    assert clients.normalize_azure_endpoint("https://x.openai.azure.com/") == "https://x.openai.azure.com"
    with pytest.raises(ValueError, match="https://"):
        clients.normalize_azure_endpoint("http://127.0.0.1:8123")

    monkeypatch.setattr(clients.config.settings, "azure_openai_allow_loopback_http", True)
    assert clients.normalize_azure_endpoint("http://127.0.0.1:8123/") == "http://127.0.0.1:8123"
    with pytest.raises(ValueError, match="https://"):
        clients.normalize_azure_endpoint("http://x.openai.azure.com")


def test_entra_listing_never_mints_a_token_for_a_non_azure_host(monkeypatch):
    """A half-typed or foreign endpoint in Entra mode gets the manual-entry fallback, not a live token."""
    minted: list[str] = []

    class Credential:
        def token(self):
            minted.append("token")
            return "entra-token"

    _install_fake_azure_identity(monkeypatch, Credential)
    seen: list[httpx.Request] = []
    for endpoint in ("https://x.openai.azure", "https://evil.example.com", "https://x.openai.azure.com.evil.io"):
        credentials = CatalogCredentials(None, endpoint, auth_mode="entra")
        with pytest.raises(ModelCatalogError, match=r"only sent to Azure OpenAI endpoints.*deployment name"):
            catalog.list_models(
                models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200), seen)
            )
    assert minted == [] and seen == []

    for endpoint in (
        "https://a.openai.azure.com",
        "https://b.cognitiveservices.azure.com",
        "https://c.services.ai.azure.com",
    ):
        assert clients.is_entra_endpoint(endpoint)


def test_entra_chat_client_refuses_a_non_azure_endpoint(db, monkeypatch, fake_azure):
    """The connection test with a draft foreign endpoint in Entra mode fails before any token is minted."""
    _store(db, monkeypatch, **_azure_settings(azure_openai_endpoint="https://proxy.example.com"))

    with pytest.raises(ValueError, match="only sent to Azure OpenAI endpoints"):
        llm_service.LLMService()
    assert fake_azure.calls == []


def test_azure_listing_keeps_only_usable_chat_deployments():
    """Embedding/audio/image deployments and ones not yet provisioned are left out, like the OpenAI listing."""
    deployments = {
        "data": [
            {"id": "chat", "model": "gpt-4.1-mini", "status": "succeeded"},
            {"id": "embed", "model": "text-embedding-3-small", "status": "succeeded"},
            {"id": "voice", "model": "gpt-4o-realtime-preview", "status": "succeeded"},
            {"id": "pending", "model": "gpt-4.1", "status": "creating"},
            {"id": "failed", "model": "gpt-4.1", "status": "failed"},
        ]
    }
    credentials = CatalogCredentials("k", "https://x.openai.azure.com", auth_mode="api_key")

    options = catalog.list_models(
        models.LLMProvider.AZURE_OPENAI, credentials, client=_azure_client(httpx.Response(200, json=deployments), [])
    )

    assert [option.id for option in options] == ["chat"]


def test_entra_failure_is_remembered_briefly(monkeypatch):
    """A failed sign-in is re-raised from memory within the TTL instead of re-running the credential chain."""
    attempts: list[int] = []

    class Credential:
        def token(self):
            attempts.append(1)
            raise RuntimeError("no az login")

    _install_fake_azure_identity(monkeypatch, Credential)

    for _ in range(3):
        with pytest.raises(clients.AzureCredentialError):
            clients.azure_entra_token()
    assert len(attempts) == 1

    monkeypatch.setattr(clients, "ENTRA_FAILURE_TTL_SECONDS", 0.0)
    with pytest.raises(clients.AzureCredentialError):
        clients.azure_entra_token()
    assert len(attempts) == 2


def test_draft_azure_endpoint_withholds_the_stored_key():
    """A draft endpoint never receives the stored Azure key; the endpoint is Azure's base URL."""
    settings = models.Settings(
        llm_provider=models.LLMProvider.AZURE_OPENAI,
        azure_openai_auth_mode="api_key",
        azure_openai_endpoint="https://mine.openai.azure.com",
        azure_openai_api_key=security.security_service.encrypt("azure-secret"),
    )

    assert catalog.draft_withholds_stored_key(settings, {"azure_openai_endpoint": "https://evil.example.com"})
    assert not catalog.draft_withholds_stored_key(settings, {"azure_openai_endpoint": "https://mine.openai.azure.com"})
    assert not catalog.draft_withholds_stored_key(
        settings, {"azure_openai_endpoint": "https://evil.example.com", "azure_openai_api_key": "draft"}
    )


def test_entra_mode_ignores_a_leftover_stored_key_when_the_endpoint_changes():
    """Entra ID never sends the stored key, so a changed draft endpoint can still be tested."""
    settings = models.Settings(
        llm_provider=models.LLMProvider.AZURE_OPENAI,
        azure_openai_auth_mode="api_key",
        azure_openai_endpoint="https://mine.openai.azure.com",
        azure_openai_api_key=security.security_service.encrypt("azure-secret"),
    )
    draft = {"azure_openai_endpoint": "https://other.openai.azure.com", "azure_openai_auth_mode": "entra"}

    assert not catalog.draft_withholds_stored_key(settings, draft)
    settings.azure_openai_auth_mode = "entra"
    assert not catalog.draft_withholds_stored_key(settings, {"azure_openai_endpoint": "https://other.openai.azure.com"})


# --- Chat Completions vs the Responses API (JAN-354) -------------------------


def _completion_endpoint(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-6-luna",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def _noop(text: str) -> str:
    """Does nothing; a tool to bind."""
    return text


def _azure_api_key_settings(**overrides) -> models.Settings:
    settings = models.Settings(**_azure_settings(azure_openai_auth_mode="api_key", **overrides))
    settings.azure_openai_api_key = security.security_service.encrypt("azure-key")
    return settings


def test_gpt6_azure_deployment_with_tools_calls_chat_completions():
    """A gpt-6* deployment with tools uses the deployments route (Chat Completions), not /openai/responses."""
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _azure_api_key_settings(), None, "gpt-6-luna", {"http_client": _completion_endpoint(seen)}
    )
    assert client.bind_tools([_noop]).invoke("ping").content == "pong"

    assert seen[0].url.path == "/openai/deployments/gpt-6-luna/chat/completions"


def test_gpt6_github_model_with_tools_calls_chat_completions():
    """GitHub Models only serves Chat Completions, so a bare gpt-6* id with tools must not go to /responses."""
    seen: list[httpx.Request] = []
    settings = models.Settings(github_models_api_key=security.security_service.encrypt("gho"))

    client = clients.build_github_models(settings, None, "gpt-6-mini", {"http_client": _completion_endpoint(seen)})
    assert client.bind_tools([_noop]).invoke("ping").content == "pong"

    assert seen[0].url.path == "/inference/chat/completions"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5-pro", True),
        ("openai/gpt-5.4-pro", True),
        ("gpt-5.6-sol", True),
        ("gpt-5-codex", True),
        ("my-codex-deployment", True),
        ("gpt-6-luna", False),
        ("openai/gpt-4.1-mini", False),
        ("", False),
    ],
)
def test_responses_only_models_follow_langchain_and_the_codex_rule(model, expected):
    """One helper names the models that only work with the Responses API, publisher prefix ignored."""
    assert clients.responses_only_model(model) is expected


@pytest.mark.parametrize(
    ("api_version", "deployment", "expected"),
    [
        ("2024-10-21", "gpt-6-luna", False),
        ("2025-04-01-preview", "gpt-6-luna", False),
        ("2025-04-01-preview", "gpt-5-pro", "unset"),
        ("2025-04-01-preview", "team-codex", "unset"),
    ],
)
def test_azure_routing_depends_on_api_version_and_model(db, monkeypatch, fake_azure, api_version, deployment, expected):
    """Chat Completions is forced, except for a Responses-only deployment on a preview api-version."""
    _store(
        db,
        monkeypatch,
        **_azure_settings(azure_openai_api_version=api_version, azure_openai_deployment=deployment),
    )

    llm_service.LLMService()

    assert fake_azure.kwargs.get("use_responses_api", "unset") == expected


def test_responses_only_models_fail_clearly_where_chat_completions_is_forced(db, monkeypatch, fake_azure, fake_openai):
    """GA Azure api-versions and GitHub Models cannot serve them: a clear configuration error, no client."""
    _store(
        db,
        monkeypatch,
        **_azure_settings(azure_openai_deployment="gpt-5-pro"),
        github_models_api_key=security.security_service.encrypt("gho"),
    )

    with pytest.raises(ValueError, match="only works with the OpenAI Responses API.*preview version"):
        llm_service.LLMService()
    with pytest.raises(ValueError, match="Pick another model, or use OpenAI without a base URL"):
        llm_service.LLMService(settings_override={"llm_provider": "github_models", "model_override": "gpt-5.6-sol"})
    assert fake_azure.calls == [] and fake_openai.calls == []


@pytest.mark.parametrize(
    ("settings_values", "expected"),
    [
        ({"openai_base_url": None, "openai_model": "gpt-6-luna"}, "unset"),
        ({"openai_base_url": "https://api.openai.com/v1", "openai_model": "gpt-6-luna"}, "unset"),
        ({"openai_base_url": "https://proxy.example.com/v1", "openai_model": "gpt-6-luna"}, False),
        ({"openai_base_url": "https://proxy.example.com/v1", "openai_model": "gpt-5-pro"}, "unset"),
        ({"llm_provider": models.LLMProvider.OPENROUTER, "openrouter_model": "openai/gpt-6"}, "unset"),
    ],
    ids=["native", "native-explicit", "proxy", "proxy-responses-only", "openrouter"],
)
def test_openai_routing_depends_on_the_endpoint(db, monkeypatch, settings_values, expected):
    """OpenAI itself and OpenRouter keep langchain's routing; a proxy gets Chat Completions unless Responses-only."""
    recorder = _Recorder()
    monkeypatch.setattr(llm_service, "ChatOpenAI", recorder.cls)
    secret = security.security_service.encrypt("key")
    values = {"llm_provider": models.LLMProvider.OPENAI, **settings_values}
    _store(db, monkeypatch, openai_api_key=secret, openrouter_api_key=secret, **values)

    llm_service.LLMService()

    assert recorder.kwargs.get("use_responses_api", "unset") == expected


def test_github_catalog_leaves_out_responses_only_models():
    """The GitHub Models dropdown offers no model the provider cannot serve through Chat Completions."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": "openai/gpt-5-pro", "supported_output_modalities": ["text"]},
                {"id": "openai/gpt-5-codex", "supported_output_modalities": ["text"]},
                {"id": "openai/gpt-4.1", "supported_output_modalities": ["text"]},
            ],
        )

    options = catalog.list_models(
        models.LLMProvider.GITHUB_MODELS,
        CatalogCredentials("gho", None),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert [option.id for option in options] == ["openai/gpt-4.1"]


def test_model_listing_api_passes_the_draft_azure_auth_mode(auth_client, monkeypatch):
    """`POST /api/planner/llm-models` lists Azure deployments with the draft key and auth mode."""
    seen: list[httpx.Request] = []
    monkeypatch.setattr(catalog, "_new_client", lambda: _azure_client(httpx.Response(200, json=_DEPLOYMENTS), seen))

    response = auth_client.post(
        "/api/planner/llm-models",
        json={
            "provider": "azure_openai",
            "auth_mode": "api_key",
            "api_key": "draft-key",
            "base_url": "https://x.openai.azure.com",
        },
    )

    assert response.status_code == 200
    assert response.json()["models"][1] == {"id": "Kimi-K2.6", "label": "Kimi-K2.6 (Kimi-K2.6)"}
    assert seen[0].headers["api-key"] == "draft-key"
    assert (
        auth_client.post("/api/planner/llm-models", json={"provider": "azure_openai", "auth_mode": "x"}).status_code
        == 422
    )


def test_draft_switch_to_api_key_withholds_a_leftover_key_saved_under_entra():
    """A leftover key saved under Entra mode never goes to a draft endpoint in draft API key mode.

    Regression for the PR #601 review: the guard used the saved Entra mode instead of the draft one.
    """
    settings = models.Settings(
        llm_provider=models.LLMProvider.AZURE_OPENAI,
        azure_openai_auth_mode="entra",
        azure_openai_endpoint="https://mine.openai.azure.com",
        azure_openai_api_key=security.security_service.encrypt("azure-secret"),
    )
    draft = {"azure_openai_auth_mode": "api_key", "azure_openai_endpoint": "https://evil.example.com"}

    assert catalog.draft_withholds_stored_key(settings, draft)
    assert not catalog.draft_withholds_stored_key(
        settings, {**draft, "azure_openai_endpoint": "https://mine.openai.azure.com"}
    )


@pytest.mark.parametrize(
    ("settings_values", "patch_target"),
    [
        ({"llm_provider": models.LLMProvider.OPENAI, "openai_model": "gpt-6-luna"}, (llm_service, "ChatOpenAI")),
        ({"llm_provider": models.LLMProvider.OPENROUTER, "openrouter_model": "openai/o4"}, (llm_service, "ChatOpenAI")),
        (
            {"llm_provider": models.LLMProvider.GITHUB_MODELS, "github_models_model": "openai/o4"},
            (clients, "ChatOpenAI"),
        ),
        (_azure_settings(azure_openai_deployment="gpt-6-luna"), (clients, "AzureChatOpenAI")),
    ],
    ids=["openai", "openrouter", "github_models", "azure_openai"],
)
def test_openai_family_builders_send_no_temperature(db, monkeypatch, settings_values, patch_target):
    """Reasoning models reject temperature=0 with a 400 (JAN-352), so these clients use the provider default."""
    recorder = _Recorder()
    monkeypatch.setattr(*patch_target, recorder.cls)
    secret = security.security_service.encrypt("key")
    _store(
        db,
        monkeypatch,
        openai_api_key=secret,
        openrouter_api_key=secret,
        github_models_api_key=secret,
        **settings_values,
    )

    llm_service.LLMService()

    assert "temperature" not in recorder.kwargs


# --- the v1 endpoint (JAN-356) ------------------------------------------------


def _v1_endpoint(seen: list[httpx.Request], monkeypatch=None):
    """A fake Azure v1 data plane: Responses for /responses, Chat Completions for /chat/completions.

    Returns a client for Entra-mode builds; with ``monkeypatch`` it also becomes the transport the
    API-key builds wrap in their own clients (they ignore a caller's ``http_client``).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": body["model"],
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "_noop",
                            "arguments": '{"text": "x"}',
                            "status": "completed",
                        }
                    ],
                    "parallel_tool_calls": True,
                    "tool_choice": "auto",
                    "tools": [],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "c",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
            },
        )

    if monkeypatch is not None:
        monkeypatch.setattr(clients, "_azure_transport", lambda: httpx.MockTransport(handler))
    return httpx.Client(transport=httpx.MockTransport(handler))


def _v1_settings(auth_mode: str = "entra", **overrides) -> models.Settings:
    settings = models.Settings(
        **_azure_settings(azure_openai_api_version="preview", azure_openai_auth_mode=auth_mode, **overrides)
    )
    settings.azure_openai_api_key = security.security_service.encrypt("azure-key")
    return settings


def _entra_token(monkeypatch) -> None:
    class Credential:
        def token(self):
            return "entra-token"

    _install_fake_azure_identity(monkeypatch, Credential)


def test_gpt6_deployment_with_tools_uses_v1_responses_with_the_entra_bearer(monkeypatch):
    """The gpt-6-luna case: tools plus reasoning only work on /openai/v1/responses."""
    _entra_token(monkeypatch)
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _v1_settings(), None, "gpt-6-luna", {"http_client": _v1_endpoint(seen, monkeypatch)}
    )
    message = client.bind_tools([_noop]).invoke("ping")

    assert message.tool_calls[0]["name"] == "_noop"
    request = seen[0]
    assert request.url.path == "/openai/v1/responses"
    assert request.url.params["api-version"] == "preview"
    assert request.headers["authorization"] == "Bearer entra-token"
    assert "api-key" not in request.headers
    assert json.loads(request.content)["model"] == "gpt-6-luna"


@pytest.mark.parametrize("auth_mode", ["entra", "api_key"])
def test_other_deployments_use_v1_chat_completions_in_both_auth_modes(monkeypatch, auth_mode):
    """Kimi-K2.6 (with tools) stays on /openai/v1/chat/completions; API key mode sends the api-key header."""
    _entra_token(monkeypatch)
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _v1_settings(auth_mode), None, "Kimi-K2.6", {"http_client": _v1_endpoint(seen, monkeypatch)}
    )
    assert client.bind_tools([_noop]).invoke("ping").content == "pong"

    request = seen[0]
    assert request.url.path == "/openai/v1/chat/completions"
    assert request.url.params["api-version"] == "preview"
    assert json.loads(request.content)["model"] == "Kimi-K2.6"
    assert "temperature" not in json.loads(request.content)
    if auth_mode == "entra":
        assert request.headers["authorization"] == "Bearer entra-token"
        assert "api-key" not in request.headers
    else:
        assert request.headers["api-key"] == "azure-key"
        assert "authorization" not in request.headers


def test_gpt6_deployment_in_api_key_mode_uses_v1_responses_with_the_key(monkeypatch):
    """API key mode on the same v1 base: Responses for gpt-6 with tools, authenticated by the api-key header."""
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _v1_settings("api_key"), None, "gpt-6-luna", {"http_client": _v1_endpoint(seen, monkeypatch)}
    )
    client.bind_tools([_noop]).invoke("ping")

    assert seen[0].url.path == "/openai/v1/responses"
    assert seen[0].headers["api-key"] == "azure-key"
    assert "authorization" not in seen[0].headers  # the key is sent once, never as a bearer token


@pytest.mark.parametrize(
    ("api_version", "v1"),
    [
        ("preview", True),
        ("latest", True),
        ("", True),
        (None, True),
        ("2024-10-21", False),
        ("2025-04-01-preview", False),
    ],
)
def test_a_dated_api_version_selects_the_legacy_route(api_version, v1):
    """preview/latest/empty use the v1 endpoint; a dated version is the escape hatch to the deployments route."""
    assert clients.azure_v1_route(api_version) is v1


def test_v1_route_keeps_the_entra_host_check(db, monkeypatch):
    """The v1 route still never sends the host identity's token to a non-Azure endpoint."""
    _store(
        db,
        monkeypatch,
        **_azure_settings(azure_openai_api_version="preview", azure_openai_endpoint="https://proxy.example.com"),
    )

    with pytest.raises(ValueError, match="only sent to Azure OpenAI endpoints"):
        llm_service.LLMService()


def test_v1_route_lets_responses_only_deployments_through(monkeypatch):
    """Responses-only deployments (gpt-5-pro, *codex*) work on v1, unlike the GA legacy route."""
    _entra_token(monkeypatch)
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _v1_settings(), None, "gpt-5-pro", {"http_client": _v1_endpoint(seen, monkeypatch)}
    )
    client.bind_tools([_noop]).invoke("ping")

    assert seen[0].url.path == "/openai/v1/responses"


# --- routing by the deployment's model, api-version mapping (JAN-357) --------


@pytest.mark.parametrize(
    ("setting", "sent"), [("preview", "preview"), ("latest", "latest"), ("v1", "preview"), ("", "preview")]
)
def test_v1_sends_the_mapped_api_version_query(monkeypatch, setting, sent):
    """One mapping decides the route and the api-version query actually sent."""
    _entra_token(monkeypatch)
    seen: list[httpx.Request] = []
    settings = _v1_settings()
    settings.azure_openai_api_version = setting

    clients.build_azure_openai(settings, None, "Kimi-K2.6", {"http_client": _v1_endpoint(seen, monkeypatch)}).invoke(
        "ping"
    )

    assert seen[0].url.params["api-version"] == sent
    assert clients.azure_v1_api_version(setting) == sent


@pytest.mark.parametrize("auth_mode", ["entra", "api_key"])
def test_a_gpt6_model_under_another_deployment_name_uses_responses(monkeypatch, auth_mode):
    """Routing follows the deployment's model: gpt-6 deployed as "chat-prod" still goes to /responses."""
    _entra_token(monkeypatch)
    lookups: list[tuple[str, str, object]] = []

    def fetch(endpoint, deployment, api_key):
        lookups.append((endpoint, deployment, api_key))
        return "gpt-6-luna"

    monkeypatch.setattr(deployments, "_fetch_deployment_model", fetch)
    seen: list[httpx.Request] = []
    settings = _v1_settings(auth_mode)

    client = clients.build_azure_openai(settings, None, "chat-prod", {"http_client": _v1_endpoint(seen, monkeypatch)})
    client.bind_tools([_noop]).invoke("ping")
    clients.build_azure_openai(settings, None, "chat-prod", {})

    assert seen[0].url.path == "/openai/v1/responses"
    assert json.loads(seen[0].content)["model"] == "chat-prod"
    # Resolved once per endpoint and deployment for the process, with the mode's credential.
    assert lookups == [
        (
            "https://example-foundry.cognitiveservices.azure.com",
            "chat-prod",
            "azure-key" if auth_mode == "api_key" else None,
        )
    ]


def test_a_non_gpt6_model_under_a_gpt6_deployment_name_uses_chat_completions(monkeypatch):
    """The other way round: a deployment named gpt-6-anything that serves Kimi stays on Chat Completions."""
    _entra_token(monkeypatch)
    monkeypatch.setattr(deployments, "_fetch_deployment_model", lambda endpoint, deployment, api_key: "Kimi-K2.6")
    seen: list[httpx.Request] = []

    client = clients.build_azure_openai(
        _v1_settings(), None, "gpt-6-alias", {"http_client": _v1_endpoint(seen, monkeypatch)}
    )
    client.bind_tools([_noop]).invoke("ping")

    assert seen[0].url.path == "/openai/v1/chat/completions"


def test_deployment_lookup_failures_fall_back_and_are_retried_later(monkeypatch):
    """No permission to read deployments: the name heuristic applies, and the lookup waits for the TTL."""
    calls: list[str] = []

    def fetch(endpoint, deployment, api_key):
        calls.append(deployment)
        raise RuntimeError("403 PermissionDenied")

    monkeypatch.setattr(deployments, "_fetch_deployment_model", fetch)

    assert deployments.deployment_model("https://x.openai.azure.com", "d", "k") is None
    assert deployments.deployment_model("https://x.openai.azure.com", "d", "k") is None
    assert calls == ["d"]
    monkeypatch.setattr(deployments, "FAILURE_TTL_SECONDS", 0.0)
    deployments.deployment_model("https://x.openai.azure.com", "d", "k")
    assert calls == ["d", "d"]


def test_fetch_deployment_model_reads_the_single_deployment(monkeypatch):
    """The lookup asks GET /openai/deployments/<name> with the mode's credential; non-200 raises."""
    seen: list[httpx.Request] = []
    status = {"code": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status["code"] != 200:
            return httpx.Response(status["code"], json={"error": {"code": "PermissionDenied"}})
        return httpx.Response(200, json={"id": "chat prod", "model": "gpt-6-luna", "status": "succeeded"})

    monkeypatch.setattr(clients, "new_lookup_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    _entra_token(monkeypatch)
    endpoint = "https://x.openai.azure.com"

    assert _REAL_FETCH_DEPLOYMENT_MODEL(endpoint, "chat prod", None) == "gpt-6-luna"
    assert seen[0].url.raw_path.split(b"?")[0] == b"/openai/deployments/chat%20prod"
    assert seen[0].url.params["api-version"] == "2023-03-15-preview"
    assert seen[0].headers["authorization"] == "Bearer entra-token"
    assert _REAL_FETCH_DEPLOYMENT_MODEL(endpoint, "chat prod", "azure-key") == "gpt-6-luna"
    assert seen[1].headers["api-key"] == "azure-key" and "authorization" not in seen[1].headers
    status["code"] = 403
    with pytest.raises(RuntimeError, match="HTTP 403"):
        _REAL_FETCH_DEPLOYMENT_MODEL(endpoint, "chat prod", "azure-key")


# --- #607 review: non-blocking, cached and logged lookups (JAN-357) ----------

_ENDPOINT = "https://x.openai.azure.com"


def test_a_lookup_never_blocks_the_event_loop(monkeypatch):
    """Built on a running loop (the chat websocket), the lookup runs in another thread and the build returns at once."""
    loop_thread: list[threading.Thread] = []
    fetch_thread: list[threading.Thread] = []
    release = threading.Event()

    def slow_fetch(endpoint, deployment, api_key):
        fetch_thread.append(threading.current_thread())
        release.wait(5)
        return "gpt-6-luna"

    monkeypatch.setattr(deployments, "_fetch_deployment_model", slow_fetch)

    async def build_on_the_loop():
        loop_thread.append(threading.current_thread())
        llm = clients.build_azure_openai(_v1_settings("api_key"), None, "chat-prod", {})
        return llm.use_responses_api

    routing = asyncio.run(asyncio.wait_for(build_on_the_loop(), timeout=1))
    release.set()
    deployments.wait_for_lookups()

    assert routing is None  # the name heuristic until the answer arrives
    assert fetch_thread and fetch_thread[0] is not loop_thread[0]
    # The answer is then used by the next build, on or off the loop.
    assert clients.build_azure_openai(_v1_settings("api_key"), None, "chat-prod", {}).use_responses_api is True


def test_sync_callers_look_up_inline_and_concurrent_callers_share_one_lookup(monkeypatch):
    """Off the loop the lookup runs in the caller's thread; callers arriving meanwhile wait for it."""
    calls: list[threading.Thread] = []
    started = threading.Event()
    release = threading.Event()

    def fetch(endpoint, deployment, api_key):
        calls.append(threading.current_thread())
        started.set()
        release.wait(5)
        return "Kimi-K2.6"

    monkeypatch.setattr(deployments, "_fetch_deployment_model", fetch)
    results: list[str | None] = []
    first = threading.Thread(target=lambda: results.append(deployments.deployment_model(_ENDPOINT, "d", "k")))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: results.append(deployments.deployment_model(_ENDPOINT, "d", "k")))
    second.start()
    release.set()
    first.join(5)
    second.join(5)

    assert results == ["Kimi-K2.6", "Kimi-K2.6"]
    assert calls == [first]


def test_answers_expire_so_a_repointed_deployment_is_picked_up(monkeypatch):
    """A resolved model is cached for the lookup TTL, then read again."""
    answers = iter(["gpt-4.1", "gpt-6-luna"])
    monkeypatch.setattr(deployments, "_fetch_deployment_model", lambda endpoint, deployment, api_key: next(answers))

    assert deployments.deployment_model(_ENDPOINT, "d", "k") == "gpt-4.1"
    assert deployments.deployment_model(_ENDPOINT, "d", "k") == "gpt-4.1"
    monkeypatch.setattr(clients, "LOOKUP_CACHE_TTL_SECONDS", 0.0)
    assert deployments.deployment_model(_ENDPOINT, "d", "k") == "gpt-6-luna"


def test_lookups_are_cached_per_credential(monkeypatch):
    """An Entra failure never blocks the API-key lookup of the same deployment (or another key's)."""
    calls: list[object] = []

    def fetch(endpoint, deployment, api_key):
        calls.append(api_key)
        if api_key is None:
            raise RuntimeError("no az login")
        return "gpt-6-luna"

    monkeypatch.setattr(deployments, "_fetch_deployment_model", fetch)

    assert deployments.deployment_model(_ENDPOINT, "d", None) is None
    assert deployments.deployment_model(_ENDPOINT, "d", "key-1") == "gpt-6-luna"
    assert deployments.deployment_model(_ENDPOINT, "d", "key-2") == "gpt-6-luna"
    assert calls == [None, "key-1", "key-2"]


def test_each_failed_lookup_is_logged_once(monkeypatch):
    """A 403 or network error is logged per failed lookup, not per client build."""
    logged: list[str] = []
    monkeypatch.setattr(
        deployments.event_logger, "log", lambda kind, source, message, details=None: logged.append(message)
    )

    def fetch(endpoint, deployment, api_key):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(deployments, "_fetch_deployment_model", fetch)
    for _ in range(3):
        deployments.deployment_model(_ENDPOINT, "chat-prod", "k")

    assert len(logged) == 1
    assert "chat-prod" in logged[0] and "HTTP 403" in logged[0]


def test_api_key_clients_are_built_per_client_and_never_shared(monkeypatch):
    """Each API-key build gets its own sync and async HTTP clients; a caller's client is left untouched."""
    monkeypatch.setattr(clients, "_azure_transport", lambda: httpx.MockTransport(lambda request: httpx.Response(200)))
    caller_client = httpx.Client()

    first = clients.build_azure_openai(_v1_settings("api_key"), None, "Kimi-K2.6", {"http_client": caller_client})
    second = clients.build_azure_openai(_v1_settings("api_key"), None, "Kimi-K2.6", {})

    assert first.http_client is not second.http_client
    assert first.http_async_client is not second.http_async_client
    assert first.http_client is not caller_client
    assert caller_client.event_hooks == {"request": [], "response": []}
