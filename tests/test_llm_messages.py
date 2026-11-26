"""Tests for reading chat model answers (JAN-410)."""

from langchain_core.messages import AIMessage

from janus.services.llm_messages import message_text


def test_plain_content_is_returned_as_is():
    """String content and raw strings pass through; missing content is empty."""
    assert message_text(AIMessage(content="Hello")) == "Hello"
    assert message_text("raw") == "raw"
    assert message_text(None) == ""


def test_only_text_blocks_of_list_content_are_kept():
    """Reasoning, thinking, and tool blocks are dropped; text blocks and plain-string parts are joined."""
    content = [
        {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "gAAAAABsecret"},
        {"type": "thinking", "thinking": "hmm"},
        {"type": "text", "text": "A", "annotations": []},
        "B",
        {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
    ]
    assert message_text(AIMessage(content=content)) == "AB"
