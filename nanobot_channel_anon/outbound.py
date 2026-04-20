"""OneBot v11 outbound helpers."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from nanobot_channel_anon.onebot import OneBotMessageSegment

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}


def build_send_request(
    chat_id: str,
    content: str,
    *,
    media: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the OneBot action and params for an outbound message."""
    target_kind, target_id = _parse_chat_target(chat_id)
    action = "send_group_msg" if target_kind == "group" else "send_private_msg"
    id_key = "group_id" if target_kind == "group" else "user_id"
    segments = build_message_segments(content, media=media or [], metadata=metadata)
    return action, {
        id_key: target_id,
        "message": [segment.model_dump(exclude_none=True) for segment in segments],
    }


def build_message_segments(
    content: str,
    *,
    media: list[str],
    metadata: dict[str, Any] | None = None,
) -> list[OneBotMessageSegment]:
    """Build OneBot segments for text and file:// media outbound."""
    segments = [
        *_build_reply_placeholder_segments(metadata),
        *_build_mention_placeholder_segments(metadata),
    ]

    for media_ref in media:
        segments.append(
            OneBotMessageSegment(
                type=_guess_media_segment_type(media_ref),
                data={"file": _normalize_media_ref(media_ref)},
            )
        )

    if content:
        segments.append(OneBotMessageSegment(type="text", data={"text": content}))

    return segments


def _parse_chat_target(chat_id: str) -> tuple[str, int]:
    normalized_chat_id = chat_id.strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required")

    prefix, separator, raw_target_id = normalized_chat_id.partition(":")
    if not separator:
        raise ValueError("chat_id must use group:<id> or private:<id>")
    if prefix not in {"group", "private"}:
        raise ValueError(f"unsupported chat_id prefix: {prefix}")

    target_id = _parse_target_id(raw_target_id)
    return prefix, target_id


def _parse_target_id(value: str) -> int:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("chat_id target is required")
    try:
        return int(normalized_value)
    except ValueError as exc:
        raise ValueError(f"chat_id target must be numeric: {normalized_value}") from exc


def _build_reply_placeholder_segments(
    metadata: dict[str, Any] | None,
) -> list[OneBotMessageSegment]:
    del metadata
    return []


def _build_mention_placeholder_segments(
    metadata: dict[str, Any] | None,
) -> list[OneBotMessageSegment]:
    del metadata
    return []


def _guess_media_segment_type(media_ref: str) -> str:
    suffix = PurePosixPath(_media_path_from_file_uri(media_ref)).suffix.lower()
    if suffix in _IMAGE_EXTENSIONS:
        return "image"
    if suffix in _VIDEO_EXTENSIONS:
        return "video"
    if suffix in _AUDIO_EXTENSIONS:
        return "record"
    return "file"


def _normalize_media_ref(media_ref: str) -> str:
    normalized_media_ref = media_ref.strip()
    if not normalized_media_ref:
        raise ValueError("media ref is required")
    if not normalized_media_ref.startswith("file://"):
        raise ValueError("media refs must use file:// URIs")
    _media_path_from_file_uri(normalized_media_ref)
    return normalized_media_ref


def _media_path_from_file_uri(media_ref: str) -> str:
    parsed = urlparse(media_ref)
    if parsed.scheme != "file":
        raise ValueError("media refs must use file:// URIs")
    path = parsed.path.strip()
    if not path:
        raise ValueError("file:// media ref must include a path")
    return path
