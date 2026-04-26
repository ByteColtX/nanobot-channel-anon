"""set_msg_emoji_like MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetMsgEmojiLikeRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_msg_emoji_like_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the set_msg_emoji_like tool on the provided MCP server."""

    @mcp.tool(
        name="set_msg_emoji_like",
        description="Set emoji like state on a QQ message",
    )
    async def set_msg_emoji_like(
        message_id: str,
        emoji_id: str,
        set: bool,
    ) -> dict[str, Any]:
        """Set message emoji-like state through NapCat HTTP."""
        request = SetMsgEmojiLikeRequest.from_tool_input(
            {"message_id": message_id, "emoji_id": emoji_id, "set": set}
        )
        result = await client.set_msg_emoji_like(request)
        return {
            "ok": True,
            "action": "set_msg_emoji_like",
            "message_id": request.message_id,
            "emoji_id": request.emoji_id,
            "set": request.set,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
