"""qq_send_poke MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SendPokeRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_send_poke_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the qq_send_poke tool on the provided MCP server."""

    @mcp.tool(
        name="qq_send_poke",
        description=(
            "发送 QQ 戳一戳."
            "user_id 必填, 必须是目标用户的真实 QQ 号."
            "group_id 选填: 传 group_id 表示群聊戳; 不传 group_id 表示私聊戳."
            "group_id 兼容 'group:<digits>' 和纯 '<digits>' 两种写法."
            "只能使用当前上下文里明确出现过的真实 ID, 不要猜测或编造."
            "如果当前消息无法明确目标用户, 应先询问用户, 不要直接调用工具."
        ),
    )
    async def qq_send_poke(
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
