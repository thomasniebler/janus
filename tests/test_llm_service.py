"""Tests for provider-specific LLM service initialization."""

import asyncio
import json
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from janus import database, llm_service, models, security


def _make_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    database.Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def test_llm_service_initializes_ollama_with_provider_specific_settings(monkeypatch):
    """Uses provider-specific Ollama settings when initializing the local backend."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.OLLAMA,
            ollama_model="llama3.2:latest",
            ollama_base_url="http://ollama:11434",
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatOllama", FakeOllama)

    service = llm_service.LLMService()
    assert captured["model"] == "llama3.2:latest"
    assert captured["base_url"] == "http://ollama:11434"
    assert captured["client_kwargs"] == {}
    assert service.test_connection() == (True, "Successfully connected to LLM provider.")


def test_llm_service_initializes_ollama_cloud_with_decrypted_api_key(monkeypatch):
    """Decrypts and passes a stored Ollama API key as a bearer token."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.OLLAMA,
            ollama_model="gemma3:27b",
            ollama_base_url="https://ollama.com/api",
            ollama_api_key=security.security_service.encrypt("ollama-secret"),
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatOllama", FakeOllama)

    llm_service.LLMService()
    assert captured["model"] == "gemma3:27b"
    assert captured["base_url"] == "https://ollama.com"
    assert captured["client_kwargs"] == {"headers": {"Authorization": "Bearer ollama-secret"}}


def test_llm_service_preserves_self_hosted_ollama_base_url_paths(monkeypatch):
    """Leaves self-hosted base paths intact when only Ollama Cloud needs normalization."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.OLLAMA,
            ollama_model="llama3.2:latest",
            ollama_base_url="https://ollama.internal/api",
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeOllama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatOllama", FakeOllama)

    llm_service.LLMService()
    assert captured["base_url"] == "https://ollama.internal/api"


def test_llm_service_initializes_gemini_with_decrypted_api_key(monkeypatch):
    """Decrypts and passes the stored Gemini API key into the Gemini client."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.GEMINI,
            gemini_model="gemini-2.5-flash",
            gemini_api_key=security.security_service.encrypt("gemini-secret"),
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatGoogleGenerativeAI", FakeGemini)

    llm_service.LLMService()
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["google_api_key"] == "gemini-secret"


def _seed_anthropic_settings(**overrides):
    testing_session_local = _make_test_session()
    db = testing_session_local()
    values = {
        "llm_provider": models.LLMProvider.ANTHROPIC,
        "anthropic_model": "claude-sonnet-5",
        "anthropic_api_key": security.security_service.encrypt("anthropic-secret"),
    }
    values.update(overrides)
    db.add(models.Settings(**values))
    db.commit()
    db.close()
    return testing_session_local


def test_llm_service_initializes_anthropic_with_decrypted_api_key(monkeypatch):
    """Decrypts and passes the stored Anthropic API key into the Claude client (JAN-214)."""
    monkeypatch.setattr(database, "SessionLocal", _seed_anthropic_settings())
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatAnthropic", FakeAnthropic)

    llm_service.LLMService()
    assert captured["model"] == "claude-sonnet-5"
    # Current Claude models 400 on non-default temperature, so Janus must not send one.
    assert "temperature" not in captured
    assert captured["anthropic_api_key"] == "anthropic-secret"
    assert captured["anthropic_api_url"] is None


def test_llm_service_anthropic_honours_draft_overrides(monkeypatch):
    """Unsaved draft values (test-connection flow) override stored Anthropic settings."""
    monkeypatch.setattr(database, "SessionLocal", _seed_anthropic_settings())
    captured: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_service, "ChatAnthropic", FakeAnthropic)

    llm_service.LLMService(
        settings_override={
            "anthropic_model": "claude-haiku-4-5",
            "anthropic_api_key": "draft-key",
            "anthropic_base_url": "https://anthropic-proxy.internal",
        }
    )
    assert captured["model"] == "claude-haiku-4-5"
    assert captured["anthropic_api_key"] == "draft-key"
    assert captured["anthropic_api_url"] == "https://anthropic-proxy.internal"


def test_llm_service_anthropic_kwargs_match_real_client(monkeypatch):
    """The kwargs Janus passes must be accepted by the installed langchain-anthropic ChatAnthropic."""
    monkeypatch.setattr(
        database,
        "SessionLocal",
        _seed_anthropic_settings(anthropic_base_url="https://anthropic-proxy.internal"),
    )

    service = llm_service.LLMService()
    assert isinstance(service.llm, llm_service.ChatAnthropic)
    assert service.llm.model == "claude-sonnet-5"
    assert service.llm.anthropic_api_key.get_secret_value() == "anthropic-secret"
    assert service.llm.anthropic_api_url == "https://anthropic-proxy.internal"
    assert service.llm.temperature is None


def test_llm_service_anthropic_requires_api_key(monkeypatch):
    """Selecting Anthropic without a key fails fast with the same error pattern as other clouds."""
    monkeypatch.setattr(database, "SessionLocal", _seed_anthropic_settings(anthropic_api_key=None))

    with pytest.raises(ValueError, match="Anthropic API key not found in settings."):
        llm_service.LLMService()


def test_get_active_model_name_for_anthropic_defaults():
    """The active model resolves per provider, with a Claude default when unset."""
    settings = models.Settings(llm_provider=models.LLMProvider.ANTHROPIC, anthropic_model="claude-sonnet-5")
    assert llm_service.get_active_model_name(settings) == "claude-sonnet-5"
    settings = models.Settings(llm_provider=models.LLMProvider.ANTHROPIC, anthropic_model=None, llm_model=None)
    assert llm_service.get_active_model_name(settings) == llm_service.DEFAULT_ANTHROPIC_MODEL == "claude-opus-5"


def test_llm_service_initializes_openrouter_with_decrypted_api_key(monkeypatch):
    """Decrypts and passes the stored OpenRouter API key into the OpenAI-compatible client."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.OPENROUTER,
            openrouter_model="openrouter/auto",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_api_key=security.security_service.encrypt("openrouter-secret"),
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatOpenAI", FakeOpenAI)

    llm_service.LLMService()
    assert captured["model"] == "openrouter/auto"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["api_key"] == "openrouter-secret"


def test_llm_service_uses_openrouter_default_base_url_when_setting_is_empty(monkeypatch):
    """OpenRouter should never fall back to the OpenAI default endpoint."""
    testing_session_local = _make_test_session()
    db = testing_session_local()
    db.add(
        models.Settings(
            llm_provider=models.LLMProvider.OPENROUTER,
            openrouter_model="openrouter/auto",
            openrouter_base_url="",
            openrouter_api_key=security.security_service.encrypt("openrouter-secret"),
        )
    )
    db.commit()
    db.close()

    monkeypatch.setattr(database, "SessionLocal", testing_session_local)
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt):
            return prompt

    monkeypatch.setattr(llm_service, "ChatOpenAI", FakeOpenAI)

    llm_service.LLMService()
    assert captured["base_url"] == "https://openrouter.ai/api/v1"


def test_reminder_failure_log_data_redacts_email_content():
    """Reminder extraction logs should keep identifiers and counts, not raw content."""
    log_data = llm_service._reminder_failure_log_data(
        {
            "account_id": 7,
            "message_id": "msg-123",
            "subject": "Sensitive subject",
            "snippet": "Very sensitive details",
        },
        {
            "active_reminders": [{"title": "Reminder"}],
            "calendar_upcoming": [{"summary": "Event"}],
        },
    )

    assert log_data == {
        "message_id": "msg-123",
        "account_id": 7,
        "active_reminder_count": 1,
        "calendar_upcoming_count": 1,
    }


def test_topic_failure_log_data_redacts_email_content():
    """Topic extraction logs should keep counts and identifiers, not raw mail content."""
    log_data = llm_service._topic_failure_log_data(
        {
            "account_id": 7,
            "message_id": "msg-456",
            "subject": "Sensitive topic",
            "snippet": "Private discussion",
            "body": "Extremely private details",
        },
        {
            "active_topics": [{"title": "Hiring"}],
            "planner_context": {"calendar_today": [{"summary": "Interview"}]},
            "reminder_context": {"active_reminders": [{"title": "Send feedback"}]},
        },
    )

    assert log_data == {
        "message_id": "msg-456",
        "account_id": 7,
        "active_topic_count": 1,
        "calendar_today_count": 1,
        "active_reminder_count": 1,
    }


def test_topic_failure_log_data_handles_null_active_topics():
    """Topic extraction failure logging should tolerate an explicit null active-topics list."""
    log_data = llm_service._topic_failure_log_data(
        {
            "account_id": 7,
            "message_id": "msg-789",
        },
        {
            "active_topics": None,
            "planner_context": {"calendar_today": [{"summary": "Interview"}]},
            "reminder_context": {"active_reminders": [{"title": "Send feedback"}]},
        },
    )

    assert log_data == {
        "message_id": "msg-789",
        "account_id": 7,
        "active_topic_count": 0,
        "calendar_today_count": 1,
        "active_reminder_count": 1,
    }


def test_serialize_compacted_context_caps_large_prompt_payloads():
    """LLM prompt context should be compacted before it can exceed model limits."""
    context = {
        "recent_emails": [{"subject": f"Email {index}", "body": "x" * 20_000} for index in range(50)],
        "pending_suggestions": [{"title": f"Suggestion {index}", "reason": "y" * 8_000} for index in range(20)],
    }

    serialized = llm_service._serialize_compacted_context(context, max_chars=4_000)

    assert len(serialized) <= 4_000
    assert "Context truncated to fit prompt budget" in serialized or "truncated_items" in serialized
    assert '"recent_emails"' in serialized


def test_format_email_content_truncates_large_bodies():
    """Single-email prompt content should cap oversized message bodies."""
    rendered = llm_service.format_email_content(
        1,
        "sender@example.com",
        "Long email",
        "A" * 20_000,
        max_content_chars=2_000,
    )

    assert len(rendered) < 2_500
    assert "[truncated " in rendered
    assert "<email_content " in rendered


def test_truncate_text_returns_empty_for_non_positive_budgets():
    """Non-positive truncation limits should never leak the full original text."""
    assert llm_service._truncate_text("abcdef", 0) == ""
    assert llm_service._truncate_text("abcdef", -5) == ""


def test_truncate_text_falls_back_to_hard_cap_for_tiny_budgets():
    """Tiny budgets should still return a bounded prefix when the suffix cannot fit."""
    truncated = llm_service._truncate_text("abcdefghij", 5)

    assert truncated == "abcde"
    assert len(truncated) == 5


def test_truncate_text_reports_actual_removed_character_count():
    """The truncation notice should report the number of removed characters accurately."""
    text = "x" * 100

    truncated = llm_service._truncate_text(text, 30)

    match = re.fullmatch(r"(.*)\n\.\.\.\[truncated (\d+) chars\]", truncated, flags=re.DOTALL)
    assert match is not None
    kept_prefix, removed_count = match.groups()
    assert len(truncated) <= 30
    assert int(removed_count) == len(text) - len(kept_prefix)


def test_serialize_compacted_context_stays_bounded_for_tiny_budgets():
    """Context serialization should stay within even extremely small prompt budgets."""
    serialized = llm_service._serialize_compacted_context({"body": "x" * 1_000}, max_chars=20)

    assert len(serialized) <= 20


@pytest.mark.parametrize(
    "context",
    [
        {"body": "x" * 1_000},
        {"body": '"\\' * 2_000, "nested": {"items": ["\n\t\u2028" * 300] * 30}},
        [{"subject": f"Email {index}", "body": "y" * 5_000} for index in range(40)],
        "z" * 10_000,
    ],
    ids=["plain", "escape-heavy", "list", "scalar"],
)
@pytest.mark.parametrize("max_chars", [*range(2, 140), 200, 500, 1_000, 4_000])
def test_serialize_compacted_context_returns_bounded_json_for_any_budget(context, max_chars):
    """JAN-145: every budget of at least two characters yields parseable JSON within the budget."""
    serialized = llm_service._serialize_compacted_context(context, max_chars=max_chars)

    assert 2 <= len(serialized) <= max_chars
    json.loads(serialized)


@pytest.mark.parametrize("max_chars", [-10, 0, 1])
def test_serialize_compacted_context_returns_empty_string_when_no_json_fits(max_chars):
    """JAN-145: below two characters not even ``{}`` fits, so the result is an empty string."""
    assert llm_service._serialize_compacted_context({"body": "x" * 100}, max_chars=max_chars) == ""


def test_serialize_compacted_context_degrades_through_minimal_envelopes():
    """JAN-145: tiny budgets fall back to the documented minimal JSON objects in order."""
    context = {"body": "x" * 1_000}
    notice_only = json.dumps({"notice": llm_service._CONTEXT_TRUNCATED_NOTICE})

    assert llm_service._serialize_compacted_context(context, max_chars=2) == "{}"
    assert llm_service._serialize_compacted_context(context, max_chars=18) == "{}"
    assert llm_service._serialize_compacted_context(context, max_chars=19) == '{"truncated": true}'
    assert llm_service._serialize_compacted_context(context, max_chars=len(notice_only) - 1) == '{"truncated": true}'
    assert llm_service._serialize_compacted_context(context, max_chars=len(notice_only)) == notice_only


def test_serialize_compacted_context_preview_survives_json_escaping():
    """JAN-145: escape-heavy previews are shrunk until the escaped envelope fits."""
    context = {"body": '"' * 5_000}

    serialized = llm_service._serialize_compacted_context(context, max_chars=300)
    parsed = json.loads(serialized)

    assert len(serialized) <= 300
    assert parsed["notice"] == llm_service._CONTEXT_TRUNCATED_NOTICE
    assert parsed["preview"]


def test_build_digest_topic_threads_text_prefers_structured_topics():
    """Digest prompt context should preserve durable topic groupings before ad-hoc buckets."""
    rendered = llm_service._build_digest_topic_threads_text(
        [
            {
                "sender": "manager@example.com",
                "subject": "Interview feedback",
                "snippet": "We need the final panel decision.",
                "topics": [
                    {"id": 11, "title": "Hiring pipeline", "summary": "Interview coordination.", "pinned": True}
                ],
            },
            {
                "sender": "alerts@example.com",
                "subject": "Nightly backup",
                "snippet": "Backup completed successfully.",
                "topics": [],
            },
        ]
    )

    assert "STRUCTURED TOPIC THREADS:" in rendered
    assert "- Hiring pipeline: Interview coordination. [pinned]" in rendered
    assert "Interview feedback" in rendered
    assert "UNGROUPED EMAILS:" in rendered
    assert "Nightly backup" in rendered


def test_stream_agent_chat_emits_tool_and_token_events(monkeypatch):
    """Streams tool call/result markers plus chat model token chunks."""
    service = object.__new__(llm_service.LLMService)
    service.llm = object()

    captured: dict[str, object] = {}

    def fake_create_tool_calling_agent(llm, tools, prompt):
        captured["create_args"] = (llm, tools, prompt)
        return "fake-agent"

    class _Chunk:
        def __init__(self, content):
            self.content = content

    class FakeAgentExecutor:
        def __init__(self, *, agent, tools, verbose):
            captured["executor_args"] = (agent, tools, verbose)

        async def astream_events(self, payload, version):
            captured["astream_payload"] = payload
            captured["astream_version"] = version
            events = [
                {
                    "event": "on_tool_start",
                    "name": "list_memory_facts",
                    "run_id": "run-1",
                    "parent_ids": [],
                    "data": {"input": {"query": "plans", "api_key": "sk-123"}},
                },
                # The runtime's wrapper re-invokes the tool: a nested run, not a second call.
                {
                    "event": "on_tool_start",
                    "name": "list_memory_facts",
                    "run_id": "run-1-inner",
                    "parent_ids": ["run-1"],
                    "data": {"input": {"query": "plans"}},
                },
                {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("Hello ")}},
                {"event": "on_chat_model_stream", "data": {"chunk": _Chunk([{"text": "world"}])}},
                {
                    "event": "on_tool_end",
                    "name": "list_memory_facts",
                    "run_id": "run-1-inner",
                    "parent_ids": ["run-1"],
                    "data": {"output": "fact"},
                },
                {
                    "event": "on_tool_end",
                    "name": "list_memory_facts",
                    "run_id": "run-1",
                    "parent_ids": [],
                    "data": {"output": "fact"},
                },
                {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("")}},
            ]
            for event in events:
                yield event

    monkeypatch.setattr(llm_service, "create_tool_calling_agent", fake_create_tool_calling_agent)
    monkeypatch.setattr(llm_service, "AgentExecutor", FakeAgentExecutor)

    async def _collect_tokens():
        tokens = []
        async for token in service.stream_agent_chat(
            "You are Janus.",
            tools=["tool-a"],
            history=[("Hi", "Hello there")],
            user_message="What should I do next?",
        ):
            tokens.append(token)
        return tokens

    tokens = asyncio.run(_collect_tokens())

    prefix = "__TOOL_EVENT__:"
    plan_prefix = "__PLAN_EVENT__:"
    tool_events = [json.loads(token[len(prefix) :]) for token in tokens if token.startswith(prefix)]
    plan_events = [json.loads(token[len(plan_prefix) :]) for token in tokens if token.startswith(plan_prefix)]
    text_and_legacy = [token for token in tokens if not token.startswith((prefix, plan_prefix))]
    # The deprecated one-word markers keep streaming unchanged for older clients.
    assert text_and_legacy == [
        "__TOOL_CALL__:list_memory_facts",
        "__TOOL_CALL__:list_memory_facts",
        "Hello ",
        "world",
        "__TOOL_RESULT__:list_memory_facts",
        "__TOOL_RESULT__:list_memory_facts",
    ]
    # One call, reported once: the nested wrapper run is not a second call.
    assert [(event["call_id"], event["phase"]) for event in tool_events] == [("run-1", "start"), ("run-1", "end")]
    start, end = tool_events
    # No model events in this stream: the call still gets a step (JAN-316), closed at the end.
    assert [(event["step_id"], event["status"]) for event in plan_events] == [
        ("step-1", "pending"),
        ("step-1", "running"),
        ("step-1", "completed"),
    ]
    assert start["step_id"] == end["step_id"] == "step-1"
    assert start["args"] == {"query": "plans", "api_key": "[redacted]"}
    assert start["tool_name"] == "list_memory_facts"
    assert start["tool_id"] is None
    assert end["result_preview"] == "fact"
    assert end["args"] == start["args"]
    assert isinstance(end["duration_ms"], int)
    assert captured["executor_args"] == ("fake-agent", ["tool-a"], False)
    assert captured["astream_version"] == "v2"
    assert captured["astream_payload"]["input"] == "What should I do next?"
    assert captured["astream_payload"]["system_prompt"] == "You are Janus."
    history_messages = captured["astream_payload"]["history"]
    assert len(history_messages) == 2
    assert history_messages[0].content == "Hi"
    assert history_messages[1].content == "Hello there"


def _stream_text(monkeypatch, events) -> str:
    """Return the reply text ``stream_agent_chat`` yields for a fake event stream."""
    service = object.__new__(llm_service.LLMService)
    service.llm = object()

    class FakeAgentExecutor:
        def __init__(self, *, agent, tools, verbose):
            pass

        async def astream_events(self, payload, version):
            for event in events:
                yield event

    monkeypatch.setattr(llm_service, "create_tool_calling_agent", lambda llm, tools, prompt: "fake-agent")
    monkeypatch.setattr(llm_service, "AgentExecutor", FakeAgentExecutor)

    async def _collect():
        return [token async for token in service.stream_agent_chat("Janus", [], [], "Check my mail")]

    return "".join(token for token in asyncio.run(_collect()) if not token.startswith("__"))


def _round(*texts: str, tool: str | None = None, run_id: str = "") -> list[dict]:
    """Return one agent round: a model call streaming ``texts``, then optionally one tool call."""

    class _Chunk:
        def __init__(self, content):
            self.content = content

    class _Message:
        def __init__(self, tool_calls):
            self.tool_calls = tool_calls

    calls = [{"name": tool, "args": {}, "id": run_id}] if tool else []
    events = [{"event": "on_chat_model_start", "data": {}}]
    events += [{"event": "on_chat_model_stream", "data": {"chunk": _Chunk(text)}} for text in texts]
    events.append({"event": "on_chat_model_end", "data": {"output": _Message(calls)}})
    if tool:
        events.append({"event": "on_tool_start", "name": tool, "run_id": run_id, "parent_ids": [], "data": {}})
        events.append(
            {"event": "on_tool_end", "name": tool, "run_id": run_id, "parent_ids": [], "data": {"output": "ok"}}
        )
    return events


def test_stream_agent_chat_separates_each_rounds_text_with_a_blank_line(monkeypatch):
    """A plan, its progress notes, and the answer stream as separate paragraphs, never glued together."""
    events = [
        *_round("Plan: check the ", "inbox, then the calendar.", tool="list_emails", run_id="r1"),
        *_round("3 new mails, as expected.\n", tool="list_events", run_id="r2"),
        # A round that only calls a tool adds no stray break.
        *_round(tool="list_events", run_id="r3"),
        *_round("Done: ", "you're free at 3pm."),
    ]
    assert _stream_text(monkeypatch, events) == (
        "Plan: check the inbox, then the calendar.\n\n3 new mails, as expected.\n\nDone: you're free at 3pm."
    )


def test_stream_agent_chat_adds_no_break_before_the_first_text(monkeypatch):
    """A turn that starts with a silent tool round begins its reply without leading blank lines."""
    events = [*_round(tool="list_emails", run_id="r1"), *_round("Nothing new.\n\n"), *_round("Bye.")]
    assert _stream_text(monkeypatch, events) == "Nothing new.\n\nBye."


def test_stream_agent_chat_yields_error_message_when_agent_construction_fails(monkeypatch):
    """Returns an in-band error token instead of raising when agent setup fails."""
    service = object.__new__(llm_service.LLMService)
    service.llm = object()
    log_calls = []

    def fake_create_tool_calling_agent(llm, tools, prompt):
        raise RuntimeError("agent build exploded")

    def fake_log(event_type, source, message, details=None):
        log_calls.append((event_type, source, message, details))

    monkeypatch.setattr(llm_service, "create_tool_calling_agent", fake_create_tool_calling_agent)
    monkeypatch.setattr(llm_service.event_logger, "log", fake_log)

    async def _collect_tokens():
        tokens = []
        async for token in service.stream_agent_chat(
            "You are Janus.",
            tools=["tool-a"],
            history=[],
            user_message="What should I do next?",
        ):
            tokens.append(token)
        return tokens

    tokens = asyncio.run(_collect_tokens())

    assert tokens == ["\n\n[Error during chat]: agent build exploded"]
    assert log_calls == [(models.EventType.ERROR, "LLMService", "LLM Chat failed: agent build exploded", None)]


def test_stream_agent_chat_yields_error_message_when_event_stream_fails(monkeypatch):
    """Keeps partial output and emits an in-band error token on event stream failure."""
    service = object.__new__(llm_service.LLMService)
    service.llm = object()
    log_calls = []

    def fake_create_tool_calling_agent(llm, tools, prompt):
        return "fake-agent"

    class _Chunk:
        def __init__(self, content):
            self.content = content

    class FakeAgentExecutor:
        def __init__(self, *, agent, tools, verbose):
            pass

        async def astream_events(self, payload, version):
            yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("Hello ")}}
            raise RuntimeError("stream exploded")

    def fake_log(event_type, source, message, details=None):
        log_calls.append((event_type, source, message, details))

    monkeypatch.setattr(llm_service, "create_tool_calling_agent", fake_create_tool_calling_agent)
    monkeypatch.setattr(llm_service, "AgentExecutor", FakeAgentExecutor)
    monkeypatch.setattr(llm_service.event_logger, "log", fake_log)

    async def _collect_tokens():
        tokens = []
        async for token in service.stream_agent_chat(
            "You are Janus.",
            tools=["tool-a"],
            history=[],
            user_message="What should I do next?",
        ):
            tokens.append(token)
        return tokens

    tokens = asyncio.run(_collect_tokens())

    assert tokens == ["Hello ", "\n\n[Error during chat]: stream exploded"]
    assert log_calls == [(models.EventType.ERROR, "LLMService", "LLM Chat failed: stream exploded", None)]
