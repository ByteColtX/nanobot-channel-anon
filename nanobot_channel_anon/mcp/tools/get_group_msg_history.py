"""get_group_msg_history MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetGroupMsgHistoryRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_group_msg_history_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_group_msg_history tool on the provided MCP server."""

    @mcp.tool(
        name="get_group_msg_history",
        description="Get the message history of a QQ group",
    )
    async def get_group_msg_history(
        group_id: str,
        message_seq: str | None = None,
        count: int = 20,
        reverse_order: bool = False,
        disable_get_url: bool = False,
        parse_mult_msg: bool = True,
        quick_reply: bool = False,
    ) -> dict[str, Any]:
        """Fetch group message history through NapCat HTTP."""
        request = GetGroupMsgHistoryRequest.from_tool_input(
            {
                "group_id": group_id,
                "message_seq": message_seq,
                "count": count,
                "reverse_order": reverse_order,
                "disable_get_url": disable_get_url,
                "parse_mult_msg": parse_mult_msg,
                "quick_reply": quick_reply,
            }
        )
        result = await client.get_group_msg_history(request)
        return {
            "ok": True,
            "action": "get_group_msg_history",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
