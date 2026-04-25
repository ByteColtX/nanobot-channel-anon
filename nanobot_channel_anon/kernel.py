"""Main runtime orchestrator for the anon channel."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from pydantic import BaseModel

from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import ChannelSendRequest, NormalizedMessage
from nanobot_channel_anon.policy import PolicyContext, PolicyEngine
from nanobot_channel_anon.presenter import ContextPresenter


@runtime_checkable
class RequestBatchSender(Protocol):
    """支持批量协议请求发送的传输层扩展."""

    async def send_requests(self, requests: Sequence[BaseModel]) -> None:
        """发送一批协议层出站请求."""


class ChannelTransport(Protocol):
    """内核依赖的传输层协议."""

    async def start(self) -> None:
        """启动传输层并保持运行."""

    async def stop(self) -> None:
        """停止传输层."""


class Kernel:
    """频道主运行时编排器."""

    def __init__(
        self,
        *,
        config: AnonConfig,
        bus: MessageBus,
        transport: ChannelTransport,
        mapper: OneBotMapper | None = None,
        state: OneBotStateAdapter | None = None,
        context_store: ContextStore | None = None,
        policy: PolicyEngine | None = None,
        presenter: ContextPresenter | None = None,
    ) -> None:
        """初始化内核并保存依赖."""
        self.config = config
        self.bus = bus
        self.transport = transport
        self.state = state or OneBotStateAdapter()
        self.mapper = mapper or OneBotMapper(self_id=self.state.self_id)
        self.context_store = context_store or ContextStore(
            max_messages_per_conversation=config.max_context_messages
        )
        self.policy = policy or PolicyEngine(config)
        self.presenter = presenter or ContextPresenter()
        self._running = False

        binder = getattr(self.transport, "set_inbound_handler", None)
        if callable(binder):
            binder(self.handle_inbound)

    @property
    def is_running(self) -> bool:
        """返回内核是否处于运行中."""
        return self._running

    async def start(self) -> None:
        """启动内核并委托到底层传输层."""
        if self._running:
            return
        if self.config.enabled and not self.config.ws_url:
            raise ValueError("ws_url is required when anon channel is enabled")

        self._running = True
        try:
            await self.transport.start()
        finally:
            self._running = False

    async def stop(self) -> None:
        """停止内核及其底层传输层."""
        if not self._running:
            return
        await self.transport.stop()

    async def handle_inbound(self, message: NormalizedMessage) -> None:
        """处理适配层产出的标准化入站消息."""
        message = self._hydrate_message(message)

        if message.from_self:
            await self.remember_message(message)
            return
        if not self.policy.is_allowed(message):
            return

        await self.remember_message(message)
        command = self.policy.parse_command(message)
        if command is not None:
            await self._publish_inbound(
                message,
                trigger_reason="slash_command",
                command=command,
            )
            return
        decision = self.policy.decide_trigger(
            message,
            context=PolicyContext(self_id=self.state.self_id),
        )
        if not decision.triggered:
            return

        await self._publish_inbound(message, trigger_reason=decision.reason.value)

    async def _publish_inbound(
        self,
        message: NormalizedMessage,
        *,
        trigger_reason: str,
        command: BaseModel | None = None,
    ) -> None:
        """把当前上下文窗口投递到消息总线."""
        presented = self.presenter.present_recent_window(
            self.context_store,
            message.conversation,
            self_id=self.state.self_id,
            max_ctx_length=self.config.max_ctx_length,
            media_max_size_bytes=self.config.media_max_size_mb * 1024 * 1024,
        )
        metadata = {
            "message": message.model_dump(mode="json"),
            "presented": presented.metadata,
            "trigger_reason": trigger_reason,
            "ctx_message_ids": list(presented.metadata.get("message_ids", [])),
            "ctx_count": presented.metadata.get("count", 0),
        }
        if command is not None:
            metadata["command"] = command.model_dump(mode="json")
        await self.bus.publish_inbound(
            InboundMessage(
                channel="anon",
                sender_id=message.sender_id,
                chat_id=message.conversation.key,
                content=presented.text,
                media=[
                    item["url"]
                    for item in presented.media
                    if isinstance(item.get("url"), str) and item["url"]
                ],
                metadata=metadata,
            )
        )
        self.context_store.mark_consumed(message.conversation, message.message_id)

    async def remember_message(self, message: NormalizedMessage) -> None:
        """把消息写入上下文与最小状态."""
        self.context_store.append(message)
        self.state.set_member_profile(
            message.conversation,
            user_id=message.sender_id,
            display_name=message.sender_name,
            nickname=message.sender_name,
        )

    async def send(self, msg: OutboundMessage) -> None:
        """将 nanobot 出站消息映射为 OneBot 请求后发送."""
        request = ChannelSendRequest(
            chat_id=msg.chat_id,
            content=msg.content,
            media=list(msg.media),
            metadata=dict(msg.metadata),
        )
        await self._dispatch_outbound(request)

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """将增量文本按普通文本消息发送."""
        if not delta:
            return
        await self._dispatch_outbound(
            ChannelSendRequest(
                chat_id=chat_id,
                content=delta,
                metadata={} if metadata is None else dict(metadata),
            )
        )

    def _hydrate_message(self, message: NormalizedMessage) -> NormalizedMessage:
        """补全依赖上下文的入站字段."""
        reply_to_self = message.reply_to_self
        if not reply_to_self and message.reply_to_message_id is not None:
            target = self.context_store.get_message(
                message.conversation,
                message.reply_to_message_id,
            )
            reply_to_self = bool(target is not None and target.from_self)
        return message.model_copy(update={"reply_to_self": reply_to_self})

    async def _dispatch_outbound(self, request: ChannelSendRequest) -> None:
        """通过映射层与传输层发出协议请求."""
        requests = self.mapper.map_outbound_request(request)
        sender = self.transport
        if not isinstance(sender, RequestBatchSender):
            raise TypeError("transport does not support send_requests(requests)")
        await sender.send_requests(requests)
