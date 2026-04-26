"""Environment-backed settings for the QQ admin MCP server."""

from __future__ import annotations

from typing import Any, cast

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="NAPCAT_",
        extra="ignore",
    )

    http_url: str
    http_access_token: str = ""
    http_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("http_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        """Require an absolute HTTP(S) NapCat API base URL."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("NAPCAT_HTTP_URL is required")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("NAPCAT_HTTP_URL must start with http:// or https://")
        return normalized.rstrip("/")

    @field_validator("http_access_token")
    @classmethod
    def strip_access_token(cls, value: str) -> str:
        """Normalize optional auth token input."""
        return value.strip()


def load_settings() -> MCPSettings:
    """Load MCP runtime settings from environment variables."""
    try:
        settings_cls = cast(Any, MCPSettings)
        return settings_cls()
    except ValidationError as exc:
        raise RuntimeError(
            "Missing or invalid MCP settings. Set NAPCAT_HTTP_URL, and optionally "
            "NAPCAT_HTTP_ACCESS_TOKEN, before starting nanobot-anon-mcp."
        ) from exc
