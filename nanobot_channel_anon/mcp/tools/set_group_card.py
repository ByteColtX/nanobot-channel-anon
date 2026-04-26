"""set_group_card MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupCardRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_card_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_group_card tool on the provided MCP server."""

    @mcp.tool(
        name="set_group_card",
        description="Set a QQ group card",
    )
    async def set_group_card(
        group_id: str,
        user_id: str,
        card: str,
    ) -> dict[str, Any]:
        """Set a group card through NapCat HTTP."""
        request = SetGroupCardRequest.from_tool_input(
            {"group_id": group_id, "user_id": user_id, "card": card}
        )
        result = await client.set_group_card(request)
        return {
            "ok": True,
            "action": "set_group_card",
            "group_id": request.group_id,
            "user_id": request.user_id,
            "card": request.card,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
