"""Shared scalar utilities for the anon channel."""

from __future__ import annotations

from typing import Any, Literal


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


def build_private_chat_id(user_id: str) -> str:
    """Build a normalized private chat ID."""
    return f"private:{user_id}"


def build_group_chat_id(group_id: str) -> str:
    """Build a normalized group chat ID."""
    return f"group:{group_id}"


def parse_chat_id(chat_id: str) -> tuple[Literal["group", "private"], int]:
    """Parse a normalized chat ID into target kind and numeric ID."""
    normalized_chat_id = chat_id.strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required")

    prefix, separator, raw_target_id = normalized_chat_id.partition(":")
    if not separator:
        raise ValueError("chat_id must use group:<id> or private:<id>")
    if prefix not in {"group", "private"}:
        raise ValueError(f"unsupported chat_id prefix: {prefix}")

    normalized_target_id = raw_target_id.strip()
    if not normalized_target_id:
        raise ValueError("chat_id target is required")

    try:
        target_id = int(normalized_target_id)
    except ValueError as exc:
        raise ValueError(
            f"chat_id target must be numeric: {normalized_target_id}"
        ) from exc

    if prefix == "group":
        return "group", target_id
    return "private", target_id


