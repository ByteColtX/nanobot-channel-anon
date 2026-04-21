"""Tests for per-session inbound worker behavior."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus

from nanobot_channel_anon.buffer import MessageEntry
from nanobot_channel_anon.channel import AnonChannel
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.onebot import (
    OneBotMessageSegment,
    OneBotRawEvent,
    OneBotSender,
)


class ControlledAnonChannel(AnonChannel):
    """AnonChannel variant with controllable async hooks for tests."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        image_gates: dict[str, asyncio.Event] | None = None,
    ) -> None:
        """Initialize a test channel with optional image blocking gates."""
        super().__init__(
            AnonConfig(
                allow_from=["*"],
                group_trigger_prob=1.0,
                max_context_messages=10,
            ),
            bus,
        )
        self._image_gates = image_gates or {}
        self._image_started = {
            token: asyncio.Event() for token in self._image_gates
        }

    def _write_inbound_cache_file(self) -> None:
        """Skip filesystem writes in tests."""

    async def _resolve_forward_content(self, forward_id: str) -> Any:
        return {"forward_id": forward_id, "messages": []}

    async def _download_inbound_image(self, media_item: dict[str, Any]) -> str | None:
        token = str(media_item.get("file") or media_item.get("url") or "")
        started = self._image_started.setdefault(token, asyncio.Event())
        started.set()
        gate = self._image_gates.get(token)
        if gate is not None:
            await gate.wait()
        return f"file:///{token}"

    async def _process_inbound_voice(
        self,
        media_item: dict[str, Any],
    ) -> dict[str, str] | None:
        del media_item
        return None

    async def _wait_for_image_download(self, token: str) -> None:
        """Wait until a controlled image download starts."""
        await self._image_started.setdefault(token, asyncio.Event()).wait()


class BlockingPublishBus(MessageBus):
    """Message bus that can pause publish_inbound calls."""

    def __init__(self) -> None:
        """Initialize per-publish blocking events for deterministic tests."""
        super().__init__()
        self.publish_attempts: list[InboundMessage] = []
        self.release_events: list[asyncio.Event] = []
        self.first_blocked = asyncio.Event()
        self.second_blocked = asyncio.Event()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Block each publish until the test explicitly releases it."""
        self.publish_attempts.append(msg)
        release = asyncio.Event()
        self.release_events.append(release)
        if len(self.release_events) == 1:
            self.first_blocked.set()
        elif len(self.release_events) == 2:
            self.second_blocked.set()
        await release.wait()
        await super().publish_inbound(msg)


def _group_event(
    message_id: str,
    *,
    text: str,
    user_id: str = "123",
    group_id: str = "456",
    message: str | list[OneBotMessageSegment] | None = None,
) -> OneBotRawEvent:
    return OneBotRawEvent(
        post_type="message",
        message_type="group",
        message_id=message_id,
        user_id=user_id,
        group_id=group_id,
        raw_message=text,
        message=text if message is None else message,
        sender=OneBotSender(
            user_id=user_id,
            nickname=f"user-{user_id}",
            card="",
        ),
    )


def _reply_group_event(
    message_id: str,
    *,
    reply_to_message_id: str,
    text: str,
    group_id: str = "456",
) -> OneBotRawEvent:
    return _group_event(
        message_id,
        text=text,
        group_id=group_id,
        message=[
            OneBotMessageSegment(type="reply", data={"id": reply_to_message_id}),
            OneBotMessageSegment(type="text", data={"text": text}),
        ],
    )


async def _consume_published(
    bus: MessageBus,
    count: int,
) -> list[InboundMessage]:
    messages: list[InboundMessage] = []
    for _ in range(count):
        message = await asyncio.wait_for(bus.consume_inbound(), timeout=0.5)
        messages.append(message)
    return messages


async def _assert_no_publish(bus: MessageBus) -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(bus.consume_inbound(), timeout=0.05)


def test_inbound_events_preserve_arrival_order_per_chat() -> None:
    """Later messages in one chat should wait for earlier slow messages."""

    async def case() -> None:
        bus = MessageBus()
        slow_gate = asyncio.Event()
        channel = ControlledAnonChannel(bus=bus, image_gates={"slow-image": slow_gate})
        first = _group_event(
            "1",
            text="first",
            message=[
                OneBotMessageSegment(type="text", data={"text": "first"}),
                OneBotMessageSegment(type="image", data={"file": "slow-image"}),
            ],
        )
        second = _group_event("2", text="second")

        try:
            await channel._enqueue_inbound_event(first)
            await channel._wait_for_image_download("slow-image")
            await channel._enqueue_inbound_event(second)
            slow_gate.set()

            published = await _consume_published(bus, 2)
            published_ids = [msg.metadata["cqmsg_message_ids"] for msg in published]
            assert published_ids == [["1"], ["2"]]
        finally:
            slow_gate.set()
            await channel.stop()

    asyncio.run(case())


def test_overlapping_inbound_events_do_not_duplicate_cqmsg_context() -> None:
    """Same-chat unread windows should serialize without duplication."""

    async def case() -> None:
        bus = BlockingPublishBus()
        channel = ControlledAnonChannel(bus=bus)
        first = _group_event("1", text="first")
        second = _group_event("2", text="second")

        try:
            await channel._enqueue_inbound_event(first)
            await bus.first_blocked.wait()

            await channel._enqueue_inbound_event(second)
            await asyncio.sleep(0)
            assert not bus.second_blocked.is_set()

            bus.release_events[0].set()
            await bus.second_blocked.wait()
            bus.release_events[1].set()

            published = await _consume_published(bus, 2)
            published_ids = [msg.metadata["cqmsg_message_ids"] for msg in published]
            assert published_ids == [["1"], ["2"]]
            assert channel._buffer.get_unconsumed_chat_entries("group:456") == []
        finally:
            for release in bus.release_events:
                release.set()
            await channel.stop()

    asyncio.run(case())


def test_reply_target_from_self_sees_earlier_buffer_update() -> None:
    """Queued reply events should observe self messages added before they run."""

    async def case() -> None:
        bus = MessageBus()
        slow_gate = asyncio.Event()
        channel = ControlledAnonChannel(bus=bus, image_gates={"slow-image": slow_gate})
        first = _group_event(
            "1",
            text="first",
            message=[
                OneBotMessageSegment(type="text", data={"text": "first"}),
                OneBotMessageSegment(type="image", data={"file": "slow-image"}),
            ],
        )
        second = _reply_group_event(
            "2",
            reply_to_message_id="bot-1",
            text="replying",
        )

        try:
            await channel._enqueue_inbound_event(first)
            await channel._wait_for_image_download("slow-image")
            channel._buffer.add(
                MessageEntry(
                    message_id="bot-1",
                    chat_id="group:456",
                    sender_id="42",
                    sender_name="bot",
                    is_from_self=True,
                    content="bot reply",
                )
            )
            await channel._enqueue_inbound_event(second)
            slow_gate.set()

            published = await _consume_published(bus, 2)
            assert published[1].metadata["reply_target_from_self"] is True
        finally:
            slow_gate.set()
            await channel.stop()

    asyncio.run(case())


def test_different_chats_still_process_concurrently() -> None:
    """A slow chat should not block a fast message in another chat."""

    async def case() -> None:
        bus = MessageBus()
        slow_gate = asyncio.Event()
        channel = ControlledAnonChannel(bus=bus, image_gates={"slow-image": slow_gate})
        slow = _group_event(
            "1",
            text="slow",
            group_id="456",
            message=[
                OneBotMessageSegment(type="text", data={"text": "slow"}),
                OneBotMessageSegment(type="image", data={"file": "slow-image"}),
            ],
        )
        fast = _group_event("9", text="fast", group_id="789")

        try:
            await channel._enqueue_inbound_event(slow)
            await channel._wait_for_image_download("slow-image")
            await channel._enqueue_inbound_event(fast)

            first_published = await _consume_published(bus, 1)
            assert first_published[0].chat_id == "group:789"
            assert first_published[0].metadata["cqmsg_message_ids"] == ["9"]

            slow_gate.set()
            second_published = await _consume_published(bus, 1)
            assert second_published[0].chat_id == "group:456"
            assert second_published[0].metadata["cqmsg_message_ids"] == ["1"]
        finally:
            slow_gate.set()
            await channel.stop()

    asyncio.run(case())


def test_stop_cancels_session_workers_and_drops_queued_messages() -> None:
    """Stopping the channel should cancel workers and drop queued work."""

    async def case() -> None:
        bus = MessageBus()
        slow_gate = asyncio.Event()
        channel = ControlledAnonChannel(bus=bus, image_gates={"slow-image": slow_gate})
        first = _group_event(
            "1",
            text="first",
            message=[
                OneBotMessageSegment(type="text", data={"text": "first"}),
                OneBotMessageSegment(type="image", data={"file": "slow-image"}),
            ],
        )
        second = _group_event("2", text="second")

        try:
            await channel._enqueue_inbound_event(first)
            await channel._wait_for_image_download("slow-image")
            await channel._enqueue_inbound_event(second)

            await channel.stop()
            slow_gate.set()

            assert channel._session_workers == {}
            await _assert_no_publish(bus)
        finally:
            slow_gate.set()
            await channel.stop()

    asyncio.run(case())
