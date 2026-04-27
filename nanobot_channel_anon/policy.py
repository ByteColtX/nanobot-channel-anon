"""Platform-agnostic allowlist and trigger policy logic."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import (
    NormalizedMessage,
    TriggerDecision,
    TriggerReason,
)


@dataclass(slots=True)
class PolicyContext:
    """策略决策时的外部上下文."""

    self_id: str | None = None
    now_monotonic: float | None = None


class PolicyEngine:
    """统一处理允许访问、命令识别与触发决策."""

    _KNOWN_SLASH_COMMANDS = frozenset(
        {
            "/new",
            "/stop",
            "/restart",
            "/status",
            "/dream",
            "/dream-log",
            "/dream-restore",
            "/help",
        }
    )
    def __init__(self, config: AnonConfig) -> None:
        """初始化策略引擎."""
        self.config = config
        self._last_poke_at: dict[str, float] = {}

    def is_allowed(self, message: NormalizedMessage) -> bool:
        """判断发送者或会话是否在允许范围内."""
        if message.sender_id in self.config.super_admins:
            return True
        if not self.config.allow_from:
            return False
        if self.config.allow_all:
            return True
        return message.conversation.key in self.config.allowed_conversation_keys

    def is_super_admin(self, message: NormalizedMessage) -> bool:
        """判断发送者是否为超级管理员."""
        return message.sender_id in self.config.super_admins

    def classify_slash_command(self, message: NormalizedMessage) -> str | None:
        """按固定菜单识别允许透传的斜杠命令."""
        content = message.content.strip()
        if not content.startswith("/"):
            return None

        command = content.split(maxsplit=1)[0].lower()
        if command in self._KNOWN_SLASH_COMMANDS:
            return command
        return None

    def should_passthrough_slash_command(self, message: NormalizedMessage) -> bool:
        """判断斜杠命令是否应按管理员直通上游."""
        return (
            self.classify_slash_command(message) is not None
            and self.is_super_admin(message)
        )

    def decide_trigger(
        self,
        message: NormalizedMessage,
        *,
        context: PolicyContext | None = None,
    ) -> TriggerDecision:
        """根据标准化消息和配置给出触发决策."""
        resolved_context = context or PolicyContext()
        if message.message_type == "poke":
            return self._decide_poke(message, resolved_context)
        if message.conversation.kind == "private":
            return self._decide_private(message)
        return self._decide_group(message)

    def _decide_private(self, message: NormalizedMessage) -> TriggerDecision:
        if not message.content:
            return TriggerDecision(
                triggered=False,
                reason=TriggerReason.EMPTY_CONTENT,
            )
        if self._passes_probability(message, self.config.private_trigger_prob):
            return TriggerDecision(
                triggered=True,
                reason=TriggerReason.PRIVATE_PROBABILITY,
            )
        return TriggerDecision(triggered=False, reason=TriggerReason.NONE)

    def _decide_group(self, message: NormalizedMessage) -> TriggerDecision:
        if self._matches_keyword(message.content):
            return TriggerDecision(triggered=True, reason=TriggerReason.KEYWORD)
        if self.config.trigger_on_reply and message.reply_to_self:
            return TriggerDecision(
                triggered=True,
                reason=TriggerReason.REPLY_TO_SELF,
            )
        if self.config.trigger_on_at and message.mentioned_self:
            return TriggerDecision(
                triggered=True,
                reason=TriggerReason.MENTIONED_SELF,
            )
        if not message.content:
            return TriggerDecision(
                triggered=False,
                reason=TriggerReason.EMPTY_CONTENT,
            )
        if self._passes_probability(message, self.config.group_trigger_prob):
            return TriggerDecision(
                triggered=True,
                reason=TriggerReason.GROUP_PROBABILITY,
            )
        return TriggerDecision(triggered=False, reason=TriggerReason.NONE)

    def _decide_poke(
        self,
        message: NormalizedMessage,
        context: PolicyContext,
    ) -> TriggerDecision:
        if not self.config.trigger_on_poke:
            return TriggerDecision(
                triggered=False,
                reason=TriggerReason.POKE_DISABLED,
            )
        target_id = message.metadata.get("target_id")
        if context.self_id is None or str(target_id) != context.self_id:
            return TriggerDecision(
                triggered=False,
                reason=TriggerReason.NOT_TARGETED,
            )

        now = (
            context.now_monotonic
            if context.now_monotonic is not None
            else time.monotonic()
        )
        last_poke_at = self._last_poke_at.get(message.conversation.key)
        if (
            last_poke_at is not None
            and now - last_poke_at < self.config.poke_cooldown_seconds
        ):
            return TriggerDecision(
                triggered=False,
                reason=TriggerReason.POKE_COOLDOWN,
            )
        self._last_poke_at[message.conversation.key] = now
        return TriggerDecision(triggered=True, reason=TriggerReason.POKE)

    def _matches_keyword(self, content: str) -> bool:
        folded_content = content.casefold()
        for keyword in self.config.trigger_on_keywords:
            normalized = keyword.strip().casefold()
            if normalized and normalized in folded_content:
                return True
        return False

    @staticmethod
    def _passes_probability(message: NormalizedMessage, probability: float) -> bool:
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return PolicyEngine._sample_value(message) < probability

    @staticmethod
    def _sample_value(message: NormalizedMessage) -> float:
        seed = "\x1f".join(
            (
                message.conversation.key,
                message.sender_id,
                message.message_id,
            )
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], byteorder="big")
        return value / 2**64
