"""OneBot transport facade for the new anon channel kernel."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

import aiohttp
from loguru import logger

from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import NormalizedMessage
from nanobot_channel_anon.onebot import OneBotAPIRequest, OneBotRawEvent
from nanobot_channel_anon.utils import normalize_onebot_id

_RESPONSE_TIMEOUT_SECONDS = 10.0
_OUTBOUND_UPLOAD_CHUNK_SIZE = 64 * 1024
_OUTBOUND_UPLOAD_FILE_RETENTION_MS = 30 * 1000
_GROUP_MUTE_NOTICE_SUBTYPES = {"ban", "lift_ban"}


class OneBotConnection(Protocol):
    """传输层依赖的最小连接协议."""

    async def receive_json(self) -> dict[str, Any]:
        """读取下一条 JSON 消息."""
        raise NotImplementedError

    async def send_json(self, payload: dict[str, Any]) -> None:
        """发送一条 JSON 消息."""
        raise NotImplementedError

    async def close(self) -> None:
        """关闭底层连接."""
        raise NotImplementedError


class AiohttpOneBotConnection:
    """基于 aiohttp 的 OneBot WebSocket 连接适配器."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        websocket: aiohttp.ClientWebSocketResponse,
    ) -> None:
        """保存 aiohttp 连接对象."""
        self._session = session
        self._websocket = websocket

    async def receive_json(self) -> dict[str, Any]:
        """读取下一条 JSON 消息."""
        while True:
            message = await self._websocket.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                text = message.data
                if not isinstance(text, str):
                    raise RuntimeError("unexpected non-text websocket payload")
                payload = json.loads(text)
                if not isinstance(payload, dict):
                    raise RuntimeError("websocket payload must be a JSON object")
                return payload
            if message.type == aiohttp.WSMsgType.BINARY:
                raise RuntimeError("binary websocket frame is not supported")
            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }:
                raise EOFError
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(
                    "websocket receive failed"
                ) from self._websocket.exception()

    async def send_json(self, payload: dict[str, Any]) -> None:
        """发送一条 JSON 消息."""
        await self._websocket.send_json(payload)

    async def close(self) -> None:
        """关闭 WebSocket 与会话."""
        await self._websocket.close()
        await self._session.close()


async def _default_connect(config: AnonConfig) -> OneBotConnection:
    """建立默认 OneBot WebSocket 连接."""
    headers = {}
    if config.access_token:
        headers["Authorization"] = f"Bearer {config.access_token}"
    session = aiohttp.ClientSession(headers=headers or None)
    websocket = await session.ws_connect(config.ws_url)
    return AiohttpOneBotConnection(session=session, websocket=websocket)


class OneBotTransport:
    """真正负责 WebSocket 生命周期与请求收发的传输层."""

    def __init__(
        self,
        *,
        config: AnonConfig,
        mapper: OneBotMapper | None = None,
        state: OneBotStateAdapter | None = None,
        connect: Callable[[], Awaitable[OneBotConnection]] | None = None,
        bus: object | None = None,
    ) -> None:
        """初始化传输层依赖."""
        del bus
        self.config = config
        self.state = state or OneBotStateAdapter()
        self.mapper = mapper or OneBotMapper(self_id=self.state.self_id)
        self._connect = connect or (lambda: _default_connect(config))
        self._stop_event = asyncio.Event()
        self._running = False
        self._connection: OneBotConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._pending_responses: dict[str, asyncio.Future[OneBotRawEvent]] = {}
        self._inbound_handler: (
            Callable[[NormalizedMessage], Awaitable[None]] | None
        ) = None
        self._inbound_queue: asyncio.Queue[NormalizedMessage | None] = asyncio.Queue()
        self.sent_requests: list[OneBotAPIRequest] = []
        self._startup_group_ids: set[str] = set()

    @property
    def is_running(self) -> bool:
        """返回传输层是否正在运行."""
        return self._running

    def set_inbound_handler(
        self,
        handler: Callable[[NormalizedMessage], Awaitable[None]],
    ) -> None:
        """注册标准化入站消息处理器."""
        self._inbound_handler = handler

    async def start(self) -> None:
        """启动传输层并保持阻塞直到停止."""
        if self._running:
            return
        self._stop_event = asyncio.Event()
        logger.info("Anon OneBot transport starting: {}", self.config.ws_url)
        try:
            self._connection = await self._connect()
            logger.info("Anon WebSocket connected: {}", self.config.ws_url)
            self._running = True
            self._reader_task = asyncio.create_task(self._read_loop())
            self._dispatcher_task = asyncio.create_task(self._dispatch_inbound_loop())
            await self._run_startup_sync()
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            done, pending = await asyncio.wait(
                [self._reader_task, self._dispatcher_task, stop_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                await task
        finally:
            try:
                await self._shutdown_reader()
            finally:
                self._running = False
                logger.info("Anon OneBot transport stopped")

    async def stop(self) -> None:
        """停止传输层."""
        if not self._running:
            return
        self._stop_event.set()
        await self._shutdown_reader()

    async def send_requests(
        self,
        requests: Sequence[OneBotAPIRequest],
    ) -> list[OneBotRawEvent]:
        """发送一批 OneBot 动作请求."""
        responses: list[OneBotRawEvent] = []
        for request in requests:
            response = await self._send_api_request(
                request.action,
                request.params if isinstance(request.params, dict) else None,
                request=request,
            )
            self._raise_for_failed_response(request, response)
            responses.append(response)
        return responses

    async def upload_local_media(self, path: Path) -> str:
        """将本地媒体文件上传为 NapCat 可发送的 file_path."""
        return await self._upload_outbound_file_via_stream(path)

    async def _read_loop(self) -> None:
        """持续读取 OneBot 事件与响应."""
        assert self._connection is not None
        try:
            while not self._stop_event.is_set():
                payload = await self._connection.receive_json()
                raw = OneBotRawEvent.model_validate(payload)
                self._update_state(raw)
                if raw.echo:
                    future = self._pending_responses.get(raw.echo)
                    if future is not None and not future.done():
                        future.set_result(raw)
                    continue
                if self._handle_group_ban_notice(raw):
                    continue

                message = self.mapper.map_inbound_event(raw)
                if message is None or self._inbound_handler is None:
                    continue
                if (
                    message.conversation.kind == "group"
                    and self.state.is_group_muted(message.conversation.id)
                ):
                    logger.info(
                        "Anon ignored muted group inbound: group_id={} sender_id={}",
                        message.conversation.id,
                        message.sender_id,
                    )
                    continue
                await self._inbound_queue.put(message)
        except EOFError:
            self._stop_event.set()
        finally:
            await self._inbound_queue.put(None)
            for future in self._pending_responses.values():
                if not future.done():
                    future.cancel()

    async def _dispatch_inbound_loop(self) -> None:
        """串行消费标准化入站消息, 避免阻塞读循环."""
        try:
            while not self._stop_event.is_set():
                message = await self._inbound_queue.get()
                if message is None:
                    break
                if self._inbound_handler is None:
                    continue
                await self._inbound_handler(message)
        except EOFError:
            self._stop_event.set()

    def _update_state(self, raw: OneBotRawEvent) -> None:
        """按事件更新最小运行时状态."""
        if raw.self_id is not None:
            normalized_self_id = normalize_onebot_id(raw.self_id)
            self.state.set_self_id(normalized_self_id)
            self.mapper.self_id = self.state.self_id

    @staticmethod
    def _raise_for_failed_response(
        request: OneBotAPIRequest,
        response: OneBotRawEvent,
    ) -> None:
        """校验动作响应是否明确成功."""
        if response.status == "ok" and str(response.retcode or 0) == "0":
            return
        raise RuntimeError(f"OneBot action failed: {request.action}")

    async def _shutdown_reader(self) -> None:
        """关闭读取循环与底层连接."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        if self._reader_task is not None:
            with contextlib.suppress(asyncio.CancelledError, EOFError):
                await self._reader_task
            self._reader_task = None
        if self._dispatcher_task is not None:
            await self._inbound_queue.put(None)
            with contextlib.suppress(asyncio.CancelledError, EOFError):
                await self._dispatcher_task
            self._dispatcher_task = None

    async def _run_startup_sync(self) -> None:
        """执行启动后的 bot 信息与群禁言状态同步."""
        await self._fetch_self_identity()
        await self._refresh_group_mute_states()

    async def get_message(self, message_id: str) -> OneBotRawEvent | None:
        """按 message_id 拉取一条原始 OneBot 消息响应."""
        try:
            numeric_message_id = int(message_id)
        except ValueError:
            return None
        response = await self._send_api_request(
            "get_msg",
            {"message_id": numeric_message_id},
        )
        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            return None
        payload = response.data if isinstance(response.data, dict) else None
        if payload is None:
            return None
        return OneBotRawEvent.model_validate(
            {
                "post_type": "message",
                "message_type": payload.get("message_type") or "private",
                "message_id": payload.get("message_id"),
                "group_id": payload.get("group_id"),
                "user_id": payload.get("user_id"),
                "message": payload.get("message"),
                "raw_message": str(payload.get("message") or ""),
                "sender": payload.get("sender"),
                "self_id": self.state.self_id,
                "time": payload.get("time"),
            }
        )

    async def _send_api_request(
        self,
        action: str,
        params: dict[str, Any] | None,
        *,
        request: OneBotAPIRequest | None = None,
    ) -> OneBotRawEvent:
        """发送一条需要等待 echo 响应的 API 请求."""
        outbound_request = request or OneBotAPIRequest(action=action, params=params)
        if self._connection is None:
            raise RuntimeError("transport is not running")

        outbound = outbound_request.model_copy(
            update={"echo": outbound_request.echo or self._next_echo()}
        )
        future: asyncio.Future[OneBotRawEvent] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_responses[outbound.echo or ""] = future
        self.sent_requests.append(outbound)
        await self._connection.send_json(outbound.model_dump(exclude_none=True))
        try:
            response = await asyncio.wait_for(
                future, timeout=_RESPONSE_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise RuntimeError(f"OneBot action timed out: {outbound.action}") from exc
        finally:
            self._pending_responses.pop(outbound.echo or "", None)
        return response

    async def _upload_outbound_file_via_stream(self, path: Path) -> str:
        """分块上传本地媒体文件并返回 NapCat file_path."""
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
            stream_id = self._next_echo()
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
        """校验上传分块响应至少未显式失败."""
        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            raise RuntimeError(f"upload_file_stream failed for {filename}")

    @staticmethod
    def _extract_uploaded_file_path(
        response: OneBotRawEvent,
        *,
        filename: str,
    ) -> str:
        """从上传完成响应里提取 NapCat file_path."""
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

    async def _fetch_self_identity(self) -> None:
        """拉取并记录 bot 自身身份信息."""
        try:
            response = await self._send_api_request("get_login_info", None)
        except Exception as exc:
            if self._stop_event.is_set():
                logger.debug("Anon skipped get_login_info during shutdown")
                return
            logger.warning("Anon get_login_info failed: {}", exc)
            return

        if response.status == "failed" or str(response.retcode or "") not in {"", "0"}:
            logger.warning(
                "Anon get_login_info returned failure: status={} retcode={}",
                response.status,
                response.retcode,
            )
            return

        info = response.data
        if not isinstance(info, dict):
            logger.warning("Anon get_login_info returned invalid data")
            return

        user_id = normalize_onebot_id(info.get("user_id"))
        if user_id is None:
            logger.warning("Anon get_login_info missing user_id: {}", info)
            return

        nickname = str(info.get("nickname") or "")
        self.state.set_self_profile(user_id=user_id, nickname=nickname)
        self.mapper.self_id = self.state.self_id
        logger.info(
            "Anon bot identity loaded: self_id={} nickname={}",
            user_id,
            nickname,
        )

    async def _refresh_group_mute_states(self) -> None:
        """扫描白名单群并同步 bot 禁言状态."""
        self._startup_group_ids = set()
        if self.state.self_id is None:
            return

        try:
            response = await self._send_api_request(
                "get_group_list",
                {"no_cache": False},
            )
        except Exception as exc:
            if self._stop_event.is_set():
                logger.debug("Anon skipped group mute sync during shutdown")
                return
            logger.warning("Anon get_group_list failed during mute sync: {}", exc)
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
            return

        groups = response.data
        if not isinstance(groups, list):
            logger.warning("Anon get_group_list returned invalid data during mute sync")
            return

        allowed_groups = self.config.allowed_conversation_keys
        group_ids: list[str] = []
        for item in groups:
            if not isinstance(item, dict):
                continue
            group_id = normalize_onebot_id(item.get("group_id"))
            if group_id is None:
                continue
            if not self.config.allow_all and f"group:{group_id}" not in allowed_groups:
                continue
            group_ids.append(group_id)

        self._startup_group_ids = set(group_ids)
        logger.info("Anon mute sync discovered groups: {}", len(group_ids))

        if not group_ids:
            logger.info("Anon mute sync completed: scanned_groups=0 muted_groups=0")
            return

        for group_id in group_ids:
            muted_until = await self._fetch_group_muted_until(group_id)
            if muted_until is None:
                self.state.clear_group_mute(group_id)
                continue
            self.state.set_group_muted_until(group_id, muted_until)
            logger.info(
                "Anon mute sync marked group as muted: group_id={} muted_until={}",
                group_id,
                muted_until,
            )

        logger.info(
            "Anon mute sync completed: scanned_groups={} muted_groups={}",
            len(group_ids),
            len(self.state.muted_group_ids()),
        )

    async def _fetch_group_muted_until(self, group_id: str) -> int | None:
        """查询 bot 在指定群中的禁言截止时间."""
        try:
            response = await self._send_api_request(
                "get_group_shut_list",
                {"group_id": group_id},
            )
        except Exception as exc:
            if self._stop_event.is_set():
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

        for item in entries:
            if not isinstance(item, dict):
                continue
            user_id = normalize_onebot_id(item.get("uin"))
            if user_id != self.state.self_id:
                continue
            muted_until = self._int_value(item.get("shutUpTime"))
            if muted_until is None:
                return None
            return muted_until
        return None

    def _handle_group_ban_notice(self, raw: OneBotRawEvent) -> bool:
        """处理 bot 自身相关的群禁言通知."""
        if raw.post_type != "notice" or raw.notice_type != "group_ban":
            return False

        group_id = normalize_onebot_id(raw.group_id)
        if group_id is None:
            return True

        self_id = normalize_onebot_id(raw.self_id) or self.state.self_id
        user_id = normalize_onebot_id(raw.user_id)
        if self_id is None or user_id != self_id:
            return True

        if raw.sub_type == "lift_ban":
            self.state.clear_group_mute(group_id)
            logger.info("Anon bot mute lifted: group_id={}", group_id)
            return True
        if raw.sub_type not in _GROUP_MUTE_NOTICE_SUBTYPES:
            return True

        event_time = self._int_value(raw.time)
        duration = self._int_value(raw.duration)
        if event_time is None or duration is None:
            logger.warning(
                (
                    "Anon ignored malformed bot mute notice: "
                    "group_id={} time={} duration={}"
                ),
                group_id,
                raw.time,
                raw.duration,
            )
            return True

        muted_until = event_time + duration
        self.state.set_group_muted_until(group_id, muted_until)
        logger.info(
            "Anon bot muted in group: group_id={} duration={} muted_until={}",
            group_id,
            duration,
            muted_until,
        )
        return True

    @staticmethod
    def _int_value(value: Any) -> int | None:
        """把协议层整型字段安全转换为 int."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            try:
                return int(normalized)
            except ValueError:
                return None
        return None

    @staticmethod
    def _next_echo() -> str:
        """生成请求回声标识."""
        return uuid.uuid4().hex
