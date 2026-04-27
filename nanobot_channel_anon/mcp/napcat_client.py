"""NapCat HTTP client for MCP tools."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from loguru import logger

from nanobot_channel_anon.mcp.models import (
    CreateFlashTaskRequest,
    DeleteFriendRequest,
    DeleteMsgRequest,
    GetAICharactersRequest,
    GetAIRecordRequest,
    GetFriendListRequest,
    GetFriendMsgHistoryRequest,
    GetGroupDetailInfoRequest,
    GetGroupInfoRequest,
    GetGroupListRequest,
    GetGroupMemberInfoRequest,
    GetGroupMemberListRequest,
    GetGroupMsgHistoryRequest,
    GetQunAlbumListRequest,
    NapCatActionResult,
    NapCatActionStatus,
    SendFlashMsgRequest,
    SendGroupAIRecordRequest,
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
    UploadImageToQunAlbumRequest,
)


class NapCatAPIError(RuntimeError):
    """Raised when a NapCat API call fails."""


def _format_log_value(value: Any, *, limit: int = 500) -> str:
    """Render structured values into a single-line truncated log string."""
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[:limit]}..."


class NapCatClient:
    """Small HTTP client for NapCat action APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        """Initialize the client with NapCat HTTP connection settings."""
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Create the shared HTTP session when the MCP server starts."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        """Close the shared HTTP session when the MCP server stops."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiohttp.ClientSession]:
        """Yield a usable HTTP session, opening a temporary one if needed."""
        if self._session is not None and not self._session.closed:
            yield self._session
            return
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            yield session

    async def call(self, action: str, params: dict[str, Any]) -> NapCatActionResult:
        """Invoke a NapCat action and return the decoded JSON payload."""
        url = f"{self._base_url}/{action.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        async with self.session() as session, session.post(
            url, json=params, headers=headers
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        if not isinstance(payload, dict):
            raise NapCatAPIError(f"unexpected response payload: {payload!r}")

        result = NapCatActionResult.model_validate(payload)
        if (
            result.status is not NapCatActionStatus.OK
            or result.retcode not in {0, None}
        ):
            raise NapCatAPIError(
                "NapCat action "
                f"{action} failed: status={result.status!r} "
                f"retcode={result.retcode!r} payload={payload!r}"
            )
        logger.debug(
            "NapCat MCP result: action={} params={} result={}",
            action,
            _format_log_value(params),
            _format_log_value(result.model_dump(mode="json", exclude_none=True)),
        )
        return result

    async def delete_msg(self, request: DeleteMsgRequest) -> NapCatActionResult:
        """Call NapCat delete_msg with a validated request model."""
        return await self.call("delete_msg", {"message_id": int(request.message_id)})

    async def send_poke(self, request: SendPokeRequest) -> NapCatActionResult:
        """Call NapCat send_poke with a validated request model."""
        params: dict[str, Any] = {"user_id": int(request.user_id)}
        if request.group_id is not None:
            params["group_id"] = int(request.group_id)
        return await self.call("send_poke", params)

    async def send_group_ai_record(
        self,
        request: SendGroupAIRecordRequest,
    ) -> NapCatActionResult:
        """Call NapCat send_group_ai_record with a validated request model."""
        return await self.call(
            "send_group_ai_record",
            {
                "group_id": int(request.group_id),
                "character": request.character,
                "text": request.text,
            },
        )

    async def get_ai_record(
        self,
        request: GetAIRecordRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_ai_record with a validated request model."""
        return await self.call(
            "get_ai_record",
            {
                "group_id": int(request.group_id),
                "character": request.character,
                "text": request.text,
            },
        )

    async def get_ai_characters(
        self,
        request: GetAICharactersRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_ai_characters with a validated request model."""
        return await self.call(
            "get_ai_characters",
            {
                "group_id": request.group_id,
                "chat_type": request.chat_type,
            },
        )

    async def set_group_add_request(
        self,
        request: SetGroupAddRequestRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_add_request with a validated request model."""
        params: dict[str, Any] = {
            "flag": request.flag,
            "sub_type": request.sub_type,
            "approve": request.approve,
        }
        if not request.approve and request.reason is not None:
            params["reason"] = request.reason
        return await self.call("set_group_add_request", params)

    async def set_friend_add_request(
        self,
        request: SetFriendAddRequestRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_friend_add_request with a validated request model."""
        return await self.call(
            "set_friend_add_request",
            {
                "flag": request.flag,
                "approve": request.approve,
                "remark": request.remark,
            },
        )

    async def send_like(self, request: SendLikeRequest) -> NapCatActionResult:
        """Call NapCat send_like with a validated request model."""
        return await self.call(
            "send_like",
            {"user_id": int(request.user_id), "times": request.times},
        )

    async def get_friend_msg_history(
        self,
        request: GetFriendMsgHistoryRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_friend_msg_history with a validated request model."""
        params: dict[str, Any] = {
            "user_id": request.user_id,
            "count": request.count,
            "reverse_order": request.reverse_order,
            "disable_get_url": request.disable_get_url,
            "parse_mult_msg": request.parse_mult_msg,
            "quick_reply": request.quick_reply,
            "reverseOrder": request.reverse_order,
        }
        if request.message_seq is not None:
            params["message_seq"] = request.message_seq
        return await self.call("get_friend_msg_history", params)

    async def get_group_msg_history(
        self,
        request: GetGroupMsgHistoryRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_msg_history with a validated request model."""
        params: dict[str, Any] = {
            "group_id": request.group_id,
            "count": request.count,
            "reverse_order": request.reverse_order,
            "disable_get_url": request.disable_get_url,
            "parse_mult_msg": request.parse_mult_msg,
            "quick_reply": request.quick_reply,
            "reverseOrder": request.reverse_order,
        }
        if request.message_seq is not None:
            params["message_seq"] = request.message_seq
        return await self.call("get_group_msg_history", params)

    async def create_flash_task(
        self,
        request: CreateFlashTaskRequest,
    ) -> NapCatActionResult:
        """Call NapCat create_flash_task with a validated request model."""
        params: dict[str, Any] = {"files": request.files}
        if request.name is not None:
            params["name"] = request.name
        if request.thumb_path is not None:
            params["thumb_path"] = request.thumb_path
        return await self.call("create_flash_task", params)

    async def send_flash_msg(
        self,
        request: SendFlashMsgRequest,
    ) -> NapCatActionResult:
        """Call NapCat send_flash_msg with a validated request model."""
        params: dict[str, Any] = {"fileset_id": request.fileset_id}
        if request.user_id is not None:
            params["user_id"] = request.user_id
        if request.group_id is not None:
            params["group_id"] = request.group_id
        return await self.call("send_flash_msg", params)

    async def get_group_info(
        self,
        request: GetGroupInfoRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_info with a validated request model."""
        return await self.call(
            "get_group_info",
            {"group_id": int(request.group_id)},
        )

    async def get_group_detail_info(
        self,
        request: GetGroupDetailInfoRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_detail_info with a validated request model."""
        return await self.call(
            "get_group_detail_info",
            {"group_id": int(request.group_id)},
        )

    async def get_group_member_list(
        self,
        request: GetGroupMemberListRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_member_list with a validated request model."""
        return await self.call(
            "get_group_member_list",
            {"group_id": int(request.group_id), "no_cache": request.no_cache},
        )

    async def get_group_member_info(
        self,
        request: GetGroupMemberInfoRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_member_info with a validated request model."""
        return await self.call(
            "get_group_member_info",
            {
                "group_id": int(request.group_id),
                "user_id": int(request.user_id),
                "no_cache": request.no_cache,
            },
        )

    async def get_qun_album_list(
        self,
        request: GetQunAlbumListRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_qun_album_list with a validated request model."""
        return await self.call(
            "get_qun_album_list",
            {
                "group_id": request.group_id,
                "attach_info": request.attach_info,
            },
        )

    async def upload_image_to_qun_album(
        self,
        request: UploadImageToQunAlbumRequest,
    ) -> NapCatActionResult:
        """Call NapCat upload_image_to_qun_album with a validated request model."""
        return await self.call(
            "upload_image_to_qun_album",
            {
                "group_id": request.group_id,
                "album_id": request.album_id,
                "album_name": request.album_name,
                "file": request.file,
            },
        )

    async def set_group_ban(
        self,
        request: SetGroupBanRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_ban with a validated request model."""
        return await self.call(
            "set_group_ban",
            {
                "group_id": int(request.group_id),
                "user_id": int(request.user_id),
                "duration": request.duration,
            },
        )

    async def set_group_kick(
        self,
        request: SetGroupKickRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_kick with a validated request model."""
        return await self.call(
            "set_group_kick",
            {
                "group_id": int(request.group_id),
                "user_id": int(request.user_id),
                "reject_add_request": request.reject_add_request,
            },
        )

    async def get_friend_list(
        self,
        request: GetFriendListRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_friend_list with a validated request model."""
        return await self.call(
            "get_friend_list",
            {"no_cache": request.no_cache},
        )

    async def get_group_list(
        self,
        request: GetGroupListRequest,
    ) -> NapCatActionResult:
        """Call NapCat get_group_list with a validated request model."""
        return await self.call(
            "get_group_list",
            {"no_cache": request.no_cache},
        )

    async def set_group_whole_ban(
        self,
        request: SetGroupWholeBanRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_whole_ban with a validated request model."""
        return await self.call(
            "set_group_whole_ban",
            {"group_id": int(request.group_id), "enable": request.enable},
        )

    async def set_group_leave(
        self,
        request: SetGroupLeaveRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_leave with a validated request model."""
        return await self.call(
            "set_group_leave",
            {"group_id": int(request.group_id), "is_dismiss": request.is_dismiss},
        )

    async def set_msg_emoji_like(
        self,
        request: SetMsgEmojiLikeRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_msg_emoji_like with a validated request model."""
        return await self.call(
            "set_msg_emoji_like",
            {
                "message_id": int(request.message_id),
                "emoji_id": int(request.emoji_id),
                "set": request.set,
            },
        )

    async def delete_friend(
        self,
        request: DeleteFriendRequest,
    ) -> NapCatActionResult:
        """Call NapCat delete_friend with a validated request model."""
        return await self.call(
            "delete_friend",
            {
                "user_id": int(request.user_id),
                "temp_block": request.temp_block,
                "temp_both_del": request.temp_both_del,
            },
        )

    async def set_group_card(
        self,
        request: SetGroupCardRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_card with a validated request model."""
        return await self.call(
            "set_group_card",
            {
                "group_id": int(request.group_id),
                "user_id": int(request.user_id),
                "card": request.card,
            },
        )
