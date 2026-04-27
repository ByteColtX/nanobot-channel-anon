"""send_group_ai_record MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendGroupAIRecordRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_group_ai_record_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the send_group_ai_record tool on the provided MCP server."""

    @mcp.tool(
        name="send_group_ai_record",
        description="Send a QQ group AI voice message",
    )
    async def send_group_ai_record(
        group_id: str,
        character: str,
        text: str,
    ) -> dict[str, Any]:
        """Send a group AI voice message through NapCat HTTP."""
        request = SendGroupAIRecordRequest.from_tool_input(
            {"group_id": group_id, "character": character, "text": text}
        )
        result = await client.send_group_ai_record(request)
        return {
            "ok": True,
            "action": "send_group_ai_record",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
