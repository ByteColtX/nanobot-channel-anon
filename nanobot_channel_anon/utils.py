"""Shared scalar utilities for compatibility helpers."""

from __future__ import annotations

from typing import Any


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
