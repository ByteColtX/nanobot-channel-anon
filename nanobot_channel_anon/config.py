"""Anon channel configuration."""

from __future__ import annotations

from typing import Any

from nanobot.config.schema import Base
from pydantic import Field, field_validator, model_validator

from nanobot_channel_anon.utils import normalize_onebot_id


class AnonConfig(Base):
    """Anon 频道配置."""

    enabled: bool = Field(default=False, description="是否启用频道。")
    allow_from: list[str] = Field(
        default_factory=list,
        description=(
            "允许访问的来源列表。支持发送者 ID、原始会话 ID, "
            "以及 group:<id>/private:<id> 形式的标准会话键; [*] 表示允许全部。"
        ),
    )
    ws_url: str = Field(default="", description="NapCat OneBot WebSocket 地址。")
    access_token: str = Field(default="", description="OneBot 连接鉴权令牌。")
    super_admins: list[str] = Field(
        default_factory=list,
        description="超级管理员发送者 ID。",
    )
    private_trigger_prob: float = Field(default=0.85, ge=0.0, le=1.0)
    group_trigger_prob: float = Field(default=0.03, ge=0.0, le=1.0)
    trigger_on_keywords: list[str] = Field(default_factory=list)
    trigger_on_at: bool = Field(default=True)
    trigger_on_reply: bool = Field(default=True)
    trigger_on_poke: bool = Field(default=False)
    poke_cooldown_seconds: int = Field(default=60, ge=0)
    max_ctx_length: int = Field(
        default=300,
        gt=0,
        description="单条 CTX 消息体允许保留的最大长度。",
    )
    max_context_messages: int = Field(
        default=25,
        gt=0,
        description="每个会话保留的最大上下文消息数。",
    )
    media_max_size_mb: int = Field(
        default=50,
        gt=0,
        description="入站媒体允许处理的最大大小, 单位 MB。",
    )

    @field_validator("ws_url")
    @classmethod
    def validate_ws_url(cls, value: str) -> str:
        """校验 WebSocket 地址格式."""
        value = value.strip()
        if value and not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        return value

    @field_validator("access_token")
    @classmethod
    def strip_access_token(cls, value: str) -> str:
        """清理鉴权令牌两端空白."""
        return value.strip()

    @field_validator("allow_from", mode="before")
    @classmethod
    def normalize_allow_from(cls, value: Any) -> list[str]:
        """标准化 allow_from 配置."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("allow_from must be a list of sender or group IDs")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized_item = cls._normalize_allow_entry(item)
            if normalized_item is None or normalized_item in seen:
                continue
            normalized.append(normalized_item)
            seen.add(normalized_item)
        return normalized

    @field_validator("super_admins", mode="before")
    @classmethod
    def normalize_super_admins(cls, value: Any) -> list[str]:
        """标准化 super_admins 配置."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("super_admins must be a list of sender IDs")

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            item_id = normalize_onebot_id(item)
            if item_id is None or item_id in seen:
                continue
            normalized.append(item_id)
            seen.add(item_id)
        return normalized

    @property
    def allow_all(self) -> bool:
        """返回是否允许全部来源."""
        return "*" in self.allow_from

    @property
    def allowed_sender_ids(self) -> frozenset[str]:
        """返回允许的发送者 ID 集合."""
        return frozenset(
            entry
            for entry in self.allow_from
            if entry != "*" and ":" not in entry
        )

    @property
    def allowed_conversation_keys(self) -> frozenset[str]:
        """返回允许的标准会话键集合."""
        canonical_keys = {
            entry
            for entry in self.allow_from
            if entry.startswith(("group:", "private:"))
        }
        canonical_keys.update(f"group:{entry}" for entry in self.allowed_sender_ids)
        return frozenset(canonical_keys)

    @classmethod
    def _normalize_allow_entry(cls, value: Any) -> str | None:
        """标准化单个 allow_from 条目."""
        item = normalize_onebot_id(value)
        if item is None:
            return None
        if item == "*":
            return item
        prefix, separator, raw_id = item.partition(":")
        if separator:
            normalized_id = normalize_onebot_id(raw_id)
            if prefix not in {"group", "private"} or normalized_id is None:
                return item
            return f"{prefix}:{normalized_id}"
        return item

    @model_validator(mode="after")
    def validate_enabled_requirements(self) -> AnonConfig:
        """校验启用频道时所需的最小配置."""
        if self.enabled and not self.allow_from:
            raise ValueError("enabled channel requires a non-empty allow_from")
        if self.enabled and not self.ws_url:
            raise ValueError("enabled channel requires a non-empty ws_url")
        return self

