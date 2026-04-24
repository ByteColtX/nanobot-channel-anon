"""qq_send_like MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendLikeRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_like_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the qq_send_like tool on the provided MCP server."""

    @mcp.tool(
        name="qq_send_like",
        description=(
            "发送 QQ 点赞."
            "user_id 必填, 必须是目标用户的真实 QQ 号."
            "times 必填, 表示点赞次数, 必须是正整数."
            "只能使用当前上下文里明确出现过的真实 ID, 不要猜测或编造."
            "如果当前消息无法明确目标用户或点赞次数, 应先询问用户, 不要直接调用工具."
        ),
    )
    async def qq_send_like(user_id: str, times: int) -> dict[str, Any]:
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
