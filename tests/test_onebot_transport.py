"""Tests for OneBot transport upload behavior."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from nanobot_channel_anon.adapters.onebot_transport import OneBotTransport
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.onebot import OneBotAPIRequest, OneBotRawEvent


class UploadRecordingTransport(OneBotTransport):
    """Test transport that records upload_file_stream requests."""

    def __init__(self) -> None:
        """Initialize the transport with a minimal valid config."""
        super().__init__(
            config=AnonConfig.model_validate(
                {
                    "enabled": True,
                    "allow_from": ["*"],
                    "ws_url": "ws://127.0.0.1:3001",
                }
            )
        )
        self.requests: list[tuple[str, dict[str, object] | None]] = []
        self.completion_data: dict[str, object] = {
            "status": "file_complete",
            "file_path": "/napcat/cache/uploaded.bin",
        }

    async def _send_api_request(
        self,
        action: str,
        params: dict[str, Any] | None,
        *,
        request: OneBotAPIRequest | None = None,
    ) -> OneBotRawEvent:
        del request
        recorded_params = None if params is None else dict(params)
        self.requests.append((action, recorded_params))
        if params and params.get("is_complete"):
            return OneBotRawEvent(
                status="ok",
                retcode=0,
                data=dict(self.completion_data),
            )
        return OneBotRawEvent(
            status="ok",
            retcode=0,
            data={"status": "chunk_received"},
        )


def test_upload_local_media_streams_chunks_and_completion() -> None:
    """本地媒体上传应分块发送, 并在完成后返回 file_path."""

    async def case() -> None:
        transport = UploadRecordingTransport()
        media_bytes = b"a" * (70 * 1024)
        expected_sha256 = hashlib.sha256(media_bytes).hexdigest()
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.png"
            path.write_bytes(media_bytes)

            uploaded = await transport.upload_local_media(path)

        assert uploaded == "/napcat/cache/uploaded.bin"
        assert len(transport.requests) == 3
        first_action, first_params = transport.requests[0]
        second_action, second_params = transport.requests[1]
        completion_action, completion_params = transport.requests[2]
        assert first_action == "upload_file_stream"
        assert second_action == "upload_file_stream"
        assert completion_action == "upload_file_stream"
        assert isinstance(first_params, dict)
        assert isinstance(second_params, dict)
        assert isinstance(completion_params, dict)
        assert first_params["stream_id"] == second_params["stream_id"]
        assert completion_params == {
            "stream_id": first_params["stream_id"],
            "is_complete": True,
        }
        assert first_params["total_chunks"] == 2
        assert second_params["total_chunks"] == 2
        assert first_params["file_size"] == len(media_bytes)
        assert second_params["file_size"] == len(media_bytes)
        assert first_params["expected_sha256"] == expected_sha256
        assert second_params["expected_sha256"] == expected_sha256
        assert first_params["filename"] == "sample.png"
        assert first_params["file_retention"] == 30 * 1000
        assert base64.b64decode(str(first_params["chunk_data"])) == (
            media_bytes[: 64 * 1024]
        )
        assert base64.b64decode(str(second_params["chunk_data"])) == (
            media_bytes[64 * 1024 :]
        )

    asyncio.run(case())


def test_upload_local_media_requires_file_path_in_completion() -> None:
    """上传完成响应缺少 file_path 时应抛错."""

    async def case() -> None:
        transport = UploadRecordingTransport()
        transport.completion_data = {"status": "file_complete"}
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample.png"
            path.write_bytes(b"upload-me")

            with pytest.raises(RuntimeError, match="missing file_path"):
                await transport.upload_local_media(path)

    asyncio.run(case())


def test_send_requests_raises_with_status_retcode_and_data() -> None:
    """动作失败异常应带上 status、retcode 与 data 摘要."""

    async def case() -> None:
        transport = UploadRecordingTransport()
        request = OneBotAPIRequest(action="send_group_msg", params={"group_id": 456})

        async def fake_send_api_request(
            action: str,
            params: dict[str, Any] | None,
            *,
            request: OneBotAPIRequest | None = None,
        ) -> OneBotRawEvent:
            del action, params, request
            return OneBotRawEvent(
                status="failed",
                retcode=1200,
                data={"wording": "bad payload"},
            )

        transport._send_api_request = fake_send_api_request

        with pytest.raises(
            RuntimeError,
            match=(
                "OneBot action failed: send_group_msg "
                "status='failed' retcode=1200 data=\\{'wording': 'bad payload'\\}"
            ),
        ):
            await transport.send_requests([request])

    asyncio.run(case())
