"""Tests for the NapCat HTTP client used by the MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from nanobot_channel_anon.mcp.models import (
    DeleteMsgRequest,
    SendLikeRequest,
    SendPokeRequest,
    SetFriendAddRequestRequest,
    SetGroupAddRequestRequest,
)
from nanobot_channel_anon.mcp.napcat_client import NapCatAPIError, NapCatClient


class NapCatHTTPServer:
    """Tiny in-process HTTP server for NapCat action tests."""

    def __init__(
        self,
        *,
        response_payload: dict[str, Any] | None = None,
        status: int = 200,
    ) -> None:
        """Initialize the test server with a fixed response payload."""
        self.response_payload = response_payload or {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": 9001},
        }
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._port: int | None = None
        self._app = web.Application()
        self._app.router.add_post("/{action}", self._handle_action)

    @property
    def url(self) -> str:
        """Return the base URL of the test server."""
        assert self._port is not None
        return f"http://127.0.0.1:{self._port}"

    async def start(self) -> None:
        """Start the HTTP server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        server = self._site._server
        assert server is not None
        sockets = getattr(server, "sockets", None)
        assert sockets is not None
        self._port = sockets[0].getsockname()[1]

    async def close(self) -> None:
        """Stop the HTTP server."""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._site = None
        self._port = None

    async def _handle_action(self, request: web.Request) -> web.Response:
        self.headers.append(dict(request.headers))
        body = await request.json()
        self.requests.append({"action": request.match_info["action"], "body": body})
        return web.json_response(self.response_payload, status=self.status)


def test_delete_msg_request_includes_message_id() -> None:
    """Delete message should post the target message_id."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.delete_msg(DeleteMsgRequest(message_id="123456"))
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {"action": "delete_msg", "body": {"message_id": 123456}}
        ]

    asyncio.run(case())


def test_set_group_add_request_includes_required_fields() -> None:
    """Group add request should post flag subtype and approve."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_add_request(
                SetGroupAddRequestRequest(
                    flag="flag_123",
                    sub_type="add",
                    approve=True,
                )
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_add_request",
                "body": {
                    "flag": "flag_123",
                    "sub_type": "add",
                    "approve": True,
                },
            }
        ]

    asyncio.run(case())


def test_set_group_add_request_includes_reason_when_rejecting() -> None:
    """Rejected group add requests should include reason."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            await client.set_group_add_request(
                SetGroupAddRequestRequest(
                    flag="flag_123",
                    sub_type="invite",
                    approve=False,
                    reason="拒绝",
                )
            )
        finally:
            await server.close()

        assert server.requests == [
            {
                "action": "set_group_add_request",
                "body": {
                    "flag": "flag_123",
                    "sub_type": "invite",
                    "approve": False,
                    "reason": "拒绝",
                },
            }
        ]

    asyncio.run(case())


def test_set_group_add_request_omits_reason_when_approving() -> None:
    """Approved group add requests should omit reason."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            await client.set_group_add_request(
                SetGroupAddRequestRequest(
                    flag="flag_123",
                    sub_type="add",
                    approve=True,
                    reason=None,
                )
            )
        finally:
            await server.close()

        assert server.requests == [
            {
                "action": "set_group_add_request",
                "body": {
                    "flag": "flag_123",
                    "sub_type": "add",
                    "approve": True,
                },
            }
        ]

    asyncio.run(case())


def test_set_group_add_request_raises_for_failure_payload() -> None:
    """Group add request failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1401,
                "data": None,
                "message": "权限不足",
                "wording": "权限不足",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="set_group_add_request"):
                await client.set_group_add_request(
                    SetGroupAddRequestRequest(
                        flag="flag_123",
                        sub_type="add",
                        approve=True,
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_set_friend_add_request_includes_required_fields() -> None:
    """Friend add request should post flag approve and remark."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": None}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_friend_add_request(
                SetFriendAddRequestRequest(
                    flag="flag_123",
                    approve=True,
                    remark="好友备注",
                )
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_friend_add_request",
                "body": {
                    "flag": "flag_123",
                    "approve": True,
                    "remark": "好友备注",
                },
            }
        ]

    asyncio.run(case())


def test_set_friend_add_request_raises_for_failure_payload() -> None:
    """Friend add request failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1400,
                "data": None,
                "message": "处理失败",
                "wording": "处理失败",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="set_friend_add_request"):
                await client.set_friend_add_request(
                    SetFriendAddRequestRequest(
                        flag="flag_123",
                        approve=False,
                        remark="好友备注",
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_delete_msg_raises_for_failure_payload() -> None:
    """Delete message failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1401,
                "data": None,
                "message": "权限不足",
                "wording": "权限不足",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="delete_msg"):
                await client.delete_msg(DeleteMsgRequest(message_id="123456"))
        finally:
            await server.close()

    asyncio.run(case())


def test_send_poke_group_request_includes_group_and_auth() -> None:
    """Group poke should send both ids and bearer auth."""

    async def case() -> None:
        server = NapCatHTTPServer()
        await server.start()
        client = NapCatClient(
            base_url=server.url,
            access_token="secret-token",
            timeout_seconds=1.0,
        )
        try:
            response = await client.send_poke(
                SendPokeRequest(user_id="123", group_id="456")
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {"action": "send_poke", "body": {"user_id": 123, "group_id": 456}}
        ]
        assert server.headers[0]["Authorization"] == "Bearer secret-token"

    asyncio.run(case())


def test_send_poke_private_request_omits_group_id() -> None:
    """Private poke should only send user_id."""

    async def case() -> None:
        server = NapCatHTTPServer()
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.send_poke(SendPokeRequest(user_id="123"))
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [{"action": "send_poke", "body": {"user_id": 123}}]
        assert "Authorization" not in server.headers[0]

    asyncio.run(case())


def test_call_raises_for_napcat_failure_payload() -> None:
    """NapCat failure status should surface as an API error."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "failed", "retcode": 100, "data": None}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="send_poke"):
                await client.send_poke(SendPokeRequest(user_id="123"))
        finally:
            await server.close()

    asyncio.run(case())


def test_send_like_request_includes_user_id_and_times() -> None:
    """Like requests should post both user_id and times."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.send_like(
                SendLikeRequest(user_id="123456", times=10)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {"action": "send_like", "body": {"user_id": 123456, "times": 10}}
        ]

    asyncio.run(case())


def test_send_like_raises_for_failure_payload() -> None:
    """Like failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1400,
                "data": None,
                "message": "点赞失败(频率过快或用户不存在)",
                "wording": "点赞失败(频率过快或用户不存在)",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="send_like"):
                await client.send_like(SendLikeRequest(user_id="123456", times=10))
        finally:
            await server.close()

    asyncio.run(case())
