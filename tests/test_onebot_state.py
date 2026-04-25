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
        card="群名片",
        nickname="tester",
    )

    profile = adapter.get_member_profile(conversation, "123")

    assert profile is not None
    assert profile.card == "群名片"
    assert profile.nickname == "tester"


def test_self_profile_roundtrip_keeps_nickname() -> None:
    """State adapter should keep the hydrated bot nickname."""
    adapter = OneBotStateAdapter()

    adapter.set_self_profile(user_id="42", nickname="AnonBot")

    assert adapter.self_id == "42"
    assert adapter.self_nickname == "AnonBot"


def test_preferred_name_follows_conversation_fallback_rules() -> None:
    """State adapter should expose card/nickname fallbacks by conversation type."""
    adapter = OneBotStateAdapter()
    group_conversation = ConversationRef(kind="group", id="456")
    private_conversation = ConversationRef(kind="private", id="123")

    adapter.set_member_profile(
        group_conversation,
        user_id="1001",
        card="群名片",
        nickname="昵称",
    )
    adapter.set_member_profile(
        group_conversation,
        user_id="1002",
        nickname="昵称",
    )
    adapter.set_member_profile(
        private_conversation,
        user_id="2001",
        card="不会使用",
        nickname="私聊昵称",
    )

    assert adapter.preferred_name(group_conversation, "1001") == "群名片"
    assert adapter.preferred_name(group_conversation, "1002") == "昵称"
    assert adapter.preferred_name(private_conversation, "2001") == "私聊昵称"
    assert adapter.preferred_name(private_conversation, "9999") is None


def test_group_mute_state_roundtrip_and_expiry() -> None:
    """State adapter should track live group mute states and expire stale ones."""
    adapter = OneBotStateAdapter()

    adapter.set_group_muted_until("456", int(time.time()) + 60)
    adapter.set_group_muted_until("789", int(time.time()) - 1)

    assert adapter.is_group_muted("456") is True
    assert adapter.is_group_muted("789") is False
    assert adapter.muted_group_ids() == frozenset({"456"})
