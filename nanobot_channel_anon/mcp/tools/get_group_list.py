"""get_group_list MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetGroupListRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_group_list_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_group_list tool on the provided MCP server."""

    @mcp.tool(
        name="get_group_list",
        description="Get the group list of the current QQ account",
    )
    async def get_group_list(no_cache: bool = False) -> dict[str, Any]:
        """Fetch a group list through NapCat HTTP."""
        request = GetGroupListRequest.from_tool_input({"no_cache": no_cache})
        result = await client.get_group_list(request)
        return {
            "ok": True,
            "action": "get_group_list",
            "no_cache": request.no_cache,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
