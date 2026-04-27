"""get_ai_record MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetAIRecordRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_ai_record_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_ai_record tool on the provided MCP server."""

    @mcp.tool(
        name="get_ai_record",
        description="Get a QQ group AI voice URL",
    )
    async def get_ai_record(
        group_id: str,
        character: str,
        text: str,
    ) -> dict[str, Any]:
        """Fetch an AI voice URL through NapCat HTTP."""
        request = GetAIRecordRequest.from_tool_input(
            {"group_id": group_id, "character": character, "text": text}
        )
        result = await client.get_ai_record(request)
        return {
            "ok": True,
            "action": "get_ai_record",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
