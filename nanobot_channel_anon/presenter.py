"""CTX/1 presentation for normalized conversation context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from nanobot_channel_anon.context_store import ContextStore
from nanobot_channel_anon.domain import (
    Attachment,
    ConversationRef,
    ForwardExpanded,
    NormalizedMessage,
    PresentedConversation,
)
from nanobot_channel_anon.utils import parse_forward_expanded_slots

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
    transcription_text: str | None = None
    transcription_failed: bool = False


class ContextPresenter:
    """把标准化消息窗口渲染为 CTX/1 文本."""

    def present_recent_window(
        self,
        store: ContextStore,
        conversation: ConversationRef,
        *,
        self_id: str | None = None,
        self_name: str | None = None,
        max_ctx_length: int = 300,
        media_max_size_bytes: int = 50 * 1024 * 1024,
        limit: int | None = None,
        extra_messages: list[NormalizedMessage] | None = None,
    ) -> PresentedConversation:
        """渲染最近未消费窗口为 CTX/1."""
        unread_messages = store.unconsumed_window(conversation, limit=limit)
        resolved_extra_messages = [] if extra_messages is None else list(extra_messages)
        if not unread_messages:
            messages = self._merge_messages_in_store_order(
                store,
                conversation,
                resolved_extra_messages,
                [],
            )
            message_ids: list[str] = []
            unread_count = 0
        else:
            unread_messages_for_display = [
                message for message in unread_messages if not message.from_self
            ]
            unread_message_ids = {
                message.message_id for message in unread_messages_for_display
            }
            quoted_messages = self._collect_reply_targets(
                store,
                unread_messages_for_display,
                extra_messages=resolved_extra_messages,
            )
            messages = self._merge_messages_in_store_order(
                store,
                conversation,
                quoted_messages,
                unread_messages_for_display,
            )
            message_ids = [
                message.message_id
                for message in unread_messages_for_display
                if message.message_type != "poke"
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
            self_name=self_name,
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
        *,
        extra_messages: list[NormalizedMessage],
    ) -> list[NormalizedMessage]:
        """补齐未消费窗口引用到的回复目标消息."""
        seen_message_ids = {message.message_id for message in unread_messages}
        extra_by_id = {message.message_id: message for message in extra_messages}
        quoted_messages: list[NormalizedMessage] = []
        for message in unread_messages:
            if message.reply_to_message_id is None:
                continue
            if message.reply_to_message_id in seen_message_ids:
                continue
            reply_target = extra_by_id.get(message.reply_to_message_id)
            if reply_target is None:
                reply_target = store.get_message(
                    message.conversation,
                    message.reply_to_message_id,
                )
            if reply_target is None:
                continue
            quoted_messages.append(reply_target)
            seen_message_ids.add(reply_target.message_id)
        return quoted_messages

    @staticmethod
    def _merge_messages_in_store_order(
        store: ContextStore,
        conversation: ConversationRef,
        quoted_messages: list[NormalizedMessage],
        unread_messages: list[NormalizedMessage],
    ) -> list[NormalizedMessage]:
        """按上下文存储顺序合并消息, 并为缓存外引用目标补稳定插入位."""
        selected_by_id = {
            message.message_id: message
            for message in [*quoted_messages, *unread_messages]
        }
        ordered_messages = [
            selected_by_id[message.message_id]
            for message in store.recent_window(conversation)
            if message.message_id in selected_by_id
        ]
        present_ids = {message.message_id for message in ordered_messages}
        missing_quoted = [
            message
            for message in quoted_messages
            if message.message_id not in present_ids
        ]
        if not missing_quoted:
            return ordered_messages

        for quoted_message in reversed(missing_quoted):
            ordered_messages.insert(0, quoted_message)
        return ordered_messages


class _CTXBuilder:
    """构建 CTX/1 文本与结构化元数据."""

    def __init__(
        self,
        *,
        conversation: ConversationRef,
        messages: list[NormalizedMessage],
        self_id: str | None,
        self_name: str | None,
        message_ids: list[str],
        unread_count: int,
        max_ctx_length: int,
        media_max_size_bytes: int,
    ) -> None:
        """初始化 CTX 构建器."""
        self.conversation = conversation
        self.messages = messages
        self.self_id = self_id
        self.self_name = self_name
        self.message_ids = message_ids
        self.unread_count = unread_count
        self.max_ctx_length = max_ctx_length
        self.media_max_size_bytes = media_max_size_bytes
        self._users: list[_UserRow] = []
        self._user_ids: dict[tuple[str, bool], str] = {}
        self._images: list[_ImageRow] = []
        self._image_ids: dict[str, str] = {}
        self._voices: list[_VoiceRow] = []
        self._voice_ids: dict[tuple[str, str | None, bool], str] = {}
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
            body, forward_rows = self._render_message_body(message, media)
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
            body_rows.extend(forward_rows)

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
            return self._ensure_user(
                self.self_id,
                self.self_name or self.self_id,
                is_bot=True,
            )
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
    ) -> tuple[str, list[str]]:
        """渲染单条消息体及其 forward 容器行."""
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
        body, forward_rows = self._render_forward_containers(
            body.strip(),
            message,
            media,
        )
        return self._truncate_message_body(body), forward_rows

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
            image_ref = self._image_media_ref(attachment)
            if image_ref is not None and self._is_media_allowed(attachment):
                iid = self._ensure_image(image_ref, attachment)
                if not any(item["url"] == image_ref for item in media):
                    media.append({"kind": attachment.kind, "url": image_ref})
                return f"[{iid}]"
            return "[image]"
        if attachment.kind == "voice":
            voice_ref = self._voice_media_ref(attachment)
            vid = self._ensure_voice(attachment)
            if voice_ref is not None and not any(
                item["url"] == voice_ref for item in media
            ):
                media.append({"kind": attachment.kind, "url": voice_ref})
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

    def _ensure_image(self, media_ref: str, attachment: Attachment) -> str:
        """分配图片别名."""
        existing_iid = self._image_ids.get(media_ref)
        if existing_iid is not None:
            return existing_iid
        iid = f"i{len(self._images)}"
        self._image_ids[media_ref] = iid
        self._images.append(
            _ImageRow(iid=iid, filename=self._image_filename(attachment, media_ref))
        )
        return iid

    def _ensure_voice(self, attachment: Attachment) -> str:
        """分配语音别名."""
        media_ref = self._voice_media_ref(attachment)
        if media_ref is None:
            media_ref = attachment.url or attachment.name or "voice"
        filename = self._basename(media_ref)
        transcription_text = self._transcription_text(attachment)
        transcription_failed = self._transcription_failed(attachment)
        key = (filename, transcription_text, transcription_failed)
        existing_vid = self._voice_ids.get(key)
        if existing_vid is not None:
            return existing_vid
        vid = f"v{len(self._voices)}"
        self._voice_ids[key] = vid
        self._voices.append(
            _VoiceRow(
                vid=vid,
                filename=filename,
                transcription_text=transcription_text,
                transcription_failed=transcription_failed,
            )
        )
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
        rows: list[str] = []
        for voice in self._voices:
            row = ["V", voice.vid, self._escape(voice.filename)]
            if voice.transcription_text is not None:
                row.append(f"={self._escape(voice.transcription_text)}")
            elif voice.transcription_failed:
                row.append("!")
            rows.append("|".join(row))
        return rows

    @classmethod
    def _image_filename(cls, attachment: Attachment, media_ref: str) -> str:
        """返回图片在 CTX/1 中使用的文件名."""
        metadata = attachment.metadata
        candidates = (
            metadata.get("cache_name"),
            attachment.name,
            metadata.get("original_file"),
            metadata.get("local_path"),
            media_ref,
        )
        for candidate in candidates:
            name = cls._basename_from_candidate(candidate)
            if cls._is_informative_image_name(name):
                return name
        return "image"

    @staticmethod
    def _is_informative_image_name(name: str) -> bool:
        """判断图片文件名是否足够提供语义信息."""
        normalized = name.strip().lower()
        if not normalized:
            return False
        return normalized not in {"download", "image"}

    @staticmethod
    def _basename_from_candidate(candidate: object) -> str:
        """从任意候选值中提取文件名."""
        if not isinstance(candidate, str):
            return ""
        parsed = urlparse(candidate)
        raw_value = parsed.path or candidate
        name = PurePosixPath(raw_value).name.strip()
        return name or candidate.strip()

    @classmethod
    def _basename(cls, media_ref: str) -> str:
        """提取媒体引用的文件名."""
        return cls._basename_from_candidate(media_ref)

    @staticmethod
    def _image_media_ref(attachment: Attachment) -> str | None:
        """返回图片优先使用的媒体引用."""
        local_path = attachment.metadata.get("local_path")
        if isinstance(local_path, str) and local_path:
            return local_path
        if attachment.url:
            return attachment.url
        return None

    @staticmethod
    def _voice_media_ref(attachment: Attachment) -> str | None:
        """返回语音优先使用的媒体引用."""
        for key in (
            "transcription_input_local_file_uri",
            "local_file_uri",
        ):
            value = attachment.metadata.get(key)
            if isinstance(value, str) and value:
                return value
        if attachment.url:
            return attachment.url
        return None

    @staticmethod
    def _transcription_text(attachment: Attachment) -> str | None:
        """返回转写文本."""
        value = attachment.metadata.get("transcription_text")
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _transcription_failed(attachment: Attachment) -> bool:
        """返回转写是否失败."""
        return attachment.metadata.get("transcription_status") == "failed"

    def _render_forward_containers(
        self,
        body: str,
        message: NormalizedMessage,
        media: list[dict[str, str]],
    ) -> tuple[str, list[str]]:
        """把消息体中的 forward 占位替换为容器引用, 并生成 F/N 行."""
        slots = self._forward_expanded_slots_from_metadata(message.metadata)
        if not slots or "[forward]" not in body:
            return body, []

        parts = body.split("[forward]")
        rebuilt = [parts[0]]
        rows: list[str] = []
        forward_index = 0
        for occurrence_index, tail in enumerate(parts[1:]):
            expanded = (
                slots[occurrence_index]
                if occurrence_index < len(slots)
                else None
            )
            if expanded is None:
                rebuilt.append("[forward]")
            else:
                fid = f"f{forward_index}"
                forward_index += 1
                rebuilt.append(f"[F:{fid}]")
                rows.extend(self._render_forward_container_rows(fid, expanded, media))
            rebuilt.append(tail)
        return "".join(rebuilt), rows

    @staticmethod
    def _forward_expanded_slots_from_metadata(
        metadata: dict[str, object],
    ) -> list[ForwardExpanded | None]:
        return parse_forward_expanded_slots(metadata)

    def _render_forward_container_rows(
        self,
        fid: str,
        expanded: ForwardExpanded,
        media: list[dict[str, str]],
    ) -> list[str]:
        rows = [
            "|".join(
                (
                    "F",
                    fid,
                    str(len(expanded.nodes)),
                    self._escape(expanded.summary),
                )
            )
        ]
        node_index_by_message_id = {
            node.message_id: index
            for index, node in enumerate(expanded.nodes)
            if node.message_id
        }
        for index, node in enumerate(expanded.nodes):
            node_uid = self._ensure_user(
                node.sender_id,
                node.sender_name,
                is_bot=node.sender_id == self.self_id,
            )
            reply_prefix = ""
            if node.reply_to_message_id is not None:
                reply_index = node_index_by_message_id.get(node.reply_to_message_id)
                if reply_index is not None and reply_index < index:
                    reply_prefix = f"^{reply_index} "
            node_body = f"{reply_prefix}{node.content}".strip()
            for attachment in node.attachments:
                token = self._render_attachment_token(attachment, media)
                if token is None:
                    continue
                node_body = self._replace_first_placeholder(
                    node_body,
                    token,
                    attachment.kind,
                )
            node_body = self._truncate_message_body(node_body)
            rows.append(
                "|".join(
                    (
                        "N",
                        str(index),
                        node_uid,
                        self._escape(node_body),
                    )
                )
            )
        return rows

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
