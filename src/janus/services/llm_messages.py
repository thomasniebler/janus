"""Reading chat model answers (JAN-410)."""

from langchain_core.messages import BaseMessage


def message_text(message: object) -> str:
    """The answer text of a chat model message, or of raw content such as a test fake's string.

    Some providers (the OpenAI Responses API, Anthropic with thinking) answer
    with a list of content blocks: reasoning blocks, often with encrypted
    content, then the text. ``str()`` of that list would keep all of it;
    ``BaseMessage.text`` keeps only the text blocks.
    """
    if isinstance(message, BaseMessage):
        return message.text
    content = getattr(message, "content", message)
    return "" if content is None else str(content)
