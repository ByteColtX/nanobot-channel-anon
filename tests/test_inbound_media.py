"""Tests for inbound media enrichment."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import Attachment, ConversationRef, NormalizedMessage
from nanobot_channel_anon.inbound_media import InboundMediaEnricher
from nanobot_channel_anon.utils import parse_forward_expanded_slots


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


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def raise_for_status(self) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeClientSession:
    def __init__(self, body: bytes, calls: list[str]) -> None:
        self._body = body
        self._calls = calls

    async def __aenter__(self) -> _FakeClientSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def get(self, url: str, *, timeout: aiohttp.ClientTimeout) -> _FakeResponse:
        _ = timeout
        self._calls.append(url)
        return _FakeResponse(self._body)


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


def test_enricher_downloads_forward_node_image_into_forward_expanded() -> None:
    """Forward 节点内图片也应被下载并写回 local_path."""
    enricher = StubInboundMediaEnricher(config=_config())
    enricher.materialized = (Path("/tmp/forward-image.png"), {"file_size": "123"})
    message = _message(
        content="[forward]",
        attachments=[],
        metadata={
            "forward_expanded": [
                {
                    "forward_id": "fw-1",
                    "summary": "",
                    "nodes": [
                        {
                            "sender_id": "123456",
                            "sender_name": "香港奶龙",
                            "message_id": "node-4",
                            "content": "[image]",
                            "attachments": [
                                {
                                    "kind": "image",
                                    "url": "https://example.com/09D75D4956CA5B4C21139F1701173408.png",
                                    "name": "09D75D4956CA5B4C21139F1701173408.png",
                                    "metadata": {"file_size": "123"},
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )

    enriched = __import__("asyncio").run(enricher.enrich(message))

    forward_expanded = enriched.metadata["forward_expanded"]
    assert isinstance(forward_expanded, list)
    node = forward_expanded[0]["nodes"][0]
    attachment = node["attachments"][0]
    local_path = Path(str(attachment["metadata"]["local_path"]))
    assert attachment["metadata"]["download_status"] == "downloaded"
    assert local_path.name == "forward-image.png"
    assert local_path.as_uri() == attachment["metadata"]["local_file_uri"]
    assert attachment["metadata"]["cache_name"] == "forward-image.png"


def test_enricher_ignores_invalid_forward_expanded_slot() -> None:
    """坏的 forward_expanded 槽位不应让 enrich 整体失败."""
    enricher = StubInboundMediaEnricher(config=_config())
    message = _message(
        content="[forward]",
        attachments=[],
        metadata={
            "forward_expanded": [
                {"forward_id": "bad", "summary": 123, "nodes": "oops"}
            ]
        },
    )

    enriched = asyncio.run(enricher.enrich(message))

    assert enriched.metadata == message.metadata


def test_enricher_keeps_valid_forward_expanded_slot_when_mixed_with_invalid() -> None:
    """Mixed valid/invalid forward_expanded 槽位时应仍增强合法节点."""
    enricher = StubInboundMediaEnricher(config=_config())
    enricher.materialized = (Path("/tmp/mixed-forward-image.png"), {"file_size": "123"})
    message = _message(
        content="[forward][forward]",
        attachments=[],
        metadata={
            "forward_expanded": [
                {
                    "forward_id": "fw-1",
                    "summary": "",
                    "nodes": [
                        {
                            "sender_id": "123456",
                            "sender_name": "香港奶龙",
                            "message_id": "node-4",
                            "content": "[image]",
                            "attachments": [
                                {
                                    "kind": "image",
                                    "url": "https://example.com/valid.png",
                                    "name": "valid.png",
                                    "metadata": {"file_size": "123"},
                                }
                            ],
                        }
                    ],
                },
                {"forward_id": "bad", "summary": 123, "nodes": "oops"},
            ]
        },
    )

    enriched = asyncio.run(enricher.enrich(message))

    forward_expanded = enriched.metadata["forward_expanded"]
    assert isinstance(forward_expanded, list)
    node = forward_expanded[0]["nodes"][0]
    attachment = node["attachments"][0]
    local_path = Path(str(attachment["metadata"]["local_path"]))
    assert attachment["metadata"]["download_status"] == "downloaded"
    assert local_path.name == "mixed-forward-image.png"
    assert forward_expanded[1] == message.metadata["forward_expanded"][1]
    rendered_slots = parse_forward_expanded_slots(enriched.metadata)
    assert rendered_slots[0] is not None
    assert rendered_slots[1] is None


def test_materialize_attachment_reuses_existing_cache_when_file_exists() -> None:
    """已有同名缓存文件时应直接复用, 不再下载."""
    enricher = InboundMediaEnricher(config=_config())
    attachment = Attachment(
        kind="image",
        url="https://example.com/media/a.png",
        name="A9381FD36555D392AA81D0F1B5B38AAF.jpg",
        metadata={
            "file_size": "3",
            "original_file": "A9381FD36555D392AA81D0F1B5B38AAF.jpg",
        },
    )

    with TemporaryDirectory() as tmpdir:
        cached_path = Path(tmpdir) / "A9381FD36555D392AA81D0F1B5B38AAF.jpg"
        cached_path.write_bytes(b"x")
        calls: list[str] = []
        original_cache_path = enricher._cache_path
        original_client_session = aiohttp.ClientSession
        try:
            enricher._cache_path = lambda _attachment: cached_path  # type: ignore[method-assign]
            aiohttp.ClientSession = lambda: _FakeClientSession(b"zzz", calls)  # type: ignore[assignment]
            local_path, metadata = asyncio.run(
                enricher._materialize_attachment(attachment)
            )
        finally:
            enricher._cache_path = original_cache_path  # type: ignore[method-assign]
            aiohttp.ClientSession = original_client_session  # type: ignore[assignment]

    assert local_path == cached_path
    assert metadata == {
        "file_size": "3",
        "original_file": "A9381FD36555D392AA81D0F1B5B38AAF.jpg",
    }
    assert calls == []


def test_materialize_attachment_downloads_when_named_cache_missing() -> None:
    """同名缓存不存在时应正常下载并写入该文件名."""
    enricher = InboundMediaEnricher(config=_config())
    attachment = Attachment(
        kind="image",
        url="https://example.com/media/a.png",
        name="A9381FD36555D392AA81D0F1B5B38AAF.jpg",
        metadata={
            "file_size": "4",
            "original_file": "A9381FD36555D392AA81D0F1B5B38AAF.jpg",
        },
    )

    with TemporaryDirectory() as tmpdir:
        cached_path = Path(tmpdir) / "A9381FD36555D392AA81D0F1B5B38AAF.jpg"
        calls: list[str] = []
        original_cache_path = enricher._cache_path
        original_client_session = aiohttp.ClientSession
        try:
            enricher._cache_path = lambda _attachment: cached_path  # type: ignore[method-assign]
            aiohttp.ClientSession = lambda: _FakeClientSession(b"abcd", calls)  # type: ignore[assignment]
            local_path, metadata = asyncio.run(
                enricher._materialize_attachment(attachment)
            )
        finally:
            enricher._cache_path = original_cache_path  # type: ignore[method-assign]
            aiohttp.ClientSession = original_client_session  # type: ignore[assignment]

        assert cached_path.read_bytes() == b"abcd"

    assert local_path == cached_path
    assert metadata == {
        "file_size": "4",
        "original_file": "A9381FD36555D392AA81D0F1B5B38AAF.jpg",
    }
    assert calls == ["https://example.com/media/a.png"]


def test_cache_path_uses_message_file_name_directly() -> None:
    """缓存文件名应直接使用 message.data.file."""
    enricher = InboundMediaEnricher(config=_config())
    attachment = Attachment(
        kind="image",
        url="https://example.com/download?appid=1407&fileid=abc",
        name="A9381FD36555D392AA81D0F1B5B38AAF.jpg",
        metadata={"original_file": "A9381FD36555D392AA81D0F1B5B38AAF.jpg"},
    )

    first = enricher._cache_path(attachment)
    second = enricher._cache_path(attachment)

    assert first.name == "A9381FD36555D392AA81D0F1B5B38AAF.jpg"
    assert second.name == first.name
