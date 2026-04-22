"""Tests for the OneBot WebSocket channel transport."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus

import nanobot_channel_anon.channel as channel_module
from nanobot_channel_anon.buffer import MessageEntry
from nanobot_channel_anon.channel import AnonChannel
from nanobot_channel_anon.onebot import BotStatus, OneBotAPIRequest, OneBotRawEvent


class OneBotWebSocketServer:
    """Tiny OneBot-compatible WebSocket server for transport tests."""

    def __init__(
        self,
        *,
        login_info_response: dict[str, Any] | None = None,
        on_action: Any = None,
    ) -> None:
        """Initialize the in-process test server."""
        self.login_info_response = login_info_response or {
            "status": "ok",
            "data": {"user_id": "42", "nickname": "anon-bot"},
        }
        self.on_action = on_action
        self.connection_count = 0
        self.headers: list[dict[str, str]] = []
        self.actions: list[dict[str, Any]] = []
        self.current_ws: web.WebSocketResponse | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_ws)

    @property
    def url(self) -> str:
        """Return the test server WebSocket URL."""
        assert self._site is not None
        sockets = self._site._server.sockets  # type: ignore[union-attr]
        port = sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/"

    async def start(self) -> None:
        """Start the test server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()

    async def close(self) -> None:
        """Stop the test server."""
        if self.current_ws is not None and not self.current_ws.closed:
            await self.current_ws.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._site = None
        self.current_ws = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Send a JSON event to the connected client."""
        assert self.current_ws is not None
        await self.current_ws.send_json(payload)

    async def close_current_connection(self) -> None:
        """Close the currently active WebSocket connection."""
        if self.current_ws is not None:
            await self.current_ws.close()

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        self.current_ws = ws
        self.connection_count += 1
        self.headers.append(dict(request.headers))

        try:
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                await self._handle_action(payload, ws)
        finally:
            if self.current_ws is ws:
                self.current_ws = None

        return ws

    async def _handle_action(
        self,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        self.actions.append(payload)
        handled = False
        if payload.get("action") == "get_login_info":
            response = dict(self.login_info_response)
            response["echo"] = payload["echo"]
            await ws.send_json(response)
            handled = True
        if self.on_action is not None:
            handled = bool(await self.on_action(self, payload, ws)) or handled
        if handled:
            return
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )


async def _wait_for(predicate: Any, timeout: float = 1.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


async def _stop_channel(
    channel: AnonChannel,
    task: asyncio.Task[None],
    server: OneBotWebSocketServer,
) -> None:
    await channel.stop()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await server.close()


async def _consume_inbound(bus: MessageBus, timeout: float = 1.0) -> InboundMessage:
    return await asyncio.wait_for(bus.consume_inbound(), timeout=timeout)


async def _reply_to_send_action(
    payload: dict[str, Any],
    ws: web.WebSocketResponse,
    *,
    status: str = "ok",
    retcode: int = 0,
    message_id: int = 9001,
) -> None:
    if payload.get("action") not in {"send_private_msg", "send_group_msg"}:
        return
    await ws.send_json(
        {
            "status": status,
            "retcode": retcode,
            "data": {"message_id": message_id},
            "echo": payload["echo"],
        }
    )


def _find_action(
    server: OneBotWebSocketServer,
    action_name: str,
) -> dict[str, Any]:
    return next(item for item in server.actions if item["action"] == action_name)


def _find_actions(
    server: OneBotWebSocketServer,
    action_name: str,
) -> list[dict[str, Any]]:
    return [item for item in server.actions if item["action"] == action_name]


class FakeDownloadResponse:
    """Minimal aiohttp-like response stub for media download tests."""

    def __init__(
        self,
        body: bytes,
        *,
        read_gate: asyncio.Event | None = None,
    ) -> None:
        """Store the response body returned by read()."""
        self._body = body
        self._read_gate = read_gate

    async def __aenter__(self) -> FakeDownloadResponse:
        """Enter the async context manager."""
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Exit the async context manager."""
        del exc_type, exc, tb

    def raise_for_status(self) -> None:
        """Pretend the response status is successful."""
        return None

    async def read(self) -> bytes:
        """Return the configured response body."""
        if self._read_gate is not None:
            await self._read_gate.wait()
        return self._body


def test_start_requires_url() -> None:
    """start() should reject an empty WebSocket URL."""

    async def case() -> None:
        channel = AnonChannel({"ws_url": ""}, MessageBus())
        with pytest.raises(ValueError, match="url"):
            await channel.start()

    asyncio.run(case())


def test_start_connects_with_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client should send Authorization when access_token is set."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "access_token": "secret-token",
            },
            MessageBus(),
        )
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: server.connection_count >= 1)
            assert server.headers[-1]["Authorization"] == "Bearer secret-token"
            await _wait_for(lambda: channel._self_id == "42")
        finally:
            await _stop_channel(channel, task, server)
        assert channel._ws is None
        assert channel._session is None

    asyncio.run(case())


def test_stop_during_connect_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() during connect should not log a connect failure warning."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    class SlowConnectChannel(AnonChannel):
        async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
            await asyncio.sleep(0.2)
            raise ConnectionError("Connector is closed.")

    async def case() -> None:
        messages: list[str] = []
        sink_id = channel_module.logger.add(
            lambda message: messages.append(str(message)),
            level="DEBUG",
            format="{level}|{message}",
        )
        channel = SlowConnectChannel({"ws_url": "ws://127.0.0.1:1/"}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await asyncio.sleep(0.05)
            await channel.stop()
            await task
        finally:
            channel_module.logger.remove(sink_id)

        assert not any(
            "WARNING|Anon WebSocket connect failed:" in msg for msg in messages
        )

    asyncio.run(case())


def test_send_api_request_round_trips_by_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Echo should route the API response back to the waiting caller."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") == "custom_action":
            await ws.send_json(
                {
                    "status": "ok",
                    "echo": payload["echo"],
                    "data": {"value": payload["params"]["value"]},
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            response = await channel._send_api_request(
                "custom_action",
                {"value": 7},
                timeout=0.5,
            )
            assert isinstance(response.data, dict)
            assert response.data["value"] == 7
            assert channel._pending == {}
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_api_request_timeout_cleans_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timed-out API requests should leave no pending waiter behind."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            with pytest.raises(TimeoutError):
                await channel._send_api_request("slow_action", {}, timeout=0.05)
            assert channel._pending == {}
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_pending_request_fails_when_connection_drops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnecting the socket should fail in-flight API requests."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            request_task = asyncio.create_task(
                channel._send_api_request("hang_forever", {}, timeout=1.0)
            )
            await _wait_for(lambda: bool(channel._pending))
            await server.close_current_connection()
            with pytest.raises(ConnectionError, match="WebSocket closed"):
                await request_task
            assert channel._pending == {}
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_pending_request_fails_when_channel_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping the channel should wake any in-flight API waiter."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            request_task = asyncio.create_task(
                channel._send_api_request("hang_until_stop", {}, timeout=1.0)
            )
            await _wait_for(lambda: bool(channel._pending))
            await channel.stop()
            with pytest.raises(ConnectionError, match="Channel stopped"):
                await request_task
            await task
            assert channel._pending == {}
        finally:
            await server.close()

    asyncio.run(case())


def test_stop_during_fetch_self_id_after_reader_exits_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() should not turn start() into await None during get_login_info."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    class ClosingLoginServer(OneBotWebSocketServer):
        async def _handle_action(
            self,
            payload: dict[str, Any],
            ws: web.WebSocketResponse,
        ) -> None:
            if payload.get("action") == "get_login_info":
                await ws.close()
                return
            await super()._handle_action(payload, ws)

    async def case() -> None:
        messages: list[str] = []
        sink_id = channel_module.logger.add(
            lambda message: messages.append(str(message)),
            level="DEBUG",
            format="{level}|{message}",
        )
        server = ClosingLoginServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(
                lambda: channel._reader_task is not None
                and channel._reader_task.done()
                and bool(channel._pending)
            )
            await channel.stop()
            await task
            assert channel._pending == {}
        finally:
            channel_module.logger.remove(sink_id)
            await server.close()

        assert not any("WARNING|Anon get_login_info failed:" in msg for msg in messages)

    asyncio.run(case())


@pytest.mark.parametrize(
    ("response", "expected_id"),
    [
        ({"status": "ok", "data": {"user_id": "101", "nickname": "wrapped"}}, "101"),
        ({"status": "ok", "user_id": 202, "nickname": "flat"}, "202"),
    ],
)
def test_fetch_self_id_supports_wrapped_and_flat_payloads(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    expected_id: str,
) -> None:
    """get_login_info parsing should accept both common response shapes."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer(login_info_response=response)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == expected_id)
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_writes_private_message_to_cache_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A triggered private message should be written to cache and published."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "7000",
                    "user_id": "123",
                    "raw_message": "hello",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["private:123"][0]["sender_id"] == "123"
            assert snapshot["private:123"][0]["content"] == "hello"
            assert (
                snapshot["private:123"][0]["metadata"]["trigger_reason"]
                == "private_prob"
            )
            assert inbound.chat_id == "private:123"
            assert inbound.sender_id == "123"
            assert inbound.content == "\n".join(
                [
                    "<CQMSG/1 bot:me n:1>",
                    "U|me|42|anon-bot|bot",
                    "U|peer|123|123",
                    "M|7000|peer|hello",
                    "</CQMSG/1>",
                ]
            )
            assert inbound.metadata["trigger_reason"] == "private_prob"
            assert inbound.metadata["cqmsg_message_ids"] == ["7000"]
            assert inbound.metadata["cqmsg_count"] == 1
            assert channel._buffer.get_unconsumed_chat_entries("private:123") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_keeps_forward_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Forward segments should stay as placeholders in cached content."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "700",
                    "user_id": "123",
                    "message": [
                        {"type": "text", "data": {"text": "看看"}},
                        {
                            "type": "forward",
                            "data": {
                                "id": "fwd-1",
                                "summary": "聊天记录",
                                "content": [
                                    {
                                        "type": "node",
                                        "data": {
                                            "user_id": "7",
                                            "nickname": "Alice",
                                            "content": [
                                                {
                                                    "type": "text",
                                                    "data": {"text": "hello"},
                                                }
                                            ],
                                        },
                                    }
                                ],
                            },
                        },
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["private:123"][0]["content"] == "看看[forward]"
            assert (
                snapshot["private:123"][0]["metadata"]["forward_refs"][0]
                ["forward_id"]
                == "fwd-1"
            )
            assert (
                snapshot["private:123"][0]["expanded_forwards"][0]["nodes"][0]
                ["content"]
                == "hello"
            )
            buffered = channel._buffer.get("private:123", "700")
            assert buffered is not None
            assert buffered.expanded_forwards[0].nodes[0].content == "hello"
            assert inbound.metadata["cqmsg_message_ids"] == ["700"]
            assert "M|700|peer|看看[F:f0]" in inbound.content
            assert "F|f0|1|聊天记录" in inbound.content
            assert "N|f0.0|u0||hello" in inbound.content
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_drops_disallowed_sender_before_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disallowed senders should be rejected before publish and buffering."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "7010",
                    "user_id": "123",
                    "raw_message": "hello",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "7010") is None
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_fetches_forward_content_by_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Forward IDs should be expanded through get_forward_msg before buffering."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") != "get_forward_msg":
            return
        await ws.send_json(
            {
                "status": "ok",
                "retcode": 0,
                "data": {
                    "messages": [
                        {
                            "data": {
                                "user_id": "8",
                                "nickname": "Bob",
                                "content": [
                                    {"type": "text", "data": {"text": "forwarded"}}
                                ],
                            }
                        }
                    ]
                },
                "echo": payload["echo"],
            }
        )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "701",
                    "user_id": "123",
                    "message": [
                        {"type": "text", "data": {"text": "看看"}},
                        {"type": "forward", "data": {"id": "fwd-2"}},
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["private:123"][0]["content"] == "看看[forward]"
            assert (
                snapshot["private:123"][0]["metadata"]["expanded_forwards"][0]
                ["nodes"][0]["content"]
                == "forwarded"
            )
            assert _find_action(server, "get_forward_msg")["params"] == {"id": "fwd-2"}
            assert inbound.metadata["cqmsg_message_ids"] == ["701"]
            assert "N|f0.0|u0||forwarded" in inbound.content
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_marks_get_forward_msg_failure_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Failed get_forward_msg responses should be treated as unresolved forwards."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") != "get_forward_msg":
            return
        await ws.send_json(
            {
                "status": "failed",
                "retcode": 200,
                "data": None,
                "message": "消息已过期或者为内层消息,无法获取转发消息",
                "wording": "消息已过期或者为内层消息,无法获取转发消息",
                "echo": payload["echo"],
                "stream": "normal-action",
            }
        )

    async def case() -> None:
        messages: list[str] = []
        sink_id = channel_module.logger.add(
            lambda message: messages.append(str(message)),
            level="DEBUG",
            format="{level}|{message}",
        )
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "702",
                    "user_id": "123",
                    "message": [
                        {"type": "text", "data": {"text": "看看"}},
                        {"type": "forward", "data": {"id": "fwd-expired"}},
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            expanded = snapshot["private:123"][0]["metadata"]["expanded_forwards"][0]
            assert expanded["forward_id"] == "fwd-expired"
            assert expanded["unresolved"] is True
            assert expanded["nodes"] == []
            assert inbound.metadata["cqmsg_message_ids"] == ["702"]
            assert "M|702|peer|看看[F:f0]" in inbound.content
            assert "F|f0|0" in inbound.content
            assert any(
                "WARNING|Anon get_forward_msg returned failure:" in msg
                for msg in messages
            )
            assert any(
                (
                    "status=failed retcode=200 "
                    "message=消息已过期或者为内层消息,无法获取转发消息"
                )
                in msg
                for msg in messages
            )
        finally:
            channel_module.logger.remove(sink_id)
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_reader_marks_get_forward_msg_null_data_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Null get_forward_msg data should be treated as unresolved forwards."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") != "get_forward_msg":
            return
        await ws.send_json(
            {
                "status": "ok",
                "retcode": 0,
                "data": None,
                "message": "",
                "wording": "",
                "echo": payload["echo"],
                "stream": "normal-action",
            }
        )

    async def case() -> None:
        messages: list[str] = []
        sink_id = channel_module.logger.add(
            lambda message: messages.append(str(message)),
            level="DEBUG",
            format="{level}|{message}",
        )
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._ws is not None)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "703",
                    "user_id": "123",
                    "message": [
                        {"type": "text", "data": {"text": "看看"}},
                        {"type": "forward", "data": {"id": "fwd-null"}},
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            expanded = snapshot["private:123"][0]["metadata"]["expanded_forwards"][0]
            assert expanded["forward_id"] == "fwd-null"
            assert expanded["unresolved"] is True
            assert expanded["nodes"] == []
            assert inbound.metadata["cqmsg_message_ids"] == ["703"]
            assert "M|703|peer|看看[F:f0]" in inbound.content
            assert "F|f0|0" in inbound.content
            assert any(
                (
                    "WARNING|Anon get_forward_msg returned failure: "
                    "status=ok retcode=0 message="
                )
                in msg
                for msg in messages
            )
        finally:
            channel_module.logger.remove(sink_id)
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_channel_reconnects_after_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dropped socket should trigger the reconnect loop."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    close_once: dict[str, Any] = {"done": False, "task": None}

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del payload
        if server.connection_count == 1 and not close_once["done"]:
            close_once["done"] = True

            async def close_soon() -> None:
                await asyncio.sleep(0.05)
                await ws.close()

            close_once["task"] = asyncio.create_task(close_soon())

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: server.connection_count >= 2, timeout=1.5)
        finally:
            await _stop_channel(channel, task, server)
            close_task = close_once.get("task")
            if close_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await close_task

    asyncio.run(case())


def test_send_routes_private_text_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private chat IDs should map to send_private_msg."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(channel="anon", chat_id="private:1", content="hello")
            )
            action = _find_action(server, "send_private_msg")
            assert action["params"] == {
                "user_id": 1,
                "message": [{"type": "text", "data": {"text": "hello"}}],
            }
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_routes_group_text_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Group chat IDs should map to send_group_msg."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(channel="anon", chat_id="group:2", content="hello")
            )
            action = _find_action(server, "send_group_msg")
            assert action["params"] == {
                "group_id": 2,
                "message": [{"type": "text", "data": {"text": "hello"}}],
            }
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_rejects_bare_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare chat IDs should be rejected."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            with pytest.raises(ValueError, match="group:<id> or private:<id>"):
                await channel.send(
                    OutboundMessage(channel="anon", chat_id="3", content="hello")
                )
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_ignores_explicit_reply_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outbound send should not emit reply segments."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content="hello",
                    reply_to="99",
                    metadata={"reply_to_message_id": "88"},
                )
            )
            action = _find_action(server, "send_private_msg")
            assert action["params"]["message"] == [
                {"type": "text", "data": {"text": "hello"}},
            ]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_parses_inline_cq_at_and_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inline CQ at/reply should become OneBot segments in order."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="group:2",
                    content="前[CQ:reply,id=456][CQ:at,qq=123]后",
                )
            )
            action = _find_action(server, "send_group_msg")
            assert action["params"]["message"] == [
                {"type": "reply", "data": {"id": "456"}},
                {"type": "text", "data": {"text": "前"}},
                {"type": "at", "data": {"qq": "123"}},
                {"type": "text", "data": {"text": "后"}},
            ]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_keeps_unsupported_or_invalid_cq_as_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported malformed or invalid-target CQ should remain literal text."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content=(
                        "a[CQ:image,file=file:///tmp/demo.png]"
                        "b[CQ:at]c[CQ:reply]"
                        "d[CQ:at,qq=对方QQ号]e[CQ:reply,id=原消息ID]"
                    ),
                )
            )
            action = _find_action(server, "send_private_msg")
            assert action["params"]["message"] == [
                {
                    "type": "text",
                    "data": {
                        "text": (
                            "a[CQ:image,file=file:///tmp/demo.png]"
                            "b[CQ:at]c[CQ:reply]"
                            "d[CQ:at,qq=对方QQ号]e[CQ:reply,id=原消息ID]"
                        )
                    },
                },
            ]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_does_not_reply_to_last_inbound_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Inbound message IDs should not create implicit reply segments."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "777",
                    "user_id": "123",
                    "raw_message": "hello",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.metadata["cqmsg_message_ids"] == ["777"]
            await channel.send(
                OutboundMessage(channel="anon", chat_id="private:123", content="reply")
            )
            action = _find_action(server, "send_private_msg")
            assert action["params"]["message"] == [
                {"type": "text", "data": {"text": "reply"}},
            ]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_buffers_outbound_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful sends should buffer the outbound OneBot message ID."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws, message_id=9012)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(channel="anon", chat_id="group:2", content="hello")
            )
            buffered = channel._buffer.get("group:2", "9012")
            assert buffered is not None
            assert buffered.is_from_self is True
            assert buffered.content == "hello"
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_suppresses_known_fallback_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known nanobot fallback text should not be sent to OneBot."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        messages: list[str] = []
        sink_id = channel_module.logger.add(
            lambda message: messages.append(str(message)),
            level="DEBUG",
            format="{level}|{message}",
        )
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content=(
                        "I completed the tool steps but couldn't produce a "
                        "final answer. Please try again or narrow the task."
                    ),
                )
            )
            await asyncio.sleep(0.05)
            assert not any(
                action["action"] == "send_private_msg" for action in server.actions
            )
            assert channel._buffer.get_chat_entries("private:1") == []
            assert any(
                "DEBUG|Suppressing outbound nanobot fallback for private:1: "
                "empty_final_response" in msg
                for msg in messages
            )
        finally:
            channel_module.logger.remove(sink_id)
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_suppresses_error_prefix_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error:-prefixed outbound text should not be sent to OneBot."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="group:2",
                    content="Error: Task interrupted before a response was generated.",
                )
            )
            await asyncio.sleep(0.05)
            assert not any(
                action["action"] == "send_group_msg" for action in server.actions
            )
            assert channel._buffer.get_chat_entries("group:2") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_delta_suppresses_background_task_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_delta() should reuse suppression rules from send()."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send_delta("private:1", "Background task completed.")
            await asyncio.sleep(0.05)
            assert not any(
                action["action"] == "send_private_msg" for action in server.actions
            )
            assert channel._buffer.get_chat_entries("private:1") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_allows_restart_status_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slash-command status text should still be sent."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content="Restarting...",
                )
            )
            action = _find_action(server, "send_private_msg")
            assert action["params"] == {
                "user_id": 1,
                "message": [{"type": "text", "data": {"text": "Restarting..."}}],
            }
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_group_reply_only_triggers_for_bot_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group reply trigger should require replying to a buffered bot message."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws, message_id=9013)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "group_trigger_prob": 0.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(channel="anon", chat_id="group:2", content="hello")
            )
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9100",
                    "group_id": "2",
                    "user_id": "123",
                    "message": [
                        {"type": "reply", "data": {"id": "9013"}},
                        {"type": "text", "data": {"text": "收到"}},
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["group:2"][0]["metadata"]["trigger_reason"] == "reply"
            assert snapshot["group:2"][0]["metadata"]["reply_target_from_self"] is True
            assert inbound.chat_id == "group:2"
            assert inbound.sender_id == "123"
            assert inbound.metadata["trigger_reason"] == "reply"
            assert inbound.metadata["cqmsg_message_ids"] == ["9013", "9100"]
            assert inbound.metadata["cqmsg_count"] == 2
            assert "M|9013|u0|hello" in inbound.content
            assert "M|9100|u1|>m:9013 收到" in inbound.content
            assert channel._buffer.get_unconsumed_chat_entries("group:2") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_group_reply_to_non_bot_message_does_not_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group reply should not trigger when replying to a non-bot buffered message."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "group_trigger_prob": 0.0,
            },
            bus,
        )
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            channel._buffer.add(
                MessageEntry(
                    message_id="555",
                    chat_id="group:2",
                    sender_id="999",
                    sender_name="peer",
                    is_from_self=False,
                    content="peer msg",
                )
            )
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9101",
                    "group_id": "2",
                    "user_id": "123",
                    "message": [
                        {"type": "reply", "data": {"id": "555"}},
                        {"type": "text", "data": {"text": "收到"}},
                    ],
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            buffered = channel._buffer.get("group:2", "9101")
            assert buffered is not None
            assert buffered.is_from_self is False
            assert buffered.content == "收到"
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_allowlisted_group_inbound_accepts_matching_group_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group inbound should pass when allow_from contains the group ID."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9102",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "hello group",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["group:2"][0]["sender_id"] == "123"
            assert snapshot["group:2"][0]["content"] == "hello group"
            assert inbound.chat_id == "group:2"
            assert inbound.sender_id == "123"
            assert inbound.metadata["group_id"] == "2"
            assert inbound.metadata["trigger_reason"] == "group_prob"
            assert inbound.metadata["cqmsg_message_ids"] == ["9102"]
            assert "<CQMSG/1 g:2 bot:u0 n:1>" in inbound.content
            assert "M|9102|u1|hello group" in inbound.content
            assert channel._buffer.get_unconsumed_chat_entries("group:2") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_allowlisted_group_inbound_accepts_matching_sender_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group inbound should pass when allow_from contains the sender ID."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9103",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "hello sender",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["group:2"][0]["sender_id"] == "123"
            assert snapshot["group:2"][0]["content"] == "hello sender"
            assert inbound.chat_id == "group:2"
            assert inbound.sender_id == "123"
            assert inbound.metadata["group_id"] == "2"
            assert inbound.metadata["cqmsg_message_ids"] == ["9103"]
            assert channel._buffer.get_unconsumed_chat_entries("group:2") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_group_inbound_rejects_unlisted_sender_and_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group inbound should stop before cache/publish when sender/group are unlisted."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9104",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "blocked group",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("group:2", "9104") is None
            assert not channel._inbound_cache_path.exists()
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_cold_start_group_mute_sync_blocks_muted_group_inbound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cold-start sync should block group inbound when the bot is muted."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": "2"}],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"uin": "42", "shutUpTime": 4102444800}],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await _wait_for(lambda: channel._muted_groups == {"2": 4102444800})
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-1",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "hello muted group",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("group:2", "mute-1") is None
            assert not channel._inbound_cache_path.exists()
            assert _find_action(server, "get_group_list")["params"] == {
                "no_cache": False
            }
            assert _find_action(server, "get_group_shut_list")["params"] == {
                "group_id": "2"
            }
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_group_ban_notice_blocks_and_lift_ban_restores_group_inbound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group ban notices should update muted state immediately."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": "2"}],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await _wait_for(lambda: "2" in channel._known_group_mute_states)
            await server.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "group_ban",
                    "sub_type": "ban",
                    "time": 4102444200,
                    "self_id": "42",
                    "group_id": "2",
                    "user_id": "42",
                    "operator_id": "7",
                    "duration": 600,
                }
            )
            await _wait_for(lambda: channel._muted_groups.get("2") == 4102444800)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-2",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "still muted",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("group:2", "mute-2") is None

            await server.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "group_ban",
                    "sub_type": "lift_ban",
                    "time": 1776821666,
                    "self_id": "42",
                    "group_id": "2",
                    "user_id": "42",
                    "operator_id": "7",
                    "duration": 0,
                }
            )
            await _wait_for(lambda: "2" not in channel._muted_groups)
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-3",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "restored",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:2"
            assert inbound.metadata["cqmsg_message_ids"] == ["mute-3"]
            assert channel._buffer.get("group:2", "mute-3") is not None
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_other_user_group_ban_notice_does_not_block_group_inbound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group ban notices for other users should not affect the bot state."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": "2"}],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "group_ban",
                    "sub_type": "ban",
                    "time": 1776821686,
                    "self_id": "42",
                    "group_id": "2",
                    "user_id": "123",
                    "operator_id": "7",
                    "duration": 600,
                }
            )
            await asyncio.sleep(0.05)
            assert channel._muted_groups == {}
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-4",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "allowed",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:2"
            assert inbound.metadata["cqmsg_message_ids"] == ["mute-4"]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_expired_muted_group_entry_is_cleared_before_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Expired muted-group entries should not keep blocking inbound."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": "2"}],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"uin": "42", "shutUpTime": 1}],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            assert channel._muted_groups == {}
            channel._muted_groups["2"] = 1
            channel._known_group_mute_states.add("2")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-5",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "expired mute",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:2"
            assert inbound.metadata["cqmsg_message_ids"] == ["mute-5"]
            assert "2" not in channel._muted_groups
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_cold_start_mute_sync_blocks_only_the_target_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A slow mute lookup should block only that group's inbound."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    group_2_gate = asyncio.Event()
    delayed_tasks: list[asyncio.Task[None]] = []

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> bool | None:
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [{"group_id": "2"}, {"group_id": "3"}],
                    "echo": payload["echo"],
                }
            )
            return True
        if payload.get("action") != "get_group_shut_list":
            return None
        group_id = payload["params"]["group_id"]
        if group_id == "2":
            async def delayed_response() -> None:
                await group_2_gate.wait()
                await ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": [{"uin": "42", "shutUpTime": 4102444800}],
                        "echo": payload["echo"],
                    }
                )

            delayed_task = asyncio.create_task(delayed_response())
            delayed_tasks.append(delayed_task)
            return True
        if group_id == "3":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )
            return True
        return None

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2", "3"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await _wait_for(lambda: channel._group_mute_seed_ready.is_set())
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-6a",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "blocked",
                }
            )
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-6b",
                    "group_id": "3",
                    "user_id": "123",
                    "raw_message": "allowed",
                }
            )
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:3"
            assert inbound.metadata["cqmsg_message_ids"] == ["mute-6b"]
            assert channel._buffer.get("group:2", "mute-6a") is None

            group_2_gate.set()
            await _wait_for(lambda: channel._muted_groups == {"2": 4102444800})
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("group:2", "mute-6a") is None
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_first_message_in_new_group_triggers_single_lazy_mute_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A runtime new group should fetch mute state once before publishing."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    get_group_shut_list_calls = 0

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        nonlocal get_group_shut_list_calls
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            get_group_shut_list_calls += 1
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-7",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "new group",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:2"
            assert inbound.metadata["cqmsg_message_ids"] == ["mute-7"]
            assert get_group_shut_list_calls == 1
            assert "2" in channel._known_group_mute_states
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_same_group_waiters_share_one_mute_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Queued messages for the same unknown group should reuse one lookup."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    gate = asyncio.Event()
    get_group_shut_list_calls = 0

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        nonlocal get_group_shut_list_calls
        del server
        if payload.get("action") == "get_group_list":
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )
            return
        if payload.get("action") == "get_group_shut_list":
            get_group_shut_list_calls += 1
            await gate.wait()
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": [],
                    "echo": payload["echo"],
                }
            )

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["2"],
                "group_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-8a",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "first",
                }
            )
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "mute-8b",
                    "group_id": "2",
                    "user_id": "123",
                    "raw_message": "second",
                }
            )
            await _wait_for(lambda: get_group_shut_list_calls == 1)
            gate.set()

            await _wait_for(lambda: channel._inbound_cache_path.exists())
            first = await _consume_inbound(bus)
            second = await _consume_inbound(bus)
            assert first.metadata["cqmsg_message_ids"] == ["mute-8a"]
            assert second.metadata["cqmsg_message_ids"] == ["mute-8b"]
            assert get_group_shut_list_calls == 1
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_wildcard_allow_from_allows_private_inbound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Wildcard allow_from should allow private inbound from any sender."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["*"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9105",
                    "user_id": "456",
                    "raw_message": "wildcard",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            assert snapshot["private:456"][0]["sender_id"] == "456"
            assert inbound.chat_id == "private:456"
            assert inbound.sender_id == "456"
            assert inbound.metadata["cqmsg_message_ids"] == ["9105"]
            assert channel._buffer.get_unconsumed_chat_entries("private:456") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_empty_allow_from_rejects_private_inbound_before_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Empty allow_from should reject private inbound before cache and publish."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": [],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9106",
                    "user_id": "123",
                    "raw_message": "blocked private",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "9106") is None
            assert not channel._inbound_cache_path.exists()
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_allowlisted_inbound_is_cached_without_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowlisted inbound messages should be buffered when routing drops them."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 0.0,
            },
            bus,
        )
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9102",
                    "user_id": "123",
                    "raw_message": "hello",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            buffered = channel._buffer.get("private:123", "9102")
            assert buffered is not None
            assert buffered.is_from_self is False
            assert buffered.content == "hello"
            unread_entries = channel._buffer.get_unconsumed_chat_entries("private:123")
            assert [entry.message_id for entry in unread_entries] == ["9102"]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


@pytest.mark.parametrize("raw_message", ["/status", "   /STATUS   "])
def test_status_slash_bypasses_private_probability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_message: str,
) -> None:
    """Authorized /status should bypass normal gating."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "super_admins": ["123"],
                "private_trigger_prob": 0.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9301",
                    "user_id": "123",
                    "raw_message": raw_message,
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            metadata = snapshot["private:123"][0]["metadata"]
            assert metadata["trigger_reason"] == "slash_status"
            assert metadata["slash_command"] == "status"
            assert inbound.content.strip().lower() == "/status"
            assert inbound.metadata["trigger_reason"] == "slash_status"
            assert inbound.metadata["slash_command"] == "status"
            assert inbound.metadata["cqmsg_message_ids"] == ["9301"]
            assert channel._buffer.get_unconsumed_chat_entries("private:123") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_non_admin_slash_command_is_rejected_before_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-admin slash commands should stop before cache and publish."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9302",
                    "user_id": "123",
                    "raw_message": "/restart",
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "9302") is None
            assert not channel._inbound_cache_path.exists()
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_admin_slash_command_bypasses_private_routing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Authorized slash commands should publish even when private routing would drop."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "super_admins": [" 123 ", "123", None],
                "private_trigger_prob": 0.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9303",
                    "user_id": "123",
                    "raw_message": "/restart",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            snapshot = json.loads(
                channel._inbound_cache_path.read_text(encoding="utf-8")
            )
            metadata = snapshot["private:123"][0]["metadata"]
            assert inbound.content == "/restart"
            assert metadata["trigger_reason"] == "slash_command"
            assert metadata["slash_command"] == "restart"
            assert inbound.metadata["trigger_reason"] == "slash_command"
            assert inbound.metadata["slash_command"] == "restart"
            assert inbound.metadata["cqmsg_message_ids"] == ["9303"]
            assert channel._buffer.get_unconsumed_chat_entries("private:123") == []
            assert channel.config.super_admins == ["123"]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_statusx_is_treated_as_normal_slash_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Non-status slash commands should also bypass normal routing when authorized."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "super_admins": ["123"],
                "private_trigger_prob": 0.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9305",
                    "user_id": "123",
                    "raw_message": "/statusx",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.content == "/statusx"
            assert inbound.metadata["trigger_reason"] == "slash_command"
            assert inbound.metadata["slash_command"] == "statusx"
            assert inbound.metadata["cqmsg_message_ids"] == ["9305"]
            assert channel._buffer.get_unconsumed_chat_entries("private:123") == []
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_routed_poke_serializes_into_cqmsg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routed private poke events should publish through CQMSG."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "trigger_on_poke": True,
            },
            bus,
        )
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "time": 1776818315,
                    "user_id": "123",
                    "target_id": "42",
                }
            )
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "private:123"
            assert inbound.sender_id == "123"
            assert "<CQMSG/1 bot:me n:1>" in inbound.content
            assert (
                "M|notice:poke:1776818315:private:123:42|peer|[notice:poke] 戳了戳你"
                in inbound.content
            )
            assert inbound.metadata["trigger_reason"] == "poke"
            assert inbound.metadata["cqmsg_message_ids"] == [
                "notice:poke:1776818315:private:123:42"
            ]
            assert inbound.metadata["cqmsg_count"] == 1
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())



def test_group_poke_accepts_matching_group_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Group poke should pass when allow_from contains the group ID."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["456"],
                "trigger_on_poke": True,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "notice",
                    "notice_type": "notify",
                    "sub_type": "poke",
                    "time": 1776818315,
                    "group_id": "456",
                    "user_id": "123",
                    "target_id": "42",
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            assert inbound.chat_id == "group:456"
            assert inbound.sender_id == "123"
            assert inbound.metadata["group_id"] == "456"
            assert inbound.metadata["trigger_reason"] == "poke"
            assert inbound.metadata["cqmsg_message_ids"] == [
                "notice:poke:1776818315:456:123:42"
            ]
            assert inbound.metadata["cqmsg_count"] == 1
            assert "<CQMSG/1 g:456 bot:u0 n:1>" in inbound.content
            assert (
                "M|notice:poke:1776818315:456:123:42|u1|[notice:poke] 戳了戳你"
                in inbound.content
            )
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())



def test_allowed_private_image_inbound_downloads_to_media_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allowlisted image inbound should download once into cache."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9201",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "a.png",
                                "url": "https://example.com/a.png",
                                "file_size": "4",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            media_path = (tmp_path / "media" / "a.png").resolve()
            assert media_path.read_bytes() == b"test"
            buffered = channel._buffer.get("private:123", "9201")
            assert buffered is not None
            assert buffered.media == [str(media_path)]
            assert "I|i0|a.png" in inbound.content
            assert "M|9201|peer|[i0]" in inbound.content
            assert "[image]" not in inbound.content
            assert inbound.media == [str(media_path)]
            assert all(not item.startswith("file://") for item in inbound.media)
        finally:
            await _stop_channel(channel, task, server)

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        async def __aenter__(self) -> FakeResponse:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            del exc_type, exc, tb

        def raise_for_status(self) -> None:
            return None

        async def read(self) -> bytes:
            return self._body

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> FakeResponse:
        del self, kwargs
        calls.append(url)
        return FakeResponse(b"test")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == ["https://example.com/a.png"]



def test_group_inbound_attaches_history_only_image_from_unread_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A routed group message should publish unread history images too."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "group_trigger_prob": 0.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9401",
                    "group_id": "456",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "history.png",
                                "url": "https://example.com/history.png",
                                "file_size": "4",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)

            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9402",
                    "group_id": "456",
                    "user_id": "123",
                    "message": [
                        {"type": "at", "data": {"qq": "42"}},
                        {"type": "text", "data": {"text": "看图"}},
                    ],
                }
            )
            inbound = await _consume_inbound(bus)
            history_path = (tmp_path / "media" / "history.png").resolve()
            assert inbound.media == [str(history_path)]
            assert "I|i0|history.png" in inbound.content
            assert "M|9401|u1|[i0]" in inbound.content
            assert "M|9402|u1|看图" in inbound.content
            assert inbound.metadata["cqmsg_message_ids"] == ["9401", "9402"]
            assert channel._buffer.get_unconsumed_chat_entries("group:456") == []
        finally:
            await _stop_channel(channel, task, server)

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, kwargs
        assert url == "https://example.com/history.png"
        return FakeDownloadResponse(b"history")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())



def test_group_inbound_attaches_history_and_trigger_images_from_unread_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A routed group message should publish both history and trigger images."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "group_trigger_prob": 0.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9403",
                    "group_id": "456",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "history.png",
                                "url": "https://example.com/history.png",
                                "file_size": "4",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)

            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "message_id": "9404",
                    "group_id": "456",
                    "user_id": "123",
                    "message": [
                        {"type": "at", "data": {"qq": "42"}},
                        {"type": "text", "data": {"text": "看这两张"}},
                        {
                            "type": "image",
                            "data": {
                                "file": "trigger.png",
                                "url": "https://example.com/trigger.png",
                                "file_size": "4",
                            },
                        },
                    ],
                }
            )
            inbound = await _consume_inbound(bus)
            history_path = (tmp_path / "media" / "history.png").resolve()
            trigger_path = (tmp_path / "media" / "trigger.png").resolve()
            assert inbound.media == [str(history_path), str(trigger_path)]
            assert "I|i0|history.png" in inbound.content
            assert "I|i1|trigger.png" in inbound.content
            assert "M|9403|u1|[i0]" in inbound.content
            assert "M|9404|u1|看这两张[i1]" in inbound.content
            assert inbound.metadata["cqmsg_message_ids"] == ["9403", "9404"]
            assert channel._buffer.get_unconsumed_chat_entries("group:456") == []
        finally:
            await _stop_channel(channel, task, server)

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, kwargs
        body = b"history" if url.endswith("history.png") else b"trigger"
        return FakeDownloadResponse(body)

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())



def test_disallowed_private_image_inbound_skips_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Blocked sessions should return before any image download happens."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9202",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "blocked.png",
                                "url": "https://example.com/blocked.png",
                                "file_size": "4",
                            },
                        }
                    ],
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "9202") is None
            assert not (tmp_path / "media" / "blocked.png").exists()
        finally:
            await _stop_channel(channel, task, server)

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        calls.append(url)
        raise AssertionError("download should not happen")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == []



def test_cached_private_image_inbound_skips_redownload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Existing cached filenames should be reused without another fetch."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    cached_file = media_dir / "cached.png"
    cached_file.write_bytes(b"cached")

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9203",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "cached.png",
                                "url": "https://example.com/cached.png",
                                "file_size": "999999",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9203")
            assert buffered is not None
            assert buffered.media == [str(cached_file.resolve())]
            assert inbound.media == [str(cached_file.resolve())]
            assert inbound.metadata["cqmsg_message_ids"] == ["9203"]
            assert cached_file.read_bytes() == b"cached"
        finally:
            await _stop_channel(channel, task, server)

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        calls.append(url)
        raise AssertionError("download should be skipped for cache hit")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == []



def test_oversized_private_image_inbound_keeps_placeholder_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Oversized images should not download and should stay placeholder-only."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9204",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "big.png",
                                "url": "https://example.com/big.png",
                                "file_size": str(2 * 1024 * 1024),
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9204")
            assert buffered is not None
            assert buffered.media == []
            assert "I|" not in inbound.content
            assert "M|9204|peer|[image]" in inbound.content
            assert not (tmp_path / "media" / "big.png").exists()
        finally:
            await _stop_channel(channel, task, server)

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        calls.append(url)
        raise AssertionError("download should not happen for oversized image")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == []



def test_allowed_private_voice_inbound_downloads_and_transcribes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allowlisted voice inbound should download once and render transcription."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9301",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "hello.wav",
                                "url": "https://example.com/hello.wav",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            media_path = tmp_path / "media" / "hello.wav"
            assert media_path.read_bytes() == b"voice"
            buffered = channel._buffer.get("private:123", "9301")
            assert buffered is not None
            assert buffered.content == "[transcription: 今晚八点开会]"
            assert buffered.media == []
            assert (
                buffered.metadata["media_items"][0]["local_file_uri"]
                == channel._file_uri(media_path)
            )
            assert (
                "transcription_local_file_uri"
                not in buffered.metadata["media_items"][0]
            )
            assert (
                buffered.metadata["media_items"][0]["transcription_text"]
                == "今晚八点开会"
            )
            assert "I|" not in inbound.content
            assert "[voice]" not in inbound.content
            assert "M|9301|peer|[transcription: 今晚八点开会]" in inbound.content
            assert inbound.media == []
            assert (
                inbound.metadata["media_items"][0]["transcription_text"]
                == "今晚八点开会"
            )
        finally:
            await _stop_channel(channel, task, server)

    download_calls: list[str] = []
    transcribe_calls: list[Path] = []

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, kwargs
        download_calls.append(url)
        return FakeDownloadResponse(b"voice")

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        return "今晚八点开会"

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert download_calls == ["https://example.com/hello.wav"]
    assert transcribe_calls == [tmp_path / "media" / "hello.wav"]



def test_disallowed_private_voice_inbound_skips_download_and_transcription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Blocked sessions should return before any voice processing happens."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["999"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9302",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "blocked.amr",
                                "url": "https://example.com/blocked.amr",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "9302") is None
            assert not (tmp_path / "media" / "blocked.amr").exists()
        finally:
            await _stop_channel(channel, task, server)

    download_calls: list[str] = []
    transcribe_calls: list[Path] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        download_calls.append(url)
        raise AssertionError("download should not happen")

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        raise AssertionError("transcription should not happen")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert download_calls == []
    assert transcribe_calls == []



def test_cached_private_voice_inbound_skips_redownload_but_still_transcribes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cached voice files should be reused without another fetch."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    cached_file = media_dir / "cached.wav"
    cached_file.write_bytes(b"cached")

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9303",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "cached.wav",
                                "url": "https://example.com/cached.wav",
                                "file_size": "999999",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9303")
            assert buffered is not None
            assert buffered.content == "[transcription: 已命中缓存]"
            assert buffered.media == []
            assert (
                buffered.metadata["media_items"][0]["local_file_uri"]
                == channel._file_uri(cached_file)
            )
            assert (
                "transcription_local_file_uri"
                not in buffered.metadata["media_items"][0]
            )
            assert "[voice]" not in inbound.content
            assert "M|9303|peer|[transcription: 已命中缓存]" in inbound.content
            assert inbound.media == []
            assert inbound.metadata["cqmsg_message_ids"] == ["9303"]
            assert cached_file.read_bytes() == b"cached"
        finally:
            await _stop_channel(channel, task, server)

    download_calls: list[str] = []
    transcribe_calls: list[Path] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        download_calls.append(url)
        raise AssertionError("download should be skipped for cache hit")

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        return "已命中缓存"

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert download_calls == []
    assert transcribe_calls == [cached_file]



def test_oversized_private_voice_inbound_keeps_placeholder_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Oversized voice messages should not download or transcribe."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9304",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "big.amr",
                                "url": "https://example.com/big.amr",
                                "file_size": str(2 * 1024 * 1024),
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9304")
            assert buffered is not None
            assert buffered.content == "[voice]"
            assert buffered.media == []
            assert "I|" not in inbound.content
            assert "M|9304|peer|[voice]" in inbound.content
            assert not (tmp_path / "media" / "big.amr").exists()
        finally:
            await _stop_channel(channel, task, server)

    download_calls: list[str] = []
    transcribe_calls: list[Path] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        download_calls.append(url)
        raise AssertionError("download should not happen for oversized voice")

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        raise AssertionError("transcription should not happen for oversized voice")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert download_calls == []
    assert transcribe_calls == []



def test_allowed_private_voice_inbound_transcodes_amr_before_transcription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AMR voice inbound should transcode to WAV before transcription."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9307",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "hello.amr",
                                "url": "https://example.com/hello.amr",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9307")
            assert buffered is not None
            assert buffered.content == "[transcription: 今晚八点开会]"
            assert (
                buffered.metadata["media_items"][0]["transcription_text"]
                == "今晚八点开会"
            )
            assert (
                buffered.metadata["media_items"][0]["transcription_local_file_uri"]
                == channel._file_uri((tmp_path / "media" / "hello.wav").resolve())
            )
            assert not (tmp_path / "media" / "hello.amr").exists()
            assert (tmp_path / "media" / "hello.wav").exists()
            assert "M|9307|peer|[transcription: 今晚八点开会]" in inbound.content
        finally:
            await _stop_channel(channel, task, server)

    transcode_calls: list[tuple[str, str]] = []
    transcribe_calls: list[Path] = []

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, url, kwargs
        return FakeDownloadResponse(b"voice")

    async def fake_transcode(
        self: AnonChannel,
        source_path: Path,
    ) -> Path | None:
        del self
        target_path = source_path.with_suffix(".wav")
        target_path.write_bytes(b"wav")
        transcode_calls.append((str(source_path), str(target_path)))
        return target_path

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        return "今晚八点开会"

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(
        AnonChannel,
        "_transcode_voice_for_transcription",
        fake_transcode,
    )
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert transcode_calls == [
        (
            str((tmp_path / "media" / "hello.amr").resolve()),
            str((tmp_path / "media" / "hello.wav").resolve()),
        )
    ]
    assert transcribe_calls == [(tmp_path / "media" / "hello.wav").resolve()]



def test_private_voice_inbound_keeps_placeholder_when_transcoding_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Voice inbound should keep placeholder when AMR transcoding fails."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9308",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "broken.amr",
                                "url": "https://example.com/broken.amr",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9308")
            assert buffered is not None
            assert buffered.content == "[voice]"
            assert (
                buffered.metadata["media_items"][0]["local_file_uri"]
                == channel._file_uri((tmp_path / "media" / "broken.amr").resolve())
            )
            assert "transcription_text" not in buffered.metadata["media_items"][0]
            assert (
                "transcription_local_file_uri"
                not in buffered.metadata["media_items"][0]
            )
            assert "M|9308|peer|[voice]" in inbound.content
        finally:
            await _stop_channel(channel, task, server)

    transcribe_calls: list[Path] = []

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, url, kwargs
        return FakeDownloadResponse(b"voice")

    async def fake_transcode(self: AnonChannel, source_path: Path) -> Path | None:
        del self, source_path
        return None

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        return "不应调用"

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(
        AnonChannel,
        "_transcode_voice_for_transcription",
        fake_transcode,
    )
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert transcribe_calls == []



def test_private_image_inbound_keeps_placeholder_when_download_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Timed-out image downloads should keep placeholder-only content."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(channel_module, "_MEDIA_DOWNLOAD_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9310",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "image",
                            "data": {
                                "file": "slow.png",
                                "url": "https://example.com/slow.png",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9310")
            assert buffered is not None
            assert buffered.content == "[image]"
            assert buffered.media == []
            assert inbound.media == []
            assert "M|9310|peer|[image]" in inbound.content
            assert not (tmp_path / "media" / "slow.png").exists()
        finally:
            await _stop_channel(channel, task, server)

    read_gate = asyncio.Event()

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, url, kwargs
        return FakeDownloadResponse(b"image", read_gate=read_gate)

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())



def test_private_voice_inbound_keeps_placeholder_when_transcoding_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Timed-out ffmpeg should keep placeholder-only voice content."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(channel_module, "_FFMPEG_TIMEOUT_S", 0.01)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
                "media_max_size_mb": 1,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9311",
                    "user_id": "123",
                    "message": [
                        {
                            "type": "record",
                            "data": {
                                "file": "slow.amr",
                                "url": "https://example.com/slow.amr",
                                "file_size": "5",
                            },
                        }
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9311")
            assert buffered is not None
            assert buffered.content == "[voice]"
            assert "transcription_text" not in buffered.metadata["media_items"][0]
            assert "M|9311|peer|[voice]" in inbound.content
            assert not (tmp_path / "media" / "slow.wav").exists()
        finally:
            await _stop_channel(channel, task, server)

    def fake_get(
        self: aiohttp.ClientSession,
        url: str,
        **kwargs: Any,
    ) -> FakeDownloadResponse:
        del self, url, kwargs
        return FakeDownloadResponse(b"voice")

    class HangingProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self._killed = asyncio.Event()

        async def communicate(self) -> tuple[bytes, bytes]:
            await self._killed.wait()
            return b"", b""

        def kill(self) -> None:
            self.returncode = -9
            self._killed.set()

    async def fake_subprocess_exec(*args: Any, **kwargs: Any) -> HangingProcess:
        del args, kwargs
        return HangingProcess()

    transcribe_calls: list[Path] = []

    async def fake_transcribe(self: AnonChannel, file_path: str | Path) -> str:
        del self
        transcribe_calls.append(Path(file_path))
        return "不应调用"

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess_exec)
    monkeypatch.setattr(AnonChannel, "transcribe_audio", fake_transcribe)
    asyncio.run(case())
    assert transcribe_calls == []



def test_private_video_and_file_inbound_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pure video/file inbound should not be cached or published."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(
        channel_module,
        "get_media_dir",
        lambda _channel: tmp_path / "media",
    )

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9305",
                    "user_id": "123",
                    "raw_message": "[CQ:video,file=clip.mp4][CQ:file,file=archive.zip]",
                    "message": [
                        {
                            "type": "video",
                            "data": {
                                "file": "clip.mp4",
                                "url": "https://example.com/clip.mp4",
                                "file_size": "4",
                            },
                        },
                        {
                            "type": "file",
                            "data": {
                                "file": "archive.zip",
                                "url": "https://example.com/archive.zip",
                                "file_size": "4",
                            },
                        },
                    ],
                }
            )
            with pytest.raises(asyncio.TimeoutError):
                await _consume_inbound(bus, timeout=0.2)
            assert channel._buffer.get("private:123", "9305") is None
            assert not channel._inbound_cache_path.exists()
            assert not (tmp_path / "media").exists()
        finally:
            await _stop_channel(channel, task, server)

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        calls.append(url)
        raise AssertionError("download should not happen for video/file inbound")

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == []



def test_private_text_with_video_and_file_inbound_keeps_only_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mixed messages should ignore unsupported video/file segments."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def case() -> None:
        server = OneBotWebSocketServer()
        await server.start()
        bus = MessageBus()
        channel = AnonChannel(
            {
                "ws_url": server.url,
                "allow_from": ["123"],
                "private_trigger_prob": 1.0,
            },
            bus,
        )
        channel._inbound_cache_path = tmp_path / "inbound-buffer.json"
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await server.send_json(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "message_id": "9306",
                    "user_id": "123",
                    "raw_message": (
                        "前[CQ:video,file=clip.mp4][CQ:file,file=archive.zip]后"
                    ),
                    "message": [
                        {"type": "text", "data": {"text": "前"}},
                        {
                            "type": "video",
                            "data": {
                                "file": "clip.mp4",
                                "url": "https://example.com/clip.mp4",
                                "file_size": "4",
                            },
                        },
                        {
                            "type": "file",
                            "data": {
                                "file": "archive.zip",
                                "url": "https://example.com/archive.zip",
                                "file_size": "4",
                            },
                        },
                        {"type": "text", "data": {"text": "后"}},
                    ],
                }
            )
            await _wait_for(lambda: channel._inbound_cache_path.exists())
            inbound = await _consume_inbound(bus)
            buffered = channel._buffer.get("private:123", "9306")
            assert buffered is not None
            assert buffered.content == "前后"
            assert buffered.media == []
            assert inbound.metadata["cqmsg_message_ids"] == ["9306"]
            assert "[video]" not in inbound.content
            assert "[file]" not in inbound.content
            assert "M|9306|peer|前后" in inbound.content
        finally:
            await _stop_channel(channel, task, server)

    calls: list[str] = []

    def fake_get(self: aiohttp.ClientSession, url: str, **kwargs: Any) -> Any:
        del self, kwargs
        calls.append(url)
        raise AssertionError(
            "download should not happen for ignored video/file segments"
        )

    monkeypatch.setattr(aiohttp.ClientSession, "get", fake_get)
    asyncio.run(case())
    assert calls == []



def test_send_delta_sends_plain_text_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_delta() should fall back to a normal text send."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send_delta("private:1", "delta")
            action = _find_action(server, "send_private_msg")
            assert action["params"] == {
                "user_id": 1,
                "message": [{"type": "text", "data": {"text": "delta"}}],
            }
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_splits_non_image_media_from_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-image media should be sent separately and caption should be text-only."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> bool:
        await _reply_to_send_action(payload, ws, message_id=9000 + len(server.actions))
        return payload.get("action") in {"send_private_msg", "send_group_msg"}

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content="caption",
                    media=[
                        "/napcat/a.png",
                        "/napcat/b.mp4",
                        "/napcat/c.mp3",
                        "/napcat/d.zip",
                    ],
                    metadata={"source": "resolved"},
                )
            )
            actions = _find_actions(server, "send_private_msg")
            assert [action["params"]["message"] for action in actions] == [
                [{"type": "image", "data": {"file": "/napcat/a.png"}}],
                [{"type": "video", "data": {"file": "/napcat/b.mp4"}}],
                [{"type": "record", "data": {"file": "/napcat/c.mp3"}}],
                [{"type": "file", "data": {"file": "/napcat/d.zip"}}],
                [{"type": "text", "data": {"text": "caption"}}],
            ]
            assert not _find_actions(server, "upload_file_stream")
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_uploads_local_image_then_sends_caption_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local outbound images may still be sent together with caption text."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    media_path = tmp_path / "sample.png"
    media_bytes = b"a" * (70 * 1024)
    media_path.write_bytes(media_bytes)
    expected_sha256 = hashlib.sha256(media_bytes).hexdigest()
    expected_chunk_count = 2
    uploaded_path = "/napcat/cache/sample.png"

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> bool:
        del server
        if payload.get("action") == "upload_file_stream":
            params = payload["params"]
            if params.get("is_complete"):
                await ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "status": "file_complete",
                            "file_path": uploaded_path,
                            "file_size": len(media_bytes),
                            "sha256": expected_sha256,
                        },
                        "echo": payload["echo"],
                    }
                )
                return True
            chunk_index = params["chunk_index"]
            chunk_size = 64 * 1024
            start = chunk_index * chunk_size
            end = start + chunk_size
            assert params["stream_id"]
            assert params["total_chunks"] == expected_chunk_count
            assert params["file_size"] == len(media_bytes)
            assert params["expected_sha256"] == expected_sha256
            assert params["filename"] == "sample.png"
            assert params["file_retention"] == 30 * 1000
            assert base64.b64decode(params["chunk_data"]) == media_bytes[start:end]
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "status": "chunk_received",
                        "received_chunks": chunk_index + 1,
                        "total_chunks": expected_chunk_count,
                    },
                    "echo": payload["echo"],
                }
            )
            return True
        await _reply_to_send_action(payload, ws)
        return payload.get("action") in {"send_private_msg", "send_group_msg"}

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content="caption",
                    media=[str(media_path)],
                    metadata={"source": "upload"},
                )
            )
            upload_actions = _find_actions(server, "upload_file_stream")
            assert len(upload_actions) == expected_chunk_count + 1
            assert upload_actions[-1]["params"] == {
                "stream_id": upload_actions[0]["params"]["stream_id"],
                "is_complete": True,
            }
            action = _find_action(server, "send_private_msg")
            assert action["params"]["message"] == [
                {"type": "image", "data": {"file": uploaded_path}},
                {"type": "text", "data": {"text": "caption"}},
            ]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_keeps_uploaded_media_in_split_buffer_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Buffered outbound split sends should store per-message content and media."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    media_path = tmp_path / "buffer.wav"
    media_path.write_bytes(b"buffer-me")
    uploaded_path = "/napcat/cache/buffer.wav"

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> bool:
        if payload.get("action") == "upload_file_stream":
            params = payload["params"]
            if params.get("is_complete"):
                await ws.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {
                            "status": "file_complete",
                            "file_path": uploaded_path,
                        },
                        "echo": payload["echo"],
                    }
                )
                return True
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"status": "chunk_received"},
                    "echo": payload["echo"],
                }
            )
            return True
        await _reply_to_send_action(
            payload,
            ws,
            message_id=9120 + len(_find_actions(server, "send_private_msg")),
        )
        return payload.get("action") in {"send_private_msg", "send_group_msg"}

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            await channel.send(
                OutboundMessage(
                    channel="anon",
                    chat_id="private:1",
                    content="caption",
                    media=[str(media_path)],
                    metadata={"source": "buffer"},
                )
            )
            buffered = channel._buffer.get_chat_entries("private:1")
            assert len(buffered) == 2
            assert buffered[0].content == ""
            assert buffered[0].media == [uploaded_path]
            assert buffered[0].segment_types == ["record"]
            assert buffered[1].content == "caption"
            assert buffered[1].media == []
            assert buffered[1].segment_types == ["text"]
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_raises_when_upload_completion_missing_file_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing file_path in upload completion should fail before send_private_msg."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    media_path = tmp_path / "sample.png"
    media_path.write_bytes(b"upload-me")

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> bool:
        del server
        if payload.get("action") != "upload_file_stream":
            return False
        params = payload["params"]
        if params.get("is_complete"):
            await ws.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"status": "file_complete"},
                    "echo": payload["echo"],
                }
            )
            return True
        await ws.send_json(
            {
                "status": "ok",
                "retcode": 0,
                "data": {"status": "chunk_received"},
                "echo": payload["echo"],
            }
        )
        return True

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            with pytest.raises(RuntimeError, match="missing file_path"):
                await channel.send(
                    OutboundMessage(
                        channel="anon",
                        chat_id="private:1",
                        content="caption",
                        media=[str(media_path)],
                    )
                )
            assert not _find_actions(server, "send_private_msg")
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


def test_send_raises_on_failed_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed OneBot responses should raise from send()."""
    monkeypatch.setattr(channel_module, "_RECONNECT_INTERVAL_S", 0.05)

    async def on_action(
        server: OneBotWebSocketServer,
        payload: dict[str, Any],
        ws: web.WebSocketResponse,
    ) -> None:
        del server
        await _reply_to_send_action(payload, ws, status="failed", retcode=100)

    async def case() -> None:
        server = OneBotWebSocketServer(on_action=on_action)
        await server.start()
        channel = AnonChannel({"ws_url": server.url}, MessageBus())
        task = asyncio.create_task(channel.start())
        try:
            await _wait_for(lambda: channel._self_id == "42")
            with pytest.raises(RuntimeError, match="retcode=100"):
                await channel.send(
                    OutboundMessage(
                        channel="anon",
                        chat_id="private:1",
                        content="hello",
                    )
                )
        finally:
            await _stop_channel(channel, task, server)

    asyncio.run(case())


@pytest.mark.parametrize("chat_id", ["", "group:", "foo:1", "private:abc"])
def test_send_rejects_invalid_chat_id(chat_id: str) -> None:
    """Invalid chat IDs should fail before sending."""

    async def case() -> None:
        channel = AnonChannel({"ws_url": "ws://127.0.0.1:1/"}, MessageBus())
        with pytest.raises(ValueError):
            await channel.send(
                OutboundMessage(channel="anon", chat_id=chat_id, content="hello")
            )

    asyncio.run(case())


def test_onebot_raw_event_parses_message_payload() -> None:
    """The minimal OneBot event model should parse common message fields."""
    payload = OneBotRawEvent.model_validate(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "user_id": "123",
            "raw_message": "hello",
            "message": [{"type": "text", "data": {"text": "hello"}}],
            "sender": {"user_id": 123, "nickname": "tester", "card": ""},
        }
    )

    assert payload.post_type == "message"
    assert payload.message_type == "private"
    assert payload.user_id == "123"
    assert payload.sender is not None
    assert payload.sender.nickname == "tester"
    assert isinstance(payload.message, list)
    assert payload.message[0].type == "text"
    assert payload.message[0].data == {"text": "hello"}


def test_onebot_api_request_omits_empty_echo() -> None:
    """The minimal OneBot action model should keep echo optional."""
    request = OneBotAPIRequest(action="send_msg", params={"message": "hello"})

    assert request.model_dump(exclude_none=True) == {
        "action": "send_msg",
        "params": {"message": "hello"},
    }


def test_onebot_raw_event_accepts_status_object() -> None:
    """The minimal OneBot event model should parse structured bot status."""
    payload = OneBotRawEvent.model_validate(
        {
            "status": {"online": True, "good": True},
            "data": {"user_id": 42},
        }
    )

    assert payload.status == BotStatus(online=True, good=True)
    assert payload.data == {"user_id": 42}


def test_channel_decode_payload_returns_onebot_model() -> None:
    """Channel payload decoding should return the typed OneBot model."""
    payload = AnonChannel._decode_payload(
        '{"post_type":"message","message_type":"private","user_id":"123"}'
    )

    assert isinstance(payload, OneBotRawEvent)
    assert payload.post_type == "message"
    assert payload.user_id == "123"
