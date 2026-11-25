"""Tests for per-user LLM token usage accounting (JAN-408)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from janus import database, event_logger, llm_usage, models
from janus.services.stats_service import StatsService


@pytest.fixture
def usage_db(db, testing_session_local, monkeypatch):
    """Point the recorder's own sessions at the test database."""
    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    return db


def _rows(db):
    db.expire_all()
    return db.query(models.LlmUsageDay).order_by(models.LlmUsageDay.id).all()


def test_tokens_from_usage_metadata_llm_output_or_none():
    """Usage comes from the message's usage_metadata, else llm_output; None when neither exists."""
    message = AIMessage(content="hi", usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})
    assert llm_usage._tokens(LLMResult(generations=[[ChatGeneration(message=message)]])) == (12, 3)
    legacy = LLMResult(generations=[[]], llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 2}})
    assert llm_usage._tokens(legacy) == (7, 2)
    assert llm_usage._tokens(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="x"))]])) is None


def test_record_upserts_one_row_per_user_day_surface_model(usage_db):
    """Calls on the same key add up; another surface or model gets its own row; no-usage calls are counted."""
    llm_usage.record(5, "chat", "openai/gpt", (10, 2))
    llm_usage.record(5, "chat", "openai/gpt", (5, 1))
    llm_usage.record(5, "trigger", "openai/gpt", None)
    llm_usage.record(None, "other", "ollama/llama", (1, 1))
    rows = _rows(usage_db)
    assert [(r.user_id, r.surface, r.calls, r.input_tokens, r.output_tokens, r.unreported_calls) for r in rows] == [
        (5, "chat", 2, 15, 3, 0),
        (5, "trigger", 1, 0, 0, 1),
        (None, "other", 1, 1, 1, 0),
    ]


def test_record_never_raises(monkeypatch):
    """A broken database never breaks the model call."""

    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(database, "SessionLocal", broken)
    llm_usage.record(1, "chat", "m", (1, 1))


def test_callback_counts_calls_for_the_bound_owner_and_surface(usage_db):
    """An attached model records each call under the current owner and surface."""
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="ok", usage_metadata={"input_tokens": 20, "output_tokens": 4, "total_tokens": 24})]
    )
    llm_usage.attach(model, "fake/model")
    with event_logger.owned_by(9), llm_usage.usage_surface("delegated"):
        model.invoke("hello")
    (row,) = _rows(usage_db)
    assert (row.user_id, row.surface, row.model, row.calls, row.input_tokens, row.output_tokens) == (
        9,
        "delegated",
        "fake/model",
        1,
        20,
        4,
    )


def test_unknown_surfaces_fall_back_to_other(usage_db):
    """Only known surfaces are stored."""
    with llm_usage.usage_surface("made-up"):
        assert llm_usage._CURRENT_SURFACE.get() == "other"
    assert llm_usage._CURRENT_SURFACE.get() == "other"


def test_llm_service_attaches_the_callback(usage_db):
    """Every model LLMService builds carries the usage callback with its label."""
    from janus import llm_service

    service = llm_service.LLMService(
        settings_override={"llm_provider": "ollama", "model_override": "llama3"}, db=usage_db
    )
    callbacks = [cb for cb in (service.llm.callbacks or []) if isinstance(cb, llm_usage.UsageCallback)]
    assert len(callbacks) == 1 and callbacks[0].model == service.model_label


def test_stats_summary_reports_usage_by_day_surface_and_model(usage_db):
    """The stats summary sums the user's rows only."""
    today = datetime.now(UTC).date()
    usage_db.add_all(
        [
            models.LlmUsageDay(
                user_id=3,
                day=today,
                surface="chat",
                model="a/x",
                calls=2,
                input_tokens=100,
                output_tokens=10,
                unreported_calls=0,
            ),
            models.LlmUsageDay(
                user_id=3,
                day=today,
                surface="trigger",
                model="b/y",
                calls=1,
                input_tokens=500,
                output_tokens=50,
                unreported_calls=1,
            ),
            models.LlmUsageDay(
                user_id=4,
                day=today,
                surface="chat",
                model="a/x",
                calls=9,
                input_tokens=9,
                output_tokens=9,
                unreported_calls=0,
            ),
        ]
    )
    usage_db.commit()
    llm = StatsService(usage_db).summary(3, days=7, tz_name="UTC")["llm"]
    assert llm["totals"] == {"calls": 3, "input_tokens": 600, "output_tokens": 60, "unreported_calls": 1}
    assert [m["model"] for m in llm["by_model"]] == ["b/y", "a/x"]
    assert {s["surface"]: s["calls"] for s in llm["by_surface"]} == {"chat": 2, "trigger": 1}
    assert llm["per_day"][-1] == {"date": today.isoformat(), "calls": 3, "input_tokens": 600, "output_tokens": 60}


def test_stats_window_follows_utc_days_not_the_local_calendar(usage_db):
    """Regression: west of UTC the local date lags, but today's UTC usage must still be in the window."""
    now = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)  # still Nov 24 in Los Angeles
    usage_db.add(
        models.LlmUsageDay(
            user_id=3,
            day=now.date(),
            surface="chat",
            model="a/x",
            calls=1,
            input_tokens=5,
            output_tokens=1,
            unreported_calls=0,
        )
    )
    usage_db.commit()
    llm = StatsService(usage_db).summary(3, days=7, tz_name="America/Los_Angeles", now=now)["llm"]
    assert llm["totals"]["calls"] == 1
    assert llm["per_day"][-1]["date"] == "2026-11-25"


def test_owner_and_surface_follow_the_toolset_selection_thread(usage_db):
    """Regression: a model called on toolset selection's worker thread is still counted for the owner."""
    from janus import toolset_selection

    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="ok", usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})]
    )
    llm_usage.attach(model, "fake/selector")

    def selector(objective, candidates):
        model.invoke(objective)
        return (), "picked nothing"

    with event_logger.owned_by(11), llm_usage.usage_surface("chat"):
        toolset_selection._call_bounded(selector, "pick tools", (), 5)
    (row,) = _rows(usage_db)
    assert (row.user_id, row.surface) == (11, "chat")
