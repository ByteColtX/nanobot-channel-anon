"""OneBot v11 outbound helpers."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from nanobot_channel_anon.onebot import OneBotMessageSegment
from nanobot_channel_anon.utils import parse_chat_id

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
_SUPPRESSED_OUTBOUND_TEXT_REASONS = {
    (
        "I completed the tool steps but couldn't produce a final answer. "
        "Please try again or narrow the task."
    ): "empty_final_response",
    "Sorry, I encountered an error calling the AI model.": "model_error",
    "Sorry, I encountered an error.": "generic_error",
    "Task completed but no final response was generated.": "missing_final_response",
    "Background task completed.": "background_task_completed",
}
_MAX_ITERATIONS_PATTERN = re.compile(
    r"^I reached the maximum number of tool call iterations \(\d+\) without "
    r"completing the task\. You can try breaking the task into smaller steps\.$"
)


def build_send_request(
    chat_id: str,
    content: str,
    *,
    media: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the OneBot action and params for an outbound message."""
    target_kind, target_id = parse_chat_id(chat_id)
    action = "send_group_msg" if target_kind == "group" else "send_private_msg"
    id_key = "group_id" if target_kind == "group" else "user_id"
    segments = build_message_segments(content, media=media or [], metadata=metadata)
    return action, {
        id_key: target_id,
        "message": [segment.model_dump(exclude_none=True) for segment in segments],
    }


def get_suppressed_outbound_reason(content: str) -> str | None:
    """Return the suppression reason for known nanobot fallback/error text."""
    normalized = content.strip()
    if not normalized:
        return None
    if normalized.startswith("Error:"):
        return "error_prefix"
    reason = _SUPPRESSED_OUTBOUND_TEXT_REASONS.get(normalized)
    if reason is not None:
        return reason
    if _MAX_ITERATIONS_PATTERN.fullmatch(normalized):
        return "max_iterations"
    return None


def build_message_segments(
    content: str,
    *,
    media: list[str],
    metadata: dict[str, Any] | None = None,
) -> list[OneBotMessageSegment]:
    """Build OneBot segments for text and resolved media outbound."""
    segments = [
        *_build_reply_placeholder_segments(metadata),
        *_build_mention_placeholder_segments(metadata),
    ]

    for media_ref in media:
        segments.append(
            OneBotMessageSegment(
                type=guess_media_segment_type(media_ref),
                data={"file": _normalize_media_ref(media_ref)},
            )
        )

    if content:
        segments.append(OneBotMessageSegment(type="text", data={"text": content}))

    return segments


def split_outbound_batches(
    content: str,
    media: list[str],
) -> list[tuple[str, list[str]]]:
    """Split outbound content into NapCat-compatible send batches."""
    batches: list[tuple[str, list[str]]] = []
    image_batch: list[str] = []

    for media_ref in media:
        if guess_media_segment_type(media_ref) == "image":
            image_batch.append(media_ref)
            continue
        if image_batch:
            batches.append(("", image_batch))
            image_batch = []
        batches.append(("", [media_ref]))

    if image_batch:
        batches.append((content, image_batch))
    elif content:
        batches.append((content, []))

    return batches


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


def guess_media_segment_type(media_ref: str) -> str:
    """Guess the OneBot segment type for a resolved media ref."""
    suffix = PurePosixPath(media_path_from_media_ref(media_ref)).suffix.lower()
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
    media_path_from_media_ref(normalized_media_ref)
    return normalized_media_ref


def media_path_from_media_ref(media_ref: str) -> str:
    """Extract a usable path component from a local or resolved media ref."""
    parsed = urlparse(media_ref)
    if parsed.scheme == "file":
        path = unquote(parsed.path).strip()
        if not path:
            raise ValueError("file:// media ref must include a path")
        return path
    if parsed.scheme:
        path = parsed.path.strip()
        if not path:
            raise ValueError("media ref must include a path")
        return path
    path = media_ref.strip()
    if not path:
        raise ValueError("media ref is required")
    return path
