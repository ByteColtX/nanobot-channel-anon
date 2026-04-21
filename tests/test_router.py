"""Tests for OneBot inbound routing."""

import hashlib
import math
from typing import Any

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.inbound import EventKind, InboundCandidate
from nanobot_channel_anon.router import InboundRouter


def _candidate(
    *,
    event_kind: EventKind = "group_message",
    chat_id: str = "group:456",
    content: str = "hello",
    mentioned_self: bool = False,
    mentioned_all: bool = False,
    reply_to_message_id: str | None = None,
    reply_target_from_self: bool = False,
    metadata: dict[str, Any] | None = None,
) -> InboundCandidate:
    merged_metadata = {
        "event_kind": event_kind,
        "self_id": "42",
        "target_id": "42",
    }
    if metadata is not None:
        merged_metadata.update(metadata)
    return InboundCandidate(
        sender_id="123",
        chat_id=chat_id,
        content=content,
        media=[],
        metadata=merged_metadata,
        event_kind=event_kind,
        mentioned_self=mentioned_self,
        mentioned_all=mentioned_all,
        reply_to_message_id=reply_to_message_id,
        reply_target_from_self=reply_target_from_self,
        session_key=None,
    )


def test_route_private_message_uses_probability() -> None:
    """Private messages should pass when probability is 1."""
    router = InboundRouter(AnonConfig(private_trigger_prob=1.0))

    routed = router.route(
        _candidate(
            event_kind="private_message",
            chat_id="private:123",
        )
    )

    assert routed is not None
    assert routed.metadata["trigger_reason"] == "private_prob"


def test_route_group_message_prefers_keyword() -> None:
    """Configured keywords should trigger group delivery."""
    router = InboundRouter(
        AnonConfig(group_trigger_prob=0.0, trigger_on_keywords=["bot"])
    )

    routed = router.route(_candidate(content="hello bot"))

    assert routed is not None
    assert routed.metadata["trigger_reason"] == "keyword"


def test_route_group_message_uses_reply_trigger() -> None:
    """Reply trigger should require a buffered self-message match."""
    router = InboundRouter(AnonConfig(group_trigger_prob=0.0, trigger_on_reply=True))

    routed = router.route(
        _candidate(
            reply_to_message_id="9",
            reply_target_from_self=True,
        )
    )

    assert routed is not None
    assert routed.metadata["trigger_reason"] == "reply"


def test_route_group_message_does_not_trigger_on_non_self_reply() -> None:
    """Replys to non-bot messages should not trigger reply routing."""
    router = InboundRouter(AnonConfig(group_trigger_prob=0.0, trigger_on_reply=True))

    routed = router.route(
        _candidate(
            reply_to_message_id="9",
            reply_target_from_self=False,
        )
    )

    assert routed is None


def test_route_group_message_drops_without_trigger() -> None:
    """Group messages should be dropped when no trigger matches."""
    router = InboundRouter(AnonConfig(group_trigger_prob=0.0))

    routed = router.route(_candidate(content="plain text"))

    assert routed is None


def test_route_probability_sampling_is_stable() -> None:
    """Stable sampling should make repeated routing decisions deterministic."""
    candidate = _candidate(
        event_kind="private_message",
        chat_id="private:123",
        content="stable sample",
        metadata={"message_id": "1001"},
    )
    probability = math.nextafter(InboundRouter._sample_value(candidate), 1.0)
    router = InboundRouter(AnonConfig(private_trigger_prob=probability))

    first = router.route(candidate)
    second = router.route(
        _candidate(
            event_kind="private_message",
            chat_id="private:123",
            content="stable sample",
            metadata={"message_id": "1001"},
        )
    )

    assert first is not None
    assert second is not None
    assert first.metadata["trigger_reason"] == "private_prob"
    assert second.metadata["trigger_reason"] == "private_prob"


def test_sample_value_uses_chat_sender_and_message_id() -> None:
    """Stable sampling should hash chat_id, sender_id, and message_id only."""
    candidate = _candidate(
        event_kind="group_message",
        chat_id="group:456",
        content="hello",
        metadata={"message_id": "2002"},
    )

    expected_seed = "\x1f".join(("group:456", "123", "2002"))
    expected_digest = hashlib.sha256(expected_seed.encode("utf-8")).digest()
    expected_value = int.from_bytes(expected_digest[:8], byteorder="big") / 2**64

    assert InboundRouter._sample_value(candidate) == expected_value


def test_route_poke_applies_cooldown() -> None:
    """Repeated poke events should respect cooldown."""
    router = InboundRouter(
        AnonConfig(trigger_on_poke=True, poke_cooldown_seconds=60)
    )
    candidate = _candidate(
        event_kind="poke",
        content="戳了戳你",
        metadata={"self_id": "42", "target_id": "42"},
    )

    first = router.route(candidate)
    second = router.route(candidate)

    assert first is not None
    assert first.metadata["trigger_reason"] == "poke"
    assert second is None
