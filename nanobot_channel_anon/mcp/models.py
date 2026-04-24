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


class DeleteMsgRequest(ToolRequestModel):
    """Request payload for NapCat /delete_msg."""

    message_id: str

    @field_validator("message_id", mode="before")
    @classmethod
    def validate_message_id(cls, value: Any) -> str:
        """Normalize a target message ID."""
        return _normalize_scalar_id(value, field_name="message_id")


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
