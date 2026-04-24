"""Tests for CQMSG serialization."""

import base64
from pathlib import Path

from nanobot.agent.context import ContextBuilder

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

_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7ZxioAAAAASUVORK5CYII="
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


def test_serialize_private_chat_prefers_nickname_for_u_rows() -> None:
    """Private chats should prefer nickname over raw QQ IDs in U rows."""
    entries = [
        MessageEntry(
            message_id="1",
            chat_id="private:123",
            sender_id="123",
            sender_name="123",
            sender_nickname="Alice",
            is_from_self=False,
            content="hello",
        ),
        MessageEntry(
            message_id="2",
            chat_id="private:123",
            sender_id="42",
            sender_name="42",
            sender_nickname="anon-bot",
            is_from_self=True,
            content="reply",
        ),
    ]

    serialized = serialize_chat_entries("private:123", entries, self_id="42")

    assert serialized is not None
    assert "U|peer|123|Alice" in serialized.text
    assert "U|me|42|anon-bot|bot" in serialized.text



def test_serialize_group_chat_prefers_card_then_nickname_then_qq() -> None:
    """Group chats should prefer card, then nickname, then QQ ID in U rows."""
    entries = [
        MessageEntry(
            message_id="1",
            chat_id="group:456",
            sender_id="1001",
            sender_name="1001",
            sender_nickname="Alice",
            sender_card="AliceCard",
            is_from_self=False,
            content="a",
        ),
        MessageEntry(
            message_id="2",
            chat_id="group:456",
            sender_id="1002",
            sender_name="1002",
            sender_nickname="Bob",
            is_from_self=False,
            content="b",
        ),
        MessageEntry(
            message_id="3",
            chat_id="group:456",
            sender_id="1003",
            sender_name="1003",
            is_from_self=False,
            content="c",
        ),
    ]

    serialized = serialize_chat_entries("group:456", entries, self_id=None)

    assert serialized is not None
    assert "U|u0|1001|AliceCard" in serialized.text
    assert "U|u1|1002|Bob" in serialized.text
    assert "U|u2|1003|1003" in serialized.text



def test_serialize_buffer_chat_is_incremental_until_ack() -> None:
    """Unread CQMSG should exclude self entries while ack still advances past them."""
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
    assert first.message_ids == ["1"]
    assert "M|1|peer|first" in first.text
    assert "M|2|me|second" not in first.text
    assert buffer.get_unconsumed_llm_chat_entries("private:123")[0].message_id == "1"
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
    """Consumption should reject IDs that do not match unread non-self order."""
    buffer = Buffer(max_messages=10)
    for message_id, is_from_self in (("1", False), ("2", True), ("3", False)):
        buffer.add(
            MessageEntry(
                message_id=message_id,
                chat_id="private:123",
                sender_id="42" if is_from_self else "123",
                sender_name="bot" if is_from_self else "peer",
                is_from_self=is_from_self,
                content=message_id,
            )
        )

    assert buffer.mark_chat_entries_consumed("private:123", ["3"]) is False
    unread_ids = [
        entry.message_id
        for entry in buffer.get_unconsumed_chat_entries("private:123")
    ]
    assert unread_ids == ["1", "2", "3"]
    unread_llm_ids = [
        entry.message_id
        for entry in buffer.get_unconsumed_llm_chat_entries("private:123")
    ]
    assert unread_llm_ids == ["1", "3"]


def test_serialize_buffer_chat_includes_reply_target_context(
) -> None:
    """Reply targets should be included in CQMSG text but excluded from ack IDs."""
    buffer = Buffer(max_messages=10)
    buffer.add(
        MessageEntry(
            message_id="1",
            chat_id="group:456",
            sender_id="1001",
            sender_name="Alice",
            is_from_self=False,
            content="original",
        )
    )
    buffer.mark_chat_entries_consumed("group:456", ["1"])
    buffer.add(
        MessageEntry(
            message_id="2",
            chat_id="group:456",
            sender_id="1002",
            sender_name="Bob",
            is_from_self=False,
            content="reply",
            reply_to_message_id="1",
        )
    )

    serialized = serialize_buffer_chat(buffer, "group:456", self_id="42")

    assert serialized is not None
    assert serialized.message_ids == ["2"]
    assert serialized.count == 1
    assert "M|1|u1|original" in serialized.text
    assert "M|2|u2|>m:1 reply" in serialized.text


def test_serialize_buffer_chat_includes_self_reply_target_context(
) -> None:
    """Replies to self messages should include the quoted bot message in CQMSG text."""
    buffer = Buffer(max_messages=10)
    buffer.add(
        MessageEntry(
            message_id="1",
            chat_id="group:456",
            sender_id="42",
            sender_name="bot",
            is_from_self=True,
            content="hello",
        )
    )
    buffer.add(
        MessageEntry(
            message_id="2",
            chat_id="group:456",
            sender_id="1002",
            sender_name="Bob",
            is_from_self=False,
            content="reply",
            reply_to_message_id="1",
        )
    )

    serialized = serialize_buffer_chat(buffer, "group:456", self_id="42")

    assert serialized is not None
    assert serialized.message_ids == ["2"]
    assert serialized.count == 1
    assert "M|1|u0|hello" in serialized.text
    assert "M|2|u1|>m:1 reply" in serialized.text



def test_serialize_buffer_chat_does_not_restore_evicted_reply_target() -> None:
    """Evicted reply targets should remain unavailable in CQMSG context."""
    buffer = Buffer(max_messages=2)
    buffer.add(
        MessageEntry(
            message_id="1",
            chat_id="group:456",
            sender_id="1001",
            sender_name="Alice",
            is_from_self=False,
            content="original",
        )
    )
    buffer.add(
        MessageEntry(
            message_id="2",
            chat_id="group:456",
            sender_id="1003",
            sender_name="Carol",
            is_from_self=False,
            content="filler",
        )
    )
    buffer.mark_chat_entries_consumed("group:456", ["1", "2"])
    buffer.add(
        MessageEntry(
            message_id="3",
            chat_id="group:456",
            sender_id="1002",
            sender_name="Bob",
            is_from_self=False,
            content="reply",
            reply_to_message_id="1",
        )
    )

    serialized = serialize_buffer_chat(buffer, "group:456", self_id="42")

    assert serialized is not None
    assert serialized.message_ids == ["3"]
    assert serialized.count == 1
    assert "M|1|" not in serialized.text
    assert "M|3|u1|>m:1 reply" in serialized.text



def test_serialize_transcription_text_stays_in_message_body() -> None:
    """Voice transcription text should serialize as plain M-body text."""
    serialized = serialize_chat_entries(
        "private:123",
        [
            MessageEntry(
                message_id="9002001",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="[transcription: 今晚八点开会]",
            )
        ],
        self_id="42",
    )

    assert serialized is not None
    assert "I|" not in serialized.text
    assert "M|9002001|peer|[transcription: 今晚八点开会]" in serialized.text



def test_serialize_image_and_transcription_without_audio_rows() -> None:
    """Mixed image and transcription content should only emit image rows."""
    serialized = serialize_chat_entries(
        "private:123",
        [
            MessageEntry(
                message_id="9002002",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="看下这个 [image] [transcription: 收到语音]",
                media=["file:///tmp/sample-image.png"],
            )
        ],
        self_id="42",
    )

    assert serialized is not None
    assert serialized.text.count("I|") == 1
    assert "I|i0|sample-image.png" in serialized.text
    assert (
        "M|9002002|peer|看下这个 [i0] [transcription: 收到语音]"
        in serialized.text
    )



def test_context_builder_attaches_all_unread_window_images(
    tmp_path: Path,
) -> None:
    """Unread-window history and trigger images should all become multimodal blocks."""
    history_image = tmp_path / "history.png"
    history_image.write_bytes(_MINIMAL_PNG)
    trigger_image = tmp_path / "trigger.png"
    trigger_image.write_bytes(_MINIMAL_PNG)

    serialized = serialize_chat_entries(
        "private:123",
        [
            MessageEntry(
                message_id="9003001",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="历史图 [image]",
                media=[str(history_image)],
            ),
            MessageEntry(
                message_id="9003002",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="当前图 [image]",
                media=[str(trigger_image)],
            ),
        ],
        self_id="42",
    )
    assert serialized is not None
    assert serialized.media == [str(history_image), str(trigger_image)]

    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[],
        current_message=serialized.text,
        media=serialized.media,
        channel="anon",
        chat_id="private:123",
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, list)
    image_blocks = [
        item
        for item in user_content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    text_blocks = [
        item
        for item in user_content
        if isinstance(item, dict) and item.get("type") == "text"
    ]

    assert [item["_meta"]["path"] for item in image_blocks] == [
        str(history_image),
        str(trigger_image),
    ]
    assert "I|i0|history.png" in text_blocks[-1]["text"]
    assert "I|i1|trigger.png" in text_blocks[-1]["text"]



def test_context_builder_attaches_history_only_images_from_unread_window(
    tmp_path: Path,
) -> None:
    """History-only unread-window images should still become multimodal blocks."""
    history_image = tmp_path / "history.png"
    history_image.write_bytes(_MINIMAL_PNG)

    serialized = serialize_chat_entries(
        "private:123",
        [
            MessageEntry(
                message_id="9003003",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="只有历史图 [image]",
                media=[str(history_image)],
            )
        ],
        self_id="42",
    )
    assert serialized is not None
    assert serialized.media == [str(history_image)]

    builder = ContextBuilder(tmp_path)
    messages = builder.build_messages(
        history=[],
        current_message=serialized.text,
        media=serialized.media,
        channel="anon",
        chat_id="private:123",
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, list)
    image_blocks = [
        item
        for item in user_content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    assert [item["_meta"]["path"] for item in image_blocks] == [str(history_image)]



def test_serialize_chat_entries_aggregates_media_in_order_and_dedupes() -> None:
    """Serialized media should follow unread order and dedupe identical refs."""
    serialized = serialize_chat_entries(
        "private:123",
        [
            MessageEntry(
                message_id="9003004",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="[image]",
                media=["/tmp/a.png", "/tmp/b.png"],
            ),
            MessageEntry(
                message_id="9003005",
                chat_id="private:123",
                sender_id="123",
                sender_name="peer",
                is_from_self=False,
                content="[image][image]",
                media=["/tmp/b.png", "/tmp/c.png"],
            ),
        ],
        self_id="42",
    )

    assert serialized is not None
    assert serialized.media == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
