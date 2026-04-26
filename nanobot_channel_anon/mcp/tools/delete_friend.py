"""delete_friend MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import DeleteFriendRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_delete_friend_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the delete_friend tool on the provided MCP server."""

    @mcp.tool(
        name="delete_friend",
        description="Delete a QQ friend or block the user",
    )
    async def delete_friend(
        user_id: str,
        temp_block: bool = False,
        temp_both_del: bool = False,
    ) -> dict[str, Any]:
        """Delete a friend through NapCat HTTP."""
        request = DeleteFriendRequest.from_tool_input(
            {
                "user_id": user_id,
                "temp_block": temp_block,
                "temp_both_del": temp_both_del,
            }
        )
        result = await client.delete_friend(request)
        return {
            "ok": True,
            "action": "delete_friend",
            "user_id": request.user_id,
            "temp_block": request.temp_block,
            "temp_both_del": request.temp_both_del,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
