"""Gateway correlation markers in chat-completions messages.

Must stay in sync with gateway/server/src/latent/latent-ref.mjs.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterator, List, Optional

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
MARKER_PATTERN = re.compile(r'<latent_task id="([A-Za-z0-9][A-Za-z0-9_.-]{0,127})"\s*/>\n?')
TEXT_PART_TYPES = ("text", "input_text")


def is_task_id(value: str) -> bool:
    return bool(TASK_ID_PATTERN.match(value or ""))


def _texts(message: Any) -> Iterator[str]:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in TEXT_PART_TYPES:
                text = part.get("text")
                if isinstance(text, str):
                    yield text


def find_task_id(messages: List[Any]) -> Optional[str]:
    """Returns the Task id of the last marker in the conversation, or None.

    A steering message carries no marker, so the last marker still names the
    Task that the session is working on.
    """
    found = None
    for message in messages or []:
        for text in _texts(message):
            for match in MARKER_PATTERN.finditer(text):
                found = match.group(1)
    return found


def strip_markers(messages: List[Any]) -> List[Any]:
    """Returns a deep copy of messages with every marker removed."""
    cleaned = copy.deepcopy(messages or [])
    for message in cleaned:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = MARKER_PATTERN.sub("", content)
        elif isinstance(content, list):
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") in TEXT_PART_TYPES
                    and isinstance(part.get("text"), str)
                ):
                    part["text"] = MARKER_PATTERN.sub("", part["text"])
    return cleaned
