"""Tests for deterministic context presentation."""

from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import (
    Attachment,
    ConversationRef,
    ForwardExpanded,
    ForwardNode,
    NormalizedMessage,
)
from nanobot_channel_anon.presenter import ContextPresenter


def _conversation() -> ConversationRef:
    return ConversationRef(kind="group", id="456", title="测试群")


def _message(
    message_id: str,
    *,
    sender_id: str,
    sender_name: str,
    content: str,
    from_self: bool = False,
    attachments: list[Attachment] | None = None,
    reply_to_message_id: str | None = None,
    message_type: str = "message",
    metadata: dict[str, object] | None = None,
) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=message_id,
        conversation=_conversation(),
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        from_self=from_self,
        attachments=[] if attachments is None else list(attachments),
        reply_to_message_id=reply_to_message_id,
        message_type=message_type,
        metadata={} if metadata is None else dict(metadata),
    )


def test_present_recent_window_renders_ctx1_text() -> None:
    """Presenter should render the current window as CTX/1."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(_message("m1", sender_id="123", sender_name="Alice", content="你好"))
    store.append(
        _message(
            "m2",
            sender_id="bot",
            sender_name="Bot",
            content="收到",
            from_self=True,
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert rendered.text == "\n".join(
        [
            "<CTX/1 g:456 bot:u0 n:1>",
            "U|u0|bot|bot|bot",
            "U|u1|123|Alice",
            "M|m1|u1|你好",
            "</CTX/1>",
        ]
    )
    assert rendered.metadata["message_ids"] == ["m1"]
    assert rendered.metadata["count"] == 1


def test_present_recent_window_includes_reply_target_context() -> None:
    """Presenter should include quoted reply target outside unread IDs."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(_message("m1", sender_id="123", sender_name="Alice", content="原消息"))
    store.mark_consumed(_conversation(), "m1")
    store.append(
        _message(
            "m2",
            sender_id="456",
            sender_name="Bob",
            content="回复一下",
            reply_to_message_id="m1",
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "M|m1|u1|原消息" in rendered.text
    assert "M|m2|u2|^m1 回复一下" in rendered.text
    assert rendered.text.index("M|m1|u1|原消息") < rendered.text.index(
        "M|m2|u2|^m1 回复一下"
    )
    assert rendered.metadata["message_ids"] == ["m2"]
    assert rendered.metadata["count"] == 1


def test_present_recent_window_frontloads_uncached_reply_target() -> None:
    """Extra reply targets fetched outside the store should render first."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m2",
            sender_id="456",
            sender_name="Bob",
            content="回复一下",
            reply_to_message_id="m1",
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
        extra_messages=[
            _message(
                "m1",
                sender_id="bot",
                sender_name="Bot",
                content="原消息",
                from_self=True,
            )
        ],
    )

    message_rows = [
        line for line in rendered.text.splitlines() if line.startswith("M|")
    ]
    assert message_rows[0] == "M|m1|u0|原消息"
    assert message_rows[1] == "M|m2|u1|^m1 回复一下"
    assert rendered.metadata["message_ids"] == ["m2"]
    assert rendered.metadata["count"] == 1


def test_present_recent_window_excludes_unread_assistant_messages() -> None:
    """Incremental unread windows should not include assistant self-messages."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="bot",
            sender_name="Bot",
            content="在呀",
            from_self=True,
        )
    )
    store.append(_message("m2", sender_id="123", sender_name="Alice", content="1"))
    store.append(_message("m3", sender_id="123", sender_name="Alice", content="2"))

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    message_rows = [
        line for line in rendered.text.splitlines() if line.startswith("M|")
    ]
    assert message_rows == ["M|m2|u1|1", "M|m3|u1|2"]
    assert rendered.metadata["message_ids"] == ["m2", "m3"]
    assert rendered.metadata["count"] == 2


def test_present_recent_window_truncates_single_message_from_middle() -> None:
    """Overlong single-message bodies should preserve both ends."""
    long_text = "前" * 200 + "后" * 200
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content=long_text,
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
        max_ctx_length=300,
    )

    assert (
        "M|m1|u1|" + ("前" * 141) + "[...TRUNCATED...]" + ("后" * 142)
    ) in rendered.text


def test_present_recent_window_keeps_oversized_image_as_placeholder() -> None:
    """Oversized images should not become multimodal CTX media rows."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="看图[image]",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/big.png",
                    name="big.png",
                    metadata={"file_size": str(2 * 1024 * 1024)},
                )
            ],
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
        media_max_size_bytes=1024 * 1024,
    )

    assert "I|" not in rendered.text
    assert "M|m1|u1|看图[image]" in rendered.text
    assert rendered.media == []


def test_present_recent_window_collects_allowed_image_media_once() -> None:
    """Allowed images should produce CTX image rows and deduped media refs."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="先看[image]",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/a.png",
                    name="a.png",
                    metadata={"file_size": "123"},
                )
            ],
        )
    )
    store.append(
        _message(
            "m2",
            sender_id="123",
            sender_name="Alice",
            content="再看[image]",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/a.png",
                    name="a.png",
                    metadata={"file_size": "123"},
                )
            ],
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "I|i0|a.png" in rendered.text
    assert "M|m1|u1|先看[i0]" in rendered.text
    assert "M|m2|u1|再看[i0]" in rendered.text
    assert rendered.media == [{"kind": "image", "url": "https://example.com/a.png"}]


def test_present_recent_window_renders_poke_as_event_row() -> None:
    """Poke events should remain compact E rows and not increase unread count."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "notice:poke:1:456:123:42",
            sender_id="123",
            sender_name="Alice",
            content="",
            message_type="poke",
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="42",
    )

    assert "<CTX/1 g:456 bot:u0 n:0>" in rendered.text
    assert "E|notice:poke:1:456:123:42|u1" in rendered.text


def test_present_recent_window_renders_expanded_forward_block() -> None:
    """Expanded forward payload should render as a compact deterministic suffix."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="看这个[forward]",
            metadata={
                "forward_expanded": [
                    ForwardExpanded(
                        forward_id="fw-1",
                        summary="2条聊天记录",
                        nodes=[
                            ForwardNode(
                                sender_id="1001",
                                sender_name="张三",
                                content="第一条",
                            ),
                            ForwardNode(
                                sender_id="1002",
                                sender_name="李四",
                                content="第二条|含分隔符\n换行",
                            ),
                        ],
                    ).model_dump(exclude_none=True)
                ]
            },
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    expected_block = (
        "M|m1|u1|看这个[forward] "
        "[F:id=fw-1;summary=2条聊天记录;"
        "nodes=1001/张三/第一条,1002/李四/第二条\\|含分隔符\\n换行]"
    )

    assert expected_block in rendered.text


def test_present_recent_window_prefers_render_segments_for_mentions() -> None:
    """Presenter 应优先使用 render_segments 还原提及语义."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="fallback text should be ignored",
            metadata={
                "render_segments": [
                    {"type": "text", "text": "hi "},
                    {"type": "mention", "user_id": "1001", "name": "群名片"},
                    {"type": "text", "text": " and "},
                    {"type": "mention", "user_id": "1002", "name": "昵称"},
                    {"type": "text", "text": " plus "},
                    {"type": "mention", "user_id": "1003", "name": "1003"},
                    {"type": "text", "text": " "},
                    {"type": "mention_all"},
                    {"type": "text", "text": " "},
                    {"type": "forward"},
                ]
            },
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "U|u2|1001|群名片" in rendered.text
    assert "U|u3|1002|昵称" in rendered.text
    assert "U|u4|1003|1003" in rendered.text
    assert "M|m1|u1|hi @u2 and @u3 plus @u4 @all [forward]" in rendered.text


def test_present_recent_window_prefers_valid_attachment_render_segments() -> None:
    """Presenter 应按 render_segments 顺序渲染有效附件段."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="fallback should be ignored",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/rendered.png",
                    name="rendered.png",
                    metadata={"file_size": "123"},
                )
            ],
            metadata={
                "render_segments": [
                    {"type": "text", "text": "先看"},
                    {"type": "image"},
                    {"type": "text", "text": "再说"},
                ]
            },
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "I|i0|rendered.png" in rendered.text
    assert "M|m1|u1|先看[i0]再说" in rendered.text



def test_present_recent_window_falls_back_when_render_segments_invalid() -> None:
    """Presenter 应在 render_segments 非法时回退到 content 渲染."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="回退[image]",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/fallback.png",
                    name="fallback.png",
                    metadata={"file_size": "123"},
                )
            ],
            metadata={
                "render_segments": [
                    {"type": "text", "text": "broken "},
                    {"type": "mention"},
                ]
            },
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "I|i0|fallback.png" in rendered.text
    assert "M|m1|u1|回退[i0]" in rendered.text



def test_present_recent_window_ignores_invalid_forward_metadata() -> None:
    """Malformed expanded forward metadata should not break CTX rendering."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(
        _message(
            "m1",
            sender_id="123",
            sender_name="Alice",
            content="看这个[forward]",
            metadata={
                "forward_expanded": [
                    {"forward_id": "fw-1", "summary": "ok", "nodes": []},
                    {"forward_id": "bad", "summary": 123, "nodes": "oops"},
                    "skip-me",
                ]
            },
        )
    )

    rendered = ContextPresenter().present_recent_window(
        store,
        _conversation(),
        self_id="bot",
    )

    assert "[F:id=fw-1;summary=ok;nodes=]" in rendered.text
    assert "bad" not in rendered.text
