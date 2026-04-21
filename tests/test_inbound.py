"""Tests for OneBot inbound normalization."""

import asyncio

import pytest

from nanobot_channel_anon.buffer import Buffer, MessageEntry
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import (
    cache_inbound_candidate,
    normalize_inbound_event,
    process_inbound_candidate,
)
from nanobot_channel_anon.onebot import OneBotRawEvent
from nanobot_channel_anon.utils import (
    build_group_chat_id,
    build_private_chat_id,
    normalize_onebot_id,
    parse_chat_id,
)


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


def test_process_inbound_candidate_marks_reply_to_self_and_expands_forward() -> None:
    """Inbound processing should mark replies and expand forwards."""
    reply_raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": "456",
            "user_id": "123",
            "message_id": "9100",
            "message": [
                {"type": "reply", "data": {"id": "9013"}},
                {"type": "text", "data": {"text": "收到"}},
            ],
        }
    )
    reply_candidate = normalize_inbound_event(
        reply_raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )
    assert reply_candidate is not None

    buffer = Buffer(max_messages=10)
    buffer.add(
        MessageEntry(
            message_id="9013",
            chat_id="group:456",
            sender_id="42",
            sender_name="42",
            is_from_self=True,
            content="hello",
        )
    )

    async def _resolve_empty_forward(_forward_id: str) -> object:
        return {"messages": []}

    processed_reply = asyncio.run(
        process_inbound_candidate(
            reply_candidate,
            buffer=buffer,
            forward_resolver=_resolve_empty_forward,
        )
    )

    assert processed_reply.candidate.reply_target_from_self is True
    assert processed_reply.candidate.metadata["reply_target_from_self"] is True



def test_process_inbound_candidate_expands_forward_and_cache_writes_message() -> None:
    """Inbound processing should expand forwards and let cache write the entry."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": "700",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "看看"}},
                {"type": "forward", "data": {"id": "fwd-2", "summary": "聊天记录"}},
            ],
        }
    )
    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )
    assert candidate is not None

    buffer = Buffer(max_messages=10)

    async def _resolve_forward(_forward_id: str) -> object:
        return {
            "messages": [
                {
                    "data": {
                        "user_id": "8",
                        "nickname": "Bob",
                        "content": [{"type": "text", "data": {"text": "forwarded"}}],
                    }
                }
            ]
        }

    processed = asyncio.run(
        process_inbound_candidate(
            candidate,
            buffer=buffer,
            forward_resolver=_resolve_forward,
        )
    )

    assert (
        processed.candidate.metadata["expanded_forwards"][0]["nodes"][0]["content"]
        == "forwarded"
    )
    cache_inbound_candidate(
        processed.candidate,
        buffer=buffer,
        expanded_forwards=processed.expanded_forwards,
    )
    buffered = buffer.get("private:123", "700")
    assert buffered is not None
    assert buffered.expanded_forwards[0].nodes[0].content == "forwarded"
    assert buffered.expanded_forwards[0].nodes[0].message_id is None


def test_shared_id_and_chat_id_helpers() -> None:
    """Shared helpers should own ID normalization and chat_id codec rules."""
    assert normalize_onebot_id(True) is None
    assert build_private_chat_id("123") == "private:123"
    assert build_group_chat_id("456") == "group:456"
    assert parse_chat_id("private:1") == ("private", 1)

    for chat_id in ("", "group:", "foo:1", "private:abc"):
        with pytest.raises(ValueError):
            parse_chat_id(chat_id)



def test_process_inbound_candidate_keeps_forward_node_message_id() -> None:
    """Forward nodes should preserve their own message_id when available."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": "702",
            "user_id": "123",
            "message": [
                {"type": "forward", "data": {"id": "fwd-4"}},
            ],
        }
    )
    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )
    assert candidate is not None

    buffer = Buffer(max_messages=10)

    async def _resolve_forward(_forward_id: str) -> object:
        return {
            "messages": [
                {
                    "data": {
                        "message_id": "inner-1",
                        "user_id": "8",
                        "nickname": "Bob",
                        "content": [{"type": "text", "data": {"text": "forwarded"}}],
                    }
                }
            ]
        }

    processed = asyncio.run(
        process_inbound_candidate(
            candidate,
            buffer=buffer,
            forward_resolver=_resolve_forward,
        )
    )

    assert processed.expanded_forwards[0].nodes[0].message_id == "inner-1"



def test_process_inbound_candidate_leaves_forward_source_unknown() -> None:
    """Forward nodes should keep source_chat_id unset when source is unreliable."""
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": "701",
            "user_id": "123",
            "message": [
                {"type": "forward", "data": {"id": "fwd-3"}},
            ],
        }
    )
    candidate = normalize_inbound_event(
        raw,
        config=AnonConfig(max_text_length=200),
        self_id="42",
    )
    assert candidate is not None

    buffer = Buffer(max_messages=10)

    async def _resolve_forward(_forward_id: str) -> object:
        return {
            "messages": [
                {
                    "data": {
                        "group_id": "456",
                        "user_id": "8",
                        "nickname": "Bob",
                        "content": [{"type": "text", "data": {"text": "forwarded"}}],
                    }
                }
            ]
        }

    processed = asyncio.run(
        process_inbound_candidate(
            candidate,
            buffer=buffer,
            forward_resolver=_resolve_forward,
        )
    )

    assert processed.expanded_forwards[0].nodes[0].source_chat_id is None



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
