"""Tests for the NapCat HTTP client used by the MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web

from nanobot_channel_anon.mcp.models import (
    DeleteFriendRequest,
    DeleteMsgRequest,
    GetFriendListRequest,
    GetGroupListRequest,
    GetGroupMemberListRequest,
    SendLikeRequest,
    SendPokeRequest,
    SetFriendAddRequestRequest,
    SetGroupAddRequestRequest,
    SetGroupBanRequest,
    SetGroupCardRequest,
    SetGroupKickRequest,
    SetGroupLeaveRequest,
    SetGroupWholeBanRequest,
    SetMsgEmojiLikeRequest,
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


def test_get_group_member_list_request_includes_group_id_and_no_cache() -> None:
    """Group member list should post group_id and no_cache."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "ok",
                "retcode": 0,
                "data": [
                    {
                        "group_id": 123456,
                        "user_id": 123456789,
                        "nickname": "昵称",
                        "card": "名片",
                        "role": "member",
                    }
                ],
                "message": "",
                "wording": "",
                "stream": "normal-action",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.get_group_member_list(
                GetGroupMemberListRequest(group_id="123456", no_cache=True)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "get_group_member_list",
                "body": {"group_id": 123456, "no_cache": True},
            }
        ]

    asyncio.run(case())


def test_get_group_member_list_raises_for_failure_payload() -> None:
    """Group member list failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1404,
                "data": None,
                "message": "群不存在",
                "wording": "群不存在",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="get_group_member_list"):
                await client.get_group_member_list(
                    GetGroupMemberListRequest(group_id="123456", no_cache=True)
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_set_group_ban_request_includes_group_id_user_id_and_duration() -> None:
    """Group ban should post group_id user_id and duration."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_ban(
                SetGroupBanRequest(group_id="123456", user_id="654321", duration=60)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_ban",
                "body": {"group_id": 123456, "user_id": 654321, "duration": 60},
            }
        ]

    asyncio.run(case())


def test_set_group_ban_raises_for_failure_payload() -> None:
    """Group ban failures should surface the NapCat action name."""

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
            with pytest.raises(NapCatAPIError, match="set_group_ban"):
                await client.set_group_ban(
                    SetGroupBanRequest(group_id="123456", user_id="654321", duration=60)
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_set_group_kick_request_includes_group_id_user_id_and_reject_flag() -> None:
    """Group kick should post group_id user_id and reject_add_request."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_kick(
                SetGroupKickRequest(
                    group_id="123456",
                    user_id="654321",
                    reject_add_request=True,
                )
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_kick",
                "body": {
                    "group_id": 123456,
                    "user_id": 654321,
                    "reject_add_request": True,
                },
            }
        ]

    asyncio.run(case())


def test_set_group_kick_raises_for_failure_payload() -> None:
    """Group kick failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1404,
                "data": None,
                "message": "群成员不存在",
                "wording": "群成员不存在",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="set_group_kick"):
                await client.set_group_kick(
                    SetGroupKickRequest(
                        group_id="123456",
                        user_id="654321",
                        reject_add_request=False,
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_get_friend_list_request_includes_no_cache() -> None:
    """Friend list should post no_cache."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "ok",
                "retcode": 0,
                "data": [
                    {"user_id": 123456789, "nickname": "昵称", "remark": "备注"}
                ],
                "message": "",
                "wording": "",
                "stream": "normal-action",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.get_friend_list(
                GetFriendListRequest(no_cache=True)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {"action": "get_friend_list", "body": {"no_cache": True}}
        ]

    asyncio.run(case())


def test_get_friend_list_raises_for_failure_payload() -> None:
    """Friend list failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1400,
                "data": None,
                "message": "获取失败",
                "wording": "获取失败",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="get_friend_list"):
                await client.get_friend_list(GetFriendListRequest(no_cache=False))
        finally:
            await server.close()

    asyncio.run(case())


def test_set_group_card_request_includes_group_id_user_id_and_card() -> None:
    """Group card should post group_id user_id and card."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_card(
                SetGroupCardRequest(
                    group_id="123456",
                    user_id="654321",
                    card="测试名片",
                )
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_card",
                "body": {
                    "group_id": 123456,
                    "user_id": 654321,
                    "card": "测试名片",
                },
            }
        ]

    asyncio.run(case())


def test_set_group_card_raises_for_failure_payload() -> None:
    """Group card failures should surface the NapCat action name."""

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
            with pytest.raises(NapCatAPIError, match="set_group_card"):
                await client.set_group_card(
                    SetGroupCardRequest(
                        group_id="123456",
                        user_id="654321",
                        card="测试名片",
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_get_group_list_request_includes_no_cache() -> None:
    """Group list should post no_cache."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "ok",
                "retcode": 0,
                "data": [
                    {"group_id": 123456, "group_name": "测试群", "group_all_shut": 0}
                ],
                "message": "",
                "wording": "",
                "stream": "normal-action",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.get_group_list(GetGroupListRequest(no_cache=True))
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {"action": "get_group_list", "body": {"no_cache": True}}
        ]

    asyncio.run(case())


def test_get_group_list_raises_for_failure_payload() -> None:
    """Group list failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1400,
                "data": None,
                "message": "获取失败",
                "wording": "获取失败",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="get_group_list"):
                await client.get_group_list(GetGroupListRequest(no_cache=False))
        finally:
            await server.close()

    asyncio.run(case())


def test_set_group_whole_ban_request_includes_group_id_and_enable() -> None:
    """Whole group ban should post group_id and enable."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_whole_ban(
                SetGroupWholeBanRequest(group_id="123456", enable=True)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_whole_ban",
                "body": {"group_id": 123456, "enable": True},
            }
        ]

    asyncio.run(case())


def test_set_group_whole_ban_raises_for_failure_payload() -> None:
    """Whole group ban failures should surface the NapCat action name."""

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
            with pytest.raises(NapCatAPIError, match="set_group_whole_ban"):
                await client.set_group_whole_ban(
                    SetGroupWholeBanRequest(group_id="123456", enable=False)
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_set_group_leave_request_includes_group_id_and_is_dismiss() -> None:
    """Group leave should post group_id and is_dismiss."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_group_leave(
                SetGroupLeaveRequest(group_id="123456", is_dismiss=True)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_group_leave",
                "body": {"group_id": 123456, "is_dismiss": True},
            }
        ]

    asyncio.run(case())


def test_set_group_leave_raises_for_failure_payload() -> None:
    """Group leave failures should surface the NapCat action name."""

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
            with pytest.raises(NapCatAPIError, match="set_group_leave"):
                await client.set_group_leave(
                    SetGroupLeaveRequest(group_id="123456", is_dismiss=False)
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_set_msg_emoji_like_request_includes_message_id_emoji_id_and_set() -> None:
    """Message emoji like should post message_id emoji_id and set."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {"result": True}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.set_msg_emoji_like(
                SetMsgEmojiLikeRequest(message_id="123456", emoji_id="66", set=True)
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "set_msg_emoji_like",
                "body": {"message_id": 123456, "emoji_id": 66, "set": True},
            }
        ]

    asyncio.run(case())


def test_set_msg_emoji_like_raises_for_failure_payload() -> None:
    """Message emoji like failures should surface the NapCat action name."""

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
            with pytest.raises(NapCatAPIError, match="set_msg_emoji_like"):
                await client.set_msg_emoji_like(
                    SetMsgEmojiLikeRequest(
                        message_id="123456",
                        emoji_id="66",
                        set=False,
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())


def test_delete_friend_request_includes_user_id_and_flags() -> None:
    """Delete friend should post user_id and delete flags."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={"status": "ok", "retcode": 0, "data": {}}
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            response = await client.delete_friend(
                DeleteFriendRequest(
                    user_id="123456",
                    temp_block=True,
                    temp_both_del=False,
                )
            )
        finally:
            await server.close()

        assert response.status.value == "ok"
        assert server.requests == [
            {
                "action": "delete_friend",
                "body": {
                    "user_id": 123456,
                    "temp_block": True,
                    "temp_both_del": False,
                },
            }
        ]

    asyncio.run(case())


def test_delete_friend_raises_for_failure_payload() -> None:
    """Delete friend failures should surface the NapCat action name."""

    async def case() -> None:
        server = NapCatHTTPServer(
            response_payload={
                "status": "failed",
                "retcode": 1404,
                "data": None,
                "message": "好友不存在",
                "wording": "好友不存在",
            }
        )
        await server.start()
        client = NapCatClient(base_url=server.url, timeout_seconds=1.0)
        try:
            with pytest.raises(NapCatAPIError, match="delete_friend"):
                await client.delete_friend(
                    DeleteFriendRequest(
                        user_id="123456",
                        temp_block=False,
                        temp_both_del=True,
                    )
                )
        finally:
            await server.close()

    asyncio.run(case())
