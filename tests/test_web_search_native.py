"""Provider-native web search: the chat providers' own search tools as a backend (JAN-211)."""

from __future__ import annotations

import json

import httpx
import pytest

from janus import models, security
from janus.services import web_search_native as native
from janus.services import web_search_service as ws


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _settings(db, **fields) -> models.Settings:
    settings = db.query(models.Settings).first() or models.Settings()
    for key, value in fields.items():
        setattr(settings, key, value)
    db.add(settings)
    db.commit()
    return settings


def _service(provider: str, handler, *, model: str | None = None, base_url: str | None = None) -> ws.WebSearchService:
    return ws.WebSearchService(provider, api_key="sk-test", base_url=base_url, model=model, client=_client(handler))


def test_openai_uses_the_responses_api_and_keeps_the_cited_passages():
    """OpenAI: the Responses API with the forced web_search tool; cited passages become snippets."""
    seen: dict = {}
    answer = "Janus is a local agent. It keeps data local."

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": [
                    {"type": "web_search_call", "id": "ws_1"},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": answer,
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/a",
                                        "title": "A",
                                        "start_index": 0,
                                        "end_index": 23,
                                    },
                                    {
                                        "type": "url_citation",
                                        "url": "https://example.com/a",
                                        "title": "A again",
                                        "start_index": 24,
                                        "end_index": 42,
                                    },
                                    {"type": "url_citation", "url": "javascript:alert(1)", "title": "bad"},
                                    {"type": "file_citation", "file_id": "f"},
                                ],
                            }
                        ],
                    },
                ]
            },
        )

    results = _service("openai", handler).search(" janus   docs ", max_results=5)

    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["auth"] == "Bearer sk-test"
    # Only the normalized query is sent, with the search tool forced on.
    assert seen["body"] == {
        "model": native.DEFAULT_NATIVE_MODELS["openai"],
        "input": "janus docs",
        "tools": [{"type": "web_search"}],
        "tool_choice": "required",
    }
    assert results == [ws.WebSearchResult(title="A", url="https://example.com/a", snippet="Janus is a local agent.")]


def test_openai_honours_a_custom_base_url_and_model():
    """OpenAI: a configured base URL and model are used for the request."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"output": []})

    assert _service("openai", handler, model="gpt-custom", base_url="https://proxy.example/v1/").search("q") == []
    assert seen == {"url": "https://proxy.example/v1/responses", "model": "gpt-custom"}


def test_gemini_uses_google_search_grounding_and_maps_supports_to_chunks():
    """Gemini: Google Search grounding; supports map their text onto the grounding chunks."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "groundingMetadata": {
                            "groundingChunks": [
                                {"web": {"uri": "https://example.com/g1", "title": "G1"}},
                                {"web": {"uri": "https://example.com/g2", "title": "G2"}},
                            ],
                            "groundingSupports": [{"segment": {"text": "First claim."}, "groundingChunkIndices": [0]}],
                        }
                    }
                ]
            },
        )

    results = _service("gemini", handler).search("q")

    assert seen["url"] == f"{native.GEMINI_BASE_URL}/models/{native.DEFAULT_NATIVE_MODELS['gemini']}:generateContent"
    assert seen["key"] == "sk-test"
    assert "key=" not in seen["url"]
    assert seen["body"] == {"contents": [{"parts": [{"text": "q"}]}], "tools": [{"google_search": {}}]}
    assert results == [
        ws.WebSearchResult(title="G1", url="https://example.com/g1", snippet="First claim."),
        ws.WebSearchResult(title="G2", url="https://example.com/g2", snippet=""),
    ]


def test_anthropic_uses_the_web_search_tool_and_cited_text():
    """Anthropic: the web search tool; cited text becomes the snippet of its source."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = {k: request.headers[k] for k in ("x-api-key", "anthropic-version")}
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "server_tool_use", "name": "web_search"},
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {
                                "type": "web_search_result",
                                "url": "https://example.com/c",
                                "title": "C",
                                "encrypted_content": "x",
                            },
                            {"type": "web_search_result", "url": "https://example.com/d", "title": "D"},
                        ],
                    },
                    {
                        "type": "text",
                        "text": "answer",
                        "citations": [{"url": "https://example.com/c", "cited_text": "Cited C."}],
                    },
                ]
            },
        )

    results = _service("anthropic", handler).search("q")

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"] == {"x-api-key": "sk-test", "anthropic-version": native.ANTHROPIC_VERSION}
    assert seen["body"]["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
    assert seen["body"]["messages"] == [{"role": "user", "content": "q"}]
    assert "web_search" in seen["body"]["system"]
    assert results == [
        ws.WebSearchResult(title="C", url="https://example.com/c", snippet="Cited C."),
        ws.WebSearchResult(title="D", url="https://example.com/d", snippet=""),
    ]


def test_openrouter_uses_the_web_plugin_and_url_citation_annotations():
    """OpenRouter: the web plugin; url_citation annotations are the sources."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://example.com/o",
                                        "title": "O",
                                        "content": "Passage",
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    results = _service("openrouter", handler).search("q", max_results=3)

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["body"]["plugins"] == [{"id": "web", "max_results": 3}]
    assert results == [ws.WebSearchResult(title="O", url="https://example.com/o", snippet="Passage")]


@pytest.mark.parametrize("provider", native.NATIVE_PROVIDERS)
def test_failures_map_to_short_messages_without_the_key(provider):
    """Every failure mode maps to a short message that never contains the key."""

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key sk-test"})

    with pytest.raises(ws.WebSearchError) as caught:
        _service(provider, refused).search("q")
    assert "401" in str(caught.value)
    assert "sk-test" not in str(caught.value)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom sk-test")

    with pytest.raises(ws.WebSearchError) as caught:
        _service(provider, unreachable).search("q")
    assert "sk-test" not in str(caught.value)

    with pytest.raises(ws.WebSearchError, match="invalid JSON"):
        _service(provider, lambda request: httpx.Response(200, content=b"not json")).search("q")
    with pytest.raises(ws.WebSearchError, match="unexpected payload"):
        _service(provider, lambda request: httpx.Response(200, json=[1])).search("q")


@pytest.mark.parametrize("provider", native.NATIVE_PROVIDERS)
def test_a_native_provider_without_a_key_is_refused(provider):
    """A native provider cannot be built without its chat key."""
    with pytest.raises(ValueError, match="API key"):
        ws.WebSearchService(provider)


@pytest.mark.parametrize("provider", native.NATIVE_PROVIDERS)
def test_malformed_provider_payloads_yield_no_results(provider):
    """Unexpected payload shapes give no results instead of raising."""
    assert (
        _service(
            provider,
            lambda request: httpx.Response(200, json={"output": "x", "choices": 1, "candidates": {}, "content": None}),
        ).search("q")
        == []
    )


@pytest.mark.parametrize("provider", native.NATIVE_PROVIDERS)
def test_configured_only_when_the_chat_key_is_stored(db, provider):
    """A native provider counts as configured only once its chat key is stored."""
    settings = _settings(db, web_search_provider=provider)
    assert ws.is_web_search_configured(settings) is False
    assert ws.load_web_search_service(db) is None

    setattr(settings, f"{provider}_api_key", security.security_service.encrypt("sk-chat"))
    db.commit()
    assert ws.is_web_search_configured(settings) is True
    service = ws.load_web_search_service(db)
    assert service is not None
    assert service._api_key == "sk-chat"
    service.close()


def test_load_service_uses_the_stored_model_and_the_chat_base_url(db):
    """The stored model and the chat provider's base URL reach the service."""
    _settings(
        db,
        web_search_provider="openai",
        web_search_model=" gpt-stored ",
        openai_api_key=security.security_service.encrypt("sk-chat"),
        openai_base_url="https://proxy.example/v1",
    )
    service = ws.load_web_search_service(db)
    assert service is not None
    assert (service._model, service._base_url) == ("gpt-stored", "https://proxy.example/v1")
    service.close()


def test_the_independent_backends_ignore_the_model(db):
    """Brave and SearXNG never use the model column."""
    _settings(
        db, web_search_provider="searxng", web_search_base_url="http://localhost:8888", web_search_model="ignored"
    )
    service = ws.load_web_search_service(db)
    assert service is not None
    assert service._model is None
    service.close()
