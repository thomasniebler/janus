"""Reading chat model answers (JAN-410)."""

#: Block types that carry answer text; reasoning, thinking, and tool blocks are left out.
_TEXT_TYPES = ("text", "output_text")


def message_text(message: object) -> str:
    """The answer text of a chat model message (or of raw content), without non-text blocks.

    Some providers (the OpenAI Responses API, Anthropic with thinking) answer
    with a list of content blocks: reasoning blocks, often with encrypted
    content, then the text. ``str()`` of that list would keep all of it.
    """
    content = getattr(message, "content", message)
    if not isinstance(content, list):
        return "" if content is None else str(content)
    texts = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") in _TEXT_TYPES:
            texts.append(str(part.get("text") or ""))
    return "".join(texts)
