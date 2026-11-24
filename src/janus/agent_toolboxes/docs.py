"""Docs tools: Janus reads and searches its own documentation (JAN-297).

Read-only over the first-party pages in ``docs/docs`` (plus the root
``TECH_SPEC.md`` as ``tech-spec.md``), through the same path confinement as
the ``/api/docs`` router (``services/docs_service.py``). The ``reviews/``
directory -- dated security assessment and code review reports -- is not
offered.

The registry entry does not set ``reads_untrusted_content``: the pages are
written by the Janus maintainers and shipped with the code, so they are not
outside text in the JAN-127 sense, and treating them as untrusted would
spotlight every answer about Janus itself and escalate the next consequential
tool for no reason.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import BaseTool, tool

from janus.agent_toolboxes.shared import ToolContext
from janus.services import docs_service

#: Most characters one ``read_doc`` call returns; ``next_offset`` continues from there.
READ_DOC_MAX_CHARS = 12_000
#: Most hits one ``search_docs`` call returns.
SEARCH_MAX_RESULTS = 8
_SNIPPET_CHARS = 280
_WORD_RE = re.compile(r"[\w.-]+")


def _not_found(path: str) -> dict[str, Any]:
    return {
        "message": f"No documentation page {path!r}. Use list_docs or search_docs to find the right path.",
    }


def read_doc(path: str, section: str | None = None, offset: int = 0) -> dict[str, Any]:
    """Return one page (or one heading section), at most :data:`READ_DOC_MAX_CHARS` from ``offset``."""
    path = (path or "").strip()
    if "#" in path and section is None:
        path, section = path.split("#", 1)
    try:
        markdown = docs_service.read_doc_text(path, excluded_dirs=docs_service.AGENT_EXCLUDED_DIRS)
    except docs_service.DocNotFoundError:
        return _not_found(path)
    result: dict[str, Any] = {"path": path or "index.md", "title": docs_service.page_title(markdown, path)}
    text = markdown
    if section:
        found = docs_service.find_section(markdown, section)
        if found is None:
            return {
                **result,
                "message": f"No section {section!r} on this page.",
                "sections": [s.heading for s in docs_service.split_sections(markdown) if s.level > 0],
            }
        result["section"] = found.heading
        text = found.text
    start = max(0, int(offset or 0))
    chunk = text[start : start + READ_DOC_MAX_CHARS]
    end = start + len(chunk)
    result.update(
        {
            "text": chunk,
            "offset": start,
            "total_chars": len(text),
            "next_offset": end if end < len(text) else None,
        }
    )
    return result


def _terms(query: str) -> list[str]:
    return list(dict.fromkeys(term.lower() for term in _WORD_RE.findall(query) if len(term) > 1))


def _snippet(text: str, terms: list[str]) -> str:
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, min(positions) - _SNIPPET_CHARS // 3) if positions else 0
    snippet = " ".join(text[start : start + _SNIPPET_CHARS].split())
    return ("..." if start else "") + snippet + ("..." if start + _SNIPPET_CHARS < len(text) else "")


def search_docs(query: str, limit: int = SEARCH_MAX_RESULTS) -> dict[str, Any]:
    """Keyword-search every readable page's heading sections; best matches first."""
    terms = _terms(query)
    if not terms:
        return {"message": "Give one or more keywords to search for.", "results": []}
    hits: list[tuple[int, int, str, docs_service.DocSection]] = []
    for path in docs_service.list_pages(excluded_dirs=docs_service.AGENT_EXCLUDED_DIRS):
        try:
            markdown = docs_service.read_doc_text(path, excluded_dirs=docs_service.AGENT_EXCLUDED_DIRS)
        except docs_service.DocNotFoundError:
            continue
        for section in docs_service.split_sections(markdown, nested=False):
            body = section.text.lower()
            heading = section.heading.lower()
            matched = [term for term in terms if term in body]
            if not matched:
                continue
            score = sum(body.count(term) for term in matched) + 5 * sum(term in heading for term in terms)
            hits.append((len(matched), score, path, section))
    hits.sort(key=lambda hit: (-hit[0], -hit[1], hit[2]))
    bounded = max(1, min(int(limit or SEARCH_MAX_RESULTS), 20))
    results = [
        {
            "path": path,
            "heading": section.heading or None,
            "section": section.slug or None,
            "snippet": _snippet(section.text, terms),
        }
        for _matched, _score, path, section in hits[:bounded]
    ]
    if not results:
        return {"message": f"Nothing in the documentation matches {query!r}.", "results": []}
    return {"results": results, "hint": "Open a hit with read_doc(path, section)."}


def build_docs_tools(context: ToolContext) -> list[BaseTool]:
    """Build the read-only ``list_docs``, ``read_doc`` and ``search_docs`` tools."""
    del context  # the docs are the same for every user

    @tool("list_docs")
    def list_docs_tool() -> dict[str, Any]:
        """List Janus's own documentation: the navigation tree (titles and paths) plus pages not in it.

        Use it for questions about Janus itself -- how to set something up, what a setting or tool does,
        why a tool answered BLOCKED, what Janus can do. Then open a page with read_doc.
        """
        return docs_service.agent_nav()

    @tool("read_doc")
    def read_doc_tool(path: str, section: str | None = None, offset: int = 0) -> dict[str, Any]:
        """Read one page of Janus's documentation by path (e.g. ``quickstart.md`` or ``tools.md``).

        ``section`` picks one heading (its text or anchor, e.g. ``send-mail``); ``path#anchor`` works too.
        Long text is cut at about 12,000 characters: call again with ``offset=next_offset`` to continue.
        """
        return read_doc(path, section, offset)

    @tool("search_docs")
    def search_docs_tool(query: str, limit: int = SEARCH_MAX_RESULTS) -> dict[str, Any]:
        """Search Janus's documentation by keywords; returns path, heading, section anchor and a snippet per hit."""
        return search_docs(query, limit)

    return [list_docs_tool, read_doc_tool, search_docs_tool]


__all__ = ["READ_DOC_MAX_CHARS", "SEARCH_MAX_RESULTS", "build_docs_tools", "read_doc", "search_docs"]
