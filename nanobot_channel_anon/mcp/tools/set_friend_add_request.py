"""qq_set_friend_add_request MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetFriendAddRequestRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_friend_add_request_tool(
    mcp: FastMCP,
    client: NapCatClient,
) -> None:
    """Register the qq_set_friend_add_request tool on the provided MCP server."""

    @mcp.tool(
        name="qq_set_friend_add_request",
        description=(
            "处理 QQ 好友请求."
            "flag 必填, 必须是目标请求的真实 flag."
            "approve 必填, 表示是否通过请求."
            "remark 必填, 表示好友备注."
            "只能使用当前上下文或用户明确提供的真实 flag, 不要猜测或编造."
            "如果当前消息无法明确目标请求, 应先询问用户, 不要直接调用工具."
        ),
    )
    async def qq_set_friend_add_request(
        flag: str,
        approve: bool,
        remark: str,
    ) -> dict[str, Any]:
        """Handle a friend add request through NapCat HTTP."""
        request = SetFriendAddRequestRequest.from_tool_input(
            {"flag": flag, "approve": approve, "remark": remark}
        )
        result = await client.set_friend_add_request(request)
        return {
            "ok": True,
            "action": "set_friend_add_request",
            "flag": request.flag,
            "approve": request.approve,
            "remark": request.remark,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
