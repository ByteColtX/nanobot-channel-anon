"""Tests for MCP request models."""

from __future__ import annotations

import pytest

from nanobot_channel_anon.mcp.models import (
    DeleteMsgRequest,
    SendLikeRequest,
    SendPokeRequest,
    SetFriendAddRequestRequest,
    SetGroupAddRequestRequest,
    ToolInputError,
)


def test_send_poke_request_accepts_digits_user_id() -> None:
    """Digit-only user ids should be preserved."""
    request = SendPokeRequest.from_tool_input({"user_id": "123456"})
    assert request.user_id == "123456"


@pytest.mark.parametrize("value", [123456, " 123456 "])
def test_send_poke_request_accepts_numeric_user_id_scalars(value: object) -> None:
    """Numeric user ids should normalize to trimmed digit strings."""
    request = SendPokeRequest.from_tool_input({"user_id": value})
    assert request.user_id == "123456"


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_send_poke_request_rejects_invalid_user_id(value: object) -> None:
    """Invalid user ids should raise a tool input error."""
    with pytest.raises(ToolInputError):
        SendPokeRequest.from_tool_input({"user_id": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [(123, "123"), ("123", "123"), (" group:123 ", "123")],
)
def test_send_poke_request_accepts_supported_group_id_formats(
    value: object,
    expected: str,
) -> None:
    """Group ids should support raw and group-prefixed digits."""
    request = SendPokeRequest.from_tool_input({"user_id": "123", "group_id": value})
    assert request.group_id == expected


def test_send_poke_request_allows_none_group_id() -> None:
    """None means private poke."""
    request = SendPokeRequest.from_tool_input({"user_id": "123", "group_id": None})
    assert request.group_id is None


@pytest.mark.parametrize("value", ["", "group:", "private:123", "abc", True])
def test_send_poke_request_rejects_invalid_group_id(value: object) -> None:
    """Invalid group ids should raise a tool input error."""
    with pytest.raises(ToolInputError):
        SendPokeRequest.from_tool_input({"user_id": "123", "group_id": value})


@pytest.mark.parametrize(("value", "expected"), [(1, 1), ("10", 10), (" 3 ", 3)])
def test_send_like_request_accepts_positive_integer_times(
    value: object,
    expected: int,
) -> None:
    """Like counts should normalize to positive integers."""
    request = SendLikeRequest.from_tool_input({"user_id": "123", "times": value})
    assert request.times == expected


@pytest.mark.parametrize("value", [0, -1, "", "abc", "-1", True, None])
def test_send_like_request_rejects_invalid_times(value: object) -> None:
    """Invalid like counts should raise a tool input error."""
    with pytest.raises(ToolInputError):
        SendLikeRequest.from_tool_input({"user_id": "123", "times": value})


@pytest.mark.parametrize(
    ("value", "expected"),
    [(123456, "123456"), ("123456", "123456"), (" 123456 ", "123456")],
)
def test_delete_msg_request_accepts_numeric_message_id(
    value: object,
    expected: str,
) -> None:
    """Message ids should normalize to trimmed digit strings."""
    request = DeleteMsgRequest.from_tool_input({"message_id": value})
    assert request.message_id == expected


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_delete_msg_request_rejects_invalid_message_id(value: object) -> None:
    """Invalid message ids should raise a tool input error."""
    with pytest.raises(ToolInputError):
        DeleteMsgRequest.from_tool_input({"message_id": value})


def test_set_group_add_request_accepts_valid_fields() -> None:
    """Group add request inputs should normalize as expected."""
    request = SetGroupAddRequestRequest.from_tool_input(
        {
            "flag": " flag_123 ",
            "sub_type": " Invite ",
            "approve": False,
            "reason": " 拒绝 ",
        }
    )
    assert request.flag == "flag_123"
    assert request.sub_type == "invite"
    assert request.approve is False
    assert request.reason == "拒绝"


def test_set_group_add_request_normalizes_blank_reason_to_none() -> None:
    """Blank rejection reasons should normalize to none."""
    request = SetGroupAddRequestRequest.from_tool_input(
        {
            "flag": "flag_123",
            "sub_type": "add",
            "approve": True,
            "reason": None,
        }
    )
    assert request.reason is None


@pytest.mark.parametrize("value", ["", "   ", True, None])
def test_set_group_add_request_rejects_invalid_flag(value: object) -> None:
    """Group add request flag must be a non-empty string."""
    with pytest.raises(ToolInputError):
        SetGroupAddRequestRequest.from_tool_input(
            {"flag": value, "sub_type": "add", "approve": True}
        )


@pytest.mark.parametrize("value", ["", "join", True, None])
def test_set_group_add_request_rejects_invalid_sub_type(value: object) -> None:
    """Group add request subtype must be add or invite."""
    with pytest.raises(ToolInputError):
        SetGroupAddRequestRequest.from_tool_input(
            {"flag": "flag_123", "sub_type": value, "approve": True}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_group_add_request_rejects_non_boolean_approve(value: object) -> None:
    """Group add request approve must be boolean."""
    with pytest.raises(ToolInputError):
        SetGroupAddRequestRequest.from_tool_input(
            {"flag": "flag_123", "sub_type": "add", "approve": value}
        )


def test_set_friend_add_request_accepts_valid_fields() -> None:
    """Friend add request inputs should normalize as expected."""
    request = SetFriendAddRequestRequest.from_tool_input(
        {"flag": " flag_123 ", "approve": True, "remark": " 好友备注 "}
    )
    assert request.flag == "flag_123"
    assert request.approve is True
    assert request.remark == "好友备注"


@pytest.mark.parametrize("value", ["", "   ", True, None])
def test_set_friend_add_request_rejects_invalid_flag(value: object) -> None:
    """Friend add request flag must be a non-empty string."""
    with pytest.raises(ToolInputError):
        SetFriendAddRequestRequest.from_tool_input(
            {"flag": value, "approve": True, "remark": "ok"}
        )


@pytest.mark.parametrize("value", ["", "   ", True, None])
def test_set_friend_add_request_rejects_invalid_remark(value: object) -> None:
    """Friend add request remark must be a non-empty string."""
    with pytest.raises(ToolInputError):
        SetFriendAddRequestRequest.from_tool_input(
            {"flag": "flag_123", "approve": True, "remark": value}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_friend_add_request_rejects_non_boolean_approve(value: object) -> None:
    """Friend add request approve must be boolean."""
    with pytest.raises(ToolInputError):
        SetFriendAddRequestRequest.from_tool_input(
            {"flag": "flag_123", "approve": value, "remark": "ok"}
        )
