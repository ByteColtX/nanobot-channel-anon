"""Tests for CQMSG serialization."""

from nanobot_channel_anon.buffer import (
    Buffer,
    ForwardEntry,
    ForwardNodeEntry,
    MessageEntry,
)
from nanobot_channel_anon.serializer import (
    serialize_buffer_chat,
    serialize_chat_entries,
)


def test_serialize_private_chat_uses_me_peer_and_forward_rows() -> None:
    """Private chats should use me/peer aliases and emit F/N rows."""
    entries = [
        MessageEntry(
            message_id="9001001",
            chat_id="private:100000005",
            sender_id="100000005",
            sender_name="示例联系人",
            is_from_self=False,
            content="请看下这张图 [image]",
            media=["https://example.com/assets/sample-image.png"],
        ),
        MessageEntry(
            message_id="9001002",
            chat_id="private:100000005",
            sender_id="100000003",
            sender_name="示例机器人",
            is_from_self=True,
            content="已收到",
            reply_to_message_id="9001001",
        ),
        MessageEntry(
            message_id="9001003",
            chat_id="private:100000005",
            sender_id="100000005",
            sender_name="示例联系人",
            is_from_self=False,
            content="[forward]",
            expanded_forwards=[
                ForwardEntry(
                    forward_id="fwd-1",
                    summary="群聊转发内容示例",
                    nodes=[
                        ForwardNodeEntry(
                            message_id="inner-0",
                            sender_id="100000001",
                            sender_name="示例成员甲",
                            source_chat_id="g:123456789",
                            content="今天先同步一下进度",
                        ),
                        ForwardNodeEntry(
                            message_id="inner-1",
                            sender_id="100000004",
                            sender_name="示例成员丙",
                            source_chat_id="g:123456789",
                            content="我这边继续跟进",
                            reply_to_message_id="inner-0",
                        ),
                    ],
                )
            ],
        ),
    ]

    serialized = serialize_chat_entries(
        "private:100000005",
        entries,
        self_id="100000003",
    )

    assert serialized is not None
    assert serialized.message_ids == ["9001001", "9001002", "9001003"]
    assert serialized.count == 3
    assert serialized.text == "\n".join(
        [
            "<CQMSG/1 bot:me n:3>",
            "U|me|100000003|示例机器人|bot",
            "U|peer|100000005|示例联系人",
            "U|u0|100000001|示例成员甲",
            "U|u1|100000004|示例成员丙",
            "I|i0|sample-image.png",
            "M|9001001|peer|请看下这张图 [i0]",
            "M|9001002|me|>m:9001001 已收到",
            "M|9001003|peer|[F:f0]",
            "F|f0|2|群聊转发内容示例",
            "N|f0.0|u0|g:123456789|今天先同步一下进度",
            "N|f0.1|u1|g:123456789|>n:f0.0 我这边继续跟进",
            "</CQMSG/1>",
        ]
    )


def test_serialize_group_chat_uses_un_and_unresolved_forward() -> None:
    """Group chats should use uN aliases and keep unresolved forwards."""
    entries = [
        MessageEntry(
            message_id="1489689854",
            chat_id="group:123456789",
            sender_id="100000002",
            sender_name="示例成员乙",
            is_from_self=False,
            content="[image]",
            media=["/tmp/sample-image.png"],
        ),
        MessageEntry(
            message_id="520815151",
            chat_id="group:123456789",
            sender_id="100000001",
            sender_name="示例成员甲",
            is_from_self=False,
            content="这张图已收到",
            reply_to_message_id="1489689854",
        ),
        MessageEntry(
            message_id="200000001",
            chat_id="group:123456789",
            sender_id="100000004",
            sender_name="示例成员丙",
            is_from_self=False,
            content="[forward]",
            expanded_forwards=[
                ForwardEntry(
                    forward_id="fwd-2",
                    summary="待补全",
                    unresolved=True,
                )
            ],
        ),
        MessageEntry(
            message_id="200000002",
            chat_id="group:123456789",
            sender_id="100000003",
            sender_name="示例机器人",
            is_from_self=True,
            content="收到",
        ),
    ]

    serialized = serialize_chat_entries(
        "group:123456789",
        entries,
        self_id="100000003",
    )

    assert serialized is not None
    assert serialized.text == "\n".join(
        [
            "<CQMSG/1 g:123456789 bot:u0 n:4>",
            "U|u0|100000003|示例机器人|bot",
            "U|u1|100000002|示例成员乙",
            "U|u2|100000001|示例成员甲",
            "U|u3|100000004|示例成员丙",
            "I|i0|sample-image.png",
            "M|1489689854|u1|[i0]",
            "M|520815151|u2|>m:1489689854 这张图已收到",
            "M|200000001|u3|[F:f0]",
            "F|f0|0|待补全",
            "M|200000002|u0|收到",
            "</CQMSG/1>",
        ]
    )


def test_serialize_buffer_chat_is_incremental_until_ack() -> None:
    """Unread entries should repeat until acknowledged, then only new ones remain."""
    buffer = Buffer(max_messages=10)
    buffer.add(
        MessageEntry(
            message_id="1",
            chat_id="private:123",
            sender_id="123",
            sender_name="peer",
            is_from_self=False,
            content="first",
        )
    )
    buffer.add(
        MessageEntry(
            message_id="2",
            chat_id="private:123",
            sender_id="42",
            sender_name="bot",
            is_from_self=True,
            content="second",
        )
    )

    first = serialize_buffer_chat(buffer, "private:123", self_id="42")
    second = serialize_buffer_chat(buffer, "private:123", self_id="42")

    assert first is not None
    assert second is not None
    assert first.text == second.text
    assert buffer.mark_chat_entries_consumed("private:123", first.message_ids) is True
    assert serialize_buffer_chat(buffer, "private:123", self_id="42") is None

    buffer.add(
        MessageEntry(
            message_id="3",
            chat_id="private:123",
            sender_id="123",
            sender_name="peer",
            is_from_self=False,
            content="third",
        )
    )
    third = serialize_buffer_chat(buffer, "private:123", self_id="42")
    assert third is not None
    assert third.message_ids == ["3"]
    assert "M|3|peer|third" in third.text


def test_mark_chat_entries_consumed_requires_unread_prefix() -> None:
    """Consumption should reject message IDs that do not match the unread prefix."""
    buffer = Buffer(max_messages=10)
    for message_id in ("1", "2", "3"):
        buffer.add(
            MessageEntry(
                message_id=message_id,
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content=message_id,
            )
        )

    assert buffer.mark_chat_entries_consumed("private:123", ["2"]) is False
    unread_ids = [
        entry.message_id
        for entry in buffer.get_unconsumed_chat_entries("private:123")
    ]
    assert unread_ids == ["1", "2", "3"]
