"""delete_msg MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import DeleteMsgRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_delete_msg_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the delete_msg tool on the provided MCP server."""

    @mcp.tool(
        name="delete_msg",
        description="Recall a QQ message",
    )
    async def delete_msg(message_id: str) -> dict[str, Any]:
        """Recall a message through NapCat HTTP."""
        request = DeleteMsgRequest.from_tool_input({"message_id": message_id})
        result = await client.delete_msg(request)
        return {
            "ok": True,
            "action": "delete_msg",
            "message_id": request.message_id,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
