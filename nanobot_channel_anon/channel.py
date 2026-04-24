"""OneBot v11 WebSocket transport for the anon channel."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from loguru import logger
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_media_dir
from pydantic import ValidationError

from nanobot_channel_anon.buffer import Buffer, MessageEntry
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import (
    build_message_entry,
    cache_inbound_candidate,
    normalize_inbound_event,
    process_inbound_candidate,
)
from nanobot_channel_anon.onebot import BotStatus, OneBotAPIRequest, OneBotRawEvent
from nanobot_channel_anon.outbound import (
    build_send_request,
    get_suppressed_outbound_reason,
    media_path_from_media_ref,
    split_outbound_batches,
)
from nanobot_channel_anon.router import InboundRouter
from nanobot_channel_anon.serializer import serialize_buffer_chat
from nanobot_channel_anon.utils import (
    build_group_chat_id,
    build_private_chat_id,
    normalize_onebot_id,
)

_CONNECT_TIMEOUT_S = 10.0
_PING_INTERVAL_S = 30.0
_READ_TIMEOUT_S = 60.0
_API_TIMEOUT_S = 5.0
_RECONNECT_INTERVAL_S = 5.0
_SESSION_QUEUE_MAX_SIZE = 64
_GROUP_MUTE_WARMUP_CONCURRENCY = 8
_MEDIA_DOWNLOAD_TIMEOUT_S = 30.0
_FFMPEG_TIMEOUT_S = 30.0
_OUTBOUND_UPLOAD_CHUNK_SIZE = 64 * 1024
_OUTBOUND_UPLOAD_FILE_RETENTION_MS = 30 * 1000
_INBOUND_CACHE_PATH = Path(".anon_inbound_buffer.json")
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


@dataclass(slots=True)
class _SessionWorker:
    """A per-session inbound worker."""

    queue: asyncio.Queue[OneBotRawEvent]
    task: asyncio.Task[None]


class AnonChannel(BaseChannel):
    """Anon OneBot v11 channel transport."""

    name = "anon"
    display_name = "Anon"

    def __init__(self, config: Any, bus: MessageBus):
        """Initialize the channel with validated config."""
        if isinstance(config, dict):
            config = AnonConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: AnonConfig = config
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stop_event: asyncio.Event | None = None
        self._reader_task: asyncio.Task[str] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._session_workers: dict[str, _SessionWorker] = {}
        self._session_workers_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[OneBotRawEvent]] = {}
        self._echo_counter = 0
        self._self_id: str | None = None
        self._self_nickname: str = ""
        self._muted_groups: dict[str, int] = {}
        self._known_group_mute_states: set[str] = set()
        self._group_mute_seed_ready = asyncio.Event()
        self._group_mute_seed_ready.set()
        self._group_mute_state_waiters: dict[str, asyncio.Future[None]] = {}
        self._group_mute_state_tasks: dict[str, asyncio.Task[None]] = {}
        self._group_mute_warmup_task: asyncio.Task[None] | None = None
        self._buffer = Buffer(self.config.max_context_messages)
        self._router = InboundRouter(self.config)
        self._inbound_cache_path = _INBOUND_CACHE_PATH

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """Return the default channel config."""
        return AnonConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """Start the channel and keep reconnecting until stopped."""
        ws_url = self.config.ws_url
        if not ws_url:
            raise ValueError("anon channel ws_url is required")
        if self._running:
            return

        self._running = True
        self._stop_event = asyncio.Event()
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=_CONNECT_TIMEOUT_S),
        )

        logger.info("Anon OneBot transport starting: {}", ws_url)

        try:
            while not self._stop_event.is_set():
                try:
                    ws = await self._connect_ws()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._should_stop():
                        break
                    logger.warning("Anon WebSocket connect failed: {}", exc)
                    await self._sleep_until_stop(_RECONNECT_INTERVAL_S)
                    continue

                reader_task = asyncio.create_task(self._listen_loop(ws))
                self._reader_task = reader_task
                self._ping_task = asyncio.create_task(self._ping_loop(ws))
                self._reset_group_mute_sync_state()
                try:
                    await self._fetch_self_id()
                    await self._refresh_group_mute_states()
                finally:
                    self._group_mute_seed_ready.set()

                disconnect_reason = "WebSocket reader exited"
                try:
                    disconnect_reason = await reader_task
                finally:
                    self._reader_task = None
                    await self._handle_disconnect(ws, disconnect_reason)

                if not self._stop_event.is_set():
                    await self._sleep_until_stop(_RECONNECT_INTERVAL_S)
        finally:
            self._running = False
            await self._shutdown()
            self._stop_event = None
            logger.info("Anon OneBot transport stopped")

    async def stop(self) -> None:
        """Stop the channel and release all transport resources."""
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
        await self._shutdown()

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through the active OneBot WebSocket."""
        if reason := get_suppressed_outbound_reason(msg.content):
            logger.debug(
                "Suppressing outbound nanobot fallback for {}: {}",
                msg.chat_id,
                reason,
            )
            return
        resolved_media = await self._resolve_outbound_media_refs(msg.media)
        metadata = dict(msg.metadata)
        for batch_content, batch_media in split_outbound_batches(
            msg.content,
            resolved_media,
        ):
            action, params = build_send_request(
                msg.chat_id,
                batch_content,
                media=batch_media,
                metadata=metadata,
            )
            response = await self._send_api_request(action, params)
            if response.status == "failed":
                raise RuntimeError(
                    f"OneBot action {action} failed: retcode={response.retcode}"
                )
            self._buffer_outbound_message(
                msg,
                response,
                content=batch_content,
                media=batch_media,
            )

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a delta as a normal outbound text message."""
        if not delta:
            return
        outbound_metadata: dict[str, Any] = {} if metadata is None else metadata
        await self.send(
            OutboundMessage(
                channel=self.name,
                chat_id=chat_id,
                content=delta,
                metadata=outbound_metadata,
            )
        )

    async def _resolve_outbound_media_refs(self, media: list[str]) -> list[str]:
        return [
            await self._resolve_single_outbound_media_ref(item)
            for item in media
        ]

    async def _resolve_single_outbound_media_ref(self, media_ref: str) -> str:
        normalized_media_ref = media_ref.strip()
        if not normalized_media_ref:
            raise ValueError("media ref is required")
        if normalized_media_ref.startswith("file://"):
            local_path = Path(media_path_from_media_ref(normalized_media_ref))
        else:
            local_path = Path(normalized_media_ref)
            if not local_path.is_absolute():
                return normalized_media_ref
        try:
            return await self._upload_outbound_file_via_stream(local_path)
        except FileNotFoundError:
            return normalized_media_ref

    async def _upload_outbound_file_via_stream(self, path: Path) -> str:
        resolved_path = path.resolve(strict=False)
        with resolved_path.open("rb") as file_obj:
            file_obj.seek(0, 2)
            file_size = file_obj.tell()
            max_size = self.config.media_max_size_mb * 1024 * 1024
            if file_size > max_size:
                raise ValueError(
                    f"outbound media exceeds size limit: {resolved_path.name}"
                )
            file_obj.seek(0)
            digest = hashlib.sha256()
            while chunk := file_obj.read(_OUTBOUND_UPLOAD_CHUNK_SIZE):
                digest.update(chunk)
            total_chunks = max(
                1,
                (file_size + _OUTBOUND_UPLOAD_CHUNK_SIZE - 1)
                // _OUTBOUND_UPLOAD_CHUNK_SIZE,
            )
            file_obj.seek(0)
            stream_id = str(uuid4())
            for chunk_index in range(total_chunks):
                chunk = file_obj.read(_OUTBOUND_UPLOAD_CHUNK_SIZE)
                response = await self._send_api_request(
                    "upload_file_stream",
                    {
                        "stream_id": stream_id,
                        "chunk_data": base64.b64encode(chunk).decode("ascii"),
                        "chunk_index": chunk_index,
                        "total_chunks": total_chunks,
                        "file_size": file_size,
                        "expected_sha256": digest.hexdigest(),
                        "filename": resolved_path.name,
                        "file_retention": _OUTBOUND_UPLOAD_FILE_RETENTION_MS,
                    },
                )
                self._ensure_outbound_upload_chunk_ok(
                    response, filename=resolved_path.name
                )
        completion = await self._send_api_request(
            "upload_file_stream",
            {"stream_id": stream_id, "is_complete": True},
        )
        return self._extract_uploaded_file_path(completion, filename=resolved_path.name)

    @staticmethod
    def _ensure_outbound_upload_chunk_ok(
        response: OneBotRawEvent,
        *,
        filename: str,
    ) -> None:
        if response.status == "failed":
            raise RuntimeError(f"upload_file_stream failed for {filename}")

    @staticmethod
    def _extract_uploaded_file_path(
        response: OneBotRawEvent,
        *,
        filename: str,
    ) -> str:
        data = response.data
        if not isinstance(data, dict):
            raise RuntimeError(
                f"upload_file_stream completion missing data for {filename}"
            )
        status = str(data.get("status") or "").strip()
        if status != "file_complete":
            raise RuntimeError(
                f"upload_file_stream completion missing file_complete for {filename}"
            )
        file_path = str(data.get("file_path") or "").strip()
        if not file_path:
            raise RuntimeError(
                f"upload_file_stream completion missing file_path for {filename}"
            )
        return file_path

    async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        session = self._require_session()
        headers: dict[str, str] = {}
        access_token = self.config.access_token
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        timeout = aiohttp.ClientWSTimeout(ws_receive=_READ_TIMEOUT_S)  # pyright: ignore[reportCallIssue]
        ws = await session.ws_connect(
            self.config.ws_url,
            headers=headers or None,
            autoping=True,
            heartbeat=None,
            timeout=timeout,
        )
        self._ws = ws
        logger.info("Anon WebSocket connected: {}", self.config.ws_url)
        return ws

    async def _listen_loop(self, ws: aiohttp.ClientWebSocketResponse) -> str:
        while not self._should_stop() and not ws.closed:
            try:
                message = await ws.receive()
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                logger.warning("Anon WebSocket receive timed out")
                return "WebSocket receive timed out"
            except Exception as exc:
                logger.warning("Anon WebSocket receive failed: {}", exc)
                return f"WebSocket receive failed: {exc}"

            if message.type is aiohttp.WSMsgType.TEXT:
                payload = self._decode_payload(message.data)
                if payload is None:
                    continue
                if self._resolve_pending(payload):
                    continue
                if self._is_api_response(payload):
                    logger.debug(
                        "Anon received API response without echo: status={}",
                        payload.status,
                    )
                    continue
                await self._enqueue_inbound_event(payload)
                continue

            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }:
                logger.info("Anon WebSocket closing")
                return "WebSocket closed"

            if message.type is aiohttp.WSMsgType.ERROR:
                exc = ws.exception()
                if exc is None:
                    return "WebSocket error"
                logger.warning("Anon WebSocket error: {}", exc)
                return f"WebSocket error: {exc}"

            if message.type in {aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG}:
                continue

            logger.debug("Anon ignored WebSocket frame: type={}", message.type)

        if self._should_stop():
            return "Channel stopped"
        return "WebSocket closed"

    async def _ping_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not self._should_stop() and not ws.closed:
            await self._sleep_until_stop(_PING_INTERVAL_S)
            if self._should_stop() or ws.closed:
                return
            try:
                async with self._write_lock:
                    await ws.ping()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Anon WebSocket ping failed: {}", exc)
                with contextlib.suppress(Exception):
                    await ws.close()
                return

    async def _send_api_request(
        self,
        action: str,
        params: dict[str, Any] | None,
        timeout: float = _API_TIMEOUT_S,
    ) -> OneBotRawEvent:
        ws = self._ws
        if ws is None or ws.closed:
            raise ConnectionError("WebSocket not connected")

        echo = self._next_echo()
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future

        try:
            request = OneBotAPIRequest(action=action, params=params, echo=echo)
            try:
                async with self._write_lock:
                    await ws.send_json(request.model_dump(exclude_none=True))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await ws.close()
                raise ConnectionError(
                    f"failed to write API request {action}: {exc}"
                ) from exc

            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        finally:
            self._pending.pop(echo, None)

    async def _fetch_self_id(self) -> None:
        try:
            response = await self._send_api_request("get_login_info", None)
        except Exception as exc:
            if self._should_stop():
                logger.debug("Anon skipped get_login_info during shutdown")
                return
            logger.warning("Anon get_login_info failed: {}", exc)
            return

        info = response.data
        if not isinstance(info, dict):
            info = response.model_dump(exclude_none=True)

        user_id = normalize_onebot_id(info.get("user_id"))
        if not user_id:
            logger.warning(
                "Anon get_login_info missing user_id: {}",
                response.model_dump(exclude_none=True),
            )
            return

        self._self_id = user_id
        self._self_nickname = str(info.get("nickname") or "")
        logger.info(
            "Anon bot identity loaded: self_id={} nickname={}",
            user_id,
            self._self_nickname,
        )

    @staticmethod
    def _extract_slash_command(candidate: Any) -> str | None:
        """Extract a normalized slash command name from inbound content."""
        event_kind = getattr(candidate, "event_kind", None)
        if event_kind not in {"private_message", "group_message"}:
            return None

        content = getattr(candidate, "content", "")
        if not isinstance(content, str):
            return None
        text = content.lstrip()
        if not text.startswith("/"):
            return None

        command_text = text[1:].strip()
        if not command_text:
            return None
        command = command_text.split(maxsplit=1)[0].lower()
        return command or None

    def _is_super_admin(self, sender_id: str) -> bool:
        """Return whether the sender can use admin-only slash commands."""
        return sender_id in self.config.super_admins

    @staticmethod
    def _int_value(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def _reset_group_mute_sync_state(self) -> None:
        self._muted_groups.clear()
        self._known_group_mute_states.clear()
        self._group_mute_seed_ready.clear()

        warmup_task = self._group_mute_warmup_task
        self._group_mute_warmup_task = None
        if warmup_task is not None:
            warmup_task.cancel()

        for task in self._group_mute_state_tasks.values():
            task.cancel()
        self._group_mute_state_tasks.clear()

        for future in self._group_mute_state_waiters.values():
            if not future.done():
                future.cancel()
        self._group_mute_state_waiters.clear()

    async def _refresh_group_mute_states(self) -> None:
        if self._self_id is None:
            return

        try:
            response = await self._send_api_request(
                "get_group_list",
                {"no_cache": False},
            )
        except Exception as exc:
            if self._should_stop():
                logger.debug("Anon skipped group mute sync during shutdown")
                self._group_mute_seed_ready.set()
                return
            logger.warning("Anon get_group_list failed during mute sync: {}", exc)
            self._group_mute_seed_ready.set()
            return

        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            logger.warning(
                (
                    "Anon get_group_list returned failure during mute sync: "
                    "status={} retcode={}"
                ),
                response.status,
                response.retcode,
            )
            self._group_mute_seed_ready.set()
            return

        groups = response.data
        if not isinstance(groups, list):
            logger.warning("Anon get_group_list returned invalid data during mute sync")
            self._group_mute_seed_ready.set()
            return

        group_ids: list[str] = []
        for item in groups:
            if not isinstance(item, dict):
                continue
            group_id = normalize_onebot_id(item.get("group_id"))
            if group_id is None:
                continue
            group_ids.append(group_id)
            self._group_mute_state_waiters.setdefault(
                group_id,
                asyncio.get_running_loop().create_future(),
            )

        logger.info("Anon mute sync discovered groups: {}", len(group_ids))

        self._group_mute_seed_ready.set()

        if group_ids:
            self._group_mute_warmup_task = asyncio.create_task(
                self._warmup_group_mute_states(group_ids)
            )
        else:
            logger.info("Anon mute sync completed: scanned_groups=0 muted_groups=0")

    async def _warmup_group_mute_states(self, group_ids: list[str]) -> None:
        semaphore = asyncio.Semaphore(_GROUP_MUTE_WARMUP_CONCURRENCY)

        async def warm_group(group_id: str) -> None:
            async with semaphore:
                await self._ensure_group_mute_state_known(group_id)

        try:
            await asyncio.gather(*(warm_group(group_id) for group_id in group_ids))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Anon group mute warm-up failed: {}", exc)
        finally:
            logger.info(
                "Anon mute sync completed: scanned_groups={} muted_groups={}",
                len(group_ids),
                len(self._muted_groups),
            )
            if self._group_mute_warmup_task is asyncio.current_task():
                self._group_mute_warmup_task = None

    async def _ensure_group_mute_state_known(self, group_id: str) -> None:
        if group_id in self._known_group_mute_states:
            return

        waiter = self._group_mute_state_waiters.get(group_id)
        if waiter is None or waiter.cancelled():
            waiter = asyncio.get_running_loop().create_future()
            self._group_mute_state_waiters[group_id] = waiter

        task = self._group_mute_state_tasks.get(group_id)
        if task is None or task.done():
            task = asyncio.create_task(
                self._sync_single_group_mute_state(group_id, waiter)
            )
            self._group_mute_state_tasks[group_id] = task

        await asyncio.shield(waiter)

    async def _sync_single_group_mute_state(
        self,
        group_id: str,
        waiter: asyncio.Future[None],
    ) -> None:
        try:
            muted_until = await self._fetch_group_muted_until(group_id)
            current_waiter = self._group_mute_state_waiters.get(group_id)
            if current_waiter is waiter and not waiter.done():
                if muted_until is None:
                    self._muted_groups.pop(group_id, None)
                else:
                    self._muted_groups[group_id] = muted_until
                    logger.info(
                        (
                            "Anon mute sync marked group as muted: "
                            "group_id={} muted_until={}"
                        ),
                        group_id,
                        muted_until,
                    )
                self._mark_group_mute_state_known(group_id)
        except asyncio.CancelledError:
            raise
        finally:
            task = self._group_mute_state_tasks.get(group_id)
            if task is asyncio.current_task():
                self._group_mute_state_tasks.pop(group_id, None)

    def _mark_group_mute_state_known(self, group_id: str) -> None:
        self._known_group_mute_states.add(group_id)
        waiter = self._group_mute_state_waiters.get(group_id)
        if waiter is None:
            waiter = asyncio.get_running_loop().create_future()
            self._group_mute_state_waiters[group_id] = waiter
        if not waiter.done():
            waiter.set_result(None)

    async def _fetch_group_muted_until(self, group_id: str) -> int | None:
        try:
            response = await self._send_api_request(
                "get_group_shut_list",
                {"group_id": group_id},
            )
        except Exception as exc:
            if self._should_stop():
                logger.debug("Anon skipped get_group_shut_list during shutdown")
                return None
            logger.warning(
                "Anon get_group_shut_list failed for group {}: {}",
                group_id,
                exc,
            )
            return None

        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            logger.warning(
                (
                    "Anon get_group_shut_list returned failure for group {}: "
                    "status={} retcode={}"
                ),
                group_id,
                response.status,
                response.retcode,
            )
            return None

        entries = response.data
        if not isinstance(entries, list):
            logger.warning(
                "Anon get_group_shut_list returned invalid data for group {}",
                group_id,
            )
            return None

        now = int(time.time())
        for item in entries:
            if not isinstance(item, dict):
                continue
            user_id = normalize_onebot_id(item.get("uin"))
            if user_id != self._self_id:
                continue
            muted_until = self._int_value(item.get("shutUpTime"))
            if muted_until is None or muted_until <= now:
                return None
            return muted_until
        return None

    def _handle_group_ban_notice(self, payload: OneBotRawEvent) -> bool:
        if payload.post_type != "notice" or payload.notice_type != "group_ban":
            return False

        group_id = normalize_onebot_id(payload.group_id)
        if group_id is None:
            return True

        self_id = normalize_onebot_id(payload.self_id) or self._self_id
        user_id = normalize_onebot_id(payload.user_id)
        if self_id is None or user_id != self_id:
            return True

        if payload.sub_type == "lift_ban":
            self._muted_groups.pop(group_id, None)
            self._mark_group_mute_state_known(group_id)
            logger.info("Anon bot mute lifted: group_id={}", group_id)
            return True

        if payload.sub_type != "ban":
            return True

        event_time = self._int_value(payload.time)
        duration = self._int_value(getattr(payload, "duration", None))
        if event_time is None or duration is None:
            logger.warning(
                (
                    "Anon ignored malformed bot mute notice: "
                    "group_id={} time={} duration={}"
                ),
                group_id,
                payload.time,
                getattr(payload, "duration", None),
            )
            return True

        muted_until = event_time + duration
        self._muted_groups[group_id] = muted_until
        self._mark_group_mute_state_known(group_id)
        logger.info(
            "Anon bot muted in group: group_id={} duration={} muted_until={}",
            group_id,
            duration,
            muted_until,
        )
        return True

    async def _wait_for_group_mute_state(self, group_id: str) -> None:
        await self._group_mute_seed_ready.wait()
        await self._ensure_group_mute_state_known(group_id)

    def _is_group_muted(self, group_id: str) -> bool:
        muted_until = self._muted_groups.get(group_id)
        if muted_until is None:
            return False
        now = int(time.time())
        if muted_until <= now:
            self._muted_groups.pop(group_id, None)
            logger.info(
                "Anon cleared expired mute state: group_id={} muted_until={}",
                group_id,
                muted_until,
            )
            return False
        return True

    async def _handle_inbound_event(self, payload: OneBotRawEvent) -> None:
        if self._handle_group_ban_notice(payload):
            return

        candidate = normalize_inbound_event(
            payload,
            config=self.config,
            self_id=self._self_id,
        )
        if candidate is None:
            logger.debug(
                (
                    "Anon ignored OneBot event: post_type={} "
                    "meta_event_type={} notice_type={}"
                ),
                payload.post_type,
                payload.meta_event_type,
                payload.notice_type,
            )
            return

        if candidate.event_kind == "group_message":
            group_id = normalize_onebot_id(candidate.metadata.get("group_id"))
            if group_id is not None:
                await self._wait_for_group_mute_state(group_id)
                if self._is_group_muted(group_id):
                    logger.info(
                        "Anon ignored muted group inbound: group_id={} sender_id={}",
                        group_id,
                        candidate.sender_id,
                    )
                    return

        slash_command = self._extract_slash_command(candidate)
        candidate.metadata["slash_command"] = slash_command
        if slash_command is not None:
            if not self._is_super_admin(candidate.sender_id):
                logger.warning(
                    "Slash command denied for sender {} in chat {} on channel {}: /{}",
                    candidate.sender_id,
                    candidate.chat_id,
                    self.name,
                    slash_command,
                )
                return
        else:
            allow_list = self.config.allow_from
            allowed = False
            if not allow_list:
                logger.warning("{}: allow_from is empty — all access denied", self.name)
            elif "*" in allow_list or candidate.sender_id in allow_list:
                allowed = True
            else:
                group_id = normalize_onebot_id(candidate.metadata.get("group_id"))
                allowed = group_id is not None and group_id in allow_list

            if not allowed:
                logger.warning(
                    "Access denied for sender {} in chat {} on channel {}. "
                    "Add the sender ID or group ID to allow_from to grant access.",
                    candidate.sender_id,
                    candidate.chat_id,
                    self.name,
                )
                return

        try:
            processed = await process_inbound_candidate(
                candidate,
                buffer=self._buffer,
                forward_resolver=self._resolve_forward_content,
                image_downloader=self._download_inbound_image,
                voice_processor=self._process_inbound_voice,
            )
        except Exception as exc:
            logger.warning("Anon inbound processing failed: {}", exc)
            return
        candidate = processed.candidate
        reply_targets = await self._collect_reply_targets(candidate)
        if reply_targets and reply_targets[0].is_from_self:
            candidate.reply_target_from_self = True
            candidate.metadata["reply_target_from_self"] = True

        slash_command = candidate.metadata.get("slash_command")
        if slash_command is not None:
            candidate.metadata["trigger_reason"] = (
                "slash_status" if slash_command == "status" else "slash_command"
            )
            routed = candidate
        else:
            routed = self._router.route(candidate)
        cache_inbound_candidate(
            candidate,
            buffer=self._buffer,
            expanded_forwards=processed.expanded_forwards,
        )
        self._write_inbound_cache_file()
        if routed is None:
            logger.debug(
                "Anon dropped inbound candidate: event_kind={} chat_id={}",
                candidate.event_kind,
                candidate.chat_id,
            )
            return

        serialized = serialize_buffer_chat(
            self._buffer,
            routed.chat_id,
            self_id=self._self_id,
            self_nickname=self._self_nickname,
            extra_reply_targets=reply_targets,
        )
        metadata = dict(routed.metadata)

        content = routed.content
        media = list(routed.media)
        slash_command = metadata.get("slash_command")
        if serialized is not None:
            if slash_command is None:
                content = serialized.text
                media = list(serialized.media)
            metadata["cqmsg_message_ids"] = list(serialized.message_ids)
            metadata["cqmsg_count"] = serialized.count

        if self.supports_streaming:
            metadata["_wants_stream"] = True

        await self.bus.publish_inbound(
            InboundMessage(
                channel=self.name,
                sender_id=routed.sender_id,
                chat_id=routed.chat_id,
                content=content,
                media=media,
                metadata=metadata,
                session_key_override=routed.session_key,
            )
        )
        if serialized is not None:
            self._buffer.mark_chat_entries_consumed(
                routed.chat_id,
                serialized.message_ids,
            )

    def _write_inbound_cache_file(self) -> None:
        snapshot = {
            chat_id: [
                asdict(entry)
                for entry in entries.values()
                if not entry.is_from_self
            ]
            for chat_id, entries in self._buffer._messages.items()
        }
        self._inbound_cache_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def _handle_disconnect(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        reason: str,
    ) -> None:
        if self._ws is ws:
            self._ws = None

        self._fail_pending(ConnectionError(reason))

        ping_task = self._ping_task
        self._ping_task = None
        if ping_task is not None and ping_task is not asyncio.current_task():
            ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ping_task

        if not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()

        self._reset_group_mute_sync_state()

        if not self._should_stop():
            logger.info("Anon WebSocket disconnected: {}", reason)

    async def _enqueue_inbound_event(self, payload: OneBotRawEvent) -> None:
        if self._should_stop():
            return
        key = self._session_worker_key_for_payload(payload)
        worker = await self._get_or_create_session_worker(key)
        try:
            worker.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning(
                "Anon dropped inbound event for session {}: queue is full (max={})",
                key,
                _SESSION_QUEUE_MAX_SIZE,
            )

    async def _get_or_create_session_worker(self, key: str) -> _SessionWorker:
        async with self._session_workers_lock:
            existing = self._session_workers.get(key)
            if existing is not None and not existing.task.done():
                return existing

            worker = _SessionWorker(
                queue=asyncio.Queue(maxsize=_SESSION_QUEUE_MAX_SIZE),
                task=asyncio.create_task(self._run_session_worker(key)),
            )
            self._session_workers[key] = worker
            return worker

    async def _run_session_worker(self, key: str) -> None:
        try:
            while True:
                worker = self._session_workers.get(key)
                if worker is None:
                    return
                payload = await worker.queue.get()
                try:
                    await self._handle_inbound_event(payload)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Anon inbound handling failed for session {}: {}",
                        key,
                        exc,
                    )
                finally:
                    worker.queue.task_done()
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _session_worker_key_for_payload(payload: OneBotRawEvent) -> str:
        group_id = normalize_onebot_id(payload.group_id)
        if group_id is not None:
            return build_group_chat_id(group_id)

        user_id = normalize_onebot_id(payload.user_id)
        if user_id is not None:
            return build_private_chat_id(user_id)

        sender = payload.sender
        if sender is not None:
            sender_id = normalize_onebot_id(sender.user_id)
            if sender_id is not None:
                return build_private_chat_id(sender_id)

        return "private:unknown"

    async def _shutdown(self) -> None:
        self._fail_pending(ConnectionError("Channel stopped"))

        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()

        self._reset_group_mute_sync_state()

        current_task = asyncio.current_task()
        for attr in ("_ping_task", "_reader_task"):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is None or task is current_task:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        workers = list(self._session_workers.values())
        self._session_workers.clear()
        for worker in workers:
            task = worker.task
            if task is current_task:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
            self._session = None

    def _require_session(self) -> aiohttp.ClientSession:
        session = self._session
        if session is None or session.closed:
            raise RuntimeError("WebSocket session is not available")
        return session

    def _should_stop(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    async def _sleep_until_stop(self, delay: float) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            await asyncio.sleep(delay)
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)

    def _next_echo(self) -> str:
        self._echo_counter += 1
        return f"api_{self._echo_counter}"

    def _resolve_pending(self, payload: OneBotRawEvent) -> bool:
        echo = payload.echo
        if not echo:
            return False

        future = self._pending.get(echo)
        if future is None:
            logger.debug("Anon received response for unknown echo={}", echo)
            return True
        if not future.done():
            future.set_result(payload)
        return True

    def _fail_pending(self, exc: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)

    @staticmethod
    def _decode_payload(raw: str) -> OneBotRawEvent | None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Anon received non-JSON payload: {}", raw)
            return None
        if not isinstance(payload, dict):
            logger.debug("Anon ignored non-object payload: {}", payload)
            return None
        try:
            return OneBotRawEvent.model_validate(payload)
        except ValidationError as exc:
            logger.warning("Anon received invalid OneBot payload: {}", exc)
            return None

    @staticmethod
    def _is_api_response(payload: OneBotRawEvent) -> bool:
        status = payload.status
        if isinstance(status, str):
            return status in {"ok", "failed"}
        return isinstance(status, BotStatus)

    @staticmethod
    def _get_forward_failure_text(response: OneBotRawEvent) -> str:
        wording = getattr(response, "wording", None)
        if isinstance(wording, str):
            wording = wording.strip()
            if wording:
                return wording

        message = response.message
        if isinstance(message, str):
            message = message.strip()
            if message:
                return message
        return ""

    @classmethod
    def _is_get_forward_msg_failure(cls, response: OneBotRawEvent) -> bool:
        return (
            response.status == "failed"
            or str(response.retcode or "") not in {"", "0"}
            or response.data is None
        )

    async def _resolve_forward_content(self, forward_id: str) -> Any:
        try:
            response = await self._send_api_request(
                "get_forward_msg",
                {"id": forward_id},
            )
        except Exception as exc:
            logger.warning("Anon get_forward_msg failed: {}", exc)
            raise

        if self._is_get_forward_msg_failure(response):
            logger.warning(
                (
                    "Anon get_forward_msg returned failure: "
                    "status={} retcode={} message={}"
                ),
                response.status,
                response.retcode,
                self._get_forward_failure_text(response),
            )
            return None
        return response.data

    async def _collect_reply_targets(
        self,
        candidate: Any,
    ) -> list[MessageEntry]:
        reply_to_message_id = candidate.reply_to_message_id
        if not reply_to_message_id:
            return []
        if (
            self._buffer.get(candidate.chat_id, reply_to_message_id)
            is not None
        ):
            return []

        reply_entry = await self._fetch_reply_target_message_entry(
            candidate.chat_id,
            reply_to_message_id,
        )
        if reply_entry is None:
            return []
        return [reply_entry]

    async def _fetch_reply_target_message_entry(
        self,
        chat_id: str,
        message_id: str,
    ) -> MessageEntry | None:
        try:
            response = await self._send_api_request(
                "get_msg",
                {"message_id": int(message_id)},
            )
        except Exception as exc:
            logger.warning("Anon get_msg failed for {}: {}", message_id, exc)
            return None

        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            logger.warning(
                "Anon get_msg returned failure for {}: status={} retcode={}",
                message_id,
                response.status,
                response.retcode,
            )
            return None

        payload = response.data if isinstance(response.data, dict) else None
        if payload is None:
            logger.warning("Anon get_msg returned invalid data for {}", message_id)
            return None

        message_type = str(payload.get("message_type") or "")
        group_id = normalize_onebot_id(payload.get("group_id"))
        raw_sender = payload.get("sender")
        sender = raw_sender if isinstance(raw_sender, dict) else {}
        sender_id = normalize_onebot_id(
            payload.get("user_id") or sender.get("user_id")
        )
        fetched_message_id = normalize_onebot_id(payload.get("message_id"))
        if fetched_message_id is None or sender_id is None:
            logger.warning("Anon get_msg missing ids for {}", message_id)
            return None

        if message_type == "group":
            if group_id is None:
                logger.warning("Anon get_msg missing group_id for {}", message_id)
                return None
            fetched_chat_id = build_group_chat_id(group_id)
            event_kind = "group_message"
        else:
            fetched_chat_id = build_private_chat_id(sender_id)
            event_kind = "private_message"

        if fetched_chat_id != chat_id:
            logger.warning(
                "Anon get_msg returned mismatched chat for {}: expected={} actual={}",
                message_id,
                chat_id,
                fetched_chat_id,
            )
            return None

        candidate = normalize_inbound_event(
            OneBotRawEvent.model_validate(
                {
                    "post_type": "message",
                    "message_type": message_type or "private",
                    "message_id": fetched_message_id,
                    "group_id": group_id,
                    "user_id": sender_id,
                    "message": payload.get("message"),
                    "raw_message": str(payload.get("message") or ""),
                    "sender": sender,
                    "time": payload.get("time"),
                }
            ),
            config=self.config,
            self_id=self._self_id,
        )
        if candidate is None:
            logger.warning("Anon get_msg could not normalize message {}", message_id)
            return None
        candidate.event_kind = event_kind
        return build_message_entry(
            candidate,
            is_from_self=sender_id == self._self_id,
        )

    async def _download_inbound_image(self, media_item: dict[str, Any]) -> str | None:
        local_file = await self._download_inbound_media(media_item)
        if local_file is None:
            return None
        return str(local_file.resolve(strict=False))

    async def _process_inbound_voice(
        self,
        media_item: dict[str, Any],
    ) -> dict[str, str] | None:
        local_file = await self._download_inbound_media(media_item)
        if local_file is None:
            return None

        result = {"local_file_uri": self._file_uri(local_file)}
        transcription_file = await self._transcode_voice_for_transcription(local_file)
        if transcription_file is None:
            return result
        if transcription_file != local_file:
            result["transcription_local_file_uri"] = self._file_uri(
                transcription_file
            )
            with contextlib.suppress(FileNotFoundError):
                local_file.unlink()

        transcription_text = (await self.transcribe_audio(transcription_file)).strip()
        if transcription_text:
            result["transcription_text"] = transcription_text
        return result

    async def _transcode_voice_for_transcription(
        self,
        source_path: Path,
    ) -> Path | None:
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

    async def _download_inbound_media(self, media_item: dict[str, Any]) -> Path | None:
        file_name = str(media_item.get("file") or "").strip()
        url = str(media_item.get("url") or "").strip()
        if not file_name or not url:
            return None

        target_name = Path(file_name).name.strip()
        if not target_name:
            return None

        target_path = get_media_dir("anon") / target_name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            return target_path

        file_size_raw = str(media_item.get("file_size") or "").strip()
        if not file_size_raw:
            return None

        try:
            file_size = int(file_size_raw)
        except ValueError:
            return None

        if file_size > self.config.media_max_size_mb * 1024 * 1024:
            return None

        session = self._require_session()
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=_MEDIA_DOWNLOAD_TIMEOUT_S),
            ) as response:
                response.raise_for_status()
                body = await asyncio.wait_for(
                    response.read(),
                    timeout=_MEDIA_DOWNLOAD_TIMEOUT_S,
                )
        except TimeoutError:
            logger.warning(
                "Anon inbound media download timed out for {} after {}s",
                url,
                _MEDIA_DOWNLOAD_TIMEOUT_S,
            )
            return None

        target_path.write_bytes(body)
        return target_path

    @staticmethod
    def _file_uri(path: Path) -> str:
        resolved = path.resolve(strict=False)
        return f"file://{quote(resolved.as_posix(), safe='/:.-_~')}"

    @staticmethod
    def _segment_type_for_outbound_media(media_ref: str) -> str:
        suffix = Path(media_path_from_media_ref(media_ref)).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            return "image"
        if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
            return "video"
        if suffix in {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}:
            return "record"
        return "file"

    def _buffer_outbound_message(
        self,
        msg: OutboundMessage,
        response: OneBotRawEvent,
        *,
        content: str | None = None,
        media: list[str] | None = None,
    ) -> None:
        data = response.data if isinstance(response.data, dict) else {}
        message_id = normalize_onebot_id(data.get("message_id"))
        if message_id is None:
            return
        buffered_content = msg.content if content is None else content
        buffered_media = list(msg.media if media is None else media)
        segment_types = ["text"] if buffered_content else []
        segment_types.extend(
            [self._segment_type_for_outbound_media(item) for item in buffered_media]
        )
        self._buffer.add(
            MessageEntry(
                message_id=message_id,
                chat_id=msg.chat_id,
                sender_id=self._self_id or "",
                sender_name=self._self_nickname or self._self_id or "",
                is_from_self=True,
                content=buffered_content,
                sender_nickname=self._self_nickname,
                media=buffered_media,
                reply_to_message_id=normalize_onebot_id(
                    msg.metadata.get("reply_to_message_id")
                ),
                segment_types=segment_types,
                metadata=dict(msg.metadata),
            )
        )

