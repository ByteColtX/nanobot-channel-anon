"""set_group_leave MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupLeaveRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_leave_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_group_leave tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_leave",
        description="Leave or dismiss a QQ group",
    )
    async def set_group_leave(
        group_id: str,
        is_dismiss: bool = False,
    ) -> dict[str, Any]:
        """Leave or dismiss a group through NapCat HTTP."""
        request = SetGroupLeaveRequest.from_tool_input(
            {"group_id": group_id, "is_dismiss": is_dismiss}
        )
        result = await client.set_group_leave(request)
        return {
            "ok": True,
            "action": "set_group_leave",
            "group_id": request.group_id,
            "is_dismiss": request.is_dismiss,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
