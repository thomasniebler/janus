"""Turn a fetched web page's bytes into readable text (JAN-329).

Used by ``services/web_fetch_service.py``. Standard library only: an
``html.parser`` pass keeps visible text (headings, paragraphs, list items),
drops scripts, styles, forms, navigation, footers, asides, and elements
hidden with ``hidden``, ``aria-hidden="true"``, or an inline
``display:none``/``visibility:hidden`` style (a common hiding place for
injected instructions), and prefers ``<main>``/``<article>`` content when the
page has enough of it.
"""

from __future__ import annotations

import codecs
import re
from html.parser import HTMLParser

#: Elements whose content is never page text.
_SKIPPED_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "math",
        "iframe",
        "object",
        "embed",
        "canvas",
        "select",
        "button",
        "head",
        "nav",
        "footer",
        "aside",
        "form",
    }
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "blockquote",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "header",
        "hr",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tr",
        "ul",
    }
)
_HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}
_MAIN_TAGS = frozenset({"main", "article"})
_HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
#: Below this many characters, a ``<main>``/``<article>`` region is not trusted to be the page.
_MIN_MAIN_CHARS = 200


class _ReadableTextParser(HTMLParser):
    """Collect visible text, preferring ``<main>``/``<article>`` content when the page has it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.all_parts: list[str] = []
        self.main_parts: list[str] = []
        self._stack: list[tuple[str, bool, bool]] = []  # (tag, skipped, main)
        self._in_title = False
        self._pre_depth = 0

    @property
    def _skipping(self) -> bool:
        return bool(self._stack) and self._stack[-1][1]

    @property
    def _in_main(self) -> bool:
        return bool(self._stack) and self._stack[-1][2]

    def _emit(self, text: str) -> None:
        self.all_parts.append(text)
        if self._in_main:
            self.main_parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "title" and not self.title_parts:
            self._in_title = True
        if tag in _VOID_TAGS:
            if not self._skipping and tag in {"br", "hr"}:
                self._emit("\n")
            return
        hidden = (
            "hidden" in values
            or values.get("aria-hidden", "").lower() == "true"
            or bool(_HIDDEN_STYLE.search(values.get("style", "")))
        )
        skipped = self._skipping or tag in _SKIPPED_TAGS or hidden
        main = self._in_main or tag in _MAIN_TAGS or values.get("role", "").lower() == "main"
        self._stack.append((tag, skipped, main))
        if skipped:
            return
        if tag == "pre":
            self._pre_depth += 1
        if tag in _HEADING_TAGS:
            self._emit("\n\n" + "#" * _HEADING_TAGS[tag] + " ")
        elif tag == "li":
            self._emit("\n- ")
        elif tag in {"td", "th"}:
            self._emit(" ")
        elif tag in _BLOCK_TAGS:
            self._emit("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if not any(open_tag == tag for open_tag, _, _ in self._stack):
            return  # stray end tag
        while self._stack:
            open_tag, skipped, _ = self._stack.pop()
            if not skipped:
                if open_tag == "pre":
                    self._pre_depth = max(0, self._pre_depth - 1)
                if open_tag in _HEADING_TAGS or open_tag in _BLOCK_TAGS:
                    self._emit("\n\n")
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skipping:
            return
        if self._pre_depth:
            self._emit(data)
        else:
            self._emit(re.sub(r"\s+", " ", data))


def _tidy(text: str) -> str:
    """Strip each line and collapse runs of blank lines."""
    lines = [line.strip() for line in text.splitlines()]
    tidy: list[str] = []
    for line in lines:
        if not line and (not tidy or not tidy[-1]):
            continue
        tidy.append(line)
    return "\n".join(tidy).strip()


def html_to_text(markup: str) -> tuple[str | None, str]:
    """Return ``(title, readable_text)`` for an HTML document."""
    parser = _ReadableTextParser()
    parser.feed(markup)
    parser.close()
    title = " ".join("".join(parser.title_parts).split()) or None
    main_text = _tidy("".join(parser.main_parts))
    text = main_text if len(main_text) >= _MIN_MAIN_CHARS else _tidy("".join(parser.all_parts))
    return title, text


# --- body decoding ----------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.IGNORECASE)


def parse_content_type(header: str | None) -> tuple[str, str | None]:
    """Return ``(media_type, charset)`` from a ``Content-Type`` header."""
    if not header:
        return "", None
    media_type, _, params = header.partition(";")
    charset = None
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip("\"'") or None
    return media_type.strip().lower(), charset


def _known_codec(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def decode_body(body: bytes, charset: str | None, *, is_html: bool = False) -> str:
    """Decode a body with its BOM, declared charset, ``<meta charset>``, or UTF-8."""
    for bom, encoding in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if body.startswith(bom):
            return body.decode(encoding, errors="replace")
    encoding = _known_codec(charset)
    if encoding is None and is_html:
        match = _META_CHARSET.search(body[:4096])
        encoding = _known_codec(match.group(1).decode("ascii", "ignore")) if match else None
    return body.decode(encoding or "utf-8", errors="replace")


__all__ = ["decode_body", "html_to_text", "parse_content_type"]
