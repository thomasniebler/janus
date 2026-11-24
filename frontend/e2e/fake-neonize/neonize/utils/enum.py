"""Media type lookup."""

from typing import Any


class MediaTypeToMMS:
    """Only text messages are scripted, so every other message is unsupported."""

    @staticmethod
    def from_message(message: Any) -> Any:
        """Raise like the real lookup does for a message without known media."""
        raise ValueError("the fake Neonize client scripts text messages only")
