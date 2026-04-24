"""qq_set_group_add_request MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import SetGroupAddRequestRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_set_group_add_request_tool(
    mcp: FastMCP,
    client: NapCatClient,
) -> None:
    """Register the qq_set_group_add_request tool on the provided MCP server."""

    @mcp.tool(
        name="qq_set_group_add_request",
        description=(
            "处理 QQ 加群请求或群邀请."
            "flag 必填, 必须是目标请求的真实 flag."
            "sub_type 必填, 只能是 add 或 invite."
            "approve 必填, 表示是否通过请求."
            "reason 选填, 仅在拒绝时使用."
            "只能使用当前上下文或用户明确提供的真实 flag, 不要猜测或编造."
            "如果当前消息无法明确目标请求, 应先询问用户, 不要直接调用工具."
        ),
    )
    async def qq_set_group_add_request(
        flag: str,
        sub_type: str,
        approve: bool,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Handle a group add request through NapCat HTTP."""
        request = SetGroupAddRequestRequest.from_tool_input(
            {
                "flag": flag,
                "sub_type": sub_type,
                "approve": approve,
                "reason": reason,
            }
        )
        result = await client.set_group_add_request(request)
        return {
            "ok": True,
            "action": "set_group_add_request",
            "flag": request.flag,
            "sub_type": request.sub_type,
            "approve": request.approve,
            "reason": request.reason,
            "result": result.model_dump(mode="json", exclude_none=True),
        }
