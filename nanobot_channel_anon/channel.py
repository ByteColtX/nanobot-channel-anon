"""Thin channel shell for the anon plugin."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel

from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.adapters.onebot_transport import OneBotTransport
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound_forward import InboundForwardEnricher
from nanobot_channel_anon.inbound_media import InboundMediaEnricher
from nanobot_channel_anon.kernel import Kernel

_BLOCKED_UPSTREAM_OUTBOUND_PATTERNS = (
    re.compile(r"^Error:"),
    re.compile(r"^Error calling LLM:"),
    re.compile(r"^Sorry, I encountered an error(?: calling the AI model)?\."),
    re.compile(r"^I reached the maximum number of tool call iterations \(\d+\) "),
    re.compile(
        r"^I completed the tool steps but couldn't produce a final answer\."
        r" Please try again or narrow the task\.$"
    ),
    re.compile(r"^Task completed but no final response was generated\.$"),
)


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

    @staticmethod
    def _should_drop_upstream_outbound(content: str, *, has_media: bool) -> bool:
        """按最小规则拦截上游错误文本出站."""
        if has_media:
            return False
        normalized = content.strip()
        if not normalized:
            return False
        return any(
            pattern.match(normalized) for pattern in _BLOCKED_UPSTREAM_OUTBOUND_PATTERNS
        )

    async def send(self, msg: OutboundMessage) -> None:
        """发送消息并委托给内核."""
        if self._should_drop_upstream_outbound(msg.content, has_media=bool(msg.media)):
            return
        await self._kernel.send(msg)

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """按普通文本消息发送增量内容."""
        if self._should_drop_upstream_outbound(delta, has_media=False):
            return
        await self._kernel.send_delta(chat_id, delta, metadata)

    def _build_kernel(self, config: AnonConfig, bus: MessageBus) -> Kernel:
        """构建默认内核实例."""
        state = OneBotStateAdapter()
        transport = OneBotTransport(config=config, bus=bus, state=state)
        inbound_forward_enricher = InboundForwardEnricher(fetcher=transport)
        inbound_media_enricher = InboundMediaEnricher(
            config=config,
            transcribe_audio=self.transcribe_audio,
        )
        return Kernel(
            config=config,
            bus=bus,
            transport=transport,
            state=state,
            inbound_forward_enricher=inbound_forward_enricher,
            inbound_media_enricher=inbound_media_enricher,
        )
