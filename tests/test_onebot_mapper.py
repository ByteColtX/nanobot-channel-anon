"""Tests for the OneBot mapper adapter."""

from __future__ import annotations

import pytest

from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.domain import (
    Attachment,
    ChannelSendRequest,
    ForwardNode,
    ForwardRef,
)
from nanobot_channel_anon.onebot import OneBotRawEvent


def test_map_private_message_to_normalized_message() -> None:
    """Private OneBot messages should map into normalized private messages."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 9,
            "user_id": 123,
            "message": [
                {"type": "text", "data": {"text": "hello"}},
                {
                    "type": "image",
                    "data": {
                        "file": "a.png",
                        "url": "https://example.com/a.png",
                    },
                },
            ],
            "sender": {"user_id": 123, "nickname": "tester"},
            "time": 1776818315,
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.message_id == "9"
    assert message.conversation.kind == "private"
    assert message.conversation.id == "123"
    assert message.sender_id == "123"
    assert message.sender_name == "tester"
    assert message.content == "hello[image]"
    assert message.attachments == [
        Attachment(
            kind="image",
            url="https://example.com/a.png",
            name="a.png",
            metadata={
                "source": "message_segment",
                "onebot_segment_type": "image",
                "original_url": "https://example.com/a.png",
                "original_file": "a.png",
            },
        )
    ]
    assert message.metadata["onebot_message_type"] == "private"
    assert message.metadata["event_time"] == 1776818315


def test_map_group_message_extracts_mentions_reply_and_group_ref() -> None:
    """Group messages should expose conversation and routing metadata."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "10",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {"type": "at", "data": {"qq": "42"}},
                {"type": "text", "data": {"text": " hi there "}},
                {"type": "reply", "data": {"id": "99"}},
            ],
            "sender": {"user_id": "123", "nickname": "tester", "card": "群名片"},
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.conversation.kind == "group"
    assert message.conversation.id == "456"
    assert message.conversation.title == ""
    assert message.sender_name == "群名片"
    assert message.content == "hi there"
    assert message.mentioned_self is True
    assert message.reply_to_message_id == "99"
    assert message.metadata["group_id"] == "456"
    assert message.metadata["segment_types"] == ["at", "text", "reply"]


def test_map_group_message_preserves_render_segments_for_mentions() -> None:
    """映射器应保留提及相关 render_segments 顺序与展示信息."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "10a",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "hi "}},
                {"type": "at", "data": {"qq": "1001", "name": "群名片"}},
                {"type": "text", "data": {"text": " and "}},
                {"type": "at", "data": {"qq": "all"}},
                {"type": "text", "data": {"text": " done"}},
            ],
            "sender": {"user_id": "123", "nickname": "tester"},
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.metadata["render_segments"] == [
        {"type": "text", "text": "hi "},
        {
            "type": "mention",
            "user_id": "1001",
            "name": "群名片",
        },
        {"type": "text", "text": " and "},
        {"type": "mention_all"},
        {"type": "text", "text": " done"},
    ]


def test_map_group_message_preserves_self_mention_and_render_segments() -> None:
    """映射器应同时保留自提及触发语义与 render_segments 顺序."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "10b",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "hi "}},
                {"type": "at", "data": {"qq": "42"}},
                {"type": "text", "data": {"text": " there"}},
            ],
            "sender": {"user_id": "123", "nickname": "tester"},
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.mentioned_self is True
    assert message.metadata["render_segments"] == [
        {"type": "text", "text": "hi "},
        {"type": "mention", "user_id": "42", "name": ""},
        {"type": "text", "text": " there"},
    ]



def test_map_group_message_preserves_attachment_render_segments() -> None:
    """映射器应为附件段保留稳定 render_segments 占位顺序."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "10c",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "before"}},
                {
                    "type": "image",
                    "data": {
                        "file": "a.png",
                        "url": "https://example.com/a.png",
                    },
                },
                {"type": "text", "data": {"text": "after"}},
            ],
            "sender": {"user_id": "123", "nickname": "tester"},
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.metadata["render_segments"] == [
        {"type": "text", "text": "before"},
        {"type": "image"},
        {"type": "text", "text": "after"},
    ]



def test_map_notice_poke_to_domain_event_shape() -> None:
    """Poke notices should map into normalized poke messages."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "time": 1776818315,
            "user_id": "123",
            "group_id": "456",
            "target_id": "42",
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.message_type == "poke"
    assert message.conversation.kind == "group"
    assert message.conversation.id == "456"
    assert message.sender_id == "123"
    assert message.content == ""
    assert message.metadata["target_id"] == "42"
    assert message.message_id == "notice:poke:1776818315:456:123:42"


def test_map_group_message_preserves_forward_refs() -> None:
    """Forward segments should remain as compact content and metadata refs."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "11",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {"type": "text", "data": {"text": "before"}},
                {
                    "type": "forward",
                    "data": {
                        "id": "fw-1",
                        "summary": "转发摘要",
                        "content": [
                            {
                                "user_id": "1001",
                                "nickname": "Alice",
                                "content": "第一条",
                            },
                            {
                                "user_id": 1002,
                                "name": "Bob",
                                "content": [
                                    {"type": "text", "data": {"text": "第二条"}}
                                ],
                            },
                        ],
                    },
                },
                {"type": "text", "data": {"text": "after"}},
            ],
            "sender": {"user_id": "123", "nickname": "tester"},
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.content == "before[forward]after"
    assert message.metadata["segment_types"] == ["text", "forward", "text"]
    assert message.metadata["forwards"] == [
        ForwardRef(
            forward_id="fw-1",
            summary="转发摘要",
            nodes=[
                ForwardNode(sender_id="1001", sender_name="Alice", content="第一条"),
                ForwardNode(sender_id="1002", sender_name="Bob", content="第二条"),
            ],
        ).model_dump(exclude_none=True)
    ]


def test_map_group_message_preserves_wrapped_forward_nodes() -> None:
    """Wrapped OneBot node payloads should preserve embedded forward nodes."""
    mapper = OneBotMapper(self_id="42")
    raw = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": "12",
            "group_id": "456",
            "user_id": "123",
            "message": [
                {
                    "type": "forward",
                    "data": {
                        "id": "fw-2",
                        "summary": "包装节点",
                        "content": [
                            {
                                "type": "node",
                                "data": {
                                    "user_id": "1003",
                                    "nickname": "Carol",
                                    "content": [
                                        {"type": "text", "data": {"text": "第三条"}}
                                    ],
                                },
                            }
                        ],
                    },
                }
            ],
        }
    )

    message = mapper.map_inbound_event(raw)

    assert message is not None
    assert message.content == "[forward]"
    assert message.metadata["forwards"] == [
        ForwardRef(
            forward_id="fw-2",
            summary="包装节点",
            nodes=[
                ForwardNode(sender_id="1003", sender_name="Carol", content="第三条")
            ],
        ).model_dump(exclude_none=True)
    ]


def test_map_outbound_request_keeps_image_mixed_with_text() -> None:
    """图片可与文本共用同一个 OneBot 发送请求."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="caption",
        media=["https://example.com/a.png"],
        metadata={"reply_to_message_id": "99"},
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "reply", "data": {"id": "99"}},
                    {"type": "image", "data": {"file": "https://example.com/a.png"}},
                    {"type": "text", "data": {"text": "caption"}},
                ],
            },
            "echo": None,
        }
    ]


def test_map_outbound_request_sends_non_image_media_standalone() -> None:
    """语音等非图片媒体必须拆成独立 OneBot 发送请求."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="caption",
        media=["file:///tmp/voice.wav"],
        metadata={"reply_to_message_id": "99"},
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "reply", "data": {"id": "99"}},
                    {"type": "record", "data": {"file": "file:///tmp/voice.wav"}},
                ],
            },
            "echo": None,
        },
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [{"type": "text", "data": {"text": "caption"}}],
            },
            "echo": None,
        },
    ]


def test_map_outbound_request_parses_inline_cq_segments() -> None:
    """映射层应把受支持的内联 CQ 转成 OneBot 消息段."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content=(
            "[CQ:reply,id=88]hi [CQ:at,qq=123] "
            "[CQ:image,file=https://example.com/a.png] end"
        ),
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "reply", "data": {"id": "88"}},
                    {"type": "text", "data": {"text": "hi "}},
                    {"type": "at", "data": {"qq": "123"}},
                    {"type": "text", "data": {"text": " "}},
                    {
                        "type": "image",
                        "data": {"file": "https://example.com/a.png"},
                    },
                    {"type": "text", "data": {"text": " end"}},
                ],
            },
            "echo": None,
        }
    ]



def test_map_outbound_request_splits_inline_non_image_cq_media() -> None:
    """内联非图片 CQ 媒体应按 NapCat 约束拆成独立请求."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content=(
            "before[CQ:record,file=file:///tmp/voice.wav]"
            "after[CQ:image,file=https://example.com/a.png]"
        ),
        metadata={"reply_to_message_id": "99"},
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "reply", "data": {"id": "99"}},
                    {"type": "text", "data": {"text": "before"}},
                ],
            },
            "echo": None,
        },
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "record", "data": {"file": "file:///tmp/voice.wav"}},
                ],
            },
            "echo": None,
        },
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "text", "data": {"text": "after"}},
                    {
                        "type": "image",
                        "data": {"file": "https://example.com/a.png"},
                    },
                ],
            },
            "echo": None,
        },
    ]



def test_map_outbound_request_keeps_unsupported_inline_cq_as_text() -> None:
    """不支持的内联 CQ 应保留为普通文本."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="hello [CQ:unknown,x=1] world",
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "text", "data": {"text": "hello [CQ:unknown,x=1] world"}},
                ],
            },
            "echo": None,
        }
    ]



def test_map_outbound_request_keeps_malformed_inline_cq_as_text() -> None:
    """格式非法的受支持内联 CQ 也应保留为普通文本."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="hello [CQ:reply,id=bad] world",
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "text", "data": {"text": "hello [CQ:reply,id=bad] world"}},
                ],
            },
            "echo": None,
        }
    ]



def test_map_outbound_request_preserves_text_whitespace() -> None:
    """映射层不应擅自裁剪出站文本空白."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="  hello  ",
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [{"type": "text", "data": {"text": "  hello  "}}],
            },
            "echo": None,
        }
    ]



def test_map_outbound_request_metadata_reply_overrides_inline_reply() -> None:
    """显式 metadata reply 应覆盖内联 reply 头."""
    mapper = OneBotMapper(self_id="42")
    request = ChannelSendRequest(
        chat_id="group:456",
        content="[CQ:reply,id=88]hello",
        metadata={"reply_to_message_id": "99"},
    )

    actions = mapper.map_outbound_request(request)

    assert [action.model_dump(exclude_none=False) for action in actions] == [
        {
            "action": "send_group_msg",
            "params": {
                "group_id": 456,
                "message": [
                    {"type": "reply", "data": {"id": "99"}},
                    {"type": "text", "data": {"text": "hello"}},
                ],
            },
            "echo": None,
        }
    ]



def test_map_outbound_request_rejects_invalid_chat_id() -> None:
    """映射层应自行校验标准 chat_id 编码."""
    mapper = OneBotMapper(self_id="42")

    with pytest.raises(ValueError, match="chat_id"):
        mapper.map_outbound_request(ChannelSendRequest(chat_id="group:abc"))
