"""Tests for the OneBot state adapter."""

from __future__ import annotations

import time

from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.domain import ConversationRef


def test_self_identity_roundtrip() -> None:
    """State adapter should store and return the current self identity."""
    adapter = OneBotStateAdapter()

    adapter.set_self_id("42")

    assert adapter.self_id == "42"


def test_profile_hydration_roundtrip() -> None:
    """State adapter should keep hydrated member profiles by conversation."""
    adapter = OneBotStateAdapter()
    conversation = ConversationRef(kind="group", id="456")

    adapter.set_member_profile(
        conversation,
        user_id="123",
        display_name="群名片",
        nickname="tester",
    )

    profile = adapter.get_member_profile(conversation, "123")

    assert profile is not None
    assert profile.display_name == "群名片"
    assert profile.nickname == "tester"


def test_self_profile_roundtrip_keeps_nickname() -> None:
    """State adapter should keep the hydrated bot nickname."""
    adapter = OneBotStateAdapter()

    adapter.set_self_profile(user_id="42", nickname="AnonBot")

    assert adapter.self_id == "42"
    assert adapter.self_nickname == "AnonBot"


def test_group_mute_state_roundtrip_and_expiry() -> None:
    """State adapter should track live group mute states and expire stale ones."""
    adapter = OneBotStateAdapter()

    adapter.set_group_muted_until("456", int(time.time()) + 60)
    adapter.set_group_muted_until("789", int(time.time()) - 1)

    assert adapter.is_group_muted("456") is True
    assert adapter.is_group_muted("789") is False
    assert adapter.muted_group_ids() == frozenset({"456"})
