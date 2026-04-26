"""Shared utilities for compatibility helpers."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from nanobot_channel_anon.domain import ForwardExpanded


def normalize_onebot_id(value: Any) -> str | None:
    """把 OneBot 标识规范化为非空字符串."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def parse_forward_expanded_item(raw_item: object) -> ForwardExpanded | None:
    """把 metadata 中的单个 forward_expanded 槽位解析成模型."""
    if raw_item is None:
        return None
    if isinstance(raw_item, ForwardExpanded):
        return raw_item
    if not isinstance(raw_item, dict):
        return None
    try:
        return ForwardExpanded.model_validate(raw_item)
    except ValidationError:
        return None


def parse_forward_expanded_slots(
    metadata: dict[str, object],
) -> list[ForwardExpanded | None]:
    """把 metadata 中的 forward_expanded 列表解析为模型槽位."""
    raw_items = metadata.get("forward_expanded")
    if not isinstance(raw_items, list):
        return []
    return [parse_forward_expanded_item(item) for item in raw_items]
