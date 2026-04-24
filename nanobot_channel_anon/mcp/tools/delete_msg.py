"""qq_delete_msg MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import DeleteMsgRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_delete_msg_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the qq_delete_msg tool on the provided MCP server."""

    @mcp.tool(
        name="qq_delete_msg",
        description=(
            "撤回 QQ 消息."
            "message_id 必填, 必须是目标消息的真实 message_id."
            "只能使用当前上下文里明确出现过的真实消息 ID, 不要猜测或编造."
            "如果当前消息无法明确要撤回哪一条, 应先询问用户, 不要直接调用工具."
        ),
    )
    async def qq_delete_msg(message_id: str) -> dict[str, Any]:
        """Recall a message through NapCat HTTP."""
        request = DeleteMsgRequest.from_tool_input({"message_id": message_id})
        result = await client.delete_msg(request)
        return {
            "ok": True,
            "action": "delete_msg",
            "message_id": request.message_id,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
