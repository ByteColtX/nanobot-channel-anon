"""send_poke MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendPokeRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_poke_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the send_poke tool on the provided MCP server."""

    @mcp.tool(
        name="send_poke",
        description="Send a QQ poke",
    )
    async def send_poke(
        user_id: str,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a poke through NapCat HTTP."""
        request = SendPokeRequest.from_tool_input(
            {"user_id": user_id, "group_id": group_id}
        )
        result = await client.send_poke(request)
        return {
            "ok": True,
            "action": "send_poke",
            "user_id": request.user_id,
            "group_id": request.group_id,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
