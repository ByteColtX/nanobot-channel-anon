"""CTX/1 presentation for normalized conversation context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from pydantic import ValidationError

from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import (
    Attachment,
    ConversationRef,
    ForwardExpanded,
    ForwardNode,
    NormalizedMessage,
    PresentedConversation,
)

_CTX_TRUNCATION_MARKER = "[...TRUNCATED...]"


@dataclass(slots=True)
class _UserRow:
    """CTX/1 用户行."""

    uid: str
    qq: str
    name: str
    is_bot: bool = False


@dataclass(slots=True)
class _ImageRow:
    """CTX/1 图片行."""

    iid: str
    filename: str


@dataclass(slots=True)
class _VoiceRow:
    """CTX/1 语音行."""

    vid: str
    filename: str


class ContextPresenter:
    """把标准化消息窗口渲染为 CTX/1 文本."""

    def present_recent_window(
        self,
        store: ContextStore,
        conversation: ConversationRef,
        *,
        self_id: str | None = None,
        max_ctx_length: int = 300,
        media_max_size_bytes: int = 50 * 1024 * 1024,
        limit: int | None = None,
    ) -> PresentedConversation:
        """渲染最近未消费窗口为 CTX/1."""
        unread_messages = store.unconsumed_window(conversation, limit=limit)
        if not unread_messages:
            messages = store.recent_window(conversation, limit=limit)
            message_ids: list[str] = []
            unread_count = 0
        else:
            unread_message_ids = {message.message_id for message in unread_messages}
            quoted_messages = self._collect_reply_targets(store, unread_messages)
            messages = [*quoted_messages, *unread_messages]
            message_ids = [
                message.message_id
                for message in unread_messages
                if message.message_type != "poke" and not message.from_self
            ]
            unread_count = len(message_ids)
            if unread_count == 0 and any(
                message.message_id in unread_message_ids
                and message.message_type == "poke"
                for message in messages
            ):
                unread_count = 0

        builder = _CTXBuilder(
            conversation=conversation,
            messages=messages,
            self_id=self_id,
            message_ids=message_ids,
            unread_count=unread_count,
            max_ctx_length=max_ctx_length,
            media_max_size_bytes=media_max_size_bytes,
        )
        text, media, metadata = builder.build()
        return PresentedConversation(text=text, media=media, metadata=metadata)

    @staticmethod
    def _collect_reply_targets(
        store: ContextStore,
        unread_messages: list[NormalizedMessage],
    ) -> list[NormalizedMessage]:
        """补齐未消费窗口引用到的回复目标消息."""
        seen_message_ids = {message.message_id for message in unread_messages}
        quoted_messages: list[NormalizedMessage] = []
        for message in unread_messages:
            if message.reply_to_message_id is None:
                continue
            if message.reply_to_message_id in seen_message_ids:
                continue
            reply_target = store.get_message(
                message.conversation,
                message.reply_to_message_id,
            )
            if reply_target is None:
                continue
            quoted_messages.append(reply_target)
            seen_message_ids.add(reply_target.message_id)
        return quoted_messages


class _CTXBuilder:
    """构建 CTX/1 文本与结构化元数据."""

    def __init__(
        self,
        *,
        conversation: ConversationRef,
        messages: list[NormalizedMessage],
        self_id: str | None,
        message_ids: list[str],
        unread_count: int,
        max_ctx_length: int,
        media_max_size_bytes: int,
    ) -> None:
        """初始化 CTX 构建器."""
        self.conversation = conversation
        self.messages = messages
        self.self_id = self_id
        self.message_ids = message_ids
        self.unread_count = unread_count
        self.max_ctx_length = max_ctx_length
        self.media_max_size_bytes = media_max_size_bytes
        self._users: list[_UserRow] = []
        self._user_ids: dict[tuple[str, bool], str] = {}
        self._images: list[_ImageRow] = []
        self._image_ids: dict[str, str] = {}
        self._voices: list[_VoiceRow] = []
        self._voice_ids: dict[str, str] = {}
        self._private_index = 0
        self._group_index = 0

    def build(self) -> tuple[str, list[dict[str, str]], dict[str, object]]:
        """生成 CTX/1 文本、媒体列表和元数据."""
        bot_uid = self._ensure_bot_user()
        rows = [self._header(bot_uid)]
        body_rows: list[str] = []
        media: list[dict[str, str]] = []

        for message in self.messages:
            if message.message_type == "poke":
                sender_uid = self._ensure_user(
                    message.sender_id,
                    message.sender_name,
                    is_bot=message.from_self,
                )
                body_rows.append(
                    "|".join(("E", self._escape(message.message_id), sender_uid))
                )
                continue

            sender_uid = self._ensure_user(
                message.sender_id,
                message.sender_name,
                is_bot=message.from_self,
            )
            body = self._render_message_body(message, media)
            body_rows.append(
                "|".join(
                    (
                        "M",
                        self._escape(message.message_id),
                        sender_uid,
                        self._escape(body),
                    )
                )
            )

        rows.extend(self._render_user_rows())
        rows.extend(self._render_image_rows())
        rows.extend(self._render_voice_rows())
        rows.extend(body_rows)
        rows.append("</CTX/1>")
        metadata = {
            "conversation": {
                "kind": self.conversation.kind,
                "id": self.conversation.id,
                "title": self.conversation.title,
            },
            "message_ids": list(self.message_ids),
            "count": self.unread_count,
        }
        return "\n".join(rows), media, metadata

    def _header(self, bot_uid: str) -> str:
        """渲染 CTX/1 头部."""
        scope = "p" if self.conversation.kind == "private" else "g"
        return (
            f"<CTX/1 {scope}:{self.conversation.id} "
            f"bot:{bot_uid} n:{self.unread_count}>"
        )

    def _ensure_bot_user(self) -> str:
        """确保机器人身份行存在."""
        if self.self_id is not None:
            return self._ensure_user(self.self_id, self.self_id, is_bot=True)
        bot_message = next(
            (message for message in self.messages if message.from_self),
            None,
        )
        if bot_message is not None:
            return self._ensure_user(
                bot_message.sender_id,
                bot_message.sender_name,
                is_bot=True,
            )
        return self._ensure_user("", "bot", is_bot=True)

    def _ensure_user(self, sender_id: str, sender_name: str, *, is_bot: bool) -> str:
        """分配并返回 CTX/1 用户别名."""
        key = (sender_id, is_bot)
        existing_uid = self._user_ids.get(key)
        if existing_uid is not None:
            return existing_uid

        if self.conversation.kind == "private":
            if is_bot:
                uid = "me"
            elif self.conversation.id == sender_id:
                uid = "peer"
            else:
                uid = f"u{self._private_index}"
                self._private_index += 1
        else:
            if is_bot:
                uid = "u0"
            else:
                offset = 1 if any(user.is_bot for user in self._users) else 0
                uid = f"u{self._group_index + offset}"
                self._group_index += 1

        self._user_ids[key] = uid
        self._users.append(
            _UserRow(
                uid=uid,
                qq=sender_id,
                name=sender_name or sender_id,
                is_bot=is_bot,
            )
        )
        return uid

    def _render_message_body(
        self,
        message: NormalizedMessage,
        media: list[dict[str, str]],
    ) -> str:
        """渲染单条消息体."""
        body = self._render_message_body_from_segments(message, media)
        if body is None:
            body = message.content
            for attachment in message.attachments:
                token = self._render_attachment_token(attachment, media)
                if token is None:
                    continue
                body = self._replace_first_placeholder(body, token, attachment.kind)
        if message.reply_to_message_id:
            body = f"^{message.reply_to_message_id} {body}".strip()
        body = self._append_forward_block(body, message)
        return self._truncate_message_body(body.strip())

    def _render_message_body_from_segments(
        self,
        message: NormalizedMessage,
        media: list[dict[str, str]],
    ) -> str | None:
        """优先根据 render_segments 渲染消息体."""
        raw_segments = message.metadata.get("render_segments")
        if not isinstance(raw_segments, list):
            return None

        rendered_parts: list[str] = []
        attachment_index = 0
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                return None
            segment_type = raw_segment.get("type")
            if segment_type == "text":
                text = raw_segment.get("text")
                if not isinstance(text, str):
                    return None
                rendered_parts.append(text)
                continue
            if segment_type == "mention":
                user_id = raw_segment.get("user_id")
                if not isinstance(user_id, str) or not user_id:
                    return None
                name = raw_segment.get("name")
                mention_name = self._mention_display_name(name, user_id)
                mention_uid = self._ensure_user(
                    user_id,
                    mention_name,
                    is_bot=user_id == self.self_id,
                )
                rendered_parts.append(f"@{mention_uid}")
                continue
            if segment_type == "mention_all":
                rendered_parts.append("@all")
                continue
            if segment_type == "forward":
                rendered_parts.append("[forward]")
                continue
            if segment_type in {"image", "voice", "video", "file"}:
                if attachment_index >= len(message.attachments):
                    return None
                attachment = message.attachments[attachment_index]
                attachment_index += 1
                if attachment.kind != segment_type:
                    return None
                token = self._render_attachment_token(attachment, media)
                if token is None:
                    continue
                rendered_parts.append(token)
                continue
            return None
        if attachment_index != len(message.attachments):
            return None
        return "".join(rendered_parts)

    @staticmethod
    def _mention_display_name(name: object, user_id: str) -> str:
        """返回提及时用于 U 行的稳定显示名."""
        if isinstance(name, str) and name:
            return name
        return user_id

    def _render_attachment_token(
        self,
        attachment: Attachment,
        media: list[dict[str, str]],
    ) -> str | None:
        """渲染附件占位符并收集可上传媒体."""
        if attachment.kind == "image":
            if self._is_media_allowed(attachment):
                iid = self._ensure_image(attachment.url)
                if not any(item["url"] == attachment.url for item in media):
                    media.append({"kind": attachment.kind, "url": attachment.url})
                return f"[{iid}]"
            return "[image]"
        if attachment.kind == "voice":
            vid = self._ensure_voice(attachment.url)
            return f"[{vid}]"
        if attachment.kind == "video":
            return "[video]"
        if attachment.kind == "file":
            return "[file]"
        return None

    def _is_media_allowed(self, attachment: Attachment) -> bool:
        """判断媒体是否在大小限制内或缺少限制信息."""
        if not attachment.url:
            return False
        raw_size = attachment.metadata.get("file_size")
        if raw_size is None:
            return True
        try:
            file_size = int(str(raw_size).strip())
        except ValueError:
            return True
        return file_size <= self.media_max_size_bytes

    def _ensure_image(self, media_ref: str) -> str:
        """分配图片别名."""
        existing_iid = self._image_ids.get(media_ref)
        if existing_iid is not None:
            return existing_iid
        iid = f"i{len(self._images)}"
        self._image_ids[media_ref] = iid
        self._images.append(_ImageRow(iid=iid, filename=self._basename(media_ref)))
        return iid

    def _ensure_voice(self, media_ref: str) -> str:
        """分配语音别名."""
        filename = self._basename(media_ref)
        existing_vid = self._voice_ids.get(filename)
        if existing_vid is not None:
            return existing_vid
        vid = f"v{len(self._voices)}"
        self._voice_ids[filename] = vid
        self._voices.append(_VoiceRow(vid=vid, filename=filename))
        return vid

    def _render_user_rows(self) -> list[str]:
        """渲染用户定义行."""
        rows: list[str] = []
        for user in self._users:
            row = ["U", user.uid, self._escape(user.qq), self._escape(user.name)]
            if user.is_bot:
                row.append("bot")
            rows.append("|".join(row))
        return rows

    def _render_image_rows(self) -> list[str]:
        """渲染图片定义行."""
        return [
            f"I|{image.iid}|{self._escape(image.filename)}"
            for image in self._images
        ]

    def _render_voice_rows(self) -> list[str]:
        """渲染语音定义行."""
        return [
            f"V|{voice.vid}|{self._escape(voice.filename)}"
            for voice in self._voices
        ]

    @staticmethod
    def _basename(media_ref: str) -> str:
        """提取媒体引用的文件名."""
        parsed = urlparse(media_ref)
        candidate = parsed.path or media_ref
        name = PurePosixPath(candidate).name
        return name or media_ref

    def _append_forward_block(self, body: str, message: NormalizedMessage) -> str:
        """在消息体后追加紧凑转发展开块."""
        expanded = self._forward_expanded_from_metadata(message.metadata)
        if not expanded:
            return body
        blocks = [self._render_forward_block(item) for item in expanded]
        suffix = " ".join(blocks)
        return f"{body} {suffix}".strip() if body else suffix

    @staticmethod
    def _forward_expanded_from_metadata(
        metadata: dict[str, object],
    ) -> list[ForwardExpanded]:
        raw_items = metadata.get("forward_expanded")
        if not isinstance(raw_items, list):
            return []
        expanded: list[ForwardExpanded] = []
        for item in raw_items:
            if isinstance(item, ForwardExpanded):
                expanded.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                expanded.append(ForwardExpanded.model_validate(item))
            except ValidationError:
                continue
        return expanded

    def _render_forward_block(self, expanded: ForwardExpanded) -> str:
        forward_id = self._escape_forward_field(expanded.forward_id or "")
        summary = self._escape_forward_field(expanded.summary)
        nodes = ",".join(self._render_forward_node(node) for node in expanded.nodes)
        return f"[F:id={forward_id};summary={summary};nodes={nodes}]"

    def _render_forward_node(self, node: ForwardNode) -> str:
        sender_id = self._escape_forward_field(node.sender_id)
        sender_name = self._escape_forward_field(node.sender_name)
        content = self._escape_forward_field(node.content)
        return "/".join((sender_id, sender_name, content))

    @staticmethod
    def _escape_forward_field(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("/", "\\/")
            .replace(",", "\\,")
            .replace(";", "\\;")
            .replace("=", "\\=")
        )

    @staticmethod
    def _replace_first_placeholder(body: str, token: str, kind: str) -> str:
        """按旧占位符约定替换首个媒体标记."""
        placeholder_map = {
            "image": "[image]",
            "voice": "[voice]",
            "video": "[video]",
            "file": "[file]",
        }
        placeholder = placeholder_map.get(kind)
        if placeholder and placeholder in body:
            return body.replace(placeholder, token, 1)
        return f"{body}{token}" if body else token

    @staticmethod
    def _escape(value: str) -> str:
        """转义 CTX/1 保留字符."""
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")

    def _truncate_message_body(self, body: str) -> str:
        """按旧规则从中间截断超长消息体."""
        if len(body) <= self.max_ctx_length:
            return body
        keep = self.max_ctx_length - len(_CTX_TRUNCATION_MARKER)
        if keep <= 0:
            return _CTX_TRUNCATION_MARKER[: self.max_ctx_length]
        head_keep = keep // 2
        tail_keep = keep - head_keep
        return f"{body[:head_keep]}{_CTX_TRUNCATION_MARKER}{body[-tail_keep:]}"
