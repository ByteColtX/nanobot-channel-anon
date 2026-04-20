"""Tests for OneBot inbound normalization."""

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import normalize_inbound_event
from nanobot_channel_anon.onebot import OneBotRawEvent


def test_normalize_private_message_segments() -> None:
    """Private segmented messages should be normalized into a candidate."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
            "message_id": 9,
            "message": [
                {"type": "text", "data": {"text": "hello"}},
                {"type": "image", "data": {"url": "https://example.com/a.png"}},
            ],
            "sender": {"user_id": 123, "nickname": "tester", "card": ""},
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )

    assert candidate is not None
    assert candidate.event_kind == "private_message"
    assert candidate.sender_id == "123"
    assert candidate.chat_id == "private:123"
    assert candidate.content == "hello[image]"
    assert candidate.media == ["https://example.com/a.png"]
    assert candidate.metadata["sender_nickname"] == "tester"


def test_normalize_group_message_extracts_mentions_and_reply() -> None:
    """Group messages should expose mention and reply features for routing."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": 456,
            "user_id": 123,
            "message": [
                {"type": "at", "data": {"qq": "42"}},
                {"type": "text", "data": {"text": "  hi there  "}},
                {"type": "reply", "data": {"id": 99}},
            ],
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )

    assert candidate is not None
    assert candidate.event_kind == "group_message"
    assert candidate.chat_id == "group:456"
    assert candidate.content == "hi there"
    assert candidate.mentioned_self is True
    assert candidate.reply_to_message_id == "99"
    assert candidate.metadata["segment_types"] == ["at", "text", "reply"]


def test_normalize_message_truncates_content() -> None:
    """Normalized text should honor max_text_length."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": "123",
            "raw_message": "abcdef",
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=3),
        self_id="42",
    )

    assert candidate is not None
    assert candidate.content == "abc"


def test_normalize_notice_poke() -> None:
    """Poke notices should become poke candidates."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": "123",
            "group_id": "456",
            "target_id": "42",
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )

    assert candidate is not None
    assert candidate.event_kind == "poke"
    assert candidate.chat_id == "group:456"
    assert candidate.metadata["target_id"] == "42"


def test_normalize_forward_segment_collects_forward_refs() -> None:
    """Forward segments should keep refs and placeholder text."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "看看"}},
                {
                    "type": "forward",
                    "data": {
                        "id": "fwd-1",
                        "summary": "聊天记录",
                        "content": [
                            {
                                "type": "node",
                                "data": {
                                    "user_id": "7",
                                    "nickname": "Alice",
                                    "content": [
                                        {"type": "text", "data": {"text": "hello"}}
                                    ],
                                },
                            }
                        ],
                    },
                },
            ],
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )

    assert candidate is not None
    assert candidate.content == "看看[forward]"
    assert len(candidate.forward_refs) == 1
    assert candidate.forward_refs[0].forward_id == "fwd-1"
    assert candidate.metadata["forward_refs"][0]["summary"] == "聊天记录"


def test_normalize_unsupported_event_returns_none() -> None:
    """Meta events should be ignored by inbound normalization."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
        }
    )

    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )

    assert candidate is None
