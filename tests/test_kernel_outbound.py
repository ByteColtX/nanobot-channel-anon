"""Focused tests for the new kernel outbound runtime path."""

from __future__ import annotations

import asyncio

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from pydantic import BaseModel

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import ConversationRef, NormalizedMessage
from nanobot_channel_anon.kernel import Kernel
from nanobot_channel_anon.onebot import OneBotRawEvent


class RecordingTransport:
    """记录内核下发的 OneBot 请求."""

    def __init__(self) -> None:
        """初始化请求记录器."""
        self.requests: list[list[dict[str, object]]] = []
        self.responses: list[OneBotRawEvent] = []

    async def start(self) -> None:
        """满足接口要求."""

    async def stop(self) -> None:
        """满足接口要求."""

    async def send_requests(self, requests: list[BaseModel]) -> list[OneBotRawEvent]:
        """记录映射后的请求批次并返回伪响应."""
        self.requests.append(
            [request.model_dump(exclude_none=False) for request in requests]
        )
        if self.responses:
            return list(self.responses)
        dumped_requests = self.requests[-1]
        return [
            OneBotRawEvent(status="ok", retcode=0, data={"message_id": index + 1})
            for index, _ in enumerate(dumped_requests)
        ]


def _config() -> AnonConfig:
    """构造最小测试配置."""
    return AnonConfig.model_validate(
        {
            "enabled": True,
            "allow_from": ["*"],
            "ws_url": "ws://127.0.0.1:3001",
        }
    )


def test_send_uses_mapper_and_transport_request_path() -> None:
    """send() 应通过 mapper 和 transport 发出 OneBot 请求."""

    async def case() -> None:
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=MessageBus(), transport=transport)

        await kernel.send(
            OutboundMessage(
                channel="anon",
                chat_id="group:456",
                content="caption",
                media=["file:///tmp/voice.wav"],
                metadata={"reply_to_message_id": "99"},
            )
        )

        assert transport.requests == [
            [
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
        ]

    asyncio.run(case())


def test_send_delta_uses_new_outbound_path() -> None:
    """send_delta() 仍应走新的出站请求链路."""

    async def case() -> None:
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=MessageBus(), transport=transport)

        await kernel.send_delta("private:99", "partial", {"kind": "delta"})

        assert transport.requests == [
            [
                {
                    "action": "send_private_msg",
                    "params": {
                        "user_id": 99,
                        "message": [{"type": "text", "data": {"text": "partial"}}],
                    },
                    "echo": None,
                }
            ]
        ]

    asyncio.run(case())


def test_send_parses_inline_cq_media_and_mentions() -> None:
    """send() 应把内联 CQ 媒体与提及映射到新的出站链路."""

    async def case() -> None:
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=MessageBus(), transport=transport)

        await kernel.send(
            OutboundMessage(
                channel="anon",
                chat_id="group:456",
                content=(
                    "[CQ:reply,id=66]hello [CQ:at,qq=all]"
                    "[CQ:record,file=file:///tmp/voice.wav]"
                    "after[CQ:image,file=https://example.com/demo.png]"
                ),
            )
        )

        assert transport.requests == [
            [
                {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": 456,
                        "message": [
                            {"type": "reply", "data": {"id": "66"}},
                            {"type": "text", "data": {"text": "hello "}},
                            {"type": "at", "data": {"qq": "all"}},
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
                                "data": {"file": "https://example.com/demo.png"},
                            },
                        ],
                    },
                    "echo": None,
                },
            ]
        ]

    asyncio.run(case())



def test_send_persists_successful_outbound_messages_for_reply_context() -> None:
    """成功出站后应把机器人消息写回上下文."""

    async def case() -> None:
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=MessageBus(), transport=transport)
        kernel.state.set_self_profile(user_id="42", nickname="Bot")

        await kernel.send(
            OutboundMessage(
                channel="anon",
                chat_id="group:456",
                content="hello",
                metadata={"reply_to_message_id": "99"},
            )
        )

        stored = kernel.context_store.get_message(
            ConversationRef(kind="group", id="456"),
            "1",
        )
        assert stored is not None
        assert stored.from_self is True
        assert stored.sender_id == "42"
        assert stored.sender_name == "Bot"
        assert stored.content == "hello"
        assert stored.reply_to_message_id == "99"

    asyncio.run(case())


def test_send_persisted_message_enables_followup_reply_trigger() -> None:
    """用户回复刚发送成功的机器人消息时应触发 reply_to_self."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=bus, transport=transport)
        kernel.state.set_self_profile(user_id="42", nickname="Bot")

        await kernel.send(
            OutboundMessage(
                channel="anon",
                chat_id="group:456",
                content="earlier reply",
            )
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="got it",
                reply_to_message_id="1",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert inbound.metadata["trigger_reason"] == "reply_to_self"
        assert inbound.metadata["message"]["reply_to_self"] is True
        assert "M|1|u0|earlier reply" in inbound.content
        assert "M|user-1|u1|^1 got it" in inbound.content

    asyncio.run(case())


def test_send_delta_skips_empty_text() -> None:
    """空增量不应触发任何出站请求."""

    async def case() -> None:
        transport = RecordingTransport()
        kernel = Kernel(config=_config(), bus=MessageBus(), transport=transport)

        await kernel.send_delta("private:99", "", {"kind": "delta"})

        assert transport.requests == []

    asyncio.run(case())
