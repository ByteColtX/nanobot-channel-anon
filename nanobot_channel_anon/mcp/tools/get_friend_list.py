"""get_friend_list MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetFriendListRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_friend_list_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_friend_list tool on the provided MCP server."""

    @mcp.tool(
        name="get_friend_list",
        description="Get the friend list of the current QQ account",
    )
    async def get_friend_list(no_cache: bool = False) -> dict[str, Any]:
        """Fetch a friend list through NapCat HTTP."""
        request = GetFriendListRequest.from_tool_input({"no_cache": no_cache})
        result = await client.get_friend_list(request)
        return {
            "ok": True,
            "action": "get_friend_list",
            "no_cache": request.no_cache,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
