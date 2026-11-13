"""Message text extraction. Claude can return structured content (thinking, text, tool use). Only text is shown."""
from __future__ import annotations

from typing import Any


def message_text(content: Any) -> str:
    """Plain text of a message's content.

    Input: a string, or a list of content blocks such as {"type": "text", "text": ...} and {"type": "thinking", ...}.
    Output: the concatenated text blocks only. Thinking, signatures and tool blocks are dropped.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)
