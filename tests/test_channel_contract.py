"""Tests for the new channel shell and kernel contract."""

from __future__ import annotations

import asyncio

import pytest
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from pydantic import BaseModel

from nanobot_channel_anon.adapters.onebot_transport import OneBotTransport
from nanobot_channel_anon.channel import AnonChannel
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.kernel import Kernel
from nanobot_channel_anon.onebot import OneBotRawEvent


class FakeTransport:
    """测试用传输层桩."""

    def __init__(self) -> None:
        """初始化传输层记录器."""
        self.start_calls = 0
        self.stop_calls = 0
        self.sent_batches: list[list[dict[str, object]]] = []
        self.started = asyncio.Event()
        self.stop_requested = asyncio.Event()

    async def start(self) -> None:
        """启动后保持运行直到收到停止信号."""
        self.start_calls += 1
        self.started.set()
        await self.stop_requested.wait()

    async def stop(self) -> None:
        """停止传输层."""
        self.stop_calls += 1
        self.stop_requested.set()

    async def send_requests(self, requests: list[BaseModel]) -> list[OneBotRawEvent]:
        """记录一次新的协议请求批次."""
        self.sent_batches.append(
            [request.model_dump(exclude_none=False) for request in requests]
        )
        return []


def _make_channel(
    *,
    transport: FakeTransport | None = None,
) -> tuple[AnonChannel, FakeTransport]:
    fake_transport = transport or FakeTransport()
    bus = MessageBus()

    def kernel_factory(config: AnonConfig, bus: MessageBus) -> Kernel:
        return Kernel(config=config, bus=bus, transport=fake_transport)

    channel = AnonChannel(
        {
            "enabled": True,
            "allow_from": ["*"],
            "ws_url": "ws://127.0.0.1:3001",
        },
        bus,
        kernel_factory=kernel_factory,
    )
    return channel, fake_transport


def test_default_config_remains_onboard_compatible() -> None:
    """default_config() 应返回供 onboard 直接写入的 camelCase 配置."""
    config = AnonChannel.default_config()

    assert isinstance(config, dict)
    assert config["enabled"] is False
    assert config["wsUrl"] == ""
    assert config["accessToken"] == ""
    assert config["allowFrom"] == []
    assert config["superAdmins"] == []
    assert config["maxContextMessages"] == 25
    assert config["maxCtxLength"] == 300
    assert config["mediaMaxSizeMb"] == 50
    assert "transport" not in config


def test_channel_wraps_dict_config_in_anon_config() -> None:
    """AnonChannel should validate dict config into AnonConfig."""
    channel, _ = _make_channel()

    assert isinstance(channel.config, AnonConfig)
    assert channel.config.ws_url == "ws://127.0.0.1:3001"


def test_init_requires_websocket_url_when_enabled() -> None:
    """Enabled channels should reject an empty transport URL."""
    with pytest.raises(ValueError, match="ws_url"):
        AnonChannel(
            {"enabled": True, "ws_url": ""},
            MessageBus(),
        )


def test_init_requires_non_empty_allow_from_when_enabled() -> None:
    """Enabled channels should reject an empty allow_from list."""
    with pytest.raises(ValueError, match="allow_from"):
        AnonChannel(
            {
                "enabled": True,
                "allow_from": [],
                "ws_url": "ws://127.0.0.1:3001",
            },
            MessageBus(),
        )


def test_channel_start_and_stop_delegate_to_kernel_transport() -> None:
    """start() and stop() should be delegated through the kernel."""

    async def case() -> None:
        channel, transport = _make_channel()
        task = asyncio.create_task(channel.start())
        await asyncio.wait_for(transport.started.wait(), timeout=1.0)

        assert channel.is_running is True
        await channel.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert channel.is_running is False
        assert transport.start_calls == 1
        assert transport.stop_calls == 1

    asyncio.run(case())


def test_channel_send_delegates_to_transport_send_requests() -> None:
    """send() should map outbound content into transport request batches."""

    async def case() -> None:
        channel, transport = _make_channel()

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="group:123",
                content="hello",
                media=["file:///tmp/demo.png"],
                metadata={"reply_to_message_id": "42"},
            )
        )

        assert transport.sent_batches == [
            [
                {
                    "action": "send_group_msg",
                    "params": {
                        "group_id": 123,
                        "message": [
                            {"type": "reply", "data": {"id": "42"}},
                            {"type": "image", "data": {"file": "file:///tmp/demo.png"}},
                            {"type": "text", "data": {"text": "hello"}},
                        ],
                    },
                    "echo": None,
                }
            ]
        ]

    asyncio.run(case())


def test_send_delta_uses_transport_send_requests() -> None:
    """send_delta() should use the same request-batch transport contract."""

    async def case() -> None:
        channel, transport = _make_channel()

        await channel.send_delta("private:99", "partial", {"kind": "delta"})

        assert transport.sent_batches == [
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


def test_default_kernel_assembly_shares_state_with_transport() -> None:
    """默认装配路径应让内核与传输层共享同一个状态适配器."""
    channel = AnonChannel(
        {
            "enabled": True,
            "allow_from": ["*"],
            "ws_url": "ws://127.0.0.1:3001",
        },
        MessageBus(),
    )

    transport = channel._kernel.transport

    assert isinstance(transport, OneBotTransport)
    assert channel._kernel.state is transport.state


def test_kernel_rejects_empty_ws_url_when_enabled() -> None:
    """Kernel.start() 应直接拒绝启用状态下的空 ws_url."""

    async def case() -> None:
        config = AnonConfig(
            enabled=True,
            allow_from=["*"],
            ws_url="ws://127.0.0.1:3001",
        ).model_copy(update={"ws_url": ""})
        kernel = Kernel(
            config=config,
            bus=MessageBus(),
            transport=FakeTransport(),
        )

        with pytest.raises(ValueError, match="ws_url"):
            await kernel.start()

    asyncio.run(case())


def test_kernel_ignores_duplicate_start_calls() -> None:
    """Kernel should not start the transport twice while already running."""

    async def case() -> None:
        transport = FakeTransport()
        kernel = Kernel(
            config=AnonConfig(
                enabled=True,
                allow_from=["*"],
                ws_url="ws://127.0.0.1:3001",
            ),
            bus=MessageBus(),
            transport=transport,
        )
        first = asyncio.create_task(kernel.start())
        await asyncio.wait_for(transport.started.wait(), timeout=1.0)

        await kernel.start()
        await kernel.stop()
        await asyncio.wait_for(first, timeout=1.0)

        assert transport.start_calls == 1
        assert transport.stop_calls == 1

    asyncio.run(case())
