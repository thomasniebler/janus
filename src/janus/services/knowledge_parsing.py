"""Document parsing for knowledge sources (JAN-242).

Attachments and uploads are converted to Markdown with ``markitdown``; only
documents (PDF, Word, Excel, PowerPoint, text, CSV, HTML, mail) up to
``MAX_DOCUMENT_BYTES`` are accepted. Everything else is skipped with a reason
instead of being parsed.

``markitdown`` is the optional ``documents`` extra: it needs onnxruntime, which
has no wheels for older hosts such as the glibc 2.17 Uberspace host. Without it,
plain-text formats are still read as-is and every other format is rejected by
``check_document`` (so it is skipped, not staged) with a reason naming the extra.
"""

from __future__ import annotations

import importlib.util
import io
import mimetypes
from dataclasses import dataclass
from pathlib import PurePath

#: Largest attachment or upload that is parsed (10 MB).
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

#: Supported document extensions and their canonical MIME types.
DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".eml": "message/rfc822",
}
_EXTENSIONS_BY_MIME = {mime: ext for ext, mime in DOCUMENT_TYPES.items()}
#: Formats that are read as text when ``markitdown`` is not installed.
_PLAIN_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv"})


class DocumentParseError(Exception):
    """Raised when a supported document cannot be converted to text."""


@dataclass(frozen=True)
class DocumentCheck:
    """Whether a file should be parsed, with its extension and MIME type."""

    accepted: bool
    extension: str | None
    mime_type: str | None
    reason: str | None = None


def check_document(filename: str | None, mime_type: str | None, size: int) -> DocumentCheck:
    """Decides whether a file is a supported document within the size limit."""
    extension = PurePath(filename or "").suffix.lower() or None
    if extension not in DOCUMENT_TYPES and mime_type:
        extension = _EXTENSIONS_BY_MIME.get(mime_type.split(";")[0].strip().lower())
    if extension not in DOCUMENT_TYPES:
        guessed = mimetypes.guess_type(filename or "")[0]
        detail = mime_type or guessed or "unknown type"
        return DocumentCheck(False, None, mime_type, f"unsupported file type ({detail})")
    canonical = DOCUMENT_TYPES[extension]
    if size > MAX_DOCUMENT_BYTES:
        return DocumentCheck(False, extension, canonical, f"larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
    if size == 0:
        return DocumentCheck(False, extension, canonical, "empty file")
    if extension not in _PLAIN_TEXT_EXTENSIONS and not markitdown_available():
        return DocumentCheck(False, extension, canonical, f"{extension} files need the optional 'documents' extra")
    return DocumentCheck(True, extension, canonical)


def markitdown_available() -> bool:
    """Whether the optional ``documents`` extra (markitdown) is installed."""
    return importlib.util.find_spec("markitdown") is not None


def parse_document(data: bytes, extension: str) -> str:
    """Converts one supported document to Markdown text."""
    # Imported lazily: markitdown pulls in onnxruntime/pandas, which only the
    # ingestion worker and the upload endpoint need.
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        if extension not in _PLAIN_TEXT_EXTENSIONS:
            raise DocumentParseError(
                f"parsing {extension} files needs the optional 'documents' extra (markitdown), which is not installed"
            ) from exc
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            raise DocumentParseError("no text could be extracted") from None
        return text

    try:
        result = MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), file_extension=extension)
    except Exception as exc:  # markitdown raises a variety of parser-specific errors
        raise DocumentParseError(f"{type(exc).__name__}: {exc}") from exc
    text = (result.text_content or "").strip()
    if not text:
        raise DocumentParseError("no text could be extracted")
    return text
