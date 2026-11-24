"""Tests for remote skill catalog parsing and lookup."""

import httpx

from janus.services import skill_catalog_service


def test_parse_skill_markdown_extracts_agentskills_frontmatter():
    """SKILL.md parsing should preserve agentskills metadata and markdown body."""
    parsed = skill_catalog_service._parse_skill_markdown(
        "incident-response",
        """---
name: Incident Response
description: Coordinate incidents calmly.
license: MIT
---
# Incident Response

Use a structured response loop.
""",
    )

    assert parsed == {
        "remote_id": "incident-response",
        "name": "Incident Response",
        "description": "Coordinate incidents calmly.",
        "license": "MIT",
        "content": "# Incident Response\n\nUse a structured response loop.",
        "source_url": "https://github.com/anthropics/skills/tree/main/skills/incident-response",
    }


def test_search_remote_skills_filters_by_frontmatter_and_body(monkeypatch):
    """Search should match normalized skills across id, metadata, and content."""
    monkeypatch.setattr(
        skill_catalog_service,
        "_get_catalog_items",
        lambda: [
            {
                "remote_id": "incident-response",
                "name": "Incident Response",
                "description": "Handle outages",
                "license": "MIT",
                "content": "Run triage and communicate status.",
                "source_url": "https://example.invalid/incident-response",
            },
            {
                "remote_id": "spreadsheet-cleanup",
                "name": "Spreadsheet Cleanup",
                "description": "Normalize messy CSV data",
                "license": None,
                "content": "Use Python or xlsx tooling.",
                "source_url": "https://example.invalid/spreadsheet-cleanup",
            },
        ],
    )

    by_query = skill_catalog_service.search_remote_skills("triage")
    assert [item["remote_id"] for item in by_query] == ["incident-response"]

    by_slug = skill_catalog_service.get_remote_skill("spreadsheet-cleanup")
    assert by_slug is not None
    assert by_slug["name"] == "Spreadsheet Cleanup"


def test_parse_skill_markdown_tolerates_invalid_yaml():
    """Malformed frontmatter should not crash parsing."""
    parsed = skill_catalog_service._parse_skill_markdown(
        "incident-response",
        """---
name: Incident Response
description: [broken
---
# Incident Response
""",
    )

    assert parsed == {
        "remote_id": "incident-response",
        "name": "incident-response",
        "description": "",
        "license": None,
        "content": "# Incident Response",
        "source_url": "https://github.com/anthropics/skills/tree/main/skills/incident-response",
    }


def test_fetch_catalog_items_skips_failed_remote_skill_requests(monkeypatch):
    """One bad remote skill fetch should not fail the entire catalog load."""

    class _FakeResponse:
        def __init__(self, payload=None, text: str = "", should_raise: bool = False):
            self._payload = payload
            self.text = text
            self._should_raise = should_raise

        def raise_for_status(self):
            if self._should_raise:
                raise httpx.HTTPStatusError("boom", request=None, response=None)

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        def get(self, _url, params=None, headers=None):
            del params, headers
            return _FakeResponse(
                payload={
                    "tree": [
                        {"path": "skills/good-skill/SKILL.md"},
                        {"path": "skills/bad-skill/SKILL.md"},
                    ]
                }
            )

    def _fake_httpx_get(url, headers=None, timeout=None):
        del headers, timeout
        if url.endswith("/good-skill/SKILL.md"):
            return _FakeResponse(
                text="""---
name: Good Skill
description: Works
---
# Good Skill
""",
            )
        return _FakeResponse(should_raise=True)

    monkeypatch.setattr(skill_catalog_service.httpx, "Client", _FakeClient)
    monkeypatch.setattr(skill_catalog_service.httpx, "get", _fake_httpx_get)

    items = skill_catalog_service._fetch_catalog_items()

    assert [item["remote_id"] for item in items] == ["good-skill"]
