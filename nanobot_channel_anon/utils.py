"""Shared utilities for the anon channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nanobot_channel_anon.buffer import ForwardEntry, ForwardNodeEntry
from nanobot_channel_anon.onebot import OneBotMessageSegment

_MEDIA_PLACEHOLDERS = {
    "image": "[image]",
    "video": "[video]",
    "file": "[file]",
    "record": "[voice]",
}


@dataclass(slots=True)
class ParsedSegments:
    """Normalized message-segment content used by forward expansion."""

    text: str = ""
    media: list[str] = field(default_factory=list)
    reply_to_message_id: str | None = None
    segment_types: list[str] = field(default_factory=list)


def normalize_onebot_id(value: Any) -> str | None:
    """Normalize OneBot IDs to non-empty strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def string_value(value: Any) -> str | None:
    """Normalize scalar values to trimmed strings."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def parse_message_segments(message: Any) -> ParsedSegments:
    """Parse OneBot message content into text/media/reply metadata."""
    text_parts: list[str] = []
    media: list[str] = []
    reply_to_message_id: str | None = None
    segment_types: list[str] = []

    for segment in _segments_from_message(message):
        segment_types.append(segment.type)
        data = segment.data

        if segment.type == "text":
            text_parts.append(string_value(data.get("text")) or "")
            continue

        if segment.type == "reply":
            if reply_to_message_id is None:
                reply_to_message_id = normalize_onebot_id(
                    data.get("id") or data.get("message_id")
                )
            continue

        if segment.type == "forward":
            text_parts.append("[forward]")
            continue

        placeholder = _MEDIA_PLACEHOLDERS.get(segment.type)
        if placeholder is None:
            continue

        media_ref = _first_media_ref(data)
        if media_ref is not None:
            media.append(media_ref)
        text_parts.append(placeholder)

    return ParsedSegments(
        text="".join(text_parts).strip(),
        media=media,
        reply_to_message_id=reply_to_message_id,
        segment_types=segment_types,
    )


def extract_forward_nodes(payload: Any) -> list[dict[str, Any]]:
    """Extract forward nodes from a OneBot get_forward_msg response payload."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("messages", "message", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def build_forward_entry(
    *,
    forward_id: str | None,
    summary: str | None,
    raw_nodes: list[dict[str, Any]],
    unresolved: bool = False,
) -> ForwardEntry:
    """Normalize a forward container and its nodes."""
    return ForwardEntry(
        forward_id=forward_id,
        summary=summary,
        nodes=[_build_forward_node(node) for node in raw_nodes],
        unresolved=unresolved,
    )


def _build_forward_node(node: dict[str, Any]) -> ForwardNodeEntry:
    raw_data = node.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else node

    sender = data.get("sender")
    sender_id = normalize_onebot_id(
        data.get("user_id") or data.get("uin") or _dict_get(sender, "user_id")
    )
    sender_name = (
        string_value(data.get("nickname"))
        or string_value(data.get("name"))
        or string_value(_dict_get(sender, "nickname"))
        or string_value(_dict_get(sender, "card"))
        or sender_id
        or ""
    )
    source_chat_id = _source_chat_id(data)

    content_source = data.get("content")
    if content_source is None:
        content_source = data.get("message")

    parsed = parse_message_segments(content_source)
    content = parsed.text or string_value(data.get("raw_message")) or ""

    return ForwardNodeEntry(
        sender_id=sender_id,
        sender_name=sender_name,
        source_chat_id=source_chat_id,
        content=content,
        media=parsed.media,
        reply_to_message_id=parsed.reply_to_message_id,
        segment_types=parsed.segment_types,
    )


def _segments_from_message(message: Any) -> list[OneBotMessageSegment]:
    if isinstance(message, str):
        return [OneBotMessageSegment(type="text", data={"text": message})]
    if not isinstance(message, list):
        return []

    segments: list[OneBotMessageSegment] = []
    for item in message:
        if isinstance(item, OneBotMessageSegment):
            segments.append(item)
            continue
        if isinstance(item, dict):
            segments.append(OneBotMessageSegment.model_validate(item))
    return segments


def _first_media_ref(data: dict[str, Any]) -> str | None:
    for key in ("url", "file", "path", "file_id", "name"):
        value = string_value(data.get(key))
        if value is not None:
            return value
    return None


def _source_chat_id(data: dict[str, Any]) -> str | None:
    group_id = normalize_onebot_id(data.get("group_id"))
    if group_id is not None:
        return f"group:{group_id}"

    source = string_value(data.get("source"))
    if source is not None:
        return source

    return None


def _dict_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None
