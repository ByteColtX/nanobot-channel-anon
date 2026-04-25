"""Tests for Anon channel configuration validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot_channel_anon.config import AnonConfig


def test_enabled_config_requires_non_empty_ws_url() -> None:
    """启用频道时必须提供非空 WebSocket 地址."""
    with pytest.raises(
        ValidationError,
        match="enabled channel requires a non-empty ws_url",
    ):
        AnonConfig(
            enabled=True,
            allow_from=["*"],
            ws_url="",
        )


def test_enabled_config_requires_non_empty_allow_from() -> None:
    """启用频道时必须提供非空 allow_from 列表."""
    with pytest.raises(
        ValidationError,
        match=(
            "enabled channel requires a non-empty allow_from; "
            "please configure allowFrom with allowed senders or conversations, "
            "or disable the channel"
        ),
    ):
        AnonConfig(
            enabled=True,
            allow_from=[],
            ws_url="ws://127.0.0.1:3001",
        )


def test_disabled_config_allows_empty_ws_url() -> None:
    """禁用频道时允许保留空的 WebSocket 地址."""
    config = AnonConfig(enabled=False, ws_url="")

    assert config.ws_url == ""


def test_allow_from_and_super_admins_normalize_scalar_ids() -> None:
    """配置层应直接负责标准化 ID 相关字段."""
    config = AnonConfig.model_validate(
        {
            "allow_from": [" group:456 ", 123, "private:789", True, "123"],
            "super_admins": [" 42 ", 7, True, "42"],
        }
    )

    assert config.allow_from == ["group:456", "123", "private:789"]
    assert config.super_admins == ["42", "7"]


def test_context_and_media_limits_keep_legacy_defaults() -> None:
    """重构后仍应保留可配置的上下文与媒体限制字段."""
    config = AnonConfig.model_validate({})

    assert config.max_ctx_length == 300
    assert config.max_context_messages == 25
    assert config.media_max_size_mb == 50


def test_config_accepts_camel_case_keys_from_onboard_json() -> None:
    """配置模型应兼容 onboard 写出的 camelCase JSON 键."""
    config = AnonConfig.model_validate(
        {
            "enabled": True,
            "allowFrom": ["group:456"],
            "superAdmins": ["42"],
            "wsUrl": "ws://127.0.0.1:3001",
            "accessToken": "secret",
            "maxContextMessages": 40,
            "maxCtxLength": 512,
            "mediaMaxSizeMb": 64,
        }
    )

    assert config.allow_from == ["group:456"]
    assert config.super_admins == ["42"]
    assert config.ws_url == "ws://127.0.0.1:3001"
    assert config.access_token == "secret"
    assert config.max_context_messages == 40
    assert config.max_ctx_length == 512
    assert config.media_max_size_mb == 64
