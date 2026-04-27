"""Shared NapCat request and response models for MCP tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nanobot_channel_anon.utils import normalize_onebot_id


class ToolInputError(ValueError):
    """Raised when MCP tool input is invalid."""


class NapCatActionStatus(StrEnum):
    """Top-level NapCat action status values."""

    OK = "ok"
    FAILED = "failed"


class NapCatActionStream(StrEnum):
    """Known NapCat action stream values."""

    STREAM_ACTION = "stream-action"
    NORMAL_ACTION = "normal-action"


class NapCatActionResult(BaseModel):
    """Shared response envelope for NapCat action APIs."""

    model_config = ConfigDict(extra="allow")

    status: NapCatActionStatus
    retcode: int | float
    data: Any = None
    message: str = ""
    wording: str = ""
    stream: NapCatActionStream | str | None = None
    echo: str | None = None


class ToolRequestModel(BaseModel):
    """Base request model that raises ToolInputError for invalid tool input."""

    @classmethod
    def from_tool_input(cls, payload: dict[str, Any]) -> Self:
        """Validate tool payload and surface a plain tool input error."""
        try:
            return cls.model_validate(payload)
        except ValidationError as exc:
            error = exc.errors(include_url=False)[0]
            message = str(error["msg"])
            if message.startswith("Value error, "):
                message = message.removeprefix("Value error, ")
            raise ToolInputError(message) from exc


class SendPokeRequest(ToolRequestModel):
    """Request payload for NapCat /send_poke."""

    user_id: str
    group_id: str | None = None

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str | None:
        """Normalize an optional group ID in either raw or prefixed form."""
        if value is None:
            return None
        if isinstance(value, bool):
            raise ToolInputError("group_id must be <digits> or group:<digits>")
        if isinstance(value, int):
            return str(value)
        if not isinstance(value, str):
            raise ToolInputError("group_id must be <digits> or group:<digits>")

        normalized = value.strip()
        if not normalized:
            raise ToolInputError("group_id cannot be empty")
        if normalized.startswith("private:"):
            raise ToolInputError("group_id cannot use private:<id> format")
        if normalized.startswith("group:"):
            normalized = normalized.removeprefix("group:").strip()
        if not normalized or not normalized.isdigit():
            raise ToolInputError("group_id must be <digits> or group:<digits>")
        return normalized


class SendLikeRequest(ToolRequestModel):
    """Request payload for NapCat /send_like."""

    user_id: str
    times: int = Field(gt=0)

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("times", mode="before")
    @classmethod
    def validate_times(cls, value: Any) -> int:
        """Normalize a like-count integer."""
        if isinstance(value, bool):
            raise ToolInputError("times must be a positive integer")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped or not stripped.isdigit():
                raise ToolInputError("times must be a positive integer")
            normalized = int(stripped)
        else:
            raise ToolInputError("times must be a positive integer")

        if normalized <= 0:
            raise ToolInputError("times must be a positive integer")
        return normalized


class SendGroupAIRecordRequest(ToolRequestModel):
    """Request payload for NapCat /send_group_ai_record."""

    group_id: str
    character: str
    text: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("character", mode="before")
    @classmethod
    def validate_character(cls, value: Any) -> str:
        """Normalize a required voice character string."""
        return _normalize_nonempty_string(value, field_name="character")

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, value: Any) -> str:
        """Normalize a required voice text string."""
        return _normalize_nonempty_string(value, field_name="text")


class DeleteMsgRequest(ToolRequestModel):
    """Request payload for NapCat /delete_msg."""

    message_id: str

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_message_id(cls, value: Any) -> str:
        """Normalize a target message ID."""
        return _normalize_scalar_id(value, field_name="message_id")


class GetGroupMemberListRequest(ToolRequestModel):
    """Request payload for NapCat /get_group_member_list."""

    group_id: str
    no_cache: bool = False

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("no_cache", mode="before")
    @classmethod
    def validate_no_cache(cls, value: Any) -> bool:
        """Require a real boolean no_cache flag."""
        if not isinstance(value, bool):
            raise ToolInputError("no_cache must be a boolean")
        return value


class GetAIRecordRequest(ToolRequestModel):
    """Request payload for NapCat /get_ai_record."""

    group_id: str
    character: str
    text: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("character", mode="before")
    @classmethod
    def validate_character(cls, value: Any) -> str:
        """Normalize a required AI voice character."""
        return _normalize_nonempty_string(value, field_name="character")

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, value: Any) -> str:
        """Normalize a required AI voice text input."""
        return _normalize_nonempty_string(value, field_name="text")


class GetAICharactersRequest(ToolRequestModel):
    """Request payload for NapCat /get_ai_characters."""

    group_id: str
    chat_type: int = 1

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("chat_type", mode="before")
    @classmethod
    def validate_chat_type(cls, value: Any) -> int:
        """Normalize a positive chat type integer."""
        if isinstance(value, bool):
            raise ToolInputError("chat_type must be a positive integer")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped or not stripped.isdigit():
                raise ToolInputError("chat_type must be a positive integer")
            normalized = int(stripped)
        else:
            raise ToolInputError("chat_type must be a positive integer")

        if normalized <= 0:
            raise ToolInputError("chat_type must be a positive integer")
        return normalized


class GetGroupInfoRequest(ToolRequestModel):
    """Request payload for NapCat /get_group_info."""

    group_id: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")


class GetGroupDetailInfoRequest(ToolRequestModel):
    """Request payload for NapCat /get_group_detail_info."""

    group_id: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")


class GetGroupMemberInfoRequest(ToolRequestModel):
    """Request payload for NapCat /get_group_member_info."""

    group_id: str
    user_id: str
    no_cache: bool

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("no_cache", mode="before")
    @classmethod
    def validate_no_cache(cls, value: Any) -> bool:
        """Require a real boolean no_cache flag."""
        if not isinstance(value, bool):
            raise ToolInputError("no_cache must be a boolean")
        return value


class GetQunAlbumListRequest(ToolRequestModel):
    """Request payload for NapCat /get_qun_album_list."""

    group_id: str
    attach_info: str = ""

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("attach_info", mode="before")
    @classmethod
    def validate_attach_info(cls, value: Any) -> str:
        """Normalize an optional album pagination token."""
        if value is None:
            return ""
        if isinstance(value, bool):
            raise ToolInputError("attach_info must be a string")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        raise ToolInputError("attach_info must be a string")


class UploadImageToQunAlbumRequest(ToolRequestModel):
    """Request payload for NapCat /upload_image_to_qun_album."""

    group_id: str
    album_id: str
    album_name: str
    file: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("album_id", mode="before")
    @classmethod
    def validate_album_id(cls, value: Any) -> str:
        """Normalize a target album ID."""
        return _normalize_nonempty_string(value, field_name="album_id")

    @field_validator("album_name", mode="before")
    @classmethod
    def validate_album_name(cls, value: Any) -> str:
        """Normalize a target album name."""
        return _normalize_nonempty_string(value, field_name="album_name")

    @field_validator("file", mode="before")
    @classmethod
    def validate_file(cls, value: Any) -> str:
        """Normalize an image path, URL, or base64 payload string."""
        return _normalize_nonempty_string(value, field_name="file")


class SetGroupAddRequestRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_add_request."""

    flag: str
    sub_type: str
    approve: bool
    reason: str | None = None

    @field_validator("flag", mode="before")
    @classmethod
    def validate_flag(cls, value: Any) -> str:
        """Normalize a group-request flag."""
        return _normalize_nonempty_string(value, field_name="flag")

    @field_validator("sub_type", mode="before")
    @classmethod
    def validate_sub_type(cls, value: Any) -> str:
        """Normalize group request subtype."""
        normalized = _normalize_nonempty_string(value, field_name="sub_type").lower()
        if normalized not in {"add", "invite"}:
            raise ToolInputError("sub_type must be add or invite")
        return normalized

    @field_validator("approve", mode="before")
    @classmethod
    def validate_approve(cls, value: Any) -> bool:
        """Require a real boolean approval flag."""
        if not isinstance(value, bool):
            raise ToolInputError("approve must be a boolean")
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: Any) -> str | None:
        """Normalize an optional rejection reason."""
        if value is None:
            return None
        normalized = _normalize_nonempty_string(value, field_name="reason")
        return normalized or None


class SetFriendAddRequestRequest(ToolRequestModel):
    """Request payload for NapCat /set_friend_add_request."""

    flag: str
    approve: bool
    remark: str

    @field_validator("flag", mode="before")
    @classmethod
    def validate_flag(cls, value: Any) -> str:
        """Normalize a friend-request flag."""
        return _normalize_nonempty_string(value, field_name="flag")

    @field_validator("approve", mode="before")
    @classmethod
    def validate_approve(cls, value: Any) -> bool:
        """Require a real boolean approval flag."""
        if not isinstance(value, bool):
            raise ToolInputError("approve must be a boolean")
        return value

    @field_validator("remark", mode="before")
    @classmethod
    def validate_remark(cls, value: Any) -> str:
        """Normalize a required friend remark."""
        return _normalize_nonempty_string(value, field_name="remark")


class SetGroupBanRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_ban."""

    group_id: str
    user_id: str
    duration: int

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("duration", mode="before")
    @classmethod
    def validate_duration(cls, value: Any) -> int:
        """Normalize a non-negative ban duration."""
        if isinstance(value, bool):
            raise ToolInputError("duration must be a non-negative integer")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped or not stripped.isdigit():
                raise ToolInputError("duration must be a non-negative integer")
            normalized = int(stripped)
        else:
            raise ToolInputError("duration must be a non-negative integer")

        if normalized < 0:
            raise ToolInputError("duration must be a non-negative integer")
        return normalized


class SetGroupKickRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_kick."""

    group_id: str
    user_id: str
    reject_add_request: bool = False

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("reject_add_request", mode="before")
    @classmethod
    def validate_reject_add_request(cls, value: Any) -> bool:
        """Require a real boolean reject_add_request flag."""
        if not isinstance(value, bool):
            raise ToolInputError("reject_add_request must be a boolean")
        return value


class GetFriendListRequest(ToolRequestModel):
    """Request payload for NapCat /get_friend_list."""

    no_cache: bool = False

    @field_validator("no_cache", mode="before")
    @classmethod
    def validate_no_cache(cls, value: Any) -> bool:
        """Require a real boolean no_cache flag."""
        if not isinstance(value, bool):
            raise ToolInputError("no_cache must be a boolean")
        return value


class GetGroupListRequest(ToolRequestModel):
    """Request payload for NapCat /get_group_list."""

    no_cache: bool = False

    @field_validator("no_cache", mode="before")
    @classmethod
    def validate_no_cache(cls, value: Any) -> bool:
        """Require a real boolean no_cache flag."""
        if not isinstance(value, bool):
            raise ToolInputError("no_cache must be a boolean")
        return value


class SetGroupWholeBanRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_whole_ban."""

    group_id: str
    enable: bool

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("enable", mode="before")
    @classmethod
    def validate_enable(cls, value: Any) -> bool:
        """Require a real boolean enable flag."""
        if not isinstance(value, bool):
            raise ToolInputError("enable must be a boolean")
        return value


class SetGroupLeaveRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_leave."""

    group_id: str
    is_dismiss: bool = False

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("is_dismiss", mode="before")
    @classmethod
    def validate_is_dismiss(cls, value: Any) -> bool:
        """Require a real boolean is_dismiss flag."""
        if not isinstance(value, bool):
            raise ToolInputError("is_dismiss must be a boolean")
        return value


class SetMsgEmojiLikeRequest(ToolRequestModel):
    """Request payload for NapCat /set_msg_emoji_like."""

    message_id: str
    emoji_id: str
    set: bool

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_message_id(cls, value: Any) -> str:
        """Normalize a target message ID."""
        return _normalize_scalar_id(value, field_name="message_id")

    @field_validator("emoji_id", mode="before")
    @classmethod
    def validate_emoji_id(cls, value: Any) -> str:
        """Normalize a target emoji ID."""
        return _normalize_scalar_id(value, field_name="emoji_id")

    @field_validator("set", mode="before")
    @classmethod
    def validate_set(cls, value: Any) -> bool:
        """Require a real boolean set flag."""
        if not isinstance(value, bool):
            raise ToolInputError("set must be a boolean")
        return value


class DeleteFriendRequest(ToolRequestModel):
    """Request payload for NapCat /delete_friend."""

    user_id: str
    temp_block: bool = False
    temp_both_del: bool = False

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("temp_block", mode="before")
    @classmethod
    def validate_temp_block(cls, value: Any) -> bool:
        """Require a real boolean temp_block flag."""
        if not isinstance(value, bool):
            raise ToolInputError("temp_block must be a boolean")
        return value

    @field_validator("temp_both_del", mode="before")
    @classmethod
    def validate_temp_both_del(cls, value: Any) -> bool:
        """Require a real boolean temp_both_del flag."""
        if not isinstance(value, bool):
            raise ToolInputError("temp_both_del must be a boolean")
        return value


class SetGroupCardRequest(ToolRequestModel):
    """Request payload for NapCat /set_group_card."""

    group_id: str
    user_id: str
    card: str

    @field_validator("group_id", mode="before")
    @classmethod
    def validate_group_id(cls, value: Any) -> str:
        """Normalize a target group ID."""
        return _normalize_scalar_id(value, field_name="group_id")

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: Any) -> str:
        """Normalize a target QQ user ID."""
        return _normalize_scalar_id(value, field_name="user_id")

    @field_validator("card", mode="before")
    @classmethod
    def validate_card(cls, value: Any) -> str:
        """Normalize a group card string, allowing empty values."""
        if isinstance(value, bool) or value is None:
            raise ToolInputError("card must be a string")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        raise ToolInputError("card must be a string")


def _normalize_scalar_id(value: Any, *, field_name: str) -> str:
    normalized = normalize_onebot_id(value)
    if normalized is None or not normalized.isdigit():
        raise ToolInputError(f"{field_name} must be a non-empty digits string")
    return normalized


def _normalize_nonempty_string(value: Any, *, field_name: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ToolInputError(f"{field_name} must be a non-empty string")
    if isinstance(value, int):
        normalized = str(value)
    elif isinstance(value, str):
        normalized = value.strip()
    else:
        raise ToolInputError(f"{field_name} must be a non-empty string")

    if not normalized:
        raise ToolInputError(f"{field_name} must be a non-empty string")
    return normalized
