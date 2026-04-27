"""Platform-agnostic domain models for the anon channel."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


class ConversationRef(BaseModel):
    """标准化会话引用."""

    kind: Literal["private", "group"] = Field(description="会话类型。")
    id: str = Field(description="平台无关的会话标识。")
    title: str = Field(default="", description="可选的人类可读标题。")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        """返回稳定的会话键."""
        return f"{self.kind}:{self.id}"


class Attachment(BaseModel):
    """标准化附件引用."""

    kind: str = Field(description="附件类型, 例如 image。")
    url: str = Field(default="", description="可直接引用的地址。")
    name: str = Field(default="", description="可选文件名。")
    mime_type: str = Field(default="", description="可选 MIME 类型。")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="补充元数据。",
    )


class ForwardNode(BaseModel):
    """转发节点的最小展开表示."""

    sender_id: str = Field(default="", description="节点发送者 ID。")
    sender_name: str = Field(default="", description="节点发送者显示名。")
    message_id: str | None = Field(default=None, description="节点消息 ID。")
    reply_to_message_id: str | None = Field(
        default=None,
        description="节点回复目标消息 ID。",
    )
    content: str = Field(default="", description="节点正文的稳定文本。")
    attachments: list[Attachment] = Field(
        default_factory=list,
        description="节点内按顺序出现的附件。",
    )


class ForwardRef(BaseModel):
    """入站转发段的最小引用信息."""

    forward_id: str | None = Field(default=None, description="转发 ID。")
    summary: str = Field(default="", description="转发摘要。")
    nodes: list[ForwardNode] = Field(
        default_factory=list,
        description="段内直接携带的嵌入节点。",
    )


class ForwardExpanded(BaseModel):
    """Presenter 使用的展开转发内容."""

    forward_id: str | None = Field(default=None, description="转发 ID。")
    summary: str = Field(default="", description="转发摘要。")
    nodes: list[ForwardNode] = Field(
        default_factory=list,
        description="可直接渲染的转发节点。",
    )


class NormalizedMessage(BaseModel):
    """平台无关的标准化消息."""

    message_id: str = Field(description="消息唯一标识。")
    conversation: ConversationRef = Field(description="所属会话。")
    sender_id: str = Field(description="发送者标识。")
    sender_name: str = Field(description="发送者显示名。")
    content: str = Field(default="", description="标准化纯文本内容。")
    from_self: bool = Field(default=False, description="是否由机器人自身发送。")
    message_type: str = Field(
        default="message",
        description="消息类别, 如 message / poke。",
    )
    mentioned_self: bool = Field(default=False, description="是否明确提及机器人。")
    reply_to_self: bool = Field(default=False, description="是否回复机器人上一条消息。")
    reply_to_message_id: str | None = Field(
        default=None,
        description="被回复消息 ID。",
    )
    attachments: list[Attachment] = Field(
        default_factory=list,
        description="附件列表。",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="平台相关补充字段。",
    )


class TriggerReason(StrEnum):
    """触发决策原因."""

    NONE = "none"
    DISALLOWED = "disallowed"
    EMPTY_CONTENT = "empty_content"
    PRIVATE_PROBABILITY = "private_probability"
    GROUP_PROBABILITY = "group_probability"
    KEYWORD = "keyword"
    MENTIONED_SELF = "mentioned_self"
    REPLY_TO_SELF = "reply_to_self"
    NOT_TARGETED = "not_targeted"
    POKE_DISABLED = "poke_disabled"
    POKE = "poke"
    POKE_COOLDOWN = "poke_cooldown"


class TriggerDecision(BaseModel):
    """标准化触发决策."""

    triggered: bool = Field(description="是否触发投递。")
    reason: TriggerReason = Field(description="触发或拒绝原因。")


class SlashCommand(BaseModel):
    """标准化斜杠命令."""

    name: str = Field(description="命令名。")
    args: list[str] = Field(default_factory=list, description="命令参数。")
    admin_only: bool = Field(default=False, description="是否仅管理员可用。")


class PresentedConversation(BaseModel):
    """提供给上游消息构造阶段的上下文表示."""

    text: str = Field(description="稳定可复现的文本表示。")
    media: list[dict[str, Any]] = Field(
        default_factory=list,
        description="媒体引用列表。",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="补充结构化元数据。",
    )


class ChannelSendRequest(BaseModel):
    """标准化的出站发送请求."""

    chat_id: str = Field(description="目标会话 ID。")
    content: str = Field(default="", description="要发送的文本内容。")
    media: list[str | Attachment] = Field(
        default_factory=list,
        description="要发送的媒体引用或已分类附件。",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="附带元数据。")
