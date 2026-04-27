"""send_flash_msg MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendFlashMsgRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_flash_msg_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the send_flash_msg tool on the provided MCP server."""

    @mcp.tool(
        name="send_flash_msg",
        description="Send a QQ flash transfer message",
    )
    async def send_flash_msg(
        fileset_id: str,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a flash transfer message through NapCat HTTP."""
        request = SendFlashMsgRequest.from_tool_input(
            {"fileset_id": fileset_id, "user_id": user_id, "group_id": group_id}
        )
        result = await client.send_flash_msg(request)
        return {
            "ok": True,
            "action": "send_flash_msg",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
