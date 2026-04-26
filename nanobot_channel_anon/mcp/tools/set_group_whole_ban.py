"""set_group_whole_ban MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupWholeBanRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_whole_ban_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_group_whole_ban tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_whole_ban",
        description="Set QQ group whole ban state",
    )
    async def set_group_whole_ban(group_id: str, enable: bool) -> dict[str, Any]:
        """Set group whole-ban state through NapCat HTTP."""
        request = SetGroupWholeBanRequest.from_tool_input(
            {"group_id": group_id, "enable": enable}
        )
        result = await client.set_group_whole_ban(request)
        return {
            "ok": True,
            "action": "set_group_whole_ban",
            "group_id": request.group_id,
            "enable": request.enable,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
