"""set_group_kick MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupKickRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_kick_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_group_kick tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_kick",
        description="Kick a QQ group member",
    )
    async def set_group_kick(
        group_id: str,
        user_id: str,
        reject_add_request: bool = False,
    ) -> dict[str, Any]:
        """Kick a group member through NapCat HTTP."""
        request = SetGroupKickRequest.from_tool_input(
            {
                "group_id": group_id,
                "user_id": user_id,
                "reject_add_request": reject_add_request,
            }
        )
        result = await client.set_group_kick(request)
        return {
            "ok": True,
            "action": "set_group_kick",
            "group_id": request.group_id,
            "user_id": request.user_id,
            "reject_add_request": request.reject_add_request,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
