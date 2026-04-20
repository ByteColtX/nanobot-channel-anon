"""In-memory message buffer for recent chat history."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ForwardNodeEntry:
    """A normalized node inside a forwarded message."""

    sender_id: str | None
    sender_name: str
    source_chat_id: str | None
    content: str
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

    def add(self, entry: MessageEntry) -> None:
        """Add or replace a message entry for its chat."""
        chat_entries = self._messages.setdefault(entry.chat_id, OrderedDict())
        if entry.message_id in chat_entries:
            del chat_entries[entry.message_id]
        chat_entries[entry.message_id] = entry
        while len(chat_entries) > self._max_messages:
            chat_entries.popitem(last=False)

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
