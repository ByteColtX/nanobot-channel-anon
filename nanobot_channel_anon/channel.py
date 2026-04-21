"""OneBot v11 WebSocket transport for the anon channel."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from pydantic import ValidationError

from nanobot_channel_anon.buffer import Buffer, MessageEntry
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import (
    cache_inbound_candidate,
    normalize_inbound_event,
    process_inbound_candidate,
)
from nanobot_channel_anon.onebot import BotStatus, OneBotAPIRequest, OneBotRawEvent
from nanobot_channel_anon.outbound import build_send_request
from nanobot_channel_anon.router import InboundRouter
from nanobot_channel_anon.utils import normalize_onebot_id

_CONNECT_TIMEOUT_S = 10.0
_PING_INTERVAL_S = 30.0
_READ_TIMEOUT_S = 60.0
_API_TIMEOUT_S = 5.0
_RECONNECT_INTERVAL_S = 5.0
_INBOUND_CACHE_PATH = Path(".anon_inbound_buffer.json")


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
        self._inbound_tasks: set[asyncio.Task[None]] = set()
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[OneBotRawEvent]] = {}
        self._echo_counter = 0
        self._self_id: str | None = None
        self._buffer = Buffer(self.config.max_context_messages)
        self._router = InboundRouter(self.config)
        self._inbound_cache_path = _INBOUND_CACHE_PATH

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """Return the default channel config."""
        return AnonConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """Start the channel and keep reconnecting until stopped."""
        ws_url = self._ws_url()
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
                await self._fetch_self_id()

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
        action, params = build_send_request(
            msg.chat_id,
            msg.content,
            media=msg.media,
            metadata=msg.metadata,
        )
        response = await self._send_api_request(action, params)
        if response.status == "failed":
            raise RuntimeError(
                f"OneBot action {action} failed: retcode={response.retcode}"
            )
        self._buffer_outbound_message(msg, response)

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a delta as a normal outbound text message."""
        if not delta:
            return
        await self.send(
            OutboundMessage(
                channel=self.name,
                chat_id=chat_id,
                content=delta,
                metadata=metadata or {},
            )
        )

    async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        session = self._require_session()
        headers: dict[str, str] = {}
        access_token = self._access_token()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        timeout = aiohttp.ClientWSTimeout(ws_receive=_READ_TIMEOUT_S)  # pyright: ignore[reportCallIssue]
        ws = await session.ws_connect(
            self._ws_url(),
            headers=headers or None,
            autoping=True,
            heartbeat=None,
            timeout=timeout,
        )
        self._ws = ws
        logger.info("Anon WebSocket connected: {}", self._ws_url())
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
                self._spawn_inbound_task(payload)
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
        nickname = info.get("nickname")
        logger.info(
            "Anon bot identity loaded: self_id={} nickname={}",
            user_id,
            nickname,
        )

    async def _handle_inbound_event(self, payload: OneBotRawEvent) -> None:
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

        if not self.is_allowed(candidate.sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                candidate.sender_id,
                self.name,
            )
            return

        try:
            processed = await process_inbound_candidate(
                candidate,
                buffer=self._buffer,
                forward_resolver=self._resolve_forward_content,
            )
        except Exception as exc:
            logger.warning("Anon inbound processing failed: {}", exc)
            return
        candidate = processed.candidate
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

        if not self._should_stop():
            logger.info("Anon WebSocket disconnected: {}", reason)

    def _spawn_inbound_task(self, payload: OneBotRawEvent) -> None:
        task = asyncio.create_task(self._handle_inbound_event(payload))
        self._inbound_tasks.add(task)
        task.add_done_callback(self._on_inbound_task_done)

    def _on_inbound_task_done(self, task: asyncio.Task[None]) -> None:
        self._inbound_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Anon inbound handling failed: {}", exc)

    async def _shutdown(self) -> None:
        self._fail_pending(ConnectionError("Channel stopped"))

        ws = self._ws
        self._ws = None
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()

        current_task = asyncio.current_task()
        for attr in ("_ping_task", "_reader_task"):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is None or task is current_task:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        inbound_tasks = list(self._inbound_tasks)
        self._inbound_tasks.clear()
        for task in inbound_tasks:
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

    def _ws_url(self) -> str:
        return self.config.ws_url

    def _access_token(self) -> str:
        return self.config.access_token

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

    def _buffer_outbound_message(
        self,
        msg: OutboundMessage,
        response: OneBotRawEvent,
    ) -> None:
        data = response.data if isinstance(response.data, dict) else {}
        message_id = normalize_onebot_id(data.get("message_id"))
        if message_id is None:
            return
        self._buffer.add(
            MessageEntry(
                message_id=message_id,
                chat_id=msg.chat_id,
                sender_id=self._self_id or "",
                sender_name=self._self_id or "",
                is_from_self=True,
                content=msg.content,
                media=list(msg.media),
                reply_to_message_id=normalize_onebot_id(
                    msg.metadata.get("reply_to_message_id")
                ),
                segment_types=["text"] if msg.content else [],
                metadata=dict(msg.metadata),
            )
        )

