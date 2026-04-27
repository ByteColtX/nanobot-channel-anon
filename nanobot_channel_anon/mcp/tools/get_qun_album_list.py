"""get_qun_album_list MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import GetQunAlbumListRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_get_qun_album_list_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the get_qun_album_list tool on the provided MCP server."""

    @mcp.tool(
        name="get_qun_album_list",
        description="Get the album list of a QQ group",
    )
    async def get_qun_album_list(
        group_id: str,
        attach_info: str = "",
    ) -> dict[str, Any]:
        """Fetch the group album list through NapCat HTTP."""
        request = GetQunAlbumListRequest.from_tool_input(
            {"group_id": group_id, "attach_info": attach_info}
        )
        result = await client.get_qun_album_list(request)
        return {
            "ok": True,
            "action": "get_qun_album_list",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
