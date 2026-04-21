"""OneBot v11 入站触发路由."""

from __future__ import annotations

import hashlib
import time

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import InboundCandidate


class InboundRouter:
    """根据配置决定候选入站事件是否应触发 nanobot."""

    def __init__(self, config: AnonConfig) -> None:
        """初始化路由器状态."""
        self.config = config
        self._last_poke_at: dict[str, float] = {}

    def route(self, candidate: InboundCandidate) -> InboundCandidate | None:
        """返回应投递的事件; 若不触发则返回 ``None``."""
        if candidate.event_kind == "private_message":
            return self._route_private(candidate)
        if candidate.event_kind == "group_message":
            return self._route_group(candidate)
        if candidate.event_kind == "poke":
            return self._route_poke(candidate)
        return None

    def _route_private(self, candidate: InboundCandidate) -> InboundCandidate | None:
        if not candidate.content:
            return None
        if not self._passes_probability(candidate, self.config.private_trigger_prob):
            return None
        return self._with_trigger_reason(candidate, "private_prob")

    def _route_group(self, candidate: InboundCandidate) -> InboundCandidate | None:
        if self._matches_keyword(candidate.content):
            return self._with_trigger_reason(candidate, "keyword")
        if self.config.trigger_on_at and candidate.mentioned_self:
            return self._with_trigger_reason(candidate, "at")
        if self.config.trigger_on_reply and candidate.reply_target_from_self:
            return self._with_trigger_reason(candidate, "reply")
        if not candidate.content:
            return None
        if not self._passes_probability(candidate, self.config.group_trigger_prob):
            return None
        return self._with_trigger_reason(candidate, "group_prob")

    def _route_poke(self, candidate: InboundCandidate) -> InboundCandidate | None:
        if not self.config.trigger_on_poke:
            return None

        target_id = candidate.metadata.get("target_id")
        self_id = candidate.metadata.get("self_id")
        if target_id is None or self_id is None or str(target_id) != str(self_id):
            return None

        now = time.monotonic()
        last_poke_at = self._last_poke_at.get(candidate.chat_id)
        if (
            last_poke_at is not None
            and now - last_poke_at < self.config.poke_cooldown_seconds
        ):
            return None

        self._last_poke_at[candidate.chat_id] = now
        return self._with_trigger_reason(candidate, "poke")

    def _matches_keyword(self, content: str) -> bool:
        for keyword in self.config.trigger_on_keywords:
            normalized = keyword.strip()
            if normalized and normalized in content:
                return True
        return False

    @staticmethod
    def _passes_probability(candidate: InboundCandidate, probability: float) -> bool:
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return InboundRouter._sample_value(candidate) < probability

    @staticmethod
    def _sample_value(candidate: InboundCandidate) -> float:
        seed = "\x1f".join(
            (
                candidate.chat_id,
                candidate.sender_id,
                str(candidate.metadata.get("message_id") or ""),
            )
        )
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], byteorder="big")
        return value / 2**64

    @staticmethod
    def _with_trigger_reason(
        candidate: InboundCandidate,
        reason: str,
    ) -> InboundCandidate:
        candidate.metadata["trigger_reason"] = reason
        return candidate
