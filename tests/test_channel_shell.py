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
