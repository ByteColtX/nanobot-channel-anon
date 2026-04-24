"""NapCat HTTP client for MCP tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from nanobot_channel_anon.mcp.models import (
    DeleteMsgRequest,
    NapCatActionResult,
    NapCatActionStatus,
    SendLikeRequest,
    SendPokeRequest,
    SetFriendAddRequestRequest,
    SetGroupAddRequestRequest,
)


class NapCatAPIError(RuntimeError):
    """Raised when a NapCat API call fails."""


class NapCatClient:
    """Small HTTP client for NapCat action APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        """Initialize the client with NapCat HTTP connection settings."""
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Create the shared HTTP session when the MCP server starts."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        """Close the shared HTTP session when the MCP server stops."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiohttp.ClientSession]:
        """Yield a usable HTTP session, opening a temporary one if needed."""
        if self._session is not None and not self._session.closed:
            yield self._session
            return
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            yield session

    async def call(self, action: str, params: dict[str, Any]) -> NapCatActionResult:
        """Invoke a NapCat action and return the decoded JSON payload."""
        url = f"{self._base_url}/{action.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        async with self.session() as session, session.post(
            url, json=params, headers=headers
        ) as response:
            response.raise_for_status()
            payload = await response.json()

        if not isinstance(payload, dict):
            raise NapCatAPIError(f"unexpected response payload: {payload!r}")

        result = NapCatActionResult.model_validate(payload)
        if (
            result.status is not NapCatActionStatus.OK
            or result.retcode not in {0, None}
        ):
            raise NapCatAPIError(
                "NapCat action "
                f"{action} failed: status={result.status!r} "
                f"retcode={result.retcode!r} payload={payload!r}"
            )
        return result

    async def delete_msg(self, request: DeleteMsgRequest) -> NapCatActionResult:
        """Call NapCat delete_msg with a validated request model."""
        return await self.call("delete_msg", {"message_id": int(request.message_id)})

    async def send_poke(self, request: SendPokeRequest) -> NapCatActionResult:
        """Call NapCat send_poke with a validated request model."""
        params: dict[str, Any] = {"user_id": int(request.user_id)}
        if request.group_id is not None:
            params["group_id"] = int(request.group_id)
        return await self.call("send_poke", params)

    async def set_group_add_request(
        self,
        request: SetGroupAddRequestRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_group_add_request with a validated request model."""
        params: dict[str, Any] = {
            "flag": request.flag,
            "sub_type": request.sub_type,
            "approve": request.approve,
        }
        if not request.approve and request.reason is not None:
            params["reason"] = request.reason
        return await self.call("set_group_add_request", params)

    async def set_friend_add_request(
        self,
        request: SetFriendAddRequestRequest,
    ) -> NapCatActionResult:
        """Call NapCat set_friend_add_request with a validated request model."""
        return await self.call(
            "set_friend_add_request",
            {
                "flag": request.flag,
                "approve": request.approve,
                "remark": request.remark,
            },
        )

    async def send_like(self, request: SendLikeRequest) -> NapCatActionResult:
        """Call NapCat send_like with a validated request model."""
        return await self.call(
            "send_like",
            {"user_id": int(request.user_id), "times": request.times},
        )
