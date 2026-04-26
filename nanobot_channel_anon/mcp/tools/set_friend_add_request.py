"""set_friend_add_request MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetFriendAddRequestRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_friend_add_request_tool(
    mcp: FastMCP,
    client: NapCatClient,
) -> None:
    """Register the set_friend_add_request tool on the provided MCP server."""

    @mcp.tool(
        name="set_friend_add_request",
        description="Handle a QQ friend request",
    )
    async def set_friend_add_request(
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
