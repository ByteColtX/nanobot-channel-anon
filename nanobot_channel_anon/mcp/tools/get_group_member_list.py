"""get_group_member_list MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetGroupMemberListRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_group_member_list_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_group_member_list tool on the provided MCP server."""

    @mcp.tool(
        name="get_group_member_list",
        description="Get the member list of a QQ group",
    )
    async def get_group_member_list(
        group_id: str,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        """Fetch a group member list through NapCat HTTP."""
        request = GetGroupMemberListRequest.from_tool_input(
            {"group_id": group_id, "no_cache": no_cache}
        )
        result = await client.get_group_member_list(request)
        return {
            "ok": True,
            "action": "get_group_member_list",
            "group_id": request.group_id,
            "no_cache": request.no_cache,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
