"""Focused compatibility tests for the new channel shell."""

from __future__ import annotations

import asyncio

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus

from nanobot_channel_anon.channel import AnonChannel
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.kernel import Kernel


class RecordingKernel(Kernel):
    """用于测试外壳委托行为的内核桩."""

    def __init__(self) -> None:
        """初始化记录状态."""
        self.started = 0
        self.stopped = 0
        self.sent: list[OutboundMessage] = []
        self.deltas: list[tuple[str, str, dict[str, object] | None]] = []
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动后保持阻塞直到收到停止信号."""
        self.started += 1
        await self._stop_event.wait()

    async def stop(self) -> None:
        """记录停止调用并释放 start 阻塞."""
        self.stopped += 1
        self._stop_event.set()

    async def send(self, msg: OutboundMessage) -> None:
        """记录 send 调用."""
        self.sent.append(msg)

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """记录 send_delta 调用."""
        self.deltas.append((chat_id, delta, metadata))


def test_channel_shell_delegates_all_public_methods() -> None:
    """AnonChannel should delegate start stop send and send_delta."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        start_task = asyncio.create_task(channel.start())
        await asyncio.sleep(0)
        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="hello",
                metadata={"source": "test"},
            )
        )
        await channel.send_delta("private:1", "delta", {"stream": True})
        await channel.stop()
        await asyncio.wait_for(start_task, timeout=1.0)

        assert kernel.started == 1
        assert kernel.stopped == 1
        assert len(kernel.sent) == 1
        assert kernel.sent[0].content == "hello"
        assert kernel.deltas == [("private:1", "delta", {"stream": True})]

    asyncio.run(case())



def test_channel_shell_drops_upstream_error_text() -> None:
    """上游错误文本应在 channel 外壳层被拦截."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="Error: API returned empty choices.",
            )
        )
        await channel.send_delta("private:1", "Error calling LLM: timeout")

        assert kernel.sent == []
        assert kernel.deltas == []

    asyncio.run(case())


def test_channel_shell_drops_upstream_empty_final_response_text() -> None:
    """空最终答复兜底文案应在 channel 外壳层被拦截."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content=(
                    "I completed the tool steps but couldn't produce a final answer. "
                    "Please try again or narrow the task."
                ),
            )
        )
        await channel.send_delta(
            "private:1",
            "I completed the tool steps but couldn't produce a final answer. "
            "Please try again or narrow the task.",
        )

        assert kernel.sent == []
        assert kernel.deltas == []

    asyncio.run(case())


def test_channel_shell_drops_upstream_subagent_empty_final_response_text() -> None:
    """子代理空最终答复文案应在 channel 外壳层被拦截."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="Task completed but no final response was generated.",
            )
        )

        assert kernel.sent == []

    asyncio.run(case())


def test_channel_shell_allows_normal_text() -> None:
    """普通文本不应被最小错误拦截规则误伤."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="Restarting...",
            )
        )
        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="I completed the tool steps and here is the final answer.",
            )
        )

        assert [msg.content for msg in kernel.sent] == [
            "Restarting...",
            "I completed the tool steps and here is the final answer.",
        ]

    asyncio.run(case())



def test_channel_shell_allows_error_text_with_media() -> None:
    """带媒体的消息不应仅因文本前缀命中而被丢弃."""

    async def case() -> None:
        kernel = RecordingKernel()

        def kernel_factory(config: AnonConfig, bus: MessageBus) -> RecordingKernel:
            del config, bus
            return kernel

        channel = AnonChannel(
            {"ws_url": "ws://127.0.0.1:3001"},
            MessageBus(),
            kernel_factory=kernel_factory,
        )

        await channel.send(
            OutboundMessage(
                channel="anon",
                chat_id="private:1",
                content="Error: screenshot upload failed",
                media=["https://example.com/demo.png"],
            )
        )

        assert len(kernel.sent) == 1
        assert kernel.sent[0].media == ["https://example.com/demo.png"]

    asyncio.run(case())
