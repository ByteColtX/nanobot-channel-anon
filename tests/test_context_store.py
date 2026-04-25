"""Tests for the platform-agnostic context store."""

from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import Attachment, ConversationRef, NormalizedMessage


def _conversation() -> ConversationRef:
    return ConversationRef(kind="group", id="456")


def _message(
    message_id: str,
    *,
    content: str,
    from_self: bool = False,
) -> NormalizedMessage:
    return NormalizedMessage(
        message_id=message_id,
        conversation=_conversation(),
        sender_id="123" if not from_self else "bot",
        sender_name="Alice" if not from_self else "Bot",
        content=content,
        from_self=from_self,
    )


def test_append_and_recent_window_keep_insertion_order() -> None:
    """Recent window should stay bounded and ordered by insertion."""
    store = ContextStore(max_messages_per_conversation=3)

    store.append(_message("m1", content="one"))
    store.append(_message("m2", content="two"))
    store.append(_message("m3", content="three"))
    store.append(_message("m4", content="four"))

    window = store.recent_window(_conversation())

    assert [message.message_id for message in window] == ["m2", "m3", "m4"]


def test_lookup_message_returns_reply_target() -> None:
    """Stored messages should be retrievable for reply resolution."""
    store = ContextStore(max_messages_per_conversation=5)
    message = _message("m1", content="hello")

    store.append(message)

    assert store.get_message(_conversation(), "m1") == message
    assert store.get_message(_conversation(), "missing") is None


def test_consumed_cursor_tracks_explicit_message_id() -> None:
    """Consumed state should advance via explicit message IDs."""
    store = ContextStore(max_messages_per_conversation=5)
    store.append(_message("m1", content="one"))
    store.append(_message("m2", content="two", from_self=True))
    store.append(_message("m3", content="three"))

    before_ids = [
        message.message_id
        for message in store.unconsumed_window(_conversation())
    ]

    assert before_ids == ["m1", "m2", "m3"]

    store.mark_consumed(_conversation(), "m2")

    after_ids = [
        message.message_id
        for message in store.unconsumed_window(_conversation())
    ]

    assert store.consumed_through(_conversation()) == "m2"
    assert after_ids == ["m3"]


def test_consumed_cursor_ignores_evicted_messages() -> None:
    """Eviction should keep consumed state consistent with remaining messages."""
    store = ContextStore(max_messages_per_conversation=2)
    store.append(_message("m1", content="one"))
    store.append(_message("m2", content="two"))
    store.mark_consumed(_conversation(), "m1")

    store.append(_message("m3", content="three"))
    remaining_ids = [
        message.message_id
        for message in store.unconsumed_window(_conversation())
    ]

    assert store.consumed_through(_conversation()) == "m1"
    assert remaining_ids == ["m2", "m3"]


def test_message_lookup_supports_media_metadata() -> None:
    """Stored messages should preserve attachment data for later presentation."""
    store = ContextStore(max_messages_per_conversation=5)
    message = NormalizedMessage(
        message_id="m1",
        conversation=_conversation(),
        sender_id="123",
        sender_name="Alice",
        content="see image",
        attachments=[Attachment(kind="image", url="https://example.com/cat.jpg")],
        metadata={"source": "test"},
    )

    store.append(message)

    stored = store.get_message(_conversation(), "m1")
    assert stored is not None
    assert stored.attachments[0].url == "https://example.com/cat.jpg"
    assert stored.metadata == {"source": "test"}
