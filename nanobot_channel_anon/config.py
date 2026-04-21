"""Anon channel configuration."""

from typing import Self

from nanobot.config.schema import Base
from pydantic import Field, field_validator, model_validator


class AnonConfig(Base):
    """Anon channel config."""

    enabled: bool = Field(default=False, description="Enable the anon channel.")
    ws_url: str = Field(
        default="",
        description=(
            "NapCat OneBot WebSocket URL. "
            "Use ws:// or wss://, for example ws://127.0.0.1:3001."
        ),
    )
    access_token: str = Field(
        default="",
        description=(
            "Access token for the OneBot WebSocket connection. "
            "Leave empty to disable auth."
        ),
    )
    allow_from: list[str] = Field(
        default_factory=list,
        description='Allowed sender or group IDs. ["*"] allows all, [] denies all.',
    )
    private_trigger_prob: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Probability of triggering on a private chat message.",
    )
    group_trigger_prob: float = Field(
        default=0.03,
        ge=0.0,
        le=1.0,
        description="Probability of triggering on a group chat message.",
    )
    trigger_on_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords that trigger the bot directly.",
    )
    trigger_on_at: bool = Field(
        default=True,
        description="Trigger when the bot is mentioned.",
    )
    trigger_on_reply: bool = Field(
        default=True,
        description="Trigger when replying to the bot.",
    )
    trigger_on_poke: bool = Field(
        default=False,
        description="Trigger when the bot receives a poke event.",
    )
    poke_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        description="Minimum interval between poke-triggered sessions, in seconds.",
    )
    max_text_length: int = Field(
        default=200,
        gt=0,
        description="Maximum length kept from a single incoming text message.",
    )
    max_context_messages: int = Field(
        default=25,
        gt=0,
        description="Maximum number of messages kept in per-chat context.",
    )
    media_max_size_mb: int = Field(
        default=50,
        gt=0,
        description="Maximum media size to process, in MB.",
    )

    @field_validator("ws_url")
    @classmethod
    def validate_ws_url(cls, value: str) -> str:
        """Validate the WebSocket URL."""
        value = value.strip()
        if value and not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        return value

    @field_validator("access_token")
    @classmethod
    def strip_access_token(cls, value: str) -> str:
        """Strip surrounding whitespace from the access token."""
        return value.strip()

    @model_validator(mode="after")
    def validate_enabled_config(self) -> Self:
        """Ensure enabled configs are usable."""
        if self.enabled and not self.ws_url:
            raise ValueError("ws_url is required when anon channel is enabled")
        return self
