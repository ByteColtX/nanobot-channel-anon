"""Tests for MCP request models."""

from __future__ import annotations

import pytest

from nanobot_channel_anon.mcp.models import (
    DeleteFriendRequest,
    DeleteMsgRequest,
    GetFriendListRequest,
    GetGroupListRequest,
    GetGroupMemberListRequest,
    SendLikeRequest,
    SendPokeRequest,
    SetFriendAddRequestRequest,
    SetGroupAddRequestRequest,
    SetGroupBanRequest,
    SetGroupCardRequest,
    SetGroupKickRequest,
    SetGroupLeaveRequest,
    SetGroupWholeBanRequest,
    SetMsgEmojiLikeRequest,
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
def test_get_group_member_list_request_accepts_numeric_group_id(
    value: object,
    expected: str,
) -> None:
    """Group ids should normalize to trimmed digit strings."""
    request = GetGroupMemberListRequest.from_tool_input({"group_id": value})
    assert request.group_id == expected
    assert request.no_cache is False


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_get_group_member_list_request_rejects_invalid_group_id(value: object) -> None:
    """Invalid group ids should raise a tool input error."""
    with pytest.raises(ToolInputError):
        GetGroupMemberListRequest.from_tool_input({"group_id": value})


@pytest.mark.parametrize("value", [True, False])
def test_get_group_member_list_request_accepts_boolean_no_cache(value: bool) -> None:
    """no_cache should require and preserve boolean values."""
    request = GetGroupMemberListRequest.from_tool_input(
        {"group_id": "123", "no_cache": value}
    )
    assert request.no_cache is value


@pytest.mark.parametrize("value", [1, "true", None])
def test_get_group_member_list_request_rejects_non_boolean_no_cache(
    value: object,
) -> None:
    """Non-boolean no_cache values should raise a tool input error."""
    with pytest.raises(ToolInputError):
        GetGroupMemberListRequest.from_tool_input(
            {"group_id": "123", "no_cache": value}
        )


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


@pytest.mark.parametrize(
    ("group_id", "user_id", "duration", "expected_duration"),
    [
        (123456, 654321, 0, 0),
        ("123456", "654321", " 60 ", 60),
    ],
)
def test_set_group_ban_request_accepts_valid_fields(
    group_id: object,
    user_id: object,
    duration: object,
    expected_duration: int,
) -> None:
    """Group ban inputs should normalize ids and duration."""
    request = SetGroupBanRequest.from_tool_input(
        {"group_id": group_id, "user_id": user_id, "duration": duration}
    )
    assert request.group_id == "123456"
    assert request.user_id == "654321"
    assert request.duration == expected_duration


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_ban_request_rejects_invalid_group_id(value: object) -> None:
    """Group ban group_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupBanRequest.from_tool_input(
            {"group_id": value, "user_id": "654321", "duration": 60}
        )


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_ban_request_rejects_invalid_user_id(value: object) -> None:
    """Group ban user_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupBanRequest.from_tool_input(
            {"group_id": "123456", "user_id": value, "duration": 60}
        )


@pytest.mark.parametrize("value", [-1, "-1", "", "abc", True, None])
def test_set_group_ban_request_rejects_invalid_duration(value: object) -> None:
    """Group ban duration must be a non-negative integer."""
    with pytest.raises(ToolInputError):
        SetGroupBanRequest.from_tool_input(
            {"group_id": "123456", "user_id": "654321", "duration": value}
        )


@pytest.mark.parametrize(
    ("group_id", "user_id", "reject_add_request"),
    [(123456, 654321, False), ("123456", "654321", True)],
)
def test_set_group_kick_request_accepts_valid_fields(
    group_id: object,
    user_id: object,
    reject_add_request: bool,
) -> None:
    """Group kick inputs should normalize ids and preserve the flag."""
    request = SetGroupKickRequest.from_tool_input(
        {
            "group_id": group_id,
            "user_id": user_id,
            "reject_add_request": reject_add_request,
        }
    )
    assert request.group_id == "123456"
    assert request.user_id == "654321"
    assert request.reject_add_request is reject_add_request


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_kick_request_rejects_invalid_group_id(value: object) -> None:
    """Group kick group_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupKickRequest.from_tool_input(
            {"group_id": value, "user_id": "654321", "reject_add_request": False}
        )


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_kick_request_rejects_invalid_user_id(value: object) -> None:
    """Group kick user_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupKickRequest.from_tool_input(
            {"group_id": "123456", "user_id": value, "reject_add_request": False}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_group_kick_request_rejects_non_boolean_reject_add_request(
    value: object,
) -> None:
    """Group kick reject_add_request must be boolean."""
    with pytest.raises(ToolInputError):
        SetGroupKickRequest.from_tool_input(
            {"group_id": "123456", "user_id": "654321", "reject_add_request": value}
        )


@pytest.mark.parametrize("value", [True, False])
def test_get_friend_list_request_accepts_boolean_no_cache(value: bool) -> None:
    """Friend list no_cache should require and preserve boolean values."""
    request = GetFriendListRequest.from_tool_input({"no_cache": value})
    assert request.no_cache is value


@pytest.mark.parametrize("value", [1, "true", None])
def test_get_friend_list_request_rejects_non_boolean_no_cache(value: object) -> None:
    """Friend list no_cache must be boolean."""
    with pytest.raises(ToolInputError):
        GetFriendListRequest.from_tool_input({"no_cache": value})


@pytest.mark.parametrize(
    ("group_id", "user_id", "card", "expected_card"),
    [
        (123456, 654321, "测试名片", "测试名片"),
        ("123456", "654321", "  ", ""),
        ("123456", "654321", 123, "123"),
    ],
)
def test_set_group_card_request_accepts_valid_fields(
    group_id: object,
    user_id: object,
    card: object,
    expected_card: str,
) -> None:
    """Group card inputs should normalize ids and card values."""
    request = SetGroupCardRequest.from_tool_input(
        {"group_id": group_id, "user_id": user_id, "card": card}
    )
    assert request.group_id == "123456"
    assert request.user_id == "654321"
    assert request.card == expected_card


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_card_request_rejects_invalid_group_id(value: object) -> None:
    """Group card group_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupCardRequest.from_tool_input(
            {"group_id": value, "user_id": "654321", "card": "ok"}
        )


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_card_request_rejects_invalid_user_id(value: object) -> None:
    """Group card user_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupCardRequest.from_tool_input(
            {"group_id": "123456", "user_id": value, "card": "ok"}
        )


@pytest.mark.parametrize("value", [True, None])
def test_set_group_card_request_rejects_invalid_card(value: object) -> None:
    """Group card must be a string-like value."""
    with pytest.raises(ToolInputError):
        SetGroupCardRequest.from_tool_input(
            {"group_id": "123456", "user_id": "654321", "card": value}
        )


@pytest.mark.parametrize("value", [True, False])
def test_get_group_list_request_accepts_boolean_no_cache(value: bool) -> None:
    """Group list no_cache should require and preserve boolean values."""
    request = GetGroupListRequest.from_tool_input({"no_cache": value})
    assert request.no_cache is value


@pytest.mark.parametrize("value", [1, "true", None])
def test_get_group_list_request_rejects_non_boolean_no_cache(value: object) -> None:
    """Group list no_cache must be boolean."""
    with pytest.raises(ToolInputError):
        GetGroupListRequest.from_tool_input({"no_cache": value})


@pytest.mark.parametrize(
    ("group_id", "enable"),
    [(123456, True), ("123456", False)],
)
def test_set_group_whole_ban_request_accepts_valid_fields(
    group_id: object,
    enable: bool,
) -> None:
    """Whole group ban inputs should normalize ids and preserve the flag."""
    request = SetGroupWholeBanRequest.from_tool_input(
        {"group_id": group_id, "enable": enable}
    )
    assert request.group_id == "123456"
    assert request.enable is enable


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_whole_ban_request_rejects_invalid_group_id(value: object) -> None:
    """Whole group ban group_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupWholeBanRequest.from_tool_input({"group_id": value, "enable": True})


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_group_whole_ban_request_rejects_non_boolean_enable(
    value: object,
) -> None:
    """Whole group ban enable must be boolean."""
    with pytest.raises(ToolInputError):
        SetGroupWholeBanRequest.from_tool_input(
            {"group_id": "123456", "enable": value}
        )


@pytest.mark.parametrize(
    ("group_id", "is_dismiss"),
    [(123456, False), ("123456", True)],
)
def test_set_group_leave_request_accepts_valid_fields(
    group_id: object,
    is_dismiss: bool,
) -> None:
    """Group leave inputs should normalize ids and preserve the flag."""
    request = SetGroupLeaveRequest.from_tool_input(
        {"group_id": group_id, "is_dismiss": is_dismiss}
    )
    assert request.group_id == "123456"
    assert request.is_dismiss is is_dismiss


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_group_leave_request_rejects_invalid_group_id(value: object) -> None:
    """Group leave group_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetGroupLeaveRequest.from_tool_input(
            {"group_id": value, "is_dismiss": False}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_group_leave_request_rejects_non_boolean_is_dismiss(
    value: object,
) -> None:
    """Group leave is_dismiss must be boolean."""
    with pytest.raises(ToolInputError):
        SetGroupLeaveRequest.from_tool_input(
            {"group_id": "123456", "is_dismiss": value}
        )


@pytest.mark.parametrize(
    ("message_id", "emoji_id", "set"),
    [(123456, 666, True), ("123456", "666", False)],
)
def test_set_msg_emoji_like_request_accepts_valid_fields(
    message_id: object,
    emoji_id: object,
    set: bool,
) -> None:
    """Emoji-like inputs should normalize ids and preserve the flag."""
    request = SetMsgEmojiLikeRequest.from_tool_input(
        {"message_id": message_id, "emoji_id": emoji_id, "set": set}
    )
    assert request.message_id == "123456"
    assert request.emoji_id == "666"
    assert request.set is set


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_msg_emoji_like_request_rejects_invalid_message_id(value: object) -> None:
    """Emoji-like message_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetMsgEmojiLikeRequest.from_tool_input(
            {"message_id": value, "emoji_id": "666", "set": True}
        )


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_set_msg_emoji_like_request_rejects_invalid_emoji_id(value: object) -> None:
    """Emoji-like emoji_id must be numeric."""
    with pytest.raises(ToolInputError):
        SetMsgEmojiLikeRequest.from_tool_input(
            {"message_id": "123456", "emoji_id": value, "set": True}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_set_msg_emoji_like_request_rejects_non_boolean_set(value: object) -> None:
    """Emoji-like set must be boolean."""
    with pytest.raises(ToolInputError):
        SetMsgEmojiLikeRequest.from_tool_input(
            {"message_id": "123456", "emoji_id": "666", "set": value}
        )


@pytest.mark.parametrize(
    ("user_id", "temp_block", "temp_both_del"),
    [(123456, False, False), ("123456", True, True)],
)
def test_delete_friend_request_accepts_valid_fields(
    user_id: object,
    temp_block: bool,
    temp_both_del: bool,
) -> None:
    """Delete friend inputs should normalize ids and preserve flags."""
    request = DeleteFriendRequest.from_tool_input(
        {
            "user_id": user_id,
            "temp_block": temp_block,
            "temp_both_del": temp_both_del,
        }
    )
    assert request.user_id == "123456"
    assert request.temp_block is temp_block
    assert request.temp_both_del is temp_both_del


@pytest.mark.parametrize("value", ["", "abc", "123abc", True, None])
def test_delete_friend_request_rejects_invalid_user_id(value: object) -> None:
    """Delete friend user_id must be numeric."""
    with pytest.raises(ToolInputError):
        DeleteFriendRequest.from_tool_input(
            {"user_id": value, "temp_block": False, "temp_both_del": False}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_delete_friend_request_rejects_non_boolean_temp_block(value: object) -> None:
    """Delete friend temp_block must be boolean."""
    with pytest.raises(ToolInputError):
        DeleteFriendRequest.from_tool_input(
            {"user_id": "123456", "temp_block": value, "temp_both_del": False}
        )


@pytest.mark.parametrize("value", [1, "true", None])
def test_delete_friend_request_rejects_non_boolean_temp_both_del(
    value: object,
) -> None:
    """Delete friend temp_both_del must be boolean."""
    with pytest.raises(ToolInputError):
        DeleteFriendRequest.from_tool_input(
            {"user_id": "123456", "temp_block": False, "temp_both_del": value}
        )
