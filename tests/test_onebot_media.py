"""Tests for the OneBot media adapter."""

from __future__ import annotations

from pathlib import Path

from nanobot_channel_anon.adapters.onebot_media import OneBotMediaAdapter
from nanobot_channel_anon.domain import Attachment
from nanobot_channel_anon.onebot import OneBotMessageSegment


def test_classify_attachment_kind_from_segment_type() -> None:
    """Known OneBot media segment types should map predictably."""
    adapter = OneBotMediaAdapter()

    assert adapter.classify_segment_type("image") == "image"
    assert adapter.classify_segment_type("record") == "voice"
    assert adapter.classify_segment_type("video") == "video"
    assert adapter.classify_segment_type("file") == "file"
    assert adapter.classify_segment_type("unknown") == "unknown"


def test_resolve_media_ref_prefers_url_then_file_path() -> None:
    """Media refs should normalize into stable adapter-facing refs."""
    adapter = OneBotMediaAdapter()

    assert (
        adapter.resolve_media_ref({"url": "https://example.com/a.png", "file": "a.png"})
        == "https://example.com/a.png"
    )
    assert adapter.resolve_media_ref({"file": "file:///tmp/a.png"}) == "file:///tmp/a.png"


def test_extract_inbound_attachment_from_image_segment() -> None:
    """Inbound image segments should produce normalized attachments."""
    adapter = OneBotMediaAdapter()
    segment = OneBotMessageSegment(
        type="image",
        data={"file": "a.png", "url": "https://example.com/a.png"},
    )

    attachment = adapter.extract_inbound_attachment(segment)

    assert attachment == Attachment(
        kind="image",
        url="https://example.com/a.png",
        name="a.png",
        metadata={
            "source": "message_segment",
            "onebot_segment_type": "image",
            "original_url": "https://example.com/a.png",
            "original_file": "a.png",
        },
    )


def test_prepare_outbound_attachment_builds_expected_segment() -> None:
    """Outbound attachments should map into protocol segments."""
    adapter = OneBotMediaAdapter()
    attachment = Attachment(kind="voice", url="file:///tmp/a.wav")

    segment = adapter.prepare_outbound_attachment(attachment)

    assert segment == OneBotMessageSegment(
        type="record",
        data={"file": "file:///tmp/a.wav"},
    )


def test_prepare_outbound_attachment_from_media_ref_infers_kind_and_normalizes(
) -> None:
    """Media refs should be classified and normalized inside the adapter layer."""
    adapter = OneBotMediaAdapter()

    image_segment = adapter.prepare_outbound_attachment_from_media_ref(
        "  https://example.com/a.png  "
    )
    voice_segment = adapter.prepare_outbound_attachment_from_media_ref(
        "file:///tmp/a.wav"
    )

    assert image_segment == OneBotMessageSegment(
        type="image",
        data={"file": "https://example.com/a.png"},
    )
    assert voice_segment == OneBotMessageSegment(
        type="record",
        data={"file": "file:///tmp/a.wav"},
    )


def test_resolve_media_ref_and_name_trim_scalar_values() -> None:
    """媒体适配层应自行处理段数据里的标量字符串归一化."""
    adapter = OneBotMediaAdapter()
    segment = OneBotMessageSegment(
        type="image",
        data={"file": "  a.png  ", "url": 12345},
    )

    attachment = adapter.extract_inbound_attachment(segment)

    assert attachment == Attachment(
        kind="image",
        url="12345",
        name="a.png",
        metadata={
            "source": "message_segment",
            "onebot_segment_type": "image",
            "original_url": "12345",
            "original_file": "a.png",
        },
    )


def test_extract_inbound_attachment_keeps_file_size_metadata() -> None:
    """入站附件应保留 file_size 以供上层限制媒体大小."""
    adapter = OneBotMediaAdapter()
    segment = OneBotMessageSegment(
        type="image",
        data={
            "file": "a.png",
            "url": "https://example.com/a.png",
            "file_size": 123,
        },
    )

    attachment = adapter.extract_inbound_attachment(segment)

    assert attachment == Attachment(
        kind="image",
        url="https://example.com/a.png",
        name="a.png",
        metadata={
            "source": "message_segment",
            "onebot_segment_type": "image",
            "original_url": "https://example.com/a.png",
            "original_file": "a.png",
            "file_size": "123",
        },
    )


def test_extract_inbound_attachment_keeps_original_path_metadata() -> None:
    """入站附件应保留 path 以供后续增强逻辑复用."""
    adapter = OneBotMediaAdapter()
    segment = OneBotMessageSegment(
        type="record",
        data={
            "file": "voice.amr",
            "path": "/tmp/voice.amr",
        },
    )

    attachment = adapter.extract_inbound_attachment(segment)

    assert attachment == Attachment(
        kind="voice",
        url="voice.amr",
        name="voice.amr",
        metadata={
            "source": "message_segment",
            "onebot_segment_type": "record",
            "original_file": "voice.amr",
            "original_path": "/tmp/voice.amr",
        },
    )


def test_local_path_from_media_ref_accepts_file_uri_and_absolute_path() -> None:
    """本地 file:// 与绝对路径应被识别为需上传媒体."""
    adapter = OneBotMediaAdapter()

    assert adapter.local_path_from_media_ref(
        "file:///tmp/demo.wav"
    ) == Path("/tmp/demo.wav")
    assert adapter.local_path_from_media_ref("/tmp/demo.wav") == Path("/tmp/demo.wav")
    assert adapter.is_local_outbound_media_ref("file:///tmp/demo.wav") is True
    assert adapter.is_local_outbound_media_ref("/tmp/demo.wav") is True


def test_local_path_from_media_ref_skips_remote_url_and_napcat_path() -> None:
    """远端 URL 与 NapCat 路径不应再次被视为本地上传输入."""
    adapter = OneBotMediaAdapter()

    assert adapter.local_path_from_media_ref("https://example.com/demo.png") is None
    assert adapter.local_path_from_media_ref("/napcat/cache/demo.png") is None
    assert adapter.is_local_outbound_media_ref("https://example.com/demo.png") is False
    assert adapter.is_local_outbound_media_ref("/napcat/cache/demo.png") is False
