"""create_flash_task MCP tool."""

from __future__ import annotations

from typing import Any

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.models import CreateFlashTaskRequest
from nanobot_channel_anon.mcp.napcat_client import NapCatClient


def register_create_flash_task_tool(mcp: FastMCP, client: NapCatClient) -> None:
    """Register the create_flash_task tool on the provided MCP server."""

    @mcp.tool(
        name="create_flash_task",
        description="Create a QQ flash transfer task",
    )
    async def create_flash_task(
        files: str | list[str],
        name: str | None = None,
        thumb_path: str | None = None,
    ) -> dict[str, Any]:
        """Create a flash transfer task through NapCat HTTP."""
        request = CreateFlashTaskRequest.from_tool_input(
            {"files": files, "name": name, "thumb_path": thumb_path}
        )
        result = await client.create_flash_task(request)
        return {
            "ok": True,
            "action": "create_flash_task",
            "request": request.model_dump(mode="json", exclude_none=True),
            "result": result.model_dump(mode="json", exclude_none=True),
        }
