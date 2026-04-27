"""get_group_detail_info MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetGroupDetailInfoRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_group_detail_info_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_group_detail_info tool on the provided MCP server."""

    @mcp.tool(
        name="get_group_detail_info",
        description="Get detailed information for a QQ group",
    )
    async def get_group_detail_info(group_id: str) -> dict[str, Any]:
        """Fetch detailed group information through NapCat HTTP."""
        request = GetGroupDetailInfoRequest.from_tool_input({"group_id": group_id})
        result = await client.get_group_detail_info(request)
        return {
            "ok": True,
            "action": "get_group_detail_info",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
