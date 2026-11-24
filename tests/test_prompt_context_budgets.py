"""Tiny-budget coverage for the email extractors' prompt assembly (JAN-146).

The reminder, topic and planner extractors compact their context with
``_serialize_compacted_context`` and cap the email body with
``format_email_content``. These tests shrink those budgets to a few hundred
characters, render each real prompt through a stub model (no live model calls),
and check that the injected fragments stay within budget and the prompt around
them stays intact. The context fragment is also expected to parse as JSON, which
relies on the bounded-JSON contract of ``_serialize_compacted_context`` (JAN-145).
"""

import json
import re

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from janus import llm_service

_TRUNCATION_MARKER = re.compile(r"\.\.\.\[truncated \d+ chars\]")

_HUGE_CONTEXT = {
    "active_reminders": [
        {"title": f"Reminder {index}", "notes": "Prepare the quarterly review deck " * 200} for index in range(60)
    ],
    "calendar_upcoming": [{"title": f"Meeting {index}", "location": "Room 4" * 300} for index in range(60)],
    "topics": {f"topic-{index}": {"summary": 'Quoted "status" \\ update ' * 150} for index in range(80)},
    "planner": {"tasks": [{"id": index, "description": "x" * 5_000} for index in range(40)]},
}

_EMAIL = {
    "sender": "Alice <alice@example.com>",
    "subject": "Quarterly review <draft>",
    "snippet": "Please review the attached <b>draft</b> & send notes. " * 400,
    "body": "Please review the attached <b>draft</b> & send notes. " * 400,
    "priority": "IMPORTANT",
}

# (extractor method, context keyword, budget constant, context section header, empty JSON reply)
_EXTRACTORS = [
    (
        "extract_email_reminders",
        "reminder_context",
        "_TOPIC_CONTEXT_MAX_CHARS",
        "CURRENT REMINDER AND CALENDAR CONTEXT:",
        {"reminders": []},
    ),
    (
        "extract_email_topics",
        "topic_context",
        "_TOPIC_CONTEXT_MAX_CHARS",
        "CURRENT TOPIC, PLANNER, AND REMINDER CONTEXT:",
        {"topics": []},
    ),
    (
        "extract_planner_suggestions",
        "planner_context",
        "_PLANNER_CONTEXT_MAX_CHARS",
        "CURRENT PLANNER CONTEXT:",
        {"suggestions": []},
    ),
]


def _render_prompt(monkeypatch, method, context_kwarg, budget_name, context_budget, email_budget, reply):
    """Run one extractor against a stub model and return the prompt it rendered."""
    monkeypatch.setattr(llm_service, budget_name, context_budget)
    monkeypatch.setattr(llm_service, "_EMAIL_CONTENT_MAX_CHARS", email_budget)
    rendered: list[str] = []

    def fake_model(prompt_value):
        rendered.append(prompt_value.to_string())
        return AIMessage(content=json.dumps(reply))

    service = object.__new__(llm_service.LLMService)
    service.llm = RunnableLambda(fake_model)

    result = getattr(service, method)(dict(_EMAIL), **{context_kwarg: _HUGE_CONTEXT})

    assert result == []
    assert len(rendered) == 1
    return rendered[0]


def _section(prompt: str, header: str, next_header: str) -> str:
    """Return the text injected between two prompt section headers."""
    match = re.search(re.escape(header) + r"\n {8}(.*?)\n\s*" + re.escape(next_header), prompt, flags=re.DOTALL)
    assert match is not None, f"{header!r} section missing from prompt"
    return match.group(1)


@pytest.mark.parametrize(
    ("method", "context_kwarg", "budget_name", "header", "reply"),
    _EXTRACTORS,
    ids=[extractor[0] for extractor in _EXTRACTORS],
)
@pytest.mark.parametrize(("context_budget", "email_budget"), [(400, 200), (1_000, 120), (2_500, 600)])
def test_extractor_prompt_fragments_stay_bounded_under_tiny_budgets(
    monkeypatch, method, context_kwarg, budget_name, header, reply, context_budget, email_budget
):
    """Tiny budgets keep the context and email fragments bounded inside an intact prompt."""
    prompt = _render_prompt(monkeypatch, method, context_kwarg, budget_name, context_budget, email_budget, reply)

    context_text = _section(prompt, header, "EMAIL CONTENT:")
    assert 0 < len(context_text) <= context_budget
    parsed_context = json.loads(context_text)
    assert isinstance(parsed_context, dict)
    assert "truncated" in context_text.lower()

    email_block = _section(prompt, "EMAIL CONTENT:", "Return JSON with this exact shape:")
    match = re.fullmatch(
        r'<email_content id="1" from="[^"]*" subject="[^"]*" priority="IMPORTANT">\n(.*)\n</email_content>',
        email_block,
        flags=re.DOTALL,
    )
    assert match is not None, "email boundary tags must survive truncation"
    body = match.group(1)
    # The body is truncated before HTML escaping, so the cap applies to the unescaped text.
    assert len(body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")) <= email_budget
    assert _TRUNCATION_MARKER.search(body)
    assert "<b>" not in body

    assert prompt.count("<email_content id=") == 1
    assert llm_service.UNTRUSTED_EMAIL_CONTENT_WARNING in prompt
    assert "Rules:" in prompt


@pytest.mark.parametrize(
    ("method", "context_kwarg", "budget_name", "header", "reply"),
    _EXTRACTORS,
    ids=[extractor[0] for extractor in _EXTRACTORS],
)
def test_extractor_prompt_stays_readable_when_context_budget_is_microscopic(
    monkeypatch, method, context_kwarg, budget_name, header, reply
):
    """A budget smaller than the preview envelope still injects a minimal JSON object (JAN-145)."""
    prompt = _render_prompt(monkeypatch, method, context_kwarg, budget_name, 24, 80, reply)

    context_text = _section(prompt, header, "EMAIL CONTENT:")
    assert len(context_text) <= 24
    assert json.loads(context_text) == {"truncated": True}
    assert "Prepare the quarterly review deck" not in prompt
    assert "</email_content>" in prompt
    assert "Return JSON with this exact shape:" in prompt
