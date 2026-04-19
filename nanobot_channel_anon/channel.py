# nanobot_channel_anon/channel.py
import asyncio
from typing import Any

from aiohttp import web
from loguru import logger
from pydantic import Field

from nanobot.channels.base import BaseChannel
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Base


class AnonConfig(Base):
    """Anon channel configuration."""
    enabled: bool = False
    port: int = 9000
    allow_from: list[str] = Field(default_factory=list)


class AnonChannel(BaseChannel):
    name = "anon"
    display_name = "Anon"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = AnonConfig(**config)
        super().__init__(config, bus)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return AnonConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """Start an HTTP server that listens for incoming messages.

        IMPORTANT: start() must block forever (or until stop() is called).
        If it returns, the channel is considered dead.
        """
        self._running = True
        port = self.config.port

        app = web.Application()
        app.router.add_post("/message", self._on_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Anon listening on :{}", port)

        # Block until stopped
        while self._running:
            await asyncio.sleep(1)

        await runner.cleanup()

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Deliver an outbound message.

        msg.content  — markdown text (convert to platform format as needed)
        msg.media    — list of local file paths to attach
        msg.chat_id  — the recipient (same chat_id you passed to _handle_message)
        msg.metadata — may contain "_progress": True for streaming chunks
        """
        logger.info("[anon] -> {}: {}", msg.chat_id, msg.content[:80])
        # In a real plugin: POST to a callback URL, send via SDK, etc.

    async def _on_request(self, request: web.Request) -> web.Response:
        """Handle an incoming HTTP POST."""
        body = await request.json()
        sender = body.get("sender", "unknown")
        chat_id = body.get("chat_id", sender)
        text = body.get("text", "")
        media = body.get("media", [])       # list of URLs

        # This is the key call: validates allowFrom, then puts the
        # message onto the bus for the agent to process.
        await self._handle_message(
            sender_id=sender,
            chat_id=chat_id,
            content=text,
            media=media,
        )

        return web.json_response({"ok": True})
