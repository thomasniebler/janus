"""Tests for per-trigger, chat, and utility model choices that fail loudly (JAN-233)."""

from types import SimpleNamespace

import pytest

from janus import llm_service, models, security
from janus import trigger_definition_service as definitions
from janus.agent_task_runner import AgentTaskRun
from janus.main import api
from janus.trigger_runtime import run_definition_inline


def _user(db) -> models.User:
    user = models.User(username="model-owner", hashed_password="x", is_admin=True)
    db.add(user)
    db.add(models.Settings())
    db.commit()
    db.refresh(user)
    return user


def test_default_choice_uses_the_default_service(db, monkeypatch):
    """No override builds the plain default LLM service."""
    calls = []
    monkeypatch.setattr(llm_service, "LLMService", lambda **kwargs: calls.append(kwargs) or "svc")
    assert llm_service.build_llm_service(db, llm_service.ModelChoice(), purpose="chat") == "svc"
    assert calls == [{"db": db}]


def test_explicit_choice_is_passed_as_an_override(db, monkeypatch):
    """An explicit provider/model becomes a settings override."""
    calls = []
    monkeypatch.setattr(llm_service, "LLMService", lambda **kwargs: calls.append(kwargs) or "svc")
    llm_service.build_llm_service(db, llm_service.ModelChoice("anthropic", "claude-sonnet-5"), purpose="trigger")
    assert calls[0]["settings_override"] == {"llm_provider": "anthropic", "model_override": "claude-sonnet-5"}


def test_unusable_choice_fails_loudly_without_fallback(db):
    """A provider without credentials raises instead of silently using the default model."""
    _user(db)
    with pytest.raises(llm_service.ModelUnavailableError, match="does not fall back to the default"):
        llm_service.build_llm_service(db, llm_service.ModelChoice("openai", "gpt-5"), purpose="trigger")


def test_chat_names_the_unusable_model_instead_of_a_generic_error(db):
    """The chat socket's user-facing error names the model and says there is no fallback."""
    _user(db)
    with pytest.raises(llm_service.ModelUnavailableError) as raised:
        llm_service.build_llm_service(db, llm_service.ModelChoice("openai", "gpt-5"), purpose="chat")
    message = llm_service.user_facing_llm_error(raised.value)
    assert message.startswith("The chat model openai/gpt-5 is not usable.")
    assert "does not fall back to the default" in message


def test_describe_model_labels_default_and_overrides(db):
    """Run results can name the model without initializing a client."""
    _user(db)
    assert llm_service.describe_model(db, None).endswith("(default)")
    assert llm_service.describe_model(db, llm_service.ModelChoice("anthropic", "claude-sonnet-5")) == (
        "anthropic/claude-sonnet-5"
    )


def test_trigger_run_uses_its_model_and_records_it(db, testing_session_local, monkeypatch):
    """A definition's model reaches the executor, and the result names it."""
    from janus import unattended_execution

    user = _user(db)
    definition = definitions.create_definition(
        db,
        user.id,
        name="Cheap check",
        objective="Check in.",
        tool_ids=["core"],
        kind="manual",
        llm_provider="ollama",
        llm_model="qwen3:8b",
    )
    seen = []

    def executor(db, system_prompt, tools, objective):
        seen.append(unattended_execution._CURRENT_MODEL_CHOICE.get())  # noqa: SLF001
        return AgentTaskRun(output="Done.", tool_calls=())

    record = run_definition_inline(testing_session_local, definition.id, task_executor=executor)

    assert record.status == "completed"
    assert seen == [llm_service.ModelChoice("ollama", "qwen3:8b")]
    from janus.plan_state import load_plan_outcomes

    result = load_plan_outcomes(db, user.id, [record.task_id])[record.task_id].result
    assert result.startswith("Model: ollama/qwen3:8b")


def test_trigger_with_unusable_model_fails_the_run_terminally(db, testing_session_local):
    """The real executor refuses to run on anything but the chosen model."""
    user = _user(db)
    definition = definitions.create_definition(
        db,
        user.id,
        name="Needs OpenAI",
        objective="Check in.",
        tool_ids=["core"],
        kind="manual",
        llm_provider="openai",
        llm_model="gpt-5",
    )

    record = run_definition_inline(testing_session_local, definition.id)

    assert record.status == "exhausted", "a model misconfiguration must not be retried or hidden"
    assert "does not fall back to the default" in (record.last_error or "")


def test_definition_rejects_unknown_providers(db):
    """Only known providers can be chosen."""
    user = _user(db)
    with pytest.raises(definitions.TriggerDefinitionError, match="llm_provider"):
        definitions.create_definition(
            db, user.id, name="x", objective="y", tool_ids=[], kind="manual", llm_provider="skynet"
        )


def test_delegated_children_inherit_the_parent_model(db):
    """A delegation records the parent step's model choice."""
    from janus.delegation_service import create_delegation

    user = _user(db)
    record = create_delegation(
        db,
        owner_id=user.id,
        parent_task_id="parent-task",
        objective="Look something up.",
        requested_tool_ids=[],
        allowed_tool_ids=["core"],
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5",
    )
    assert (record.llm_provider, record.llm_model) == ("anthropic", "claude-haiku-4-5")


def test_settings_api_sets_and_resets_chat_and_utility_models(client, db):
    """AI & System stores per-surface choices; empty resets to the default."""
    user = _user(db)
    api.dependency_overrides[security.admin_required] = lambda: SimpleNamespace(id=user.id, is_admin=True)
    try:
        response = client.patch(
            "/api/planner/settings",
            json={"chat_llm_provider": "Anthropic", "chat_llm_model": "claude-sonnet-5", "utility_llm_model": "tiny"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["chat_llm_provider"], body["chat_llm_model"]) == ("anthropic", "claude-sonnet-5")
        assert (body["utility_llm_provider"], body["utility_llm_model"]) == (None, "tiny")

        reset = client.patch("/api/planner/settings", json={"chat_llm_provider": None, "chat_llm_model": ""})
        assert (reset.json()["chat_llm_provider"], reset.json()["chat_llm_model"]) == (None, None)

        bad = client.patch("/api/planner/settings", json={"utility_llm_provider": "skynet"})
        assert bad.status_code == 422
    finally:
        api.dependency_overrides.pop(security.admin_required, None)
    db.expire_all()
    assert llm_service.model_choice_from_settings(db, "utility") == llm_service.ModelChoice(None, "tiny")
