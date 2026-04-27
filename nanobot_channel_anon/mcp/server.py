"""FastMCP server exposing QQ admin tools backed by NapCat HTTP."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import FastMCP

from nanobot_channel_anon.mcp.napcat_client import NapCatClient
from nanobot_channel_anon.mcp.settings import load_settings
from nanobot_channel_anon.mcp.tools.delete_friend import register_delete_friend_tool
from nanobot_channel_anon.mcp.tools.delete_msg import register_delete_msg_tool
from nanobot_channel_anon.mcp.tools.get_ai_characters import (
    register_get_ai_characters_tool,
)
from nanobot_channel_anon.mcp.tools.get_ai_record import register_get_ai_record_tool
from nanobot_channel_anon.mcp.tools.get_friend_list import register_get_friend_list_tool
from nanobot_channel_anon.mcp.tools.get_group_list import register_get_group_list_tool
from nanobot_channel_anon.mcp.tools.get_group_member_list import (
    register_get_group_member_list_tool,
)
from nanobot_channel_anon.mcp.tools.send_group_ai_record import (
    register_send_group_ai_record_tool,
)
from nanobot_channel_anon.mcp.tools.send_like import register_send_like_tool
from nanobot_channel_anon.mcp.tools.send_poke import register_send_poke_tool
from nanobot_channel_anon.mcp.tools.set_friend_add_request import (
    register_set_friend_add_request_tool,
)
from nanobot_channel_anon.mcp.tools.set_group_add_request import (
    register_set_group_add_request_tool,
)
from nanobot_channel_anon.mcp.tools.set_group_ban import register_set_group_ban_tool
from nanobot_channel_anon.mcp.tools.set_group_card import register_set_group_card_tool
from nanobot_channel_anon.mcp.tools.set_group_kick import register_set_group_kick_tool
from nanobot_channel_anon.mcp.tools.set_group_leave import register_set_group_leave_tool
from nanobot_channel_anon.mcp.tools.set_group_whole_ban import (
    register_set_group_whole_ban_tool,
)
from nanobot_channel_anon.mcp.tools.set_msg_emoji_like import (
    register_set_msg_emoji_like_tool,
)


def create_client() -> NapCatClient:
    """Build the shared NapCat HTTP client from environment settings."""
    settings = load_settings()
    return NapCatClient(
        base_url=settings.http_url,
        access_token=settings.http_access_token,
        timeout_seconds=settings.http_timeout_seconds,
    )


def create_server() -> FastMCP[object]:
    """Build the MCP server and register all QQ admin tools."""
    client = create_client()

    @asynccontextmanager
    async def mcp_lifespan(_: FastMCP[object]) -> AsyncIterator[None]:
        await client.open()
        try:
            yield
        finally:
            await client.close()

    mcp = FastMCP("napcat-qq-actions", lifespan=mcp_lifespan)
    register_delete_msg_tool(mcp, client)
    register_delete_friend_tool(mcp, client)
    register_send_poke_tool(mcp, client)
    register_send_group_ai_record_tool(mcp, client)
    register_send_like_tool(mcp, client)
    register_get_ai_record_tool(mcp, client)
    register_get_ai_characters_tool(mcp, client)
    register_get_group_member_list_tool(mcp, client)
    register_get_friend_list_tool(mcp, client)
    register_get_group_list_tool(mcp, client)
    register_set_group_add_request_tool(mcp, client)
    register_set_friend_add_request_tool(mcp, client)
    register_set_group_ban_tool(mcp, client)
    register_set_group_kick_tool(mcp, client)
    register_set_group_card_tool(mcp, client)
    register_set_group_leave_tool(mcp, client)
    register_set_group_whole_ban_tool(mcp, client)
    register_set_msg_emoji_like_tool(mcp, client)
    return mcp


def main() -> None:
    """Run the MCP server over stdio."""
    create_server().run("stdio")


if __name__ == "__main__":
    main()
