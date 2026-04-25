"""In-memory OneBot-side state adapter."""

from __future__ import annotations

import time
from dataclasses import dataclass

from nanobot_channel_anon.domain import ConversationRef


@dataclass(slots=True)
class MemberProfile:
    """OneBot 成员资料快照."""

    user_id: str
    display_name: str = ""
    nickname: str = ""


@dataclass(slots=True)
class BotProfile:
    """OneBot 机器人自身资料快照."""

    user_id: str
    nickname: str = ""


class OneBotStateAdapter:
    """维护 OneBot 侧最小运行时状态."""

    def __init__(self) -> None:
        """初始化内存状态容器."""
        self._self_profile: BotProfile | None = None
        self._profiles: dict[str, dict[str, MemberProfile]] = {}
        self._muted_groups: dict[str, int] = {}

    @property
    def self_id(self) -> str | None:
        """返回当前机器人自身 ID."""
        if self._self_profile is None:
            return None
        return self._self_profile.user_id

    @property
    def self_nickname(self) -> str:
        """返回当前机器人昵称."""
        if self._self_profile is None:
            return ""
        return self._self_profile.nickname

    def set_self_id(self, self_id: str | None) -> None:
        """更新机器人自身 ID."""
        if self_id is None:
            self._self_profile = None
            return
        nickname = self.self_nickname
        self._self_profile = BotProfile(user_id=self_id, nickname=nickname)

    def set_self_profile(self, *, user_id: str, nickname: str = "") -> None:
        """更新机器人自身资料快照."""
        self._self_profile = BotProfile(user_id=user_id, nickname=nickname)

    def set_member_profile(
        self,
        conversation: ConversationRef,
        *,
        user_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """写入某个会话内成员的资料快照."""
        bucket = self._profiles.setdefault(conversation.key, {})
        bucket[user_id] = MemberProfile(
            user_id=user_id,
            display_name=display_name,
            nickname=nickname,
        )

    def get_member_profile(
        self,
        conversation: ConversationRef,
        user_id: str,
    ) -> MemberProfile | None:
        """读取某个会话内成员资料."""
        return self._profiles.get(conversation.key, {}).get(user_id)

    def set_group_muted_until(self, group_id: str, muted_until: int | None) -> None:
        """更新指定群的禁言截止时间."""
        if muted_until is None:
            self._muted_groups.pop(group_id, None)
            return
        self._muted_groups[group_id] = muted_until

    def clear_group_mute(self, group_id: str) -> None:
        """清除指定群的禁言状态."""
        self._muted_groups.pop(group_id, None)

    def is_group_muted(self, group_id: str) -> bool:
        """返回当前群是否仍处于禁言状态."""
        muted_until = self._muted_groups.get(group_id)
        if muted_until is None:
            return False
        if muted_until <= int(time.time()):
            self._muted_groups.pop(group_id, None)
            return False
        return True

    def muted_group_ids(self) -> frozenset[str]:
        """返回当前仍处于禁言状态的群集合."""
        now = int(time.time())
        expired = [
            group_id
            for group_id, muted_until in self._muted_groups.items()
            if muted_until <= now
        ]
        for group_id in expired:
            self._muted_groups.pop(group_id, None)
        return frozenset(self._muted_groups)
