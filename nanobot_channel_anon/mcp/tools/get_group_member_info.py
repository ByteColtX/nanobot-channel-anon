"""get_group_member_info MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetGroupMemberInfoRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_group_member_info_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_group_member_info tool on the provided MCP server."""

    @mcp.tool(
        name="get_group_member_info",
        description="Get information for a QQ group member",
    )
    async def get_group_member_info(
        group_id: str,
        user_id: str,
        no_cache: bool,
    ) -> dict[str, Any]:
        """Fetch group member information through NapCat HTTP."""
        request = GetGroupMemberInfoRequest.from_tool_input(
            {"group_id": group_id, "user_id": user_id, "no_cache": no_cache}
        )
        result = await client.get_group_member_info(request)
        return {
            "ok": True,
            "action": "get_group_member_info",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
