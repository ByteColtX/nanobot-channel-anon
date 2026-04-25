"""OneBot media helpers for the adapter layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from nanobot_channel_anon.domain import Attachment
from nanobot_channel_anon.onebot import OneBotMessageSegment

_SEGMENT_TO_ATTACHMENT_KIND = {
    "image": "image",
    "record": "voice",
    "video": "video",
    "file": "file",
}

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_VOICE_SUFFIXES = (
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".flac",
    ".aac",
    ".amr",
)
_VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".avi")


def _string_value(value: Any) -> str | None:
    """把段数据里的标量值规范化为非空字符串."""
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


class OneBotMediaAdapter:
    """集中处理 OneBot 媒体识别与段转换."""

    def is_mixed_message_media(self, attachment_kind: str) -> bool:
        """返回该媒体类型是否允许与文本混发."""
        return attachment_kind == "image"

    def classify_segment_type(self, segment_type: str) -> str:
        """把 OneBot 段类型映射为平台无关媒体类型."""
        return _SEGMENT_TO_ATTACHMENT_KIND.get(segment_type, "unknown")

    def resolve_media_ref(self, data: dict[str, Any]) -> str | None:
        """从 OneBot 段数据中提取稳定媒体引用."""
        for key in ("url", "file", "path"):
            value = _string_value(data.get(key))
            if value is not None:
                return value
        return None

    def extract_inbound_attachment(
        self,
        segment: OneBotMessageSegment,
    ) -> Attachment | None:
        """把入站媒体段转换为标准化附件."""
        kind = self.classify_segment_type(segment.type)
        if kind == "unknown":
            return None

        media_ref = self.resolve_media_ref(segment.data)
        if media_ref is None:
            return None

        name = _string_value(segment.data.get("file")) or ""
        metadata = {"source": "message_segment"}
        file_size = _string_value(segment.data.get("file_size"))
        if file_size is not None:
            metadata["file_size"] = file_size
        return Attachment(
            kind=kind,
            url=media_ref,
            name=name,
            metadata=metadata,
        )

    def prepare_outbound_attachment_from_media_ref(
        self,
        media_ref: str,
    ) -> OneBotMessageSegment:
        """从媒体引用推断类型并构造 OneBot 出站媒体段."""
        normalized_media_ref = self.normalize_outbound_media_ref(media_ref)
        attachment = Attachment(
            kind=self.classify_outbound_media_ref(normalized_media_ref),
            url=normalized_media_ref,
        )
        return self.prepare_outbound_attachment(attachment)

    def prepare_outbound_attachment(
        self,
        attachment: Attachment,
    ) -> OneBotMessageSegment:
        """把标准化附件转换为 OneBot 出站媒体段."""
        segment_type = next(
            (
                candidate
                for candidate, kind in _SEGMENT_TO_ATTACHMENT_KIND.items()
                if kind == attachment.kind
            ),
            None,
        )
        if segment_type is None:
            raise ValueError(f"unsupported attachment kind: {attachment.kind}")

        media_ref = self.normalize_outbound_media_ref(attachment.url)
        return OneBotMessageSegment(type=segment_type, data={"file": media_ref})

    def normalize_outbound_media_ref(self, media_ref: str) -> str:
        """规范化出站媒体引用为可发送的稳定字符串."""
        normalized_media_ref = media_ref.strip()
        if not normalized_media_ref:
            raise ValueError("attachment url is required")
        return normalized_media_ref

    def is_local_outbound_media_ref(self, media_ref: str) -> bool:
        """判断出站媒体引用是否指向本地文件."""
        return self.local_path_from_media_ref(media_ref) is not None

    def local_path_from_media_ref(self, media_ref: str) -> Path | None:
        """从本地媒体引用中提取绝对文件路径."""
        normalized_media_ref = self.normalize_outbound_media_ref(media_ref)
        parsed = urlparse(normalized_media_ref)
        if parsed.scheme == "file":
            path = unquote(parsed.path).strip()
            if not path:
                raise ValueError("file:// media ref must include a path")
            return Path(path)
        if parsed.scheme:
            return None
        path = Path(normalized_media_ref)
        if not path.is_absolute():
            return None
        if path.parts[:2] == ("/", "napcat"):
            return None
        return path

    def classify_outbound_media_ref(self, media_ref: str) -> str:
        """根据媒体引用推断标准化附件类型."""
        suffix = self._suffix_from_media_ref(media_ref)
        if suffix in _IMAGE_SUFFIXES:
            return "image"
        if suffix in _VOICE_SUFFIXES:
            return "voice"
        if suffix in _VIDEO_SUFFIXES:
            return "video"
        return "file"

    @staticmethod
    def _suffix_from_media_ref(media_ref: str) -> str:
        parsed = urlparse(media_ref)
        path = parsed.path or media_ref
        return Path(path).suffix.lower()
