"""set_group_ban MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupBanRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_ban_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_group_ban tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_ban",
        description="Mute a QQ group member",
    )
    async def set_group_ban(
        group_id: str,
        user_id: str,
        duration: int,
    ) -> dict[str, Any]:
        """Set a group member ban duration through NapCat HTTP."""
        request = SetGroupBanRequest.from_tool_input(
            {"group_id": group_id, "user_id": user_id, "duration": duration}
        )
        result = await client.set_group_ban(request)
        return {
            "ok": True,
            "action": "set_group_ban",
            "group_id": request.group_id,
            "user_id": request.user_id,
            "duration": request.duration,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
