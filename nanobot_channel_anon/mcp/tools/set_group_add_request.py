"""set_group_add_request MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupAddRequestRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_add_request_tool(
    mcp: FastMCP,
    client: NapCatClient,
) -> None:
    """Register the set_group_add_request tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_add_request",
        description="Handle a QQ group join request or invitation",
    )
    async def set_group_add_request(
        flag: str,
        sub_type: str,
        approve: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Handle a group add request through NapCat HTTP."""
        request = SetGroupAddRequestRequest.from_tool_input(
            {
                "flag": flag,
                "sub_type": sub_type,
                "approve": approve,
                "reason": reason,
            }
        )
        result = await client.set_group_add_request(request)
        return {
            "ok": True,
            "action": "set_group_add_request",
            "flag": request.flag,
            "sub_type": request.sub_type,
            "approve": request.approve,
            "reason": request.reason,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
