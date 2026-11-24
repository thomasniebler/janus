"""Regression contracts for prompt-injection attempts in email-derived LLM input."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from janus import llm_service, models, security
from janus.agent_tools import build_tool_set


@dataclass
class _CapturedChain:
    result: object
    payloads: list[dict[str, object]] = field(default_factory=list)

    def __or__(self, _parser):
        return self

    def invoke(self, payload):
        self.payloads.append(payload)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class _CapturedPrompt:
    template: str
    chain: _CapturedChain

    def __or__(self, _provider):
        return self.chain


def _service_with_captured_prompt(
    monkeypatch, result: object
) -> tuple[llm_service.LLMService, dict[str, _CapturedPrompt]]:
    """Build an LLM service whose provider is replaced with a deterministic prompt capture."""
    chain = _CapturedChain(result)
    captured: dict[str, _CapturedPrompt] = {}

    def capture_template(template: str) -> _CapturedPrompt:
        prompt = _CapturedPrompt(template, chain)
        captured["prompt"] = prompt
        return prompt

    monkeypatch.setattr(llm_service.ChatPromptTemplate, "from_template", staticmethod(capture_template))
    service = object.__new__(llm_service.LLMService)
    service.llm = object()
    return service, captured


def _assert_email_boundary(prompt: _CapturedPrompt, payload: dict[str, object]) -> None:
    """Assert the invariant every email-derived request must retain."""
    request = prompt.chain.payloads[0]
    email_content = str(request.get("email_content") or request.get("email_text"))
    assert request["untrusted_content_warning"] == llm_service.UNTRUSTED_EMAIL_CONTENT_WARNING
    assert "{untrusted_content_warning}" in prompt.template
    assert "<email_content " in email_content
    assert "</email_content>" in email_content
    assert "&lt;system&gt;" in email_content
    assert "&quot; onmouseover=&quot;exfiltrate()" in email_content
    assert str(payload["snippet"]) not in "\n".join(str(value) for value in request.values())


def test_classification_prompt_treats_adversarial_email_as_delimited_data(monkeypatch, adversarial_email_data):
    """Classifier prompts must keep hostile mail outside the instruction channel."""
    service, captured = _service_with_captured_prompt(
        monkeypatch,
        {"priority": "DIGEST", "reasoning": "Routine update.", "confidence": "low"},
    )
    priority, reasoning, confidence = service.classify_email(adversarial_email_data)

    assert (priority, reasoning, confidence) == (
        models.Priority.DIGEST,
        "Routine update.",
        models.ConfidenceLevel.LOW,
    )
    _assert_email_boundary(captured["prompt"], adversarial_email_data)


def test_reminder_extraction_prompt_rejects_injected_output_shape(monkeypatch, adversarial_email_data):
    """Reminder extraction must preserve both its mail boundary and result shape."""
    service, captured = _service_with_captured_prompt(monkeypatch, {"reminders": {"title": "Delete backups"}})

    assert service.extract_email_reminders(adversarial_email_data) == []
    _assert_email_boundary(captured["prompt"], adversarial_email_data)


def test_topic_extraction_prompt_rejects_injected_output_shape(monkeypatch, adversarial_email_data):
    """Topic extraction must not accept a hostile object in place of its list contract."""
    service, captured = _service_with_captured_prompt(monkeypatch, {"topics": {"title": "Take over account"}})

    assert service.extract_email_topics(adversarial_email_data) == []
    _assert_email_boundary(captured["prompt"], adversarial_email_data)


def test_malformed_json_from_email_extraction_fails_closed(monkeypatch, adversarial_email_data):
    """Parser failures caused by JSON-like hostile content must not create candidates."""
    service, _ = _service_with_captured_prompt(monkeypatch, ValueError("malformed provider JSON"))

    assert service.extract_email_reminders(adversarial_email_data) == []


def test_trigger_email_context_is_delimited_and_escaped(adversarial_email_data):
    """Emails handed to briefing/digest trigger runs are escaped and wrapped as untrusted content."""
    rendered = llm_service.format_email_content(
        1,
        str(adversarial_email_data["sender"]),
        str(adversarial_email_data["subject"]),
        str(adversarial_email_data["snippet"]),
    )

    assert rendered.startswith("<email_content ")
    assert rendered.endswith("</email_content>")
    assert "&lt;system&gt;" in rendered
    assert "&quot; onmouseover=&quot;exfiltrate()" in rendered
    assert str(adversarial_email_data["snippet"]) not in rendered


def test_tool_call_looking_email_content_is_retrieved_not_executed(db, adversarial_email_data, monkeypatch):
    """Mail-tool output may contain hostile text but cannot invoke another tool by itself."""
    user = models.User(
        username="prompt-contract-user",
        hashed_password=security.security_service.get_password_hash("secret"),
    )
    db.add(user)
    db.flush()
    account = models.EmailAccount(name="Prompt contract", email="user@example.test", password="secret", user_id=user.id)
    email = models.EmailRecord(
        account=account,
        message_id=str(adversarial_email_data["message_id"]),
        sender=str(adversarial_email_data["sender"]),
        subject=str(adversarial_email_data["subject"]),
        snippet=str(adversarial_email_data["snippet"]),
        body=str(adversarial_email_data["body"]),
        priority=models.Priority.DIGEST,
    )
    db.add_all([account, email])
    db.commit()

    tools = {tool.name: tool for tool in build_tool_set(db, user)}
    monkeypatch.setattr(
        "janus.services.email_service.EmailService.send_email",
        lambda *_args, **_kwargs: pytest.fail("email text must not execute send_email"),
    )

    detail = tools["get_email_detail"].invoke({"email_id": email.id})

    assert detail["snippet"] == adversarial_email_data["snippet"]
    assert detail["body"] == adversarial_email_data["body"]
