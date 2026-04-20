"""OneBot v11 入站事件规范化."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.onebot import OneBotMessageSegment, OneBotRawEvent
from nanobot_channel_anon.utils import normalize_onebot_id, string_value

EventKind = Literal["private_message", "group_message", "poke"]

_MEDIA_PLACEHOLDERS = {
    "image": "[image]",
    "video": "[video]",
    "file": "[file]",
    "record": "[voice]",
}


@dataclass(slots=True)
class ForwardRef:
    """一条消息中的 forward 引用."""

    forward_id: str | None = None
    embedded_nodes: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None


@dataclass(slots=True)
class ParsedMessage:
    """规范化后的消息段信息."""

    text: str = ""
    media: list[str] = field(default_factory=list)
    mentioned_self: bool = False
    mentioned_all: bool = False
    reply_to_message_id: str | None = None
    forward_refs: list[ForwardRef] = field(default_factory=list)
    segment_types: list[str] = field(default_factory=list)


@dataclass(slots=True)
class InboundCandidate:
    """供 router 判定是否触发的入站候选事件."""

    sender_id: str
    chat_id: str
    content: str
    media: list[str]
    metadata: dict[str, Any]
    event_kind: EventKind
    mentioned_self: bool = False
    mentioned_all: bool = False
    reply_to_message_id: str | None = None
    forward_refs: list[ForwardRef] = field(default_factory=list)
    session_key: str | None = None


def normalize_inbound_event(
    raw: OneBotRawEvent,
    *,
    config: AnonConfig,
    self_id: str | None,
) -> InboundCandidate | None:
    """把 OneBot 原始事件转换为可供路由判定的候选事件."""
    effective_self_id = normalize_onebot_id(raw.self_id) or self_id
    if raw.post_type == "message":
        return _normalize_message_event(
            raw,
            max_text_length=config.max_text_length,
            self_id=effective_self_id,
        )
    if raw.post_type == "notice":
        return _normalize_notice_event(raw, self_id=effective_self_id)
    return None


def _normalize_message_event(
    raw: OneBotRawEvent,
    *,
    max_text_length: int,
    self_id: str | None,
) -> InboundCandidate | None:
    message_type = raw.message_type
    if message_type not in {"private", "group"}:
        return None

    sender_id = _normalize_sender_id(raw)
    if sender_id is None:
        return None

    group_id = normalize_onebot_id(raw.group_id)
    if message_type == "group":
        if group_id is None:
            return None
        chat_id = _build_group_chat_id(group_id)
        event_kind: EventKind = "group_message"
    else:
        chat_id = _build_private_chat_id(sender_id)
        event_kind = "private_message"

    parsed = _parse_segments(_segments_from_message(raw.message), self_id=self_id)
    content = parsed.text or raw.raw_message.strip()
    content = content[:max_text_length].strip()

    metadata = {
        "event_kind": event_kind,
        "onebot_post_type": raw.post_type,
        "onebot_message_type": message_type,
        "onebot_sub_type": raw.sub_type,
        "message_id": normalize_onebot_id(raw.message_id),
        "user_id": sender_id,
        "group_id": group_id,
        "self_id": self_id,
        "raw_message": raw.raw_message,
        "reply_to_message_id": parsed.reply_to_message_id,
        "mentioned_self": parsed.mentioned_self,
        "mentioned_all": parsed.mentioned_all,
        "forward_refs": [_forward_ref_metadata(ref) for ref in parsed.forward_refs],
        "sender_nickname": raw.sender.nickname if raw.sender is not None else "",
        "sender_card": raw.sender.card if raw.sender is not None else "",
        "segment_types": parsed.segment_types,
        "event_time": raw.time,
    }

    return InboundCandidate(
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
        media=parsed.media,
        metadata=metadata,
        event_kind=event_kind,
        mentioned_self=parsed.mentioned_self,
        mentioned_all=parsed.mentioned_all,
        reply_to_message_id=parsed.reply_to_message_id,
        forward_refs=parsed.forward_refs,
    )


def _normalize_notice_event(
    raw: OneBotRawEvent,
    *,
    self_id: str | None,
) -> InboundCandidate | None:
    if raw.notice_type != "notify" or raw.sub_type != "poke":
        return None

    sender_id = _normalize_sender_id(raw)
    if sender_id is None:
        return None

    group_id = normalize_onebot_id(raw.group_id)
    chat_id = (
        _build_group_chat_id(group_id)
        if group_id is not None
        else _build_private_chat_id(sender_id)
    )

    target_id = normalize_onebot_id(getattr(raw, "target_id", None))
    metadata = {
        "event_kind": "poke",
        "onebot_post_type": raw.post_type,
        "onebot_notice_type": raw.notice_type,
        "onebot_sub_type": raw.sub_type,
        "message_id": normalize_onebot_id(raw.message_id),
        "user_id": sender_id,
        "group_id": group_id,
        "target_id": target_id,
        "self_id": self_id,
        "event_time": raw.time,
    }

    return InboundCandidate(
        sender_id=sender_id,
        chat_id=chat_id,
        content="戳了戳你",
        media=[],
        metadata=metadata,
        event_kind="poke",
    )


def _normalize_sender_id(raw: OneBotRawEvent) -> str | None:
    if raw.sender is not None:
        sender_id = normalize_onebot_id(raw.sender.user_id)
        if sender_id is not None:
            return sender_id
    return normalize_onebot_id(raw.user_id)


def _segments_from_message(
    message: str | list[OneBotMessageSegment] | None,
) -> list[OneBotMessageSegment]:
    if isinstance(message, str):
        return [OneBotMessageSegment(type="text", data={"text": message})]
    if not isinstance(message, list):
        return []

    segments: list[OneBotMessageSegment] = []
    for item in message:
        if isinstance(item, OneBotMessageSegment):
            segments.append(item)
            continue
        segments.append(OneBotMessageSegment.model_validate(item))
    return segments


def _parse_segments(
    segments: list[OneBotMessageSegment],
    *,
    self_id: str | None,
) -> ParsedMessage:
    text_parts: list[str] = []
    media: list[str] = []
    mentioned_self = False
    mentioned_all = False
    reply_to_message_id: str | None = None
    forward_refs: list[ForwardRef] = []
    segment_types: list[str] = []

    for segment in segments:
        segment_types.append(segment.type)
        data = segment.data

        if segment.type == "text":
            text_parts.append(string_value(data.get("text")) or "")
            continue

        if segment.type == "at":
            target_id = string_value(data.get("qq") or data.get("user_id"))
            if target_id == "all":
                mentioned_all = True
            elif self_id is not None and target_id == self_id:
                mentioned_self = True
            continue

        if segment.type == "reply":
            if reply_to_message_id is None:
                reply_to_message_id = normalize_onebot_id(
                    data.get("id") or data.get("message_id")
                )
            continue

        if segment.type == "forward":
            embedded_nodes = [
                item
                for item in _list_value(data.get("content"))
                if isinstance(item, dict)
            ]
            forward_refs.append(
                ForwardRef(
                    forward_id=string_value(data.get("id")),
                    embedded_nodes=embedded_nodes,
                    summary=string_value(data.get("summary"))
                    or string_value(data.get("title")),
                )
            )
            text_parts.append("[forward]")
            continue

        placeholder = _MEDIA_PLACEHOLDERS.get(segment.type)
        if placeholder is None:
            continue

        media_ref = _first_media_ref(data)
        if media_ref is not None:
            media.append(media_ref)
        text_parts.append(placeholder)

    return ParsedMessage(
        text="".join(text_parts).strip(),
        media=media,
        mentioned_self=mentioned_self,
        mentioned_all=mentioned_all,
        reply_to_message_id=reply_to_message_id,
        forward_refs=forward_refs,
        segment_types=segment_types,
    )


def _forward_ref_metadata(ref: ForwardRef) -> dict[str, Any]:
    return {
        "forward_id": ref.forward_id,
        "embedded_nodes": ref.embedded_nodes,
        "summary": ref.summary,
    }


def _first_media_ref(data: dict[str, Any]) -> str | None:
    for key in ("url", "file", "path", "file_id", "name"):
        value = string_value(data.get(key))
        if value is not None:
            return value
    return None



def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _build_private_chat_id(user_id: str) -> str:
    return f"private:{user_id}"


def _build_group_chat_id(group_id: str) -> str:
    return f"group:{group_id}"
