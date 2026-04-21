"""OneBot v11 入站事件规范化."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from nanobot_channel_anon.buffer import (
    Buffer,
    ForwardEntry,
    ForwardNodeEntry,
    MessageEntry,
)
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.onebot import OneBotMessageSegment, OneBotRawEvent
from nanobot_channel_anon.utils import (
    build_group_chat_id,
    build_private_chat_id,
    normalize_onebot_id,
    string_value,
)


def _display_name(*values: str | None) -> str:
    for value in values:
        normalized = string_value(value)
        if normalized is not None:
            return normalized
    return ""


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
    media_items: list[dict[str, Any]] = field(default_factory=list)
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
    reply_target_from_self: bool = False
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
        chat_id = build_group_chat_id(group_id)
        event_kind: EventKind = "group_message"
    else:
        chat_id = build_private_chat_id(sender_id)
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
        "media_items": list(parsed.media_items),
        "sender_nickname": raw.sender.nickname if raw.sender is not None else "",
        "sender_card": raw.sender.card if raw.sender is not None else "",
        "segment_types": parsed.segment_types,
        "event_time": raw.time,
    }

    return InboundCandidate(
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
        media=[],
        metadata=metadata,
        event_kind=event_kind,
        mentioned_self=parsed.mentioned_self,
        mentioned_all=parsed.mentioned_all,
        reply_to_message_id=parsed.reply_to_message_id,
        forward_refs=parsed.forward_refs,
    )


ForwardResolver = Callable[[str], Awaitable[Any]]
ImageDownloader = Callable[[dict[str, Any]], Awaitable[str | None]]
VoiceProcessor = Callable[[dict[str, Any]], Awaitable[dict[str, str] | None]]


@dataclass(slots=True)
class InboundProcessingResult:
    """入站候选事件增强后的结果."""

    candidate: InboundCandidate
    expanded_forwards: list[ForwardEntry] = field(default_factory=list)


def cache_inbound_candidate(
    candidate: InboundCandidate,
    *,
    buffer: Buffer,
    expanded_forwards: list[ForwardEntry] | None = None,
) -> None:
    """将允许的入站消息写入最近消息缓存."""
    _buffer_inbound_message(buffer, candidate, expanded_forwards or [])


async def process_inbound_candidate(
    candidate: InboundCandidate,
    *,
    buffer: Buffer,
    forward_resolver: ForwardResolver,
    image_downloader: ImageDownloader | None = None,
    voice_processor: VoiceProcessor | None = None,
) -> InboundProcessingResult:
    """补充媒体、forward 语义结果, 供调用方写入最近消息缓存."""
    _set_reply_target_from_self(candidate, buffer)
    candidate.media = await _download_candidate_images(candidate, image_downloader)
    await _process_candidate_voices(candidate, voice_processor)
    expanded_forwards = await _expand_candidate_forwards(candidate, forward_resolver)
    candidate.metadata["expanded_forwards"] = [
        _forward_entry_metadata(item) for item in expanded_forwards
    ]
    return InboundProcessingResult(
        candidate=candidate,
        expanded_forwards=expanded_forwards,
    )


async def _download_candidate_images(
    candidate: InboundCandidate,
    image_downloader: ImageDownloader | None,
) -> list[str]:
    if image_downloader is None:
        return []

    media_refs: list[str] = []
    for item in _list_value(candidate.metadata.get("media_items")):
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        media_ref = await image_downloader(item)
        if media_ref is not None:
            media_refs.append(media_ref)
    return media_refs


async def _process_candidate_voices(
    candidate: InboundCandidate,
    voice_processor: VoiceProcessor | None,
) -> None:
    if voice_processor is None:
        return

    replacements: list[str | None] = []
    for item in _list_value(candidate.metadata.get("media_items")):
        if not isinstance(item, dict) or item.get("type") != "record":
            continue

        try:
            result = await voice_processor(item)
        except Exception:
            result = None
        replacement: str | None = None
        if isinstance(result, dict):
            local_file_uri = string_value(result.get("local_file_uri"))
            if local_file_uri is not None:
                item["local_file_uri"] = local_file_uri

            transcription_text = string_value(result.get("transcription_text"))
            if transcription_text is not None:
                item["transcription_text"] = transcription_text
                replacement = f"[transcription: {transcription_text}]"

        replacements.append(replacement)

    if replacements:
        candidate.content = _replace_voice_placeholders(candidate.content, replacements)


async def _expand_candidate_forwards(
    candidate: InboundCandidate,
    forward_resolver: ForwardResolver,
) -> list[ForwardEntry]:
    """展开当前入站消息直接引用的合并转发.

    当前只展开外层 forward 引用, 不递归展开转发节点内部再次出现的 forward。

    NapCat `get_forward_msg` 返回的子消息 `group_id/source` 目前不可靠,
    无法据此稳定恢复转发节点的真实来源会话; 而外层入站消息的 `chat_id`
    只是当前承载这条合并转发的会话, 也不是子节点的真实原始来源。
    因此这里统一保留 `source_chat_id=None`, 明确表达"来源未知"。
    """
    expanded: list[ForwardEntry] = []
    for ref in candidate.forward_refs:
        if ref.embedded_nodes:
            expanded.append(
                _build_forward_entry(
                    forward_id=ref.forward_id,
                    summary=ref.summary,
                    raw_nodes=ref.embedded_nodes,
                )
            )
            continue

        if not ref.forward_id:
            expanded.append(
                _build_forward_entry(
                    forward_id=None,
                    summary=ref.summary,
                    raw_nodes=[],
                    unresolved=True,
                )
            )
            continue

        try:
            response_data = await forward_resolver(ref.forward_id)
            raw_nodes = _extract_forward_nodes(response_data)
            expanded.append(
                _build_forward_entry(
                    forward_id=ref.forward_id,
                    summary=ref.summary,
                    raw_nodes=raw_nodes,
                    unresolved=not raw_nodes,
                )
            )
        except Exception:
            expanded.append(
                _build_forward_entry(
                    forward_id=ref.forward_id,
                    summary=ref.summary,
                    raw_nodes=[],
                    unresolved=True,
                )
            )
    return expanded


def _set_reply_target_from_self(
    candidate: InboundCandidate,
    buffer: Buffer,
) -> None:
    candidate.reply_target_from_self = buffer.is_reply_to_self(
        candidate.chat_id,
        candidate.reply_to_message_id,
    )
    candidate.metadata["reply_target_from_self"] = candidate.reply_target_from_self


def _buffer_inbound_message(
    buffer: Buffer,
    candidate: InboundCandidate,
    expanded_forwards: list[ForwardEntry],
) -> None:
    message_id = normalize_onebot_id(candidate.metadata.get("message_id"))
    if message_id is None:
        return
    buffer.add(
        MessageEntry(
            message_id=message_id,
            chat_id=candidate.chat_id,
            sender_id=candidate.sender_id,
            sender_name=(
                _display_name(
                    candidate.metadata.get("sender_card"),
                    candidate.metadata.get("sender_nickname"),
                    candidate.sender_id,
                )
                if candidate.event_kind == "group_message"
                else _display_name(
                    candidate.metadata.get("sender_nickname"),
                    candidate.sender_id,
                )
            ),
            is_from_self=False,
            content=candidate.content,
            sender_nickname=_display_name(candidate.metadata.get("sender_nickname")),
            sender_card=_display_name(candidate.metadata.get("sender_card")),
            media=list(candidate.media),
            reply_to_message_id=candidate.reply_to_message_id,
            event_time=candidate.metadata.get("event_time"),
            segment_types=list(candidate.metadata.get("segment_types") or []),
            forward_refs=list(candidate.metadata.get("forward_refs") or []),
            expanded_forwards=expanded_forwards,
            metadata=dict(candidate.metadata),
        )
    )


def _forward_entry_metadata(entry: ForwardEntry) -> dict[str, Any]:
    return {
        "forward_id": entry.forward_id,
        "summary": entry.summary,
        "unresolved": entry.unresolved,
        "nodes": [
            {
                "sender_id": node.sender_id,
                "sender_name": node.sender_name,
                "sender_nickname": node.sender_nickname,
                "sender_card": node.sender_card,
                "source_chat_id": node.source_chat_id,
                "content": node.content,
                "message_id": node.message_id,
                "media": list(node.media),
                "reply_to_message_id": node.reply_to_message_id,
                "segment_types": list(node.segment_types),
            }
            for node in entry.nodes
        ],
    }


def _extract_forward_nodes(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("messages", "message", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def _build_forward_entry(
    *,
    forward_id: str | None,
    summary: str | None,
    raw_nodes: list[dict[str, Any]],
    unresolved: bool = False,
) -> ForwardEntry:
    """构建一条展开后的 forward 容器记录."""
    return ForwardEntry(
        forward_id=forward_id,
        summary=summary,
        nodes=[_build_forward_node(node) for node in raw_nodes],
        unresolved=unresolved,
    )


def _build_forward_node(node: dict[str, Any]) -> ForwardNodeEntry:
    """构建单个转发节点记录.

    当前不会尝试从子节点 payload 推断 `source_chat_id`。
    原因是 NapCat `get_forward_msg` 返回里的 `group_id/source` 已确认可能是脏数据,
    而外层入站消息 `chat_id` 也只是承载转发的当前会话, 不是子节点真实来源。
    因此这里统一写入 `None`, 表示来源未知。
    """
    raw_data = node.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else node

    sender = data.get("sender")
    sender_id = normalize_onebot_id(
        data.get("user_id") or data.get("uin") or _dict_get(sender, "user_id")
    )
    sender_nickname = _display_name(
        data.get("nickname"),
        data.get("name"),
        _dict_get(sender, "nickname"),
    )
    sender_card = _display_name(data.get("card"), _dict_get(sender, "card"))
    sender_name = _display_name(sender_card, sender_nickname, sender_id)

    content_source = data.get("content")
    if content_source is None:
        content_source = data.get("message")

    parsed = _parse_forward_message_segments(content_source)
    content = parsed.text or string_value(data.get("raw_message")) or ""

    return ForwardNodeEntry(
        sender_id=sender_id,
        sender_name=sender_name,
        source_chat_id=None,
        content=content,
        sender_nickname=sender_nickname,
        sender_card=sender_card,
        message_id=normalize_onebot_id(data.get("message_id") or data.get("id")),
        media=parsed.media,
        reply_to_message_id=parsed.reply_to_message_id,
        segment_types=parsed.segment_types,
    )


def _parse_forward_message_segments(message: Any) -> ParsedMessage:
    text_parts: list[str] = []
    media: list[str] = []
    reply_to_message_id: str | None = None
    segment_types: list[str] = []

    for segment in _segments_from_message(message):
        segment_types.append(segment.type)
        data = segment.data

        if segment.type == "text":
            text_parts.append(string_value(data.get("text")) or "")
            continue

        if segment.type == "reply":
            if reply_to_message_id is None:
                reply_to_message_id = normalize_onebot_id(
                    data.get("id") or data.get("message_id")
                )
            continue

        if segment.type == "forward":
            # 转发节点里的嵌套合并转发当前仅保留占位, 不继续递归展开。
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
        reply_to_message_id=reply_to_message_id,
        segment_types=segment_types,
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
        build_group_chat_id(group_id)
        if group_id is not None
        else build_private_chat_id(sender_id)
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
    media_items: list[dict[str, Any]] = []
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

        media_item = _build_media_item(segment.type, data)
        if media_item is not None:
            media_items.append(media_item)
        text_parts.append(placeholder)

    return ParsedMessage(
        text="".join(text_parts).strip(),
        media_items=media_items,
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


def _build_media_item(segment_type: str, data: dict[str, Any]) -> dict[str, Any] | None:
    item = {"type": segment_type}
    for key in ("file", "url", "file_size"):
        value = string_value(data.get(key))
        if value is not None:
            item[key] = value
    return item if len(item) > 1 else None


def _first_media_ref(data: dict[str, Any]) -> str | None:
    for key in ("url", "file", "path", "file_id", "name"):
        value = string_value(data.get(key))
        if value is not None:
            return value
    return None


def _replace_voice_placeholders(content: str, replacements: list[str | None]) -> str:
    updated = content
    for replacement in replacements:
        if "[voice]" not in updated:
            break
        updated = updated.replace("[voice]", replacement or "[voice]", 1)
    return updated



def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []




def _dict_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


