"""In-memory message buffer for recent chat history."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ForwardNodeEntry:
    """A normalized node inside a forwarded message."""

    sender_id: str | None
    sender_name: str
    source_chat_id: str | None
    content: str
    sender_nickname: str = ""
    sender_card: str = ""
    message_id: str | None = None
    media: list[str] = field(default_factory=list)
    reply_to_message_id: str | None = None
    segment_types: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ForwardEntry:
    """A normalized forwarded-message container."""

    forward_id: str | None
    summary: str | None
    nodes: list[ForwardNodeEntry] = field(default_factory=list)
    unresolved: bool = False


@dataclass(slots=True)
class MessageEntry:
    """A normalized recent message stored in the per-chat buffer."""

    message_id: str
    chat_id: str
    sender_id: str
    sender_name: str
    is_from_self: bool
    content: str
    sender_nickname: str = ""
    sender_card: str = ""
    media: list[str] = field(default_factory=list)
    reply_to_message_id: str | None = None
    event_time: int | float | str | None = None
    segment_types: list[str] = field(default_factory=list)
    forward_refs: list[dict[str, Any]] = field(default_factory=list)
    expanded_forwards: list[ForwardEntry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class Buffer:
    """Store recent messages per chat with FIFO eviction."""

    def __init__(self, max_messages: int) -> None:
        """Initialize the buffer with a per-chat capacity."""
        self._max_messages = max_messages
        self._messages: dict[str, OrderedDict[str, MessageEntry]] = {}
        self._consumed_counts: dict[str, int] = {}

    def add(self, entry: MessageEntry) -> None:
        """Add or replace a message entry for its chat."""
        chat_entries = self._messages.setdefault(entry.chat_id, OrderedDict())
        consumed_count = self._consumed_counts.get(entry.chat_id, 0)
        if entry.message_id in chat_entries:
            existing_index = list(chat_entries).index(entry.message_id)
            if existing_index < consumed_count:
                consumed_count -= 1
            del chat_entries[entry.message_id]
        chat_entries[entry.message_id] = entry
        while len(chat_entries) > self._max_messages:
            chat_entries.popitem(last=False)
            if consumed_count > 0:
                consumed_count -= 1
        self._consumed_counts[entry.chat_id] = consumed_count

    def get(self, chat_id: str, message_id: str | None) -> MessageEntry | None:
        """Return a buffered message by chat and message ID."""
        if message_id is None:
            return None
        chat_entries = self._messages.get(chat_id)
        if chat_entries is None:
            return None
        return chat_entries.get(message_id)

    def is_reply_to_self(self, chat_id: str, message_id: str | None) -> bool:
        """Return whether a reply target points to a buffered self message."""
        target = self.get(chat_id, message_id)
        return target is not None and target.is_from_self

    def get_chat_entries(self, chat_id: str) -> list[MessageEntry]:
        """Return buffered messages for a chat in insertion order."""
        chat_entries = self._messages.get(chat_id)
        if chat_entries is None:
            return []
        return list(chat_entries.values())

    def get_unconsumed_chat_entries(self, chat_id: str) -> list[MessageEntry]:
        """Return unread buffered messages for a chat in insertion order."""
        entries = self.get_chat_entries(chat_id)
        consumed_count = min(self._consumed_counts.get(chat_id, 0), len(entries))
        return entries[consumed_count:]

    def mark_chat_entries_consumed(
        self,
        chat_id: str,
        message_ids: Sequence[str],
    ) -> bool:
        """Advance the consumed cursor when IDs match the unread prefix."""
        if not message_ids:
            return True
        unread_entries = self.get_unconsumed_chat_entries(chat_id)
        unread_ids = [entry.message_id for entry in unread_entries[: len(message_ids)]]
        target_ids = list(message_ids)
        if unread_ids != target_ids:
            return False
        self._consumed_counts[chat_id] = min(
            self._consumed_counts.get(chat_id, 0) + len(target_ids),
            len(self.get_chat_entries(chat_id)),
        )
        return True
