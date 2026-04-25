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
from nanobot_channel_anon.domain import (
    ChannelSendRequest,
    ConversationRef,
    NormalizedMessage,
)
from nanobot_channel_anon.onebot import OneBotMessageSegment, OneBotRawEvent
from nanobot_channel_anon.policy import PolicyContext, PolicyEngine
from nanobot_channel_anon.presenter import ContextPresenter
from nanobot_channel_anon.utils import normalize_onebot_id


@runtime_checkable
class RequestBatchSender(Protocol):
    """支持批量协议请求发送的传输层扩展."""

    async def send_requests(
        self,
        requests: Sequence[BaseModel],
    ) -> Sequence[OneBotRawEvent]:
        """发送一批协议层出站请求并返回响应."""
        ...


class ChannelTransport(Protocol):
    """内核依赖的传输层协议."""

    async def start(self) -> None:
        """启动传输层并保持运行."""

    async def stop(self) -> None:
        """停止传输层."""


@runtime_checkable
class ReplyMessageFetcher(Protocol):
    """支持按消息 ID 回查协议层消息的传输层扩展."""

    async def get_message(self, message_id: str) -> OneBotRawEvent | None:
        """按 OneBot message_id 拉取原始消息响应."""
        ...


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
        message, reply_target = await self._hydrate_message(message)

        if message.from_self:
            await self.remember_message(message)
            return
        if not self.policy.is_allowed(message):
            return

        if self.policy.should_passthrough_slash_command(message):
            self._update_member_profile(message)
            await self._publish_passthrough_slash_command(message)
            return

        await self.remember_message(message)
        decision = self.policy.decide_trigger(
            message,
            context=PolicyContext(self_id=self.state.self_id),
        )
        if not decision.triggered:
            return

        await self._publish_inbound(
            message,
            trigger_reason=decision.reason.value,
            extra_messages=[] if reply_target is None else [reply_target],
        )

    async def _publish_inbound(
        self,
        message: NormalizedMessage,
        *,
        trigger_reason: str,
        extra_messages: Sequence[NormalizedMessage] = (),
    ) -> None:
        """把当前上下文窗口投递到消息总线."""
        extra_message_ids = {item.message_id for item in extra_messages}
        presented = self.presenter.present_recent_window(
            self.context_store,
            message.conversation,
            self_id=self.state.self_id,
            max_ctx_length=self.config.max_ctx_length,
            media_max_size_bytes=self.config.media_max_size_mb * 1024 * 1024,
            extra_messages=list(extra_messages),
        )
        ctx_message_ids = [
            message_id
            for message_id in presented.metadata.get("message_ids", [])
            if message_id not in extra_message_ids
        ]
        metadata = {
            "message": message.model_dump(mode="json"),
            "presented": presented.metadata,
            "trigger_reason": trigger_reason,
            "ctx_message_ids": ctx_message_ids,
            "ctx_count": len(ctx_message_ids),
        }
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

    async def _publish_passthrough_slash_command(
        self,
        message: NormalizedMessage,
    ) -> None:
        """把超级管理员的已知斜杠命令按原文直通总线."""
        await self.bus.publish_inbound(
            InboundMessage(
                channel="anon",
                sender_id=message.sender_id,
                chat_id=message.conversation.key,
                content=message.content,
                media=[],
                metadata={
                    "message": message.model_dump(mode="json"),
                    "trigger_reason": "slash_command",
                    "ctx_message_ids": [],
                    "ctx_count": 0,
                },
            )
        )

    async def remember_message(self, message: NormalizedMessage) -> None:
        """把消息写入上下文与最小状态."""
        self.context_store.append(message)
        self._update_member_profile(message)

    def _update_member_profile(self, message: NormalizedMessage) -> None:
        """更新发送者在当前会话中的资料快照."""
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

    async def _hydrate_message(
        self,
        message: NormalizedMessage,
    ) -> tuple[NormalizedMessage, NormalizedMessage | None]:
        """补全依赖上下文的入站字段."""
        reply_target: NormalizedMessage | None = None
        reply_to_self = message.reply_to_self
        if not reply_to_self and message.reply_to_message_id is not None:
            reply_target = self.context_store.get_message(
                message.conversation,
                message.reply_to_message_id,
            )
            if reply_target is None:
                reply_target = await self._fetch_reply_target(message)
            reply_to_self = bool(reply_target is not None and reply_target.from_self)
        return message.model_copy(update={"reply_to_self": reply_to_self}), reply_target

    async def _fetch_reply_target(
        self,
        message: NormalizedMessage,
    ) -> NormalizedMessage | None:
        """在本地上下文缺失时回查被回复的目标消息."""
        reply_to_message_id = message.reply_to_message_id
        if reply_to_message_id is None:
            return None
        fetcher = self.transport
        if not isinstance(fetcher, ReplyMessageFetcher):
            return None
        raw = await fetcher.get_message(reply_to_message_id)
        if raw is None:
            return None
        target = self.mapper.map_inbound_event(raw)
        if target is None:
            return None
        if target.conversation != message.conversation:
            return None
        return target

    async def _dispatch_outbound(self, request: ChannelSendRequest) -> None:
        """通过映射层与传输层发出协议请求."""
        requests = self.mapper.map_outbound_request(request)
        sender = self.transport
        if not isinstance(sender, RequestBatchSender):
            raise TypeError("transport does not support send_requests(requests)")
        responses = await sender.send_requests(requests)
        self._remember_outbound_messages(requests, responses)

    def _remember_outbound_messages(
        self,
        requests: Sequence[BaseModel],
        responses: Sequence[OneBotRawEvent],
    ) -> None:
        """把发送成功的机器人出站消息写回上下文."""
        self_id = self.state.self_id
        if self_id is None:
            return

        sender_name = self.state.self_nickname or self.state.self_id or self_id
        for outbound, response in zip(requests, responses, strict=False):
            action = self._base_model_string_field(outbound, "action")
            if action is None:
                continue
            params = self._base_model_dict_field(outbound, "params")
            if params is None:
                continue
            conversation = self._conversation_from_outbound_action(action, params)
            if conversation is None:
                continue

            message_id = self._message_id_from_response(response)
            if message_id is None:
                continue

            content, reply_to_message_id = self._outbound_message_text_and_reply(params)
            self.context_store.append(
                NormalizedMessage(
                    message_id=message_id,
                    conversation=conversation,
                    sender_id=self_id,
                    sender_name=sender_name,
                    content=content,
                    from_self=True,
                    reply_to_message_id=reply_to_message_id,
                )
            )
            self.state.set_member_profile(
                conversation,
                user_id=self_id,
                display_name=sender_name,
                nickname=sender_name,
            )

    @staticmethod
    def _base_model_string_field(model: BaseModel, field_name: str) -> str | None:
        value = getattr(model, field_name, None)
        return value if isinstance(value, str) else None

    @staticmethod
    def _base_model_dict_field(
        model: BaseModel,
        field_name: str,
    ) -> dict[str, object] | None:
        value = getattr(model, field_name, None)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _conversation_from_outbound_action(
        action: str,
        params: dict[str, object],
    ) -> ConversationRef | None:
        if action == "send_group_msg":
            group_id = normalize_onebot_id(params.get("group_id"))
            if group_id is None:
                return None
            return ConversationRef(kind="group", id=group_id)
        if action == "send_private_msg":
            user_id = normalize_onebot_id(params.get("user_id"))
            if user_id is None:
                return None
            return ConversationRef(kind="private", id=user_id)
        return None

    @staticmethod
    def _message_id_from_response(response: OneBotRawEvent) -> str | None:
        if not isinstance(response.data, dict):
            return None
        return normalize_onebot_id(response.data.get("message_id"))

    @staticmethod
    def _outbound_message_text_and_reply(
        params: dict[str, object],
    ) -> tuple[str, str | None]:
        raw_message = params.get("message")
        if isinstance(raw_message, str):
            return raw_message, None
        if not isinstance(raw_message, list):
            return "", None

        text_parts: list[str] = []
        reply_to_message_id: str | None = None
        for item in raw_message:
            segment = OneBotMessageSegment.model_validate(item)
            if segment.type == "reply" and reply_to_message_id is None:
                reply_to_message_id = normalize_onebot_id(
                    segment.data.get("id") or segment.data.get("message_id")
                )
                continue
            if segment.type == "text":
                text = segment.data.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        return "".join(text_parts).strip(), reply_to_message_id
