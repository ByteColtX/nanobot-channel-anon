"""Tests for inbound media enrichment."""

from __future__ import annotations

from pathlib import Path

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import Attachment, ConversationRef, NormalizedMessage
from nanobot_channel_anon.inbound_media import InboundMediaEnricher


class StubInboundMediaEnricher(InboundMediaEnricher):
    """测试用可控媒体增强器."""

    def __init__(self, **kwargs) -> None:
        """初始化可控桩对象."""
        super().__init__(**kwargs)
        self.materialized: tuple[Path | None, dict[str, object]] = (None, {})
        self.transcode_result: Path | None = None

    async def _materialize_attachment(
        self,
        attachment: Attachment,
    ) -> tuple[Path | None, dict[str, object]]:
        _ = attachment
        return self.materialized

    async def _transcode_voice_for_transcription(
        self,
        source_path: Path,
    ) -> Path | None:
        if self.transcode_result is not None:
            return self.transcode_result
        return source_path


def _config(**overrides: object) -> AnonConfig:
    data: dict[str, object] = {
        "enabled": True,
        "allow_from": ["*"],
        "ws_url": "ws://127.0.0.1:3001",
    }
    data.update(overrides)
    return AnonConfig.model_validate(data)


def _message(
    *,
    content: str,
    attachments: list[Attachment],
    metadata: dict[str, object] | None = None,
) -> NormalizedMessage:
    return NormalizedMessage(
        message_id="m1",
        conversation=ConversationRef(kind="group", id="456"),
        sender_id="123",
        sender_name="Alice",
        content=content,
        attachments=attachments,
        metadata={} if metadata is None else dict(metadata),
    )


async def _transcribe_ok(_file_path: str | Path) -> str:
    return "你好世界"


async def _transcribe_empty(_file_path: str | Path) -> str:
    return ""


def test_enricher_filters_video_and_file_only_message() -> None:
    """纯视频/文件消息应被标记为可抑制."""
    enricher = StubInboundMediaEnricher(config=_config())
    message = _message(
        content="[video][file]",
        attachments=[
            Attachment(kind="video", url="https://example.com/a.mp4"),
            Attachment(kind="file", url="https://example.com/a.zip"),
        ],
        metadata={
            "render_segments": [
                {"type": "video"},
                {"type": "file"},
            ]
        },
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    assert enriched.attachments == []
    assert enriched.content == ""
    assert enriched.metadata["drop_reason"] == "unsupported_media_only"


def test_enricher_keeps_text_when_video_is_mixed() -> None:
    """混合文本与视频时应仅保留文本."""
    enricher = StubInboundMediaEnricher(config=_config())
    message = _message(
        content="hello[video]",
        attachments=[Attachment(kind="video", url="https://example.com/a.mp4")],
        metadata={
            "render_segments": [
                {"type": "text", "text": "hello"},
                {"type": "video"},
            ]
        },
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    assert enriched.attachments == []
    assert enriched.content == "hello"
    assert enriched.metadata["contains_ignored_video_or_file"] is True
    assert "drop_reason" not in enriched.metadata


def test_enricher_adds_local_metadata_for_downloaded_image() -> None:
    """下载后的图片应写回本地缓存元数据."""
    enricher = StubInboundMediaEnricher(config=_config())
    enricher.materialized = (Path("/tmp/a-local.png"), {"file_size": "123"})
    message = _message(
        content="看图[image]",
        attachments=[
            Attachment(
                kind="image",
                url="https://example.com/a.png",
                name="a.png",
            )
        ],
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    attachment = enriched.attachments[0]
    local_path = Path(str(attachment.metadata["local_path"]))
    assert attachment.metadata["download_status"] == "downloaded"
    assert local_path.name == "a-local.png"
    assert local_path.as_uri() == attachment.metadata["local_file_uri"]
    assert attachment.metadata["cache_name"] == "a-local.png"


def test_enricher_marks_failed_voice_transcription() -> None:
    """空转写结果应被标记为失败."""
    enricher = StubInboundMediaEnricher(
        config=_config(),
        transcribe_audio=_transcribe_empty,
    )
    enricher.materialized = (Path("/tmp/voice.amr"), {"file_size": "123"})
    enricher.transcode_result = Path("/tmp/voice.wav")
    message = _message(
        content="听一下[voice]",
        attachments=[
            Attachment(
                kind="voice",
                url="https://example.com/voice.amr",
                name="voice.amr",
            )
        ],
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    attachment = enriched.attachments[0]
    transcription_path = Path(
        str(attachment.metadata["transcription_input_local_path"])
    )
    assert attachment.metadata["download_status"] == "downloaded"
    assert attachment.metadata["transcoded"] is True
    assert transcription_path.name == "voice.wav"
    assert (
        transcription_path.as_uri()
        == attachment.metadata["transcription_input_local_file_uri"]
    )
    assert attachment.metadata["transcription_status"] == "failed"


def test_enricher_records_successful_voice_transcription() -> None:
    """非空转写结果应被记录为成功."""
    enricher = StubInboundMediaEnricher(
        config=_config(),
        transcribe_audio=_transcribe_ok,
    )
    enricher.materialized = (Path("/tmp/voice.amr"), {"file_size": "123"})
    enricher.transcode_result = Path("/tmp/voice.wav")
    message = _message(
        content="听一下[voice]",
        attachments=[
            Attachment(
                kind="voice",
                url="https://example.com/voice.amr",
                name="voice.amr",
            )
        ],
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    attachment = enriched.attachments[0]
    assert attachment.metadata["transcription_status"] == "success"
    assert attachment.metadata["transcription_text"] == "你好世界"
