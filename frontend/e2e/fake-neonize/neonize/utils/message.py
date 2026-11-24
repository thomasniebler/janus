"""Message text extraction."""

from typing import Any


def extract_text(message: Any) -> str:
    """Return the plain text of a scripted message."""
    return getattr(message, "conversation", "") or ""
