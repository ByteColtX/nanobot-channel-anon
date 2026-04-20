"""Shared scalar utilities for the anon channel."""

from __future__ import annotations

from typing import Any


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


