"""In-memory context store for normalized conversations."""

from __future__ import annotations

from collections import OrderedDict

from nanobot_channel_anon.domain import ConversationRef, NormalizedMessage


class ContextStore:
    """按会话保存最近消息与消费游标."""

    def __init__(self, max_messages_per_conversation: int) -> None:
        """初始化上下文存储."""
        self._max_messages = max_messages_per_conversation
        self._messages: dict[str, OrderedDict[str, NormalizedMessage]] = {}
        self._consumed_through: dict[str, str] = {}

    def append(self, message: NormalizedMessage) -> None:
        """向对应会话追加一条消息."""
        conversation_key = message.conversation.key
        messages = self._messages.setdefault(conversation_key, OrderedDict())
        if message.message_id in messages:
            del messages[message.message_id]
        messages[message.message_id] = message
        while len(messages) > self._max_messages:
            messages.popitem(last=False)

    def recent_window(
        self,
        conversation: ConversationRef,
        *,
        limit: int | None = None,
    ) -> list[NormalizedMessage]:
        """返回最近消息窗口并保持插入顺序."""
        messages = list(self._messages.get(conversation.key, {}).values())
        if limit is None or limit >= len(messages):
            return messages
        return messages[-limit:]

    def get_message(
        self,
        conversation: ConversationRef,
        message_id: str,
    ) -> NormalizedMessage | None:
        """按会话和消息 ID 查找消息."""
        return self._messages.get(conversation.key, {}).get(message_id)

    def mark_consumed(self, conversation: ConversationRef, message_id: str) -> None:
        """显式记录已消费到的消息 ID."""
        if self.get_message(conversation, message_id) is None:
            raise KeyError(f"unknown message_id: {message_id}")
        self._consumed_through[conversation.key] = message_id

    def consumed_through(self, conversation: ConversationRef) -> str | None:
        """返回当前会话已消费到的消息 ID."""
        return self._consumed_through.get(conversation.key)

    def unconsumed_window(
        self,
        conversation: ConversationRef,
        *,
        limit: int | None = None,
    ) -> list[NormalizedMessage]:
        """返回消费游标之后的消息窗口."""
        messages = self.recent_window(conversation)
        consumed_id = self._consumed_through.get(conversation.key)
        if consumed_id is None:
            window = messages
        else:
            consumed_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.message_id == consumed_id
                ),
                None,
            )
            if consumed_index is None:
                window = messages
            else:
                window = messages[consumed_index + 1 :]
        if limit is None or limit >= len(window):
            return window
        return window[-limit:]
