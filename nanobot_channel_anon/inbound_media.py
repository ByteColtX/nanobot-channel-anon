"""入站媒体增强逻辑."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from loguru import logger
from nanobot.config.paths import get_media_dir

from nanobot_channel_anon.adapters.onebot_media import OneBotMediaAdapter
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import Attachment, ForwardNode, NormalizedMessage
from nanobot_channel_anon.utils import parse_forward_expanded_item

_MEDIA_DOWNLOAD_TIMEOUT_S = 30.0
_FFMPEG_TIMEOUT_S = 30.0
_SUPPORTED_TRANSCRIPTION_SUFFIXES = {
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".wav",
    ".webm",
}

TranscribeAudio = Callable[[str | Path], Awaitable[str]]


class InboundMediaEnricher:
    """下载并增强入站媒体附件."""

    def __init__(
        self,
        *,
        config: AnonConfig,
        transcribe_audio: TranscribeAudio | None = None,
        media: OneBotMediaAdapter | None = None,
    ) -> None:
        """保存增强逻辑依赖."""
        self.config = config
        self._transcribe_audio = transcribe_audio
        self._media = media or OneBotMediaAdapter()

    async def enrich(self, message: NormalizedMessage) -> NormalizedMessage:
        """过滤不支持的入站媒体并增强可处理附件."""
        if message.message_type != "message":
            return message

        filtered_message = self._filter_unsupported_inbound_media(message)
        enriched_attachments = await self._enrich_attachments(
            filtered_message.attachments
        )
        metadata = await self._enrich_forward_expanded_metadata(
            filtered_message.metadata
        )
        if (
            enriched_attachments == filtered_message.attachments
            and metadata == filtered_message.metadata
        ):
            return filtered_message
        return filtered_message.model_copy(
            update={"attachments": enriched_attachments, "metadata": metadata}
        )

    def _filter_unsupported_inbound_media(
        self,
        message: NormalizedMessage,
    ) -> NormalizedMessage:
        """忽略我们不需要专门处理的视频与文件入站消息."""
        render_segments = message.metadata.get("render_segments")
        if not isinstance(render_segments, list):
            return self._filter_unsupported_inbound_media_fallback(message)

        kept_segments: list[dict[str, str]] = []
        kept_attachments: list[Attachment] = []
        attachment_index = 0
        contains_ignored = False
        for raw_segment in render_segments:
            if not isinstance(raw_segment, dict):
                return self._filter_unsupported_inbound_media_fallback(message)
            segment_type = raw_segment.get("type")
            if segment_type not in {"image", "voice", "video", "file"}:
                kept_segments.append(raw_segment)
                continue
            if attachment_index >= len(message.attachments):
                return self._filter_unsupported_inbound_media_fallback(message)
            attachment = message.attachments[attachment_index]
            attachment_index += 1
            if attachment.kind != segment_type:
                return self._filter_unsupported_inbound_media_fallback(message)
            if segment_type in {"video", "file"}:
                contains_ignored = True
                continue
            kept_segments.append(raw_segment)
            kept_attachments.append(attachment)

        if attachment_index != len(message.attachments):
            return self._filter_unsupported_inbound_media_fallback(message)

        metadata = dict(message.metadata)
        metadata["render_segments"] = kept_segments
        if contains_ignored:
            metadata["contains_ignored_video_or_file"] = True
        sanitized_content = self._content_from_render_segments(kept_segments)
        if (
            contains_ignored
            and not sanitized_content
            and not kept_attachments
            and not message.mentioned_self
            and message.reply_to_message_id is None
        ):
            metadata["drop_reason"] = "unsupported_media_only"
        return message.model_copy(
            update={
                "content": sanitized_content,
                "attachments": kept_attachments,
                "metadata": metadata,
            }
        )

    def _filter_unsupported_inbound_media_fallback(
        self,
        message: NormalizedMessage,
    ) -> NormalizedMessage:
        """在缺少有效 render_segments 时按占位符回退过滤."""
        kept_attachments: list[Attachment] = []
        content = message.content
        contains_ignored = False
        for attachment in message.attachments:
            if attachment.kind in {"video", "file"}:
                content = content.replace(f"[{attachment.kind}]", "", 1)
                contains_ignored = True
                continue
            kept_attachments.append(attachment)

        metadata = dict(message.metadata)
        if contains_ignored:
            metadata["contains_ignored_video_or_file"] = True
        sanitized_content = content.strip()
        if (
            contains_ignored
            and not sanitized_content
            and not kept_attachments
            and not message.mentioned_self
            and message.reply_to_message_id is None
        ):
            metadata["drop_reason"] = "unsupported_media_only"
        return message.model_copy(
            update={
                "content": sanitized_content,
                "attachments": kept_attachments,
                "metadata": metadata,
            }
        )

    async def _enrich_attachments(
        self,
        attachments: list[Attachment],
    ) -> list[Attachment]:
        """增强一组附件并保持顺序."""
        enriched_attachments: list[Attachment] = []
        for attachment in attachments:
            if attachment.kind == "image":
                enriched_attachment = await self._enrich_image_attachment(attachment)
                enriched_attachments.append(enriched_attachment)
                continue
            if attachment.kind == "voice":
                enriched_attachment = await self._enrich_voice_attachment(attachment)
                enriched_attachments.append(enriched_attachment)
                continue
            enriched_attachments.append(attachment)
        return enriched_attachments

    async def _enrich_forward_expanded_metadata(
        self,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        """增强 forward_expanded 里的节点附件."""
        raw_items = metadata.get("forward_expanded")
        if not isinstance(raw_items, list):
            return metadata

        changed = False
        enriched_items: list[dict[str, object] | None] = []
        for raw_item in raw_items:
            if raw_item is None:
                enriched_items.append(None)
                continue
            expanded = parse_forward_expanded_item(raw_item)
            if expanded is None:
                enriched_items.append(raw_item)
                continue
            enriched_nodes: list[ForwardNode] = []
            node_changed = False
            for node in expanded.nodes:
                enriched_node_attachments = await self._enrich_attachments(
                    node.attachments
                )
                if enriched_node_attachments != node.attachments:
                    node_changed = True
                    enriched_nodes.append(
                        node.model_copy(
                            update={"attachments": enriched_node_attachments}
                        )
                    )
                    continue
                enriched_nodes.append(node)
            if node_changed:
                changed = True
                expanded = expanded.model_copy(update={"nodes": enriched_nodes})
                enriched_items.append(expanded.model_dump(exclude_none=True))
                continue
            enriched_items.append(raw_item)
        if not changed:
            return metadata
        updated_metadata = dict(metadata)
        updated_metadata["forward_expanded"] = enriched_items
        return updated_metadata

    async def _enrich_image_attachment(self, attachment: Attachment) -> Attachment:
        """下载图片到本地缓存并写回元数据."""
        local_path, metadata = await self._materialize_attachment(attachment)
        if local_path is None:
            return attachment.model_copy(update={"metadata": metadata})
        metadata.update(
            {
                "download_status": "downloaded",
                "local_path": str(local_path.resolve(strict=False)),
                "local_file_uri": local_path.resolve(strict=False).as_uri(),
                "cache_name": local_path.name,
            }
        )
        return attachment.model_copy(update={"metadata": metadata})

    async def _enrich_voice_attachment(self, attachment: Attachment) -> Attachment:
        """下载语音、必要时转码并尝试转写."""
        local_path, metadata = await self._materialize_attachment(attachment)
        if local_path is None:
            return attachment.model_copy(update={"metadata": metadata})

        metadata.update(
            {
                "download_status": "downloaded",
                "local_path": str(local_path.resolve(strict=False)),
                "local_file_uri": local_path.resolve(strict=False).as_uri(),
                "cache_name": local_path.name,
            }
        )

        transcription_input = await self._transcode_voice_for_transcription(local_path)
        if transcription_input is None:
            metadata["transcription_status"] = "failed"
            return attachment.model_copy(update={"metadata": metadata})

        resolved_input = transcription_input.resolve(strict=False)
        metadata.update(
            {
                "transcription_input_local_path": str(resolved_input),
                "transcription_input_local_file_uri": resolved_input.as_uri(),
                "transcoded": transcription_input != local_path,
            }
        )

        if self._transcribe_audio is None:
            metadata["transcription_status"] = "not_attempted"
            return attachment.model_copy(update={"metadata": metadata})

        transcription_text = (await self._transcribe_audio(transcription_input)).strip()
        if transcription_text:
            metadata["transcription_status"] = "success"
            metadata["transcription_text"] = transcription_text
        else:
            metadata["transcription_status"] = "failed"
        return attachment.model_copy(update={"metadata": metadata})

    async def _materialize_attachment(
        self,
        attachment: Attachment,
    ) -> tuple[Path | None, dict[str, object]]:
        """将可处理附件物化为本地文件."""
        metadata = dict(attachment.metadata)
        local_source = self._local_source_path(attachment)
        if local_source is not None:
            if not local_source.exists():
                return None, self._with_download_status(
                    metadata,
                    "missing_local_source",
                )
            if not self._is_within_size_limit(local_source.stat().st_size):
                return None, self._with_download_status(metadata, "skipped_size")
            return local_source, metadata

        source_url = self._source_url(attachment)
        if source_url is None:
            return None, self._with_download_status(metadata, "missing_url")

        file_size = self._attachment_file_size(attachment)
        if file_size is None:
            return None, self._with_download_status(metadata, "missing_file_size")
        if not self._is_within_size_limit(file_size):
            return None, self._with_download_status(metadata, "skipped_size")

        target_path = self._cache_path(attachment)
        if target_path.exists():
            return target_path, metadata

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    source_url,
                    timeout=aiohttp.ClientTimeout(total=_MEDIA_DOWNLOAD_TIMEOUT_S),
                ) as response,
            ):
                response.raise_for_status()
                body = await asyncio.wait_for(
                    response.read(),
                    timeout=_MEDIA_DOWNLOAD_TIMEOUT_S,
                )
        except TimeoutError:
            logger.warning(
                "Anon inbound media download timed out for {} after {}s",
                source_url,
                _MEDIA_DOWNLOAD_TIMEOUT_S,
            )
            return None, self._with_download_status(metadata, "timeout")
        except (aiohttp.ClientError, OSError) as exc:
            logger.warning(
                "Anon inbound media download failed for {}: {}",
                source_url,
                exc,
            )
            return None, self._with_download_status(metadata, "failed")

        target_path.write_bytes(body)
        if not self._is_within_size_limit(target_path.stat().st_size):
            with contextlib.suppress(FileNotFoundError):
                target_path.unlink()
            return None, self._with_download_status(metadata, "skipped_size")
        return target_path, metadata

    async def _transcode_voice_for_transcription(
        self,
        source_path: Path,
    ) -> Path | None:
        """把不受支持的语音格式转为转写兼容 wav."""
        suffix = source_path.suffix.lower()
        if suffix in _SUPPORTED_TRANSCRIPTION_SUFFIXES:
            return source_path

        target_path = source_path.with_suffix(".wav")
        if target_path.exists():
            return target_path

        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(target_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning(
                "Anon failed to start ffmpeg for voice transcription: {}",
                exc,
            )
            return None

        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=_FFMPEG_TIMEOUT_S,
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.communicate()
            with contextlib.suppress(FileNotFoundError):
                target_path.unlink()
            logger.warning(
                "Anon voice transcoding timed out for {} after {}s",
                source_path,
                _FFMPEG_TIMEOUT_S,
            )
            return None

        if process.returncode != 0:
            with contextlib.suppress(FileNotFoundError):
                target_path.unlink()
            logger.warning(
                "Anon voice transcoding failed for {}: {}",
                source_path,
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            return None
        return target_path

    @staticmethod
    def _with_download_status(
        metadata: dict[str, object],
        status: str,
    ) -> dict[str, object]:
        updated = dict(metadata)
        updated["download_status"] = status
        return updated

    @staticmethod
    def _content_from_render_segments(render_segments: list[dict[str, str]]) -> str:
        """从 render_segments 重建 message.content."""
        content_parts: list[str] = []
        for segment in render_segments:
            segment_type = segment.get("type")
            if segment_type == "text":
                content_parts.append(segment.get("text", ""))
                continue
            if segment_type == "image":
                content_parts.append("[image]")
                continue
            if segment_type == "voice":
                content_parts.append("[voice]")
                continue
            if segment_type == "forward":
                content_parts.append("[forward]")
        return "".join(content_parts).strip()

    def _attachment_file_size(self, attachment: Attachment) -> int | None:
        """解析附件大小元数据."""
        raw_size = attachment.metadata.get("file_size")
        if raw_size is None:
            return None
        try:
            return int(str(raw_size).strip())
        except ValueError:
            return None

    def _cache_path(self, attachment: Attachment) -> Path:
        """为远端附件生成稳定缓存路径."""
        return get_media_dir("anon") / self._attachment_name(attachment)

    @staticmethod
    def _attachment_name(attachment: Attachment) -> str:
        """返回附件的稳定文件名."""
        name = Path(attachment.name).name.strip()
        if name:
            return name
        original_file = attachment.metadata.get("original_file")
        if isinstance(original_file, str):
            candidate = Path(original_file).name.strip()
            if candidate:
                return candidate
        parsed = urlparse(attachment.url)
        candidate = Path(parsed.path or attachment.url).name.strip()
        return candidate or attachment.kind

    @staticmethod
    def _source_url(attachment: Attachment) -> str | None:
        """返回可下载的远端媒体地址."""
        for key in ("original_url", "url"):
            value = attachment.metadata.get(key) if key != "url" else attachment.url
            if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
                return value.strip()
        return None

    def _local_source_path(self, attachment: Attachment) -> Path | None:
        """把本地 file://、绝对路径或原始 path 解析为 Path."""
        candidates: list[str] = []
        original_path = attachment.metadata.get("original_path")
        if isinstance(original_path, str) and original_path:
            candidates.append(original_path)
        if attachment.url:
            candidates.append(attachment.url)
        for candidate in candidates:
            try:
                local_path = self._media.local_path_from_media_ref(candidate)
            except ValueError:
                continue
            if local_path is None:
                continue
            return local_path.resolve(strict=False)
        return None

    def _is_within_size_limit(self, file_size: int) -> bool:
        """判断文件大小是否在配置限制内."""
        return file_size <= self.config.media_max_size_mb * 1024 * 1024
