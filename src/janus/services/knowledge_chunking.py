"""Structure-aware text chunking for knowledge sources (JAN-242).

Text is first split into *blocks* that should stay together: Markdown
headings, paragraphs and tables for documents, quoted-reply blocks for mail,
and one block per message for chats. Blocks are then packed greedily, left to
right, into chunks of about ``TARGET_CHARS``. A chunk that continues its
predecessor because of the size limit starts with that predecessor's last
``OVERLAP_CHARS``, so a sentence cut at the boundary is still seen whole once.

Packing is deterministic from the start of the text, so appending messages to
a conversation leaves every earlier chunk byte-identical; the ingestion
service keeps those chunks and their embeddings.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

#: Target size of one chunk, in characters (~375 tokens at 4 characters/token).
TARGET_CHARS = 1500
#: Characters of the previous chunk repeated at the start of the next one.
OVERLAP_CHARS = 200
#: Rough characters-per-token ratio for ``token_estimate`` (no tokenizer needed).
CHARS_PER_TOKEN = 4

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    """One packed chunk of text."""

    text: str

    @property
    def content_hash(self) -> str:
        """SHA-256 of the chunk text; unchanged text keeps its embedding."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def token_estimate(self) -> int:
        """Approximate token count (characters / ``CHARS_PER_TOKEN``)."""
        return max(1, len(self.text) // CHARS_PER_TOKEN)


def document_blocks(text: str) -> list[str]:
    """Blocks of a Markdown document: headings, paragraphs, and whole tables."""
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("#"):
            flush()
            blocks.append(stripped)
        elif current and stripped.startswith("|") != current[-1].lstrip().startswith("|"):
            # A table starts or ends: keep table rows together, apart from prose.
            flush()
            current.append(line.rstrip())
        else:
            current.append(line.rstrip())
    flush()
    return [block for block in blocks if block]


def email_blocks(text: str) -> list[str]:
    """Blocks of a mail body: paragraphs, with each run of quoted (``>``) lines as one block."""
    blocks: list[str] = []
    current: list[str] = []
    quoted = False

    def flush() -> None:
        if current:
            blocks.append("\n".join(current).strip())
            current.clear()

    for line in text.splitlines():
        stripped = line.strip()
        is_quote = stripped.startswith(">")
        if not stripped:
            if not quoted:
                flush()
            continue
        if current and is_quote != quoted:
            flush()
        quoted = is_quote
        current.append(line.rstrip())
    flush()
    return [block for block in blocks if block]


def _split_long_block(block: str) -> list[str]:
    """Splits a block longer than ``TARGET_CHARS`` at sentences, then words, then characters."""
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(block):
        for word in sentence.split(" ") if len(sentence) > TARGET_CHARS else [sentence]:
            while len(word) > TARGET_CHARS:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(word[:TARGET_CHARS])
                word = word[TARGET_CHARS:]
            separator = " " if current else ""
            if len(current) + len(separator) + len(word) > TARGET_CHARS:
                pieces.append(current)
                current = word
            else:
                current = f"{current}{separator}{word}"
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


def _overlap_tail(text: str) -> str:
    """The last ``OVERLAP_CHARS`` of ``text``, starting at a word boundary."""
    if len(text) <= OVERLAP_CHARS:
        return text
    tail = text[-OVERLAP_CHARS:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail


def pack(blocks: list[str]) -> list[Chunk]:
    """Packs blocks into chunks of about ``TARGET_CHARS``.

    A heading always starts a new chunk. Only a chunk that continues its
    predecessor because of the size limit repeats that predecessor's last
    ``OVERLAP_CHARS``; a new section starts clean.
    """
    bodies: list[tuple[str, bool]] = []  # (text, continues the previous chunk)
    current = ""
    continues = False
    for block in blocks:
        units = _split_long_block(block) if len(block) > TARGET_CHARS else [block]
        for unit in units:
            starts_section = unit.startswith("#")
            # A heading always stays with the text below it, even if that overshoots the target.
            lone_heading = current.startswith("#") and "\n" not in current
            if current and not lone_heading and (starts_section or len(current) + 2 + len(unit) > TARGET_CHARS):
                bodies.append((current, continues))
                current, continues = "", not starts_section
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        bodies.append((current, continues))

    return [
        Chunk(text=f"{_overlap_tail(bodies[index - 1][0])}\n\n{body}" if continued else body)
        for index, (body, continued) in enumerate(bodies)
    ]
