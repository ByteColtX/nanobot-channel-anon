"""OneBot v11 协议归一化."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OneBotModel(BaseModel):
    """OneBot 协议对象基类."""

    model_config = ConfigDict(extra="allow")


class BotStatus(OneBotModel):
    """机器人状态响应."""

    online: bool = False
    good: bool = False


class OneBotSender(OneBotModel):
    """消息发送者信息."""

    user_id: int | str | None = None
    nickname: str = ""
    card: str = ""


class OneBotMessageSegment(OneBotModel):
    """OneBot 消息段."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class OneBotAPIRequest(OneBotModel):
    """OneBot 动作请求."""

    action: str
    params: Any = None
    echo: str | None = None


class OneBotRawEvent(OneBotModel):
    """OneBot 原始事件或响应载荷."""

    post_type: str = ""
    message_type: str = ""
    sub_type: str = ""
    message_id: int | str | None = None
    user_id: int | str | None = None
    group_id: int | str | None = None
    raw_message: str = ""
    message: str | list[OneBotMessageSegment] | None = None
    sender: OneBotSender | None = None
    self_id: int | str | None = None
    target_id: int | str | None = None
    time: int | float | str | None = None
    meta_event_type: str = ""
    notice_type: str = ""
    echo: str = ""
    retcode: int | str | None = None
    status: str | BotStatus | None = None
    data: Any = None
