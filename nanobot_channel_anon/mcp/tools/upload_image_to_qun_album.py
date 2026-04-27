"""upload_image_to_qun_album MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import UploadImageToQunAlbumRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_upload_image_to_qun_album_tool(
    mcp: FastMCP,
    client: NapCatClient,
) -> None:
    """Register the upload_image_to_qun_album tool on the provided MCP server."""

    @mcp.tool(
        name="upload_image_to_qun_album",
        description="Upload an image to a QQ group album",
    )
    async def upload_image_to_qun_album(
        group_id: str,
        album_id: str,
        album_name: str,
        file: str,
    ) -> dict[str, Any]:
        """Upload an image to a group album through NapCat HTTP."""
        request = UploadImageToQunAlbumRequest.from_tool_input(
            {
                "group_id": group_id,
                "album_id": album_id,
                "album_name": album_name,
                "file": file,
            }
        )
        result = await client.upload_image_to_qun_album(request)
        return {
            "ok": True,
            "action": "upload_image_to_qun_album",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
