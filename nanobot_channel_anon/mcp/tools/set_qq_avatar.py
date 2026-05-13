"""set_qq_avatar MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetQQAvatarRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_qq_avatar_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_qq_avatar tool on the provided MCP server."""

    @mcp.tool(
        name="set_qq_avatar",
        description="Set the current QQ account avatar from a base64:// image payload",
    )
    async def set_qq_avatar(file: str) -> dict[str, Any]:
        """Set QQ avatar through NapCat HTTP."""
        request = SetQQAvatarRequest.from_tool_input({"file": file})
        result = await client.set_qq_avatar(request)
        return {
            "ok": True,
            "action": "set_qq_avatar",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
