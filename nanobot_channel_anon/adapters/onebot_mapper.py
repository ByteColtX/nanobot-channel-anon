"""OneBot protocol mapper for adapter-layer boundaries."""

from __future__ import annotations

import re
from typing import Any

from nanobot_channel_anon.adapters.onebot_media import OneBotMediaAdapter
from nanobot_channel_anon.domain import (
    Attachment,
    ChannelSendRequest,
    ConversationRef,
    ForwardNode,
    ForwardRef,
    NormalizedMessage,
)
from nanobot_channel_anon.onebot import (
    OneBotAPIRequest,
    OneBotMessageSegment,
    OneBotRawEvent,
)
from nanobot_channel_anon.utils import (
    attachment_placeholder,
    normalize_onebot_id,
    normalize_scalar_string,
    parse_cq_params,
)

_INLINE_CQ_PATTERN = re.compile(r"\[CQ:([^,\]]+)(?:,([^\]]+))?\]")
_VALID_NUMERIC_ID_PATTERN = re.compile(r"^\d+$")
_FORWARD_CONTENT_MEDIA_ADAPTER = OneBotMediaAdapter()



def _parse_chat_id(chat_id: str) -> tuple[str, int]:
    """解析标准 chat_id 为会话类型与数值 ID."""
    normalized_chat_id = chat_id.strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required")

    prefix, separator, raw_target_id = normalized_chat_id.partition(":")
    if not separator:
        raise ValueError("chat_id must use group:<id> or private:<id>")
    if prefix not in {"group", "private"}:
        raise ValueError(f"unsupported chat_id prefix: {prefix}")

    normalized_target_id = raw_target_id.strip()
    if not normalized_target_id:
        raise ValueError("chat_id target is required")

    try:
        target_id = int(normalized_target_id)
    except ValueError as exc:
        raise ValueError(
            f"chat_id target must be numeric: {normalized_target_id}"
        ) from exc

    return prefix, target_id



def _build_inline_cq_segment(
    cq_type: str,
    params_raw: str,
    media: OneBotMediaAdapter,
) -> OneBotMessageSegment | None:
    """把受支持的内联 CQ 构造成 OneBot 段."""
    normalized_type = cq_type.strip().lower()
    params = parse_cq_params(params_raw)
    if normalized_type == "at":
        qq = params.get("qq", "").strip()
        if qq == "all" or _VALID_NUMERIC_ID_PATTERN.fullmatch(qq):
            return OneBotMessageSegment(type="at", data={"qq": qq})
        return None
    if normalized_type == "reply":
        message_id = params.get("id", "").strip()
        if _VALID_NUMERIC_ID_PATTERN.fullmatch(message_id):
            return OneBotMessageSegment(type="reply", data={"id": message_id})
        return None
    if normalized_type in {"image", "record", "video", "file"}:
        media_ref = params.get("file", "").strip()
        if not media_ref:
            return None
        return OneBotMessageSegment(
            type=normalized_type,
            data={"file": media.normalize_outbound_media_ref(media_ref)},
        )
    return None



def _parse_inline_cq_content(
    content: str,
    media: OneBotMediaAdapter,
) -> tuple[OneBotMessageSegment | None, list[OneBotMessageSegment]]:
    """解析文本中的内联 CQ 为回复头与消息体段."""
    if not content:
        return None, []

    reply_segment: OneBotMessageSegment | None = None
    body_segments: list[OneBotMessageSegment] = []
    cursor = 0
    pending_text = ""

    for match in _INLINE_CQ_PATTERN.finditer(content):
        start, end = match.span()
        pending_text += content[cursor:start]
        cq_segment = _build_inline_cq_segment(
            match.group(1),
            match.group(2) or "",
            media,
        )
        if cq_segment is None:
            pending_text += match.group(0)
        else:
            if pending_text:
                body_segments.append(
                    OneBotMessageSegment(type="text", data={"text": pending_text})
                )
                pending_text = ""
            if cq_segment.type == "reply" and reply_segment is None:
                reply_segment = cq_segment
            elif cq_segment.type != "reply":
                body_segments.append(cq_segment)
        cursor = end

    pending_text += content[cursor:]
    if pending_text:
        body_segments.append(
            OneBotMessageSegment(type="text", data={"text": pending_text})
        )
    return reply_segment, body_segments


class OneBotMapper:
    """把 OneBot 原始载荷转换为平台无关领域对象."""

    def __init__(
        self,
        *,
        self_id: str | None = None,
        media: OneBotMediaAdapter | None = None,
    ) -> None:
        """初始化映射器."""
        self.self_id = self_id
        self.media = media or OneBotMediaAdapter()

    def map_inbound_event(self, raw: OneBotRawEvent) -> NormalizedMessage | None:
        """把 OneBot 入站事件映射为标准化消息."""
        effective_self_id = normalize_onebot_id(raw.self_id) or self.self_id
        if raw.post_type == "message":
            return self._map_message_event(raw, self_id=effective_self_id)
        if raw.post_type == "notice":
            return self._map_notice_event(raw, self_id=effective_self_id)
        return None

    def map_outbound_request(
        self,
        request: ChannelSendRequest,
    ) -> list[OneBotAPIRequest]:
        """把标准化出站请求映射为一个或多个 OneBot 动作请求."""
        target_kind, target_id = _parse_chat_id(request.chat_id)
        action = "send_group_msg" if target_kind == "group" else "send_private_msg"
        id_key = "group_id" if target_kind == "group" else "user_id"

        inline_reply_segment, content_segments = _parse_inline_cq_content(
            request.content,
            self.media,
        )
        reply_to_message_id = normalize_scalar_string(
            request.metadata.get("reply_to_message_id")
        )
        reply_segment = (
            OneBotMessageSegment(type="reply", data={"id": reply_to_message_id})
            if reply_to_message_id is not None
            else inline_reply_segment
        )

        image_segments: list[OneBotMessageSegment] = []
        standalone_media_segments: list[OneBotMessageSegment] = []
        for media_ref in request.media:
            attachment = self.media.prepare_outbound_attachment_from_media_ref(
                media_ref
            )
            attachment_kind = self.media.classify_segment_type(attachment.type)
            if self.media.is_mixed_message_media(attachment_kind):
                image_segments.append(attachment)
                continue
            standalone_media_segments.append(attachment)

        actions: list[OneBotAPIRequest] = []
        for attachment in standalone_media_segments:
            actions.append(
                self._build_outbound_action(
                    action=action,
                    id_key=id_key,
                    target_id=target_id,
                    segments=self._with_optional_reply([attachment], reply_segment),
                )
            )
            reply_segment = None

        mixed_segments = [*image_segments]
        for segment in content_segments:
            attachment_kind = self.media.classify_segment_type(segment.type)
            if attachment_kind == "unknown":
                mixed_segments.append(segment)
                continue
            if self.media.is_mixed_message_media(attachment_kind):
                mixed_segments.append(segment)
                continue
            if mixed_segments:
                actions.append(
                    self._build_outbound_action(
                        action=action,
                        id_key=id_key,
                        target_id=target_id,
                        segments=self._with_optional_reply(
                            mixed_segments,
                            reply_segment,
                        ),
                    )
                )
                mixed_segments = []
                reply_segment = None
            actions.append(
                self._build_outbound_action(
                    action=action,
                    id_key=id_key,
                    target_id=target_id,
                    segments=self._with_optional_reply([segment], reply_segment),
                )
            )
            reply_segment = None

        if mixed_segments:
            actions.append(
                self._build_outbound_action(
                    action=action,
                    id_key=id_key,
                    target_id=target_id,
                    segments=self._with_optional_reply(mixed_segments, reply_segment),
                )
            )

        return actions

    def _map_message_event(
        self,
        raw: OneBotRawEvent,
        *,
        self_id: str | None,
    ) -> NormalizedMessage | None:
        if raw.message_type not in {"private", "group"}:
            return None

        sender_id = self._sender_id(raw)
        if sender_id is None:
            return None

        if raw.message_type == "group":
            group_id = normalize_onebot_id(raw.group_id)
            if group_id is None:
                return None
            conversation = ConversationRef(kind="group", id=group_id, title="")
        else:
            conversation = ConversationRef(kind="private", id=sender_id)

        parsed = self._parse_segments(raw.message, self_id=self_id)
        message_id = normalize_onebot_id(raw.message_id)
        if message_id is None:
            return None

        sender_name = self._sender_name(raw)
        return NormalizedMessage(
            message_id=message_id,
            conversation=conversation,
            sender_id=sender_id,
            sender_name=sender_name,
            content=parsed["content"],
            from_self=self_id is not None and sender_id == self_id,
            message_type="message",
            mentioned_self=parsed["mentioned_self"],
            reply_to_self=False,
            reply_to_message_id=parsed["reply_to_message_id"],
            attachments=parsed["attachments"],
            metadata={
                "onebot_post_type": raw.post_type,
                "onebot_message_type": raw.message_type,
                "onebot_sub_type": raw.sub_type,
                "group_id": normalize_onebot_id(raw.group_id),
                "event_time": raw.time,
                "segment_types": parsed["segment_types"],
                "forwards": parsed["forwards"],
                "render_segments": parsed["render_segments"],
            },
        )

    def _map_notice_event(
        self,
        raw: OneBotRawEvent,
        *,
        self_id: str | None,
    ) -> NormalizedMessage | None:
        if raw.notice_type != "notify" or raw.sub_type != "poke":
            return None

        sender_id = self._sender_id(raw)
        if sender_id is None:
            return None

        group_id = normalize_onebot_id(raw.group_id)
        target_id = normalize_onebot_id(raw.target_id)
        conversation = ConversationRef(
            kind="group" if group_id is not None else "private",
            id=group_id or sender_id,
        )
        return NormalizedMessage(
            message_id=self._notice_message_id(
                raw,
                sender_id=sender_id,
                group_id=group_id,
                target_id=target_id,
            ),
            conversation=conversation,
            sender_id=sender_id,
            sender_name=sender_id,
            content="",
            from_self=self_id is not None and sender_id == self_id,
            message_type="poke",
            metadata={
                "onebot_post_type": raw.post_type,
                "onebot_notice_type": raw.notice_type,
                "onebot_sub_type": raw.sub_type,
                "group_id": group_id,
                "target_id": target_id,
                "event_time": raw.time,
            },
        )

    def _parse_segments(
        self,
        message: str | list[OneBotMessageSegment] | None,
        *,
        self_id: str | None,
    ) -> dict[str, Any]:
        segments = self._segments_from_message(message)
        content_parts: list[str] = []
        attachments = []
        mentioned_self = False
        reply_to_message_id: str | None = None
        segment_types: list[str] = []
        forwards: list[dict[str, Any]] = []
        render_segments: list[dict[str, str]] = []

        for segment in segments:
            segment_types.append(segment.type)
            if segment.type == "text":
                raw_text = segment.data.get("text")
                text = (
                    raw_text
                    if isinstance(raw_text, str)
                    else normalize_scalar_string(raw_text) or ""
                )
                content_parts.append(text)
                render_segments.append({"type": "text", "text": text})
                continue
            if segment.type == "at":
                target_id = normalize_scalar_string(
                    segment.data.get("qq") or segment.data.get("user_id")
                )
                if self_id is not None and target_id == self_id:
                    mentioned_self = True
                mention_segment = self._mention_render_segment(segment)
                if mention_segment is not None:
                    render_segments.append(mention_segment)
                continue
            if segment.type == "reply":
                if reply_to_message_id is None:
                    reply_to_message_id = normalize_onebot_id(
                        segment.data.get("id") or segment.data.get("message_id")
                    )
                continue
            if segment.type == "forward":
                forwards.append(self._forward_ref_from_segment(segment))
                content_parts.append("[forward]")
                render_segments.append({"type": "forward"})
                continue

            attachment = self.media.extract_inbound_attachment(segment)
            if attachment is not None:
                attachments.append(attachment)
                placeholder = attachment_placeholder(attachment.kind)
                content_parts.append(placeholder)
                render_segments.append({"type": attachment.kind})

        return {
            "content": "".join(content_parts).strip(),
            "attachments": attachments,
            "mentioned_self": mentioned_self,
            "reply_to_message_id": reply_to_message_id,
            "segment_types": segment_types,
            "forwards": forwards,
            "render_segments": render_segments,
        }

    @staticmethod
    def _mention_render_segment(
        segment: OneBotMessageSegment,
    ) -> dict[str, str] | None:
        target_id = normalize_scalar_string(
            segment.data.get("qq") or segment.data.get("user_id")
        )
        if target_id is None:
            return None
        if target_id == "all":
            return {"type": "mention_all"}
        return {
            "type": "mention",
            "user_id": target_id,
            "name": normalize_scalar_string(segment.data.get("name")) or "",
        }

    @staticmethod
    def _forward_ref_from_segment(segment: OneBotMessageSegment) -> dict[str, Any]:
        forward_data = segment.data
        nodes_data = forward_data.get("content")
        nodes: list[ForwardNode] = []
        if isinstance(nodes_data, list):
            for raw_node in nodes_data:
                if not isinstance(raw_node, dict):
                    continue
                nodes.append(OneBotMapper.build_forward_node(raw_node))
        return ForwardRef(
            forward_id=normalize_scalar_string(
                forward_data.get("id") or forward_data.get("forward_id")
            ),
            summary=normalize_scalar_string(forward_data.get("summary")) or "",
            nodes=nodes,
        ).model_dump(exclude_none=True)

    @staticmethod
    def build_forward_node(node: dict[str, Any]) -> ForwardNode:
        """构建一条可渲染的浅层转发节点."""
        raw_node = OneBotMapper._unwrap_forward_node(node)
        content_source = OneBotMapper._forward_content_source(node, raw_node)
        text_content, attachments = OneBotMapper._parse_forward_content(
            content_source
        )
        outer_sender_raw = node.get("sender")
        outer_sender: dict[str, Any] = (
            outer_sender_raw if isinstance(outer_sender_raw, dict) else {}
        )
        inner_sender_raw = raw_node.get("sender")
        inner_sender: dict[str, Any] = (
            inner_sender_raw if isinstance(inner_sender_raw, dict) else {}
        )
        sender_id = normalize_onebot_id(
            outer_sender.get("user_id")
            or node.get("user_id")
            or node.get("uin")
            or node.get("sender_id")
            or inner_sender.get("user_id")
            or raw_node.get("user_id")
            or raw_node.get("uin")
            or raw_node.get("sender_id")
        )
        sender_name = (
            normalize_scalar_string(outer_sender.get("card"))
            or normalize_scalar_string(outer_sender.get("nickname"))
            or normalize_scalar_string(node.get("nickname"))
            or normalize_scalar_string(node.get("name"))
            or normalize_scalar_string(node.get("sender_name"))
            or normalize_scalar_string(inner_sender.get("card"))
            or normalize_scalar_string(inner_sender.get("nickname"))
            or normalize_scalar_string(raw_node.get("nickname"))
            or normalize_scalar_string(raw_node.get("name"))
            or normalize_scalar_string(raw_node.get("sender_name"))
            or sender_id
            or ""
        )
        return ForwardNode(
            sender_id=sender_id or "",
            sender_name=sender_name,
            message_id=normalize_onebot_id(
                node.get("message_id")
                or node.get("id")
                or raw_node.get("message_id")
                or raw_node.get("id")
            ),
            reply_to_message_id=OneBotMapper._forward_reply_to_message_id(
                content_source
            ),
            content=text_content,
            attachments=attachments,
        )

    @staticmethod
    def _unwrap_forward_node(node: dict[str, Any]) -> dict[str, Any]:
        raw_data = node.get("data")
        if isinstance(raw_data, dict):
            return raw_data
        return node

    @staticmethod
    def _forward_content_source(
        node: dict[str, Any],
        raw_node: dict[str, Any],
    ) -> Any:
        if node.get("content") is not None:
            return node.get("content")
        if node.get("message") is not None:
            return node.get("message")
        if raw_node.get("content") is not None:
            return raw_node.get("content")
        return raw_node.get("message")

    @staticmethod
    def _forward_reply_to_message_id(content: Any) -> str | None:
        for segment in OneBotMapper._segments_from_message(content):
            if segment.type != "reply":
                continue
            return normalize_onebot_id(
                segment.data.get("id") or segment.data.get("message_id")
            )
        return None

    @staticmethod
    def _parse_forward_content(content: Any) -> tuple[str, list[Attachment]]:
        if content is None:
            return "", []
        if isinstance(content, str):
            return content.strip(), []
        if not isinstance(content, list):
            return normalize_scalar_string(content) or "", []

        parts: list[str] = []
        attachments: list[Attachment] = []
        for item in content:
            if isinstance(item, OneBotMessageSegment):
                segment = item
            elif isinstance(item, dict):
                segment = OneBotMessageSegment.model_validate(item)
            else:
                continue
            if segment.type == "text":
                text = normalize_scalar_string(segment.data.get("text")) or ""
                parts.append(text)
                continue
            if segment.type == "reply":
                continue
            if segment.type == "forward":
                parts.append("[forward]")
                continue
            attachment = _FORWARD_CONTENT_MEDIA_ADAPTER.extract_inbound_attachment(
                segment
            )
            if attachment is None:
                continue
            attachments.append(attachment)
            parts.append(attachment_placeholder(attachment.kind))
        return "".join(parts).strip(), attachments

    @staticmethod
    def _segments_from_message(
        message: str | list[OneBotMessageSegment] | None,
    ) -> list[OneBotMessageSegment]:
        if isinstance(message, str):
            return [OneBotMessageSegment(type="text", data={"text": message})]
        if not isinstance(message, list):
            return []
        return [
            item
            if isinstance(item, OneBotMessageSegment)
            else OneBotMessageSegment.model_validate(item)
            for item in message
        ]

    @staticmethod
    def _build_outbound_action(
        *,
        action: str,
        id_key: str,
        target_id: int,
        segments: list[OneBotMessageSegment],
    ) -> OneBotAPIRequest:
        return OneBotAPIRequest(
            action=action,
            params={
                id_key: target_id,
                "message": [
                    segment.model_dump(exclude_none=True) for segment in segments
                ],
            },
        )

    @staticmethod
    def _with_optional_reply(
        segments: list[OneBotMessageSegment],
        reply_segment: OneBotMessageSegment | None,
    ) -> list[OneBotMessageSegment]:
        if reply_segment is None:
            return segments
        return [reply_segment, *segments]

    @staticmethod
    def _display_name(value: Any) -> str:
        return normalize_scalar_string(value) or ""

    def _sender_id(self, raw: OneBotRawEvent) -> str | None:
        if raw.sender is not None:
            sender_id = normalize_onebot_id(raw.sender.user_id)
            if sender_id is not None:
                return sender_id
        return normalize_onebot_id(raw.user_id)

    def _sender_name(self, raw: OneBotRawEvent) -> str:
        if raw.sender is None:
            return self._sender_id(raw) or ""
        return (
            self._display_name(raw.sender.card)
            or self._display_name(raw.sender.nickname)
            or self._sender_id(raw)
            or ""
        )

    @staticmethod
    def _notice_message_id(
        raw: OneBotRawEvent,
        *,
        sender_id: str,
        group_id: str | None,
        target_id: str | None,
    ) -> str:
        message_id = normalize_onebot_id(raw.message_id)
        if message_id is not None:
            return message_id
        event_time = normalize_scalar_string(raw.time) or "0"
        chat_scope = group_id or "private"
        target_part = target_id or "unknown"
        return ":".join(
            (
                "notice",
                raw.sub_type or raw.notice_type or "event",
                event_time,
                chat_scope,
                sender_id,
                target_part,
            )
        )

