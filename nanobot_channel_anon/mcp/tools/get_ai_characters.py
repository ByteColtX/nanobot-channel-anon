"""get_ai_characters MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetAICharactersRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_ai_characters_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_ai_characters tool on the provided MCP server."""

    @mcp.tool(
        name="get_ai_characters",
        description="Get the AI voice characters for a QQ group",
    )
    async def get_ai_characters(
        group_id: str,
        chat_type: int = 1,
    ) -> dict[str, Any]:
        """Fetch the AI voice character list through NapCat HTTP."""
        request = GetAICharactersRequest.from_tool_input(
            {"group_id": group_id, "chat_type": chat_type}
        )
        result = await client.get_ai_characters(request)
        return {
            "ok": True,
            "action": "get_ai_characters",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
