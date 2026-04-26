"""Main runtime orchestrator for the anon channel."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus

from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.adapters.onebot_media import OneBotMediaAdapter
from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import (
    ChannelSendRequest,
    ConversationRef,
    NormalizedMessage,
)
from nanobot_channel_anon.inbound_forward import (
    ForwardMessageFetcher,
)
from nanobot_channel_anon.inbound_forward import (
    InboundForwardEnricher as DefaultInboundForwardEnricher,
)
from nanobot_channel_anon.onebot import (
    OneBotAPIRequest,
    OneBotMessageSegment,
    OneBotRawEvent,
)
from nanobot_channel_anon.policy import PolicyContext, PolicyEngine
from nanobot_channel_anon.presenter import ContextPresenter
from nanobot_channel_anon.utils import normalize_onebot_id, parse_cq_params

_CQ_MEDIA_PATTERN = re.compile(r"\[CQ:([^,\]]+)(?:,([^\]]+))?\]")
_SUPPORTED_INLINE_MEDIA_TYPES = {"image", "record", "video", "file"}


@runtime_checkable
class RequestBatchSender(Protocol):
    """支持批量协议请求发送的传输层扩展."""

    async def send_requests(
        self,
        requests: Sequence[OneBotAPIRequest],
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


@runtime_checkable
class OutboundMediaUploader(Protocol):
    """支持上传本地媒体并返回可发送引用的传输层扩展."""

    async def upload_local_media(self, path: Path) -> str:
        """上传本地媒体文件并返回 NapCat file_path."""
        ...


@runtime_checkable
class GroupMemberInfoFetcher(Protocol):
    """支持按群成员查询资料的传输层扩展."""

    async def get_group_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> dict[str, str] | None:
        """按群和用户 ID 拉取成员资料."""
        ...


@runtime_checkable
class InboundForwardEnricher(Protocol):
    """支持入站 forward 展开的运行时扩展."""

    async def enrich(self, message: NormalizedMessage) -> NormalizedMessage:
        """增强标准化入站消息中的 forward 信息."""
        ...


@runtime_checkable
class InboundMediaEnricher(Protocol):
    """支持入站媒体增强的运行时扩展."""

    async def enrich(self, message: NormalizedMessage) -> NormalizedMessage:
        """增强标准化入站消息中的附件信息."""
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
        inbound_forward_enricher: InboundForwardEnricher | None = None,
        inbound_media_enricher: InboundMediaEnricher | None = None,
    ) -> None:
        """初始化内核并保存依赖."""
        self.config = config
        self.bus = bus
        self.transport = transport
        self.state = state or OneBotStateAdapter()
        self.mapper = mapper or OneBotMapper(self_id=self.state.self_id)
        if isinstance(self.mapper.media, OneBotMediaAdapter):
            self.media = self.mapper.media
        else:
            self.media = OneBotMediaAdapter()
        self.context_store = context_store or ContextStore(
            max_messages_per_conversation=config.max_context_messages
        )
        self.policy = policy or PolicyEngine(config)
        self.presenter = presenter or ContextPresenter()
        if (
            inbound_forward_enricher is None
            and isinstance(transport, ForwardMessageFetcher)
        ):
            inbound_forward_enricher = DefaultInboundForwardEnricher(fetcher=transport)
        self.inbound_forward_enricher = inbound_forward_enricher
        self.inbound_media_enricher = inbound_media_enricher
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
        if message.from_self:
            await self.remember_message(message)
            return
        if not self.policy.is_allowed(message):
            return

        message, reply_target = await self._prepare_inbound_message(message)
        if await self._route_passthrough_slash_command(message):
            return
        if self._should_suppress_message(message):
            return

        await self._remember_and_publish_triggered_inbound(
            message,
            reply_target=reply_target,
        )

    async def _prepare_inbound_message(
        self,
        message: NormalizedMessage,
    ) -> tuple[NormalizedMessage, NormalizedMessage | None]:
        """补全入站消息的上下文相关字段并执行增强."""
        message, reply_target = await self._hydrate_message(message)
        if self.inbound_forward_enricher is not None:
            message = await self.inbound_forward_enricher.enrich(message)
        if self.inbound_media_enricher is not None:
            message = await self.inbound_media_enricher.enrich(message)
        return message, reply_target

    async def _route_passthrough_slash_command(
        self,
        message: NormalizedMessage,
    ) -> bool:
        """按需直通超级管理员斜杠命令."""
        if not self.policy.should_passthrough_slash_command(message):
            return False
        self._update_member_profile(message)
        await self._publish_passthrough_slash_command(message)
        return True

    async def _remember_and_publish_triggered_inbound(
        self,
        message: NormalizedMessage,
        *,
        reply_target: NormalizedMessage | None,
    ) -> None:
        """保留未触发消息上下文, 并在命中策略时发布."""
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
        message = await self._hydrate_display_names(message)
        self.context_store.append(message)
        resolved_extra_messages = [
            await self._hydrate_display_names(item) for item in extra_messages
        ]
        extra_message_ids = {item.message_id for item in resolved_extra_messages}
        presented = self.presenter.present_recent_window(
            self.context_store,
            message.conversation,
            self_id=self.state.self_id,
            self_name=self.state.self_nickname or self.state.self_id,
            max_ctx_length=self.config.max_ctx_length,
            media_max_size_bytes=self.config.media_max_size_mb * 1024 * 1024,
            extra_messages=resolved_extra_messages,
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

    @staticmethod
    def _should_suppress_message(message: NormalizedMessage) -> bool:
        """抑制仅包含不支持入站媒体的消息发布."""
        if message.message_type != "message":
            return False
        drop_reason = message.metadata.get("drop_reason")
        return drop_reason == "unsupported_media_only"

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
            card=message.sender_name if message.conversation.kind == "group" else "",
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
        if message.reply_to_self or message.reply_to_message_id is None:
            return message, reply_target
        reply_target = self.context_store.get_message(
            message.conversation,
            message.reply_to_message_id,
        )
        if reply_target is None:
            reply_target = await self._fetch_reply_target(message)
        if reply_target is None or not reply_target.from_self:
            return message, reply_target
        return message.model_copy(update={"reply_to_self": True}), reply_target

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

    async def _hydrate_display_names(
        self,
        message: NormalizedMessage,
    ) -> NormalizedMessage:
        """在发布 CTX/1 前补齐发送者与 mention 的显示名."""
        sender_name = await self._resolve_display_name(
            message.conversation,
            user_id=message.sender_id,
            fallback_name=message.sender_name,
            is_bot=message.from_self,
        )
        metadata = dict(message.metadata)
        render_segments = metadata.get("render_segments")
        if isinstance(render_segments, list):
            resolved_segments: list[dict[str, object]] = []
            changed = False
            for segment in render_segments:
                if not isinstance(segment, dict):
                    resolved_segments.append(segment)
                    continue
                if segment.get("type") != "mention":
                    resolved_segments.append(segment)
                    continue
                user_id = segment.get("user_id")
                if not isinstance(user_id, str) or not user_id:
                    resolved_segments.append(segment)
                    continue
                resolved_name = await self._resolve_display_name(
                    message.conversation,
                    user_id=user_id,
                    fallback_name=str(segment.get("name") or ""),
                    is_bot=user_id == self.state.self_id,
                )
                updated_segment = dict(segment)
                updated_segment["name"] = resolved_name
                resolved_segments.append(updated_segment)
                changed = changed or updated_segment != segment
            if changed:
                metadata["render_segments"] = resolved_segments
        if sender_name == message.sender_name and metadata == message.metadata:
            return message
        return message.model_copy(
            update={"sender_name": sender_name, "metadata": metadata}
        )

    async def _resolve_display_name(
        self,
        conversation: ConversationRef,
        *,
        user_id: str,
        fallback_name: str,
        is_bot: bool = False,
    ) -> str:
        """按会话类型与本地状态解析最佳显示名."""
        if is_bot and user_id == self.state.self_id:
            return self.state.self_nickname or user_id
        preferred_name = self.state.preferred_name(conversation, user_id)
        if preferred_name:
            return preferred_name
        if conversation.kind == "group":
            fetched_profile = await self._fetch_group_member_profile(
                conversation.id,
                user_id,
            )
            if fetched_profile is not None:
                self.state.set_member_profile(
                    conversation,
                    user_id=user_id,
                    card=fetched_profile.get("card", ""),
                    nickname=fetched_profile.get("nickname", ""),
                )
                preferred_name = self.state.preferred_name(conversation, user_id)
                if preferred_name:
                    return preferred_name
        normalized_fallback = fallback_name.strip()
        if normalized_fallback and normalized_fallback != user_id:
            return normalized_fallback
        return user_id

    async def _fetch_group_member_profile(
        self,
        group_id: str,
        user_id: str,
    ) -> dict[str, str] | None:
        """按需回填群成员资料."""
        fetcher = self.transport
        if not isinstance(fetcher, GroupMemberInfoFetcher):
            return None
        return await fetcher.get_group_member_info(group_id, user_id)

    async def _dispatch_outbound(self, request: ChannelSendRequest) -> None:
        """通过映射层与传输层发出协议请求."""
        resolved_request = await self._resolve_outbound_request_media(request)
        requests = self.mapper.map_outbound_request(resolved_request)
        sender = self.transport
        if not isinstance(sender, RequestBatchSender):
            raise TypeError("transport does not support send_requests(requests)")
        responses = await sender.send_requests(requests)
        self._remember_outbound_messages(requests, responses)

    async def _resolve_outbound_request_media(
        self,
        request: ChannelSendRequest,
    ) -> ChannelSendRequest:
        """在映射前解析并上传本地媒体引用."""
        resolved_media = await self._resolve_outbound_media_refs(request.media)
        resolved_content = await self._resolve_inline_cq_media_refs(request.content)
        if resolved_media == request.media and resolved_content == request.content:
            return request
        return request.model_copy(
            update={"media": resolved_media, "content": resolved_content}
        )

    async def _resolve_outbound_media_refs(
        self,
        media_refs: Sequence[str],
    ) -> list[str]:
        """上传 request.media 中的本地媒体并返回可发送引用."""
        resolved_refs: list[str] = []
        for media_ref in media_refs:
            resolved_ref = await self._resolve_single_outbound_media_ref(media_ref)
            resolved_refs.append(resolved_ref)
        return resolved_refs

    async def _resolve_single_outbound_media_ref(self, media_ref: str) -> str:
        """按单个媒体引用决定是否需要上传."""
        normalized_media_ref = self.media.normalize_outbound_media_ref(media_ref)
        local_path = self.media.local_path_from_media_ref(normalized_media_ref)
        if local_path is None:
            return normalized_media_ref
        uploader = self.transport
        if not isinstance(uploader, OutboundMediaUploader):
            raise TypeError("transport does not support upload_local_media(path)")
        return await uploader.upload_local_media(local_path)

    async def _resolve_inline_cq_media_refs(self, content: str) -> str:
        """上传内联 CQ 媒体里的本地文件引用并重写文本."""
        if not content:
            return content

        parts: list[str] = []
        cursor = 0
        for match in _CQ_MEDIA_PATTERN.finditer(content):
            start, end = match.span()
            parts.append(content[cursor:start])
            parts.append(await self._rewrite_inline_cq_media_match(match))
            cursor = end
        parts.append(content[cursor:])
        return "".join(parts)

    async def _rewrite_inline_cq_media_match(self, match: re.Match[str]) -> str:
        """只重写受支持媒体 CQ 的 file 参数."""
        cq_type = match.group(1).strip().lower()
        params_raw = match.group(2) or ""
        if cq_type not in _SUPPORTED_INLINE_MEDIA_TYPES:
            return match.group(0)
        params = parse_cq_params(params_raw)
        if not params:
            return match.group(0)
        media_ref = params.get("file", "").strip()
        if not media_ref:
            return match.group(0)
        resolved_media_ref = await self._resolve_single_outbound_media_ref(media_ref)
        if resolved_media_ref == media_ref:
            return match.group(0)
        rebuilt_parts: list[str] = []
        replaced = False
        for part in params_raw.split(","):
            key, separator, value = part.partition("=")
            if not separator:
                return match.group(0)
            if not replaced and key.strip() == "file":
                rebuilt_parts.append(f"{key}={resolved_media_ref}")
                replaced = True
                continue
            rebuilt_parts.append(f"{key}={value}")
        if not replaced:
            return match.group(0)
        rebuilt_params = ",".join(rebuilt_parts)
        return f"[CQ:{match.group(1)},{rebuilt_params}]"

    def _remember_outbound_messages(
        self,
        requests: Sequence[OneBotAPIRequest],
        responses: Sequence[OneBotRawEvent],
    ) -> None:
        """把发送成功的机器人出站消息写回上下文."""
        self_id = self.state.self_id
        if self_id is None:
            return

        sender_name = self.state.self_nickname or self.state.self_id or self_id
        for outbound, response in zip(requests, responses, strict=False):
            params = outbound.params
            if not isinstance(params, dict):
                continue
            conversation = self._conversation_from_outbound_action(
                outbound.action,
                params,
            )
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
                card=sender_name if conversation.kind == "group" else "",
                nickname=sender_name,
            )

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
