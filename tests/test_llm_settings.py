"""Tests for the table-driven per-provider LLM settings update helper."""

from types import SimpleNamespace

import pytest

from janus import models, security
from janus.services.llm_settings import apply_llm_settings_update


def _update(**values):
    return SimpleNamespace(**values)


def test_plain_nullable_and_secret_fields_follow_their_rules():
    """Models copy verbatim, empty URL overrides clear to NULL, keys are encrypted or cleared."""
    settings = models.Settings(
        llm_provider=models.LLMProvider.OPENAI,
        openai_base_url="https://proxy.example/v1",
        gemini_api_key="old-encrypted",
    )

    apply_llm_settings_update(
        settings,
        _update(
            openai_model="gpt-4.1",
            ollama_base_url="",
            openai_base_url="",
            anthropic_api_key="sk-ant",
            gemini_api_key="",
            openrouter_api_key=None,
        ),
    )

    assert settings.openai_model == "gpt-4.1"
    assert settings.ollama_base_url == ""
    assert settings.openai_base_url is None
    assert security.security_service.decrypt(settings.anthropic_api_key) == "sk-ant"
    assert settings.gemini_api_key is None
    assert settings.openrouter_api_key is None


@pytest.mark.parametrize(
    ("provider", "column"),
    [
        (models.LLMProvider.OLLAMA, "ollama_model"),
        (models.LLMProvider.OPENAI, "openai_model"),
        (models.LLMProvider.OPENROUTER, "openrouter_model"),
        (models.LLMProvider.GEMINI, "gemini_model"),
        (models.LLMProvider.ANTHROPIC, "anthropic_model"),
    ],
)
def test_legacy_llm_model_routes_to_active_provider_and_wins(provider, column):
    """The legacy llm_model field lands on the active provider's column, overriding a same-request model."""
    settings = models.Settings(llm_provider=provider)

    apply_llm_settings_update(settings, _update(**{column: "specific", "llm_model": "legacy"}))

    assert getattr(settings, column) == "legacy"
