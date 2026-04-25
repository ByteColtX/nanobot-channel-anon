"""Thin channel shell for the anon plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel

from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.adapters.onebot_transport import OneBotTransport
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound_media import InboundMediaEnricher
from nanobot_channel_anon.kernel import Kernel


class AnonChannel(BaseChannel):
    """Anon 频道插件外壳."""

    name = "anon"
    display_name = "Anon"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        kernel_factory: Callable[[AnonConfig, MessageBus], Kernel] | None = None,
    ) -> None:
        """初始化频道外壳并装配内核."""
        validated_config = config
        if not isinstance(config, AnonConfig):
            validated_config = AnonConfig.model_validate(config)
        super().__init__(validated_config, bus)
        self.config: AnonConfig = validated_config
        if kernel_factory is None:
            self._kernel = self._build_kernel(self.config, bus)
        else:
            self._kernel = kernel_factory(self.config, bus)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """返回兼容 onboard 的默认配置."""
        return AnonConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """启动频道并委托给内核."""
        if self._running:
            return
        self._running = True
        try:
            await self._kernel.start()
        finally:
            self._running = False

    async def stop(self) -> None:
        """停止频道并委托给内核."""
        await self._kernel.stop()
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """发送消息并委托给内核."""
        await self._kernel.send(msg)

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """按普通文本消息发送增量内容."""
        await self._kernel.send_delta(chat_id, delta, metadata)

    def _build_kernel(self, config: AnonConfig, bus: MessageBus) -> Kernel:
        """构建默认内核实例."""
        state = OneBotStateAdapter()
        transport = OneBotTransport(config=config, bus=bus, state=state)
        inbound_media_enricher = InboundMediaEnricher(
            config=config,
            transcribe_audio=self.transcribe_audio,
        )
        return Kernel(
            config=config,
            bus=bus,
            transport=transport,
            state=state,
            inbound_media_enricher=inbound_media_enricher,
        )
