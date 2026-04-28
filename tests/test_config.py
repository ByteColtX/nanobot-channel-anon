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
        match="enabled channel requires a non-empty allow_from",
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


def test_allow_from_normalizes_only_canonical_conversation_keys() -> None:
    """allow_from 只应接受显式会话键并去重标准化."""
    config = AnonConfig.model_validate(
        {
            "allow_from": [" group:456 ", "private:789", "group:456", "*"],
            "super_admins": [" 42 ", 7, True, "42"],
        }
    )

    assert config.allow_from == ["group:456", "private:789", "*"]
    assert config.allowed_conversation_keys == frozenset({"group:456", "private:789"})
    assert config.super_admins == ["42", "7"]


def test_allow_from_accepts_scoped_wildcards_without_treating_them_as_explicit_keys(
) -> None:
    """Scoped wildcard 应保留在 allow_from 中, 但不算作显式会话键."""
    config = AnonConfig.model_validate(
        {
            "allow_from": [
                " group:* ",
                "private:*",
                "group:456",
                "private:789",
                "group:*",
            ]
        }
    )

    assert config.allow_from == ["group:*", "private:*", "group:456", "private:789"]
    assert config.allowed_conversation_keys == frozenset({"group:456", "private:789"})
    assert config.allow_all_groups is True
    assert config.allow_all_privates is True


def test_allow_from_rejects_bare_ids() -> None:
    """裸 ID 不再是合法 allow_from 配置."""
    with pytest.raises(
        ValidationError,
        match=(
            r"allow_from entries must use \*, group:<id>, private:<id>, "
            r"group:\*, or private:\*"
        ),
    ):
        AnonConfig.model_validate({"allow_from": ["123"]})


@pytest.mark.parametrize("entry", ["foo:*", "group:", "private:"])
def test_allow_from_rejects_invalid_scoped_wildcards(entry: str) -> None:
    """非法 scoped wildcard 写法应继续被拒绝."""
    with pytest.raises(
        ValidationError,
        match=(
            r"allow_from entries must use \*, group:<id>, private:<id>, "
            r"group:\*, or private:\*"
        ),
    ):
        AnonConfig.model_validate({"allow_from": [entry]})


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
