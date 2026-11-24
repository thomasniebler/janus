"""The model behind an Azure OpenAI deployment, for routing on the v1 endpoint (JAN-357).

langchain-openai picks the Responses API or Chat Completions from ``model_name``, which on
Azure is the deployment name. :func:`deployment_model` reads the real model once with
``GET /openai/deployments/<deployment>`` so a gpt-6 model deployed as ``chat-prod`` still
reaches ``/responses``.

The lookup never blocks an event loop, and it never fails a client build:

- On a thread with a running event loop (the chat websocket builds its client there), a missing
  or expired entry starts a background lookup and the call returns the cached model, or ``None``
  (the deployment-name heuristic) until the first answer arrives.
- Elsewhere (worker threads, the threadpool behind sync routes such as the connection test),
  the lookup runs inline, bounded by :data:`~janus.services.llm_provider_clients.LOOKUP_TIMEOUT_SECONDS`.
  A concurrent caller waits for the lookup already in flight instead of starting another.

Answers are cached per endpoint, deployment and credential for
:data:`~janus.services.llm_provider_clients.LOOKUP_CACHE_TTL_SECONDS`, so a re-pointed
deployment picks up its new model. Failures are cached for
:data:`FAILURE_TTL_SECONDS` and logged once per failed lookup.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from janus import event_logger, models
from janus.services import llm_provider_clients as clients

#: How long a failed lookup is remembered before it is tried again.
FAILURE_TTL_SECONDS = 300.0


@dataclass
class _Entry:
    model: str | None = None
    fetched_at: float | None = None
    ok: bool = False
    inflight: threading.Event | None = field(default=None, repr=False)


_entries: dict[tuple[str, str, str], _Entry] = {}
_lock = threading.Lock()


def _credential_id(api_key: str | None) -> str:
    """Separates Entra and API-key lookups (and different keys) so one mode's failure never blocks another."""
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16] if api_key else "entra"


def _fetch_deployment_model(endpoint: str, deployment: str, api_key: str | None) -> str | None:
    """``GET /openai/deployments/<deployment>``'s ``model``; raises on any failure."""
    url = f"{endpoint}/openai/deployments/{quote(deployment, safe='')}"
    with clients.new_lookup_client() as client:
        response = client.get(
            url,
            headers=clients.azure_auth_headers(api_key),
            params={"api-version": clients.AZURE_DEPLOYMENTS_API_VERSION},
        )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    model = response.json().get("model")
    if not isinstance(model, str) or not model.strip():
        raise RuntimeError("the deployment has no model")
    return model.strip()


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _fresh(entry: _Entry | None, now: float) -> bool:
    if entry is None or entry.fetched_at is None:
        return False
    ttl = clients.LOOKUP_CACHE_TTL_SECONDS if entry.ok else FAILURE_TTL_SECONDS
    return now - entry.fetched_at < ttl


def _resolve(key: tuple[str, str, str], endpoint: str, deployment: str, api_key: str | None) -> None:
    """Runs one lookup, records its answer or failure, and releases the waiters."""
    try:
        model: str | None = _fetch_deployment_model(endpoint, deployment, api_key)
        error = None
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown model", never a failed build
        model, error = None, exc
    with _lock:
        entry = _entries.setdefault(key, _Entry())
        if model:
            entry.model, entry.ok = model, True
        else:
            entry.ok = False  # keep a previously resolved model for the async path's stale answer
        entry.fetched_at = time.monotonic()
        inflight, entry.inflight = entry.inflight, None
    if error is not None:
        event_logger.log(
            models.EventType.WARNING,
            "LLMService",
            f"Could not read the model of Azure deployment {deployment}; routing by its name: {error}",
            {"llm_provider": models.LLMProvider.AZURE_OPENAI.value, "endpoint": endpoint, "deployment": deployment},
        )
    if inflight is not None:
        inflight.set()


def deployment_model(endpoint: str, deployment: str, api_key: str | None) -> str | None:
    """The model behind an Azure deployment, or ``None`` when it is not (yet) known; see the module docs."""
    key = (endpoint, deployment, _credential_id(api_key))
    with _lock:
        entry = _entries.get(key)
        if _fresh(entry, time.monotonic()):
            return entry.model if entry and entry.ok else None
        known = entry.model if entry else None
        if entry is None:
            entry = _entries[key] = _Entry()
        start = entry.inflight is None
        if start:
            entry.inflight = threading.Event()
        inflight = entry.inflight
    if _on_event_loop():
        if start:
            threading.Thread(target=_resolve, args=(key, endpoint, deployment, api_key), daemon=True).start()
        return known
    if start:
        _resolve(key, endpoint, deployment, api_key)
    else:
        inflight.wait(clients.LOOKUP_TIMEOUT_SECONDS * 3)
    with _lock:
        entry = _entries.get(key)
        return entry.model if entry and entry.ok else None


def wait_for_lookups(timeout: float = 5.0) -> None:
    """Waits for background lookups in flight (used by tests)."""
    with _lock:
        events = [entry.inflight for entry in _entries.values() if entry.inflight is not None]
    for event in events:
        event.wait(timeout)


def reset() -> None:
    """Forgets every cached or failed lookup (used by tests)."""
    with _lock:
        _entries.clear()
