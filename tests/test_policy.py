"""Tests for platform-agnostic inbound policy decisions."""

from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import (
    ConversationRef,
    NormalizedMessage,
    TriggerDecision,
    TriggerReason,
)
from nanobot_channel_anon.policy import PolicyContext, PolicyEngine

PRIVATE_CONVERSATION = ConversationRef(kind="private", id="123")
GROUP_CONVERSATION = ConversationRef(kind="group", id="456")


def _message(
    *,
    conversation: ConversationRef = GROUP_CONVERSATION,
    sender_id: str = "123",
    content: str = "hello",
    message_type: str = "message",
    mentioned_self: bool = False,
    reply_to_self: bool = False,
    metadata: dict[str, str] | None = None,
) -> NormalizedMessage:
    return NormalizedMessage(
        message_id="m1",
        conversation=conversation,
        sender_id=sender_id,
        sender_name="Alice",
        content=content,
        message_type=message_type,
        mentioned_self=mentioned_self,
        reply_to_self=reply_to_self,
        metadata={} if metadata is None else dict(metadata),
    )


def test_allow_from_normalization_preserves_raw_ids_and_conversation_keys() -> None:
    """allow_from 应保留标准会话键并标准化原始 ID."""
    config = AnonConfig(allow_from=[" 123 ", " group:456 ", "456", "private:789"])

    assert config.allow_from == ["123", "group:456", "456", "private:789"]


def test_is_allowed_matches_raw_sender_id_entry() -> None:
    """Allow checks should match raw sender IDs deterministically."""
    policy = PolicyEngine(AnonConfig(allow_from=["123"]))

    assert policy.is_allowed(_message(sender_id="123")) is True
    assert policy.is_allowed(_message(sender_id="999")) is False


def test_is_allowed_matches_raw_group_id_entry() -> None:
    """Allow checks should match raw group IDs against conversation IDs."""
    policy = PolicyEngine(AnonConfig(allow_from=["456"]))

    assert policy.is_allowed(
        _message(sender_id="999", conversation=GROUP_CONVERSATION)
    ) is True
    assert policy.is_allowed(
        _message(
            sender_id="999",
            conversation=ConversationRef(kind="group", id="999"),
        )
    ) is False



def test_is_allowed_matches_canonical_conversation_key_entry() -> None:
    """Allow checks should match canonical conversation keys explicitly."""
    policy = PolicyEngine(AnonConfig(allow_from=["group:456"]))

    assert policy.is_allowed(
        _message(sender_id="999", conversation=GROUP_CONVERSATION)
    ) is True
    assert policy.is_allowed(
        _message(
            sender_id="456",
            conversation=ConversationRef(kind="private", id="456"),
        )
    ) is False


def test_is_allowed_honors_super_admins_even_when_allowlist_rejects() -> None:
    """Super admins should bypass the normal allowlist."""
    policy = PolicyEngine(AnonConfig(allow_from=["group:456"], super_admins=["999"]))
    other_conversation = ConversationRef(kind="group", id="777")

    assert policy.is_allowed(
        _message(sender_id="999", conversation=other_conversation)
    ) is True


def test_parse_command_recognizes_normal_and_admin_slash_commands() -> None:
    """Slash parsing should distinguish normal and admin-only commands."""
    policy = PolicyEngine(AnonConfig(super_admins=["999"]))

    normal = policy.parse_command(_message(content="/help"))
    admin = policy.parse_command(_message(sender_id="999", content="/admin reload"))

    assert normal is not None
    assert normal.name == "help"
    assert normal.admin_only is False
    assert admin is not None
    assert admin.name == "reload"
    assert admin.admin_only is True


def test_parse_command_rejects_admin_command_from_non_admin() -> None:
    """Admin commands should not parse for non-admin senders."""
    policy = PolicyEngine(AnonConfig(super_admins=["999"]))

    command = policy.parse_command(
        _message(sender_id="123", content="/admin reload")
    )

    assert command is None


def test_decide_trigger_private_message_uses_probability_reason() -> None:
    """Private messages should trigger with the private probability rule."""
    policy = PolicyEngine(AnonConfig(private_trigger_prob=1.0))

    decision = policy.decide_trigger(_message(conversation=PRIVATE_CONVERSATION))

    assert decision == TriggerDecision(
        triggered=True,
        reason=TriggerReason.PRIVATE_PROBABILITY,
    )


def test_decide_trigger_group_message_prefers_keyword_then_reply_then_at() -> None:
    """Group trigger priorities should be deterministic."""
    policy = PolicyEngine(
        AnonConfig(
            group_trigger_prob=0.0,
            trigger_on_keywords=["bot"],
            trigger_on_reply=True,
            trigger_on_at=True,
        )
    )

    keyword = policy.decide_trigger(
        _message(content="hello bot", mentioned_self=True, reply_to_self=True)
    )
    reply = policy.decide_trigger(
        _message(content="hello", reply_to_self=True, mentioned_self=True)
    )
    at = policy.decide_trigger(_message(content="hello", mentioned_self=True))

    assert keyword.reason == TriggerReason.KEYWORD
    assert reply.reason == TriggerReason.REPLY_TO_SELF
    assert at.reason == TriggerReason.MENTIONED_SELF


def test_decide_trigger_group_message_can_use_probability() -> None:
    """Group messages should fall back to deterministic probability sampling."""
    policy = PolicyEngine(AnonConfig(group_trigger_prob=1.0))

    decision = policy.decide_trigger(_message(content="plain text"))

    assert decision == TriggerDecision(
        triggered=True,
        reason=TriggerReason.GROUP_PROBABILITY,
    )


def test_decide_trigger_poke_respects_target_and_cooldown() -> None:
    """Poke triggers should require a poke at self and enforce cooldown."""
    policy = PolicyEngine(AnonConfig(trigger_on_poke=True, poke_cooldown_seconds=60))
    context = PolicyContext(self_id="42", now_monotonic=100.0)
    message = _message(
        message_type="poke",
        content="",
        metadata={"target_id": "42"},
    )

    first = policy.decide_trigger(message, context=context)
    second = policy.decide_trigger(
        message,
        context=PolicyContext(self_id="42", now_monotonic=120.0),
    )

    assert first == TriggerDecision(triggered=True, reason=TriggerReason.POKE)
    assert second == TriggerDecision(
        triggered=False,
        reason=TriggerReason.POKE_COOLDOWN,
    )


def test_decide_trigger_poke_rejects_when_target_is_not_self() -> None:
    """Poke events should not trigger when they target someone else."""
    policy = PolicyEngine(AnonConfig(trigger_on_poke=True))
    message = _message(
        message_type="poke",
        content="",
        metadata={"target_id": "99"},
    )

    decision = policy.decide_trigger(
        message,
        context=PolicyContext(self_id="42", now_monotonic=10.0),
    )

    assert decision == TriggerDecision(
        triggered=False,
        reason=TriggerReason.NOT_TARGETED,
    )
