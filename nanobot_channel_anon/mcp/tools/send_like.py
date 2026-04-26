"""send_like MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendLikeRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_like_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the send_like tool on the provided MCP server."""

    @mcp.tool(
        name="send_like",
        description=(
            "Send QQ likes."
            "Each call can send at most 10 likes; sending 50 likes requires 5 calls."
            "The daily like limit varies by account and is commonly 10 or 50."
        ),
    )
    async def send_like(user_id: str, times: int) -> dict[str, Any]:
        """Send likes through NapCat HTTP."""
        request = SendLikeRequest.from_tool_input(
            {"user_id": user_id, "times": times}
        )
        result = await client.send_like(request)
        return {
            "ok": True,
            "action": "send_like",
            "user_id": request.user_id,
            "times": request.times,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
