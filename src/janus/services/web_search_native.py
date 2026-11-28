"""Provider-native web search: a chat provider's own search tool, used as a web search backend.

OpenAI, Gemini, Anthropic and OpenRouter can search the web inside a model call.
Each reuses the Fernet-encrypted API key (and base URL) stored for chat, so there
is no second credential, like embeddings (``embedding_service``) and images
(``image_generation``). Only the model-written search query is sent as the
prompt, never the conversation. The provider's answer text is discarded: what is
kept are its cited sources (title, URL, and the cited passage as the snippet),
normalized to the same :class:`~janus.services.web_search_service.WebSearchResult`
shape as Brave and SearXNG, so every result still goes through
``format_web_search_results`` as untrusted content.

The queries leave the machine for a cloud vendor, which is why this is opt-in and
labelled third-party in Settings. The HTTP contracts below follow each vendor's
documentation; the default models are editable because the supported set changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from janus.services.llm_provider_clients import DEFAULT_OPENAI_BASE_URL

PROVIDER_OPENAI = "openai"
PROVIDER_GEMINI = "gemini"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"

#: The chat providers with a built-in search tool, and the model used when none is set.
DEFAULT_NATIVE_MODELS: dict[str, str] = {
    PROVIDER_OPENAI: "gpt-4.1-mini",
    PROVIDER_GEMINI: "gemini-2.5-flash",
    PROVIDER_ANTHROPIC: "claude-haiku-4-5",
    PROVIDER_OPENROUTER: "openai/gpt-4.1-mini",
}
NATIVE_PROVIDERS: tuple[str, ...] = tuple(DEFAULT_NATIVE_MODELS)

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
ANTHROPIC_VERSION = "2023-06-01"

#: A search-and-answer call takes longer than a plain search API; bounded all the same.
NATIVE_TIMEOUT_SECONDS = 30.0
_MAX_ANSWER_CHARS = 20_000


class NativeSearchError(Exception):
    """A native search call failed; the message is short and safe to show (never the key)."""


# One normalized hit before bounding: (title, url, snippet).
RawHit = tuple[str, str, str]


def _post(client: httpx.Client, label: str, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict:
    """POST JSON and return the decoded object, mapping every failure to a safe message."""
    try:
        response = client.post(url, headers=headers, json=payload, timeout=NATIVE_TIMEOUT_SECONDS)
    except httpx.RequestError as exc:
        raise NativeSearchError(f"Could not reach {label}.") from exc
    if response.status_code != 200:
        raise NativeSearchError(f"{label} returned HTTP {response.status_code}.")
    try:
        body = response.json()
    except ValueError as exc:
        raise NativeSearchError(f"{label} returned invalid JSON.") from exc
    if not isinstance(body, dict):
        raise NativeSearchError(f"{label} returned an unexpected payload.")
    return body


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _passage(text: str, start: object, end: object) -> str:
    """The text a citation covers, when its offsets are usable."""
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        return text[start:end]
    return ""


def _openai(client: httpx.Client, api_key: str, base_url: str | None, model: str, query: str) -> list[RawHit]:
    """OpenAI Responses API with the ``web_search`` tool; sources are ``url_citation`` annotations."""
    body = _post(
        client,
        "OpenAI",
        f"{(base_url or DEFAULT_OPENAI_BASE_URL).rstrip('/')}/responses",
        {"Authorization": f"Bearer {api_key}"},
        {"model": model, "input": query, "tools": [{"type": "web_search"}], "tool_choice": "required"},
    )
    hits: list[RawHit] = []
    for item in _list(body.get("output")):
        if _dict(item).get("type") != "message":
            continue
        for block in _list(_dict(item).get("content")):
            text = _str(_dict(block).get("text"))[:_MAX_ANSWER_CHARS]
            for note in _list(_dict(block).get("annotations")):
                note = _dict(note)
                if note.get("type") == "url_citation":
                    hits.append(
                        (
                            _str(note.get("title")),
                            _str(note.get("url")),
                            _passage(text, note.get("start_index"), note.get("end_index")),
                        )
                    )
    return hits


def _openrouter(
    client: httpx.Client, api_key: str, base_url: str | None, model: str, query: str, count: int
) -> list[RawHit]:
    """OpenRouter chat completions with the ``web`` plugin; sources are ``url_citation`` annotations."""
    body = _post(
        client,
        "OpenRouter",
        f"{(base_url or DEFAULT_OPENROUTER_BASE_URL).rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "plugins": [{"id": "web", "max_results": count}],
        },
    )
    hits: list[RawHit] = []
    for choice in _list(body.get("choices")):
        for note in _list(_dict(_dict(choice).get("message")).get("annotations")):
            note = _dict(note)
            cited = _dict(note.get("url_citation"))
            if note.get("type") == "url_citation":
                hits.append((_str(cited.get("title")), _str(cited.get("url")), _str(cited.get("content"))))
    return hits


def _gemini(client: httpx.Client, api_key: str, model: str, query: str) -> list[RawHit]:
    """Gemini ``generateContent`` with Google Search grounding; sources are the grounding chunks."""
    body = _post(
        client,
        "Gemini",
        f"{GEMINI_BASE_URL}/models/{model}:generateContent",
        {"x-goog-api-key": api_key},
        {"contents": [{"parts": [{"text": query}]}], "tools": [{"google_search": {}}]},
    )
    hits: list[RawHit] = []
    for candidate in _list(body.get("candidates")):
        grounding = _dict(_dict(candidate).get("groundingMetadata"))
        chunks = [_dict(_dict(chunk).get("web")) for chunk in _list(grounding.get("groundingChunks"))]
        passages: dict[int, str] = {}
        for support in _list(grounding.get("groundingSupports")):
            text = _str(_dict(_dict(support).get("segment")).get("text"))
            for index in _list(_dict(support).get("groundingChunkIndices")):
                if isinstance(index, int) and index not in passages:
                    passages[index] = text
        hits.extend(
            (_str(web.get("title")), _str(web.get("uri")), passages.get(index, "")) for index, web in enumerate(chunks)
        )
    return hits


def _anthropic(
    client: httpx.Client, api_key: str, base_url: str | None, model: str, query: str, count: int
) -> list[RawHit]:
    """Anthropic Messages API with the web search tool; sources are the tool's result blocks."""
    body = _post(
        client,
        "Anthropic",
        f"{(base_url or DEFAULT_ANTHROPIC_BASE_URL).rstrip('/')}/v1/messages",
        {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": query}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
        },
    )
    cited: dict[str, str] = {}
    results: list[tuple[str, str]] = []
    for block in _list(body.get("content")):
        block = _dict(block)
        if block.get("type") == "web_search_tool_result":
            results.extend(
                (_str(_dict(r).get("title")), _str(_dict(r).get("url"))) for r in _list(block.get("content"))
            )
        for citation in _list(block.get("citations")):
            citation = _dict(citation)
            cited.setdefault(_str(citation.get("url")), _str(citation.get("cited_text")))
    return [(title, url, cited.get(url, "")) for title, url in results][: max(count, 1) * 2]


def native_search(
    provider: str,
    *,
    client: httpx.Client,
    api_key: str,
    base_url: str | None,
    model: str | None,
    query: str,
    count: int,
) -> list[RawHit]:
    """Run ``query`` through ``provider``'s own search tool and return its cited sources."""
    chosen = model or DEFAULT_NATIVE_MODELS[provider]
    dispatch: dict[str, Callable[[], list[RawHit]]] = {
        PROVIDER_OPENAI: lambda: _openai(client, api_key, base_url, chosen, query),
        PROVIDER_OPENROUTER: lambda: _openrouter(client, api_key, base_url, chosen, query, count),
        PROVIDER_GEMINI: lambda: _gemini(client, api_key, chosen, query),
        PROVIDER_ANTHROPIC: lambda: _anthropic(client, api_key, base_url, chosen, query, count),
    }
    return dispatch[provider]()
