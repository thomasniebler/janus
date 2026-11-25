"""Record every LLM call's token usage per user, day, surface and model (JAN-408).

``LLMService`` attaches a :class:`UsageCallback` to each chat model it builds
(:func:`attach`), so every call through any path is counted without changing
call sites. The callback reads:

* the **owner** from ``event_logger.current_owner()`` (bound for chat, trigger
  and delegated runs, mail processing and planner sync); ``None`` is system work;
* the **surface** from :func:`usage_surface` (``chat``, ``trigger``,
  ``delegated``), default ``other``;
* the **model** label it was attached with (``provider/model``);
* the **tokens** from the response's ``usage_metadata`` (or ``llm_output``).
  Some streamed responses carry none (OpenAI-compatible endpoints with a custom
  base URL); such calls are counted in ``unreported_calls`` with 0 tokens.

Recording uses its own short session and never raises: usage accounting must
never break an LLM call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from sqlalchemy import update

from janus import database, event_logger, models, time_utils

logger = logging.getLogger(__name__)

SURFACES = ("chat", "trigger", "delegated", "other")
_CURRENT_SURFACE: ContextVar[str] = ContextVar("llm_usage_surface", default="other")


def bind_surface(surface: str) -> Token[str]:
    """Count LLM calls in this context under ``surface`` until :func:`release_surface`."""
    return _CURRENT_SURFACE.set(surface if surface in SURFACES else "other")


def release_surface(token: Token[str]) -> None:
    """Undo one :func:`bind_surface`."""
    _CURRENT_SURFACE.reset(token)


@contextmanager
def usage_surface(surface: str) -> Iterator[None]:
    """Count LLM calls made inside this block under ``surface``."""
    token = bind_surface(surface)
    try:
        yield
    finally:
        release_surface(token)


def _tokens(response: Any) -> tuple[int, int] | None:
    """``(input, output)`` tokens of an ``LLMResult``, or ``None`` when the provider reported none."""
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    if token_usage:
        return int(token_usage.get("prompt_tokens") or 0), int(token_usage.get("completion_tokens") or 0)
    return None


def record(user_id: int | None, surface: str, model: str, tokens: tuple[int, int] | None) -> None:
    """Add one call to its day row (upsert); swallows every error."""
    input_tokens, output_tokens = tokens or (0, 0)
    key = {"user_id": user_id, "day": time_utils.utcnow().date(), "surface": surface, "model": model[:200]}
    try:
        with database.SessionLocal() as session:
            table = models.LlmUsageDay
            filters = [getattr(table, name) == value for name, value in key.items()]
            if user_id is None:
                filters[0] = table.user_id.is_(None)
            changed = session.execute(
                update(table)
                .where(*filters)
                .values(
                    calls=table.calls + 1,
                    input_tokens=table.input_tokens + input_tokens,
                    output_tokens=table.output_tokens + output_tokens,
                    unreported_calls=table.unreported_calls + (0 if tokens else 1),
                )
            ).rowcount
            if not changed:
                session.add(
                    table(
                        **key,
                        calls=1,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        unreported_calls=0 if tokens else 1,
                    )
                )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - accounting must never break a model call
        logger.debug("LLM usage not recorded: %s", exc)


class UsageCallback(BaseCallbackHandler):
    """Counts each finished LLM call for the current owner and surface."""

    raise_error = False

    def __init__(self, model: str):
        """Remember the ``provider/model`` label calls are counted under."""
        self.model = model

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Record the call that just ended."""
        record(event_logger.current_owner(), _CURRENT_SURFACE.get(), self.model, _tokens(response))


def attach(llm: Any, model: str) -> None:
    """Add a :class:`UsageCallback` to a LangChain model's own callbacks (kept for every call)."""
    try:
        existing = list(llm.callbacks or []) if isinstance(llm.callbacks, list | tuple) else []
        llm.callbacks = [*existing, UsageCallback(model)]
    except Exception as exc:  # noqa: BLE001 - a model that refuses callbacks is simply not counted
        logger.debug("LLM usage callback not attached: %s", exc)


__all__ = ["SURFACES", "UsageCallback", "attach", "bind_surface", "record", "release_surface", "usage_surface"]
