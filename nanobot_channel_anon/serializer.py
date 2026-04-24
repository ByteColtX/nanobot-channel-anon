# ruff: noqa: E501

"""CTX/1 上下文序列化."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from nanobot_channel_anon.buffer import (
    Buffer,
    ForwardEntry,
    ForwardNodeEntry,
    MessageEntry,
)
from nanobot_channel_anon.utils import parse_chat_id, string_value


@dataclass(slots=True)
class SerializedCQMessage:
    """A serialized CTX block plus the unread IDs and media it consumed."""

    chat_id: str
    text: str
    message_ids: list[str]
    media: list[str]
    count: int


@dataclass(slots=True)
class _UserRow:
    uid: str
    qq: str
    name: str
    is_bot: bool = False
    name_priority: int = -1


@dataclass(slots=True)
class _ImageRow:
    iid: str
    filename: str


@dataclass(slots=True)
class _VoiceRow:
    vid: str
    filename: str
    transcription_text: str | None = None
    transcription_failed: bool = False


class _CTXBuilder:
    def __init__(
        self,
        *,
        chat_id: str,
        entries: list[MessageEntry],
        self_id: str | None,
        self_nickname: str | None,
        message_ids: list[str] | None = None,
        media: list[str] | None = None,
        count: int | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.entries = entries
        self.self_id = self_id
        self.self_nickname = self_nickname
        self.chat_kind, self.target_id = parse_chat_id(chat_id)
        self.is_private = self.chat_kind == "private"
        self._users: list[_UserRow] = []
        self._user_rows: dict[tuple[str, bool], _UserRow] = {}
        self._user_uids: dict[tuple[str, bool], str] = {}
        self._images: list[_ImageRow] = []
        self._image_ids: dict[str, str] = {}
        self._voices: list[_VoiceRow] = []
        self._voice_ids: dict[tuple[str, str | None, bool], str] = {}
        self._message_ids = (
            list(message_ids)
            if message_ids is not None
            else [entry.message_id for entry in entries]
        )
        self._media = list(media) if media is not None else self._collect_media(entries)
        self._count = (
            sum(1 for entry in entries if not self._is_poke_entry(entry))
            if count is None
            else count
        )
        self._private_peer_id = str(self.target_id) if self.is_private else None
        self._private_index = 0
        self._group_index = 0
        self._forward_index = 0

    def build(self) -> SerializedCQMessage:
        bot_uid = self._ensure_bot_user()
        body_lines: list[str] = []
        unread_message_count = self._count
        rendered_message_count = 0

        for entry in self.entries:
            if self._is_poke_entry(entry):
                body_lines.append(self._render_poke(entry))
                continue
            rendered_message_count += 1
            sender_uid = self._resolve_message_sender_uid(entry)
            body, forward_lines = self._render_message_body(entry)
            body_lines.append(
                "|".join(
                    ("M", self._escape(entry.message_id), sender_uid, self._escape(body))
                )
            )
            body_lines.extend(forward_lines)

        lines = [self._header(bot_uid, unread_message_count)]
        lines.extend(self._render_user_rows())
        lines.extend(self._render_image_rows())
        lines.extend(self._render_voice_rows())
        lines.extend(body_lines)
        lines.append("</CTX/1>")
        return SerializedCQMessage(
            chat_id=self.chat_id,
            text="\n".join(lines),
            message_ids=list(self._message_ids),
            media=list(self._media),
            count=unread_message_count,
        )

    def _header(self, bot_uid: str, unread_message_count: int) -> str:
        parts = ["<CTX/1"]
        if self.is_private:
            parts.append(f"p:{self.target_id}")
        else:
            parts.append(f"g:{self.target_id}")
        parts.append(f"bot:{bot_uid}")
        parts.append(f"n:{unread_message_count}")
        return " ".join(parts) + ">"

    @staticmethod
    def _is_poke_entry(entry: MessageEntry) -> bool:
        return entry.metadata.get("event_kind") == "poke"

    def _ensure_bot_user(self) -> str:
        return self._ensure_user(
            self.self_id,
            self.self_id or "",
            sender_nickname=self.self_nickname or "",
            is_bot=True,
        )

    def _display_name_priority(
        self,
        *,
        sender_name: str,
        sender_nickname: str = "",
        sender_card: str = "",
    ) -> tuple[str, int]:
        sender_id_like = string_value(sender_name) or ""
        nickname = string_value(sender_nickname)
        card = string_value(sender_card)
        if self.is_private:
            if nickname is not None:
                return nickname, 1
            return sender_id_like, 0
        if card is not None:
            return card, 2
        if nickname is not None:
            return nickname, 1
        return sender_id_like, 0

    def _resolve_message_sender_uid(self, entry: MessageEntry) -> str:
        return self._ensure_user(
            entry.sender_id,
            entry.sender_name,
            sender_nickname=entry.sender_nickname,
            sender_card=entry.sender_card,
            is_bot=entry.is_from_self,
        )

    def _render_poke(self, entry: MessageEntry) -> str:
        sender_uid = self._resolve_message_sender_uid(entry)
        return "|".join(("P", self._escape(entry.message_id), sender_uid))

    def _render_message_body(self, entry: MessageEntry) -> tuple[str, list[str]]:
        body, forward_lines = self._render_body_parts(
            content=entry.content,
            render_segments=entry.render_segments,
            media_refs=entry.media,
            media_items=entry.media_items,
            forwards=list(entry.expanded_forwards),
        )
        if entry.reply_to_message_id:
            body = f"^{entry.reply_to_message_id} {body}".strip()
        return body, forward_lines

    def _render_body_parts(
        self,
        *,
        content: str,
        render_segments: list[dict[str, str]],
        media_refs: list[str],
        media_items: list[dict[str, str]],
        forwards: list[ForwardEntry],
    ) -> tuple[str, list[str]]:
        if not render_segments:
            return self._render_legacy_body(content, media_refs, media_items, forwards)

        body_parts: list[str] = []
        forward_lines: list[str] = []
        for segment in render_segments:
            segment_type = segment.get("type")
            if segment_type == "text":
                body_parts.append(segment.get("text", ""))
                continue
            if segment_type == "mention_all":
                body_parts.append("@all")
                continue
            if segment_type == "mention":
                mention_uid = self._ensure_user(
                    segment.get("user_id"),
                    segment.get("user_id") or "",
                    is_bot=segment.get("user_id") == self.self_id,
                )
                body_parts.append(f"@{mention_uid}")
                continue
            if segment_type == "image":
                media_ref = self._legacy_image_ref_from_segment(
                    segment,
                    media_refs,
                    media_items,
                )
                if media_ref is not None:
                    body_parts.append(f"[{self._ensure_image(media_ref)}]")
                else:
                    body_parts.append("[image]")
                continue
            if segment_type == "voice":
                media_item = self._media_item_from_segment(segment, media_items)
                if media_item is None:
                    continue
                body_parts.append(f"[{self._ensure_voice(media_item)}]")
                continue
            if segment_type == "forward":
                fid, lines = self._next_forward_lines(forwards, len(forward_lines))
                if fid is None:
                    body_parts.append("[forward]")
                else:
                    body_parts.append(f"[F:{fid}]")
                    forward_lines.extend(lines)
        return "".join(body_parts).strip(), forward_lines

    def _render_legacy_body(
        self,
        content: str,
        media_refs: list[str],
        media_items: list[dict[str, str]],
        forwards: list[ForwardEntry],
    ) -> tuple[str, list[str]]:
        body = content
        for media_ref in media_refs:
            body = self._replace_first_placeholder(body, f"[{self._ensure_image(media_ref)}]")
        for media_item in media_items:
            if media_item.get("type") != "record":
                continue
            body = body.replace("[voice]", f"[{self._ensure_voice(media_item)}]", 1)
        forward_lines: list[str] = []
        for forward in forwards:
            fid = f"f{self._forward_index}"
            self._forward_index += 1
            body = body.replace("[forward]", f"[F:{fid}]", 1)
            forward_lines.extend(self._render_forward(fid, forward))
        return body, forward_lines

    def _next_forward_lines(
        self,
        forwards: list[ForwardEntry],
        _existing_line_count: int,
    ) -> tuple[str | None, list[str]]:
        if not forwards:
            return None, []
        forward = forwards.pop(0)
        fid = f"f{self._forward_index}"
        self._forward_index += 1
        return fid, self._render_forward(fid, forward)

    def _render_forward(self, fid: str, forward: ForwardEntry) -> list[str]:
        lines = [self._render_forward_container(fid, forward)]
        message_id_to_index: dict[str, int] = {}
        for index, node in enumerate(forward.nodes):
            if node.message_id:
                message_id_to_index[node.message_id] = index
        for index, node in enumerate(forward.nodes):
            lines.append(self._render_forward_node(index, node, message_id_to_index))
        return lines

    def _render_forward_container(self, fid: str, forward: ForwardEntry) -> str:
        row = ["F", fid, str(len(forward.nodes))]
        if forward.summary:
            row.append(self._escape(forward.summary))
        if forward.unresolved:
            row.append("!")
        return "|".join(row)

    def _render_forward_node(
        self,
        index: int,
        node: ForwardNodeEntry,
        message_id_to_index: dict[str, int],
    ) -> str:
        sender_uid = self._ensure_user(
            node.sender_id,
            node.sender_name,
            sender_nickname=node.sender_nickname,
            sender_card=node.sender_card,
        )
        body, _ = self._render_body_parts(
            content=node.content,
            render_segments=node.render_segments,
            media_refs=node.media,
            media_items=node.media_items,
            forwards=[],
        )
        reply_index = message_id_to_index.get(node.reply_to_message_id or "")
        if reply_index is not None:
            body = f"^{reply_index} {body}".strip()
        return "|".join(("N", str(index), sender_uid, self._escape(body)))

    @staticmethod
    def _media_item_from_segment(
        segment: dict[str, str],
        media_items: list[dict[str, str]],
    ) -> dict[str, str] | None:
        raw_index = segment.get("index")
        if raw_index is None:
            return None
        try:
            index = int(raw_index)
        except ValueError:
            return None
        if 0 <= index < len(media_items):
            return media_items[index]
        return None

    @staticmethod
    def _legacy_image_ref_from_segment(
        segment: dict[str, str],
        media_refs: list[str],
        media_items: list[dict[str, str]],
    ) -> str | None:
        raw_index = segment.get("index")
        if raw_index is None:
            return None
        try:
            target_index = int(raw_index)
        except ValueError:
            return None
        image_index = -1
        for index in range(min(target_index + 1, len(media_items))):
            if media_items[index].get("type") != "image":
                continue
            image_index += 1
        if 0 <= image_index < len(media_refs):
            return media_refs[image_index]
        return None

    @classmethod
    def _media_ref_from_item(cls, media_item: dict[str, str] | None) -> str | None:
        if media_item is None:
            return None
        for key in ("local_file_uri", "transcription_local_file_uri", "url", "file"):
            media_ref = string_value(media_item.get(key))
            if media_ref is not None:
                return media_ref
        return None

    @staticmethod
    def _collect_media(entries: list[MessageEntry]) -> list[str]:
        media: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            if entry.metadata.get("event_kind") == "poke":
                continue
            for media_ref in entry.media:
                if media_ref in seen:
                    continue
                seen.add(media_ref)
                media.append(media_ref)
        return media

    def _ensure_user(
        self,
        sender_id: str | None,
        sender_name: str,
        *,
        sender_nickname: str = "",
        sender_card: str = "",
        is_bot: bool = False,
    ) -> str:
        normalized_sender_id = sender_id or ""
        display_name, display_priority = self._display_name_priority(
            sender_name=sender_name or normalized_sender_id,
            sender_nickname=sender_nickname,
            sender_card=sender_card,
        )
        key = (normalized_sender_id, is_bot)
        existing_uid = self._user_uids.get(key)
        if existing_uid is not None:
            existing_row = self._user_rows[key]
            if display_priority > existing_row.name_priority or (
                display_priority == existing_row.name_priority
                and existing_row.name == normalized_sender_id
                and display_name != normalized_sender_id
            ):
                existing_row.name = display_name
                existing_row.name_priority = display_priority
            return existing_uid

        if self.is_private:
            if is_bot:
                uid = "me"
            elif sender_id is not None and sender_id == self._private_peer_id:
                uid = "peer"
            else:
                uid = f"u{self._private_index}"
                self._private_index += 1
        else:
            if is_bot:
                uid = "u0"
            else:
                group_offset = 1 if self.self_id is not None else 0
                uid = f"u{self._group_index + group_offset}"
                self._group_index += 1

        user = _UserRow(
            uid=uid,
            qq=normalized_sender_id,
            name=display_name,
            is_bot=is_bot,
            name_priority=display_priority,
        )
        self._users.append(user)
        self._user_rows[key] = user
        self._user_uids[key] = uid
        return uid

    def _ensure_image(self, media_ref: str) -> str:
        filename = self._basename(media_ref)
        existing_iid = self._image_ids.get(filename)
        if existing_iid is not None:
            return existing_iid
        iid = f"i{len(self._images)}"
        self._images.append(_ImageRow(iid=iid, filename=filename))
        self._image_ids[filename] = iid
        return iid

    def _ensure_voice(self, media_item: dict[str, str]) -> str:
        media_ref = string_value(media_item.get("transcription_local_file_uri"))
        if media_ref is None and media_item.get("transcription_status") != "failed":
            media_ref = string_value(media_item.get("local_file_uri"))
        if media_ref is None:
            media_ref = string_value(media_item.get("file")) or "voice"
        filename = self._basename(media_ref)
        transcription_text = string_value(media_item.get("transcription_text"))
        transcription_failed = media_item.get("transcription_status") == "failed"
        key = (filename, transcription_text, transcription_failed)
        existing_vid = self._voice_ids.get(key)
        if existing_vid is not None:
            return existing_vid
        vid = f"v{len(self._voices)}"
        self._voices.append(
            _VoiceRow(
                vid=vid,
                filename=filename,
                transcription_text=transcription_text,
                transcription_failed=transcription_failed,
            )
        )
        self._voice_ids[key] = vid
        return vid

    def _render_user_rows(self) -> list[str]:
        return ["|".join(self._user_row_parts(user)) for user in self._users]

    def _user_row_parts(self, user: _UserRow) -> list[str]:
        parts = ["U", user.uid, self._escape(user.qq), self._escape(user.name)]
        if user.is_bot:
            parts.append("bot")
        return parts

    def _render_image_rows(self) -> list[str]:
        return [f"I|{image.iid}|{self._escape(image.filename)}" for image in self._images]

    def _render_voice_rows(self) -> list[str]:
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
    def _replace_first_placeholder(cls, content: str, replacement: str) -> str:
        for placeholder in ("[image]", "[video]", "[file]", "[voice]"):
            if placeholder in content:
                return content.replace(placeholder, replacement, 1)
        return f"{content}{replacement}" if content else replacement

    @staticmethod
    def _basename(media_ref: str) -> str:
        parsed = urlparse(media_ref)
        candidate = parsed.path or media_ref
        name = PurePosixPath(candidate).name
        return name or media_ref

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")


def serialize_chat_entries(
    chat_id: str,
    entries: list[MessageEntry],
    *,
    self_id: str | None,
    self_nickname: str | None = None,
) -> SerializedCQMessage | None:
    """Serialize buffered chat entries into one CTX block."""
    if not entries:
        return None
    return _CTXBuilder(
        chat_id=chat_id,
        entries=entries,
        self_id=self_id,
        self_nickname=self_nickname,
    ).build()


def serialize_buffer_chat(
    buffer: Buffer,
    chat_id: str,
    *,
    self_id: str | None,
    self_nickname: str | None = None,
    extra_reply_targets: list[MessageEntry] | None = None,
) -> SerializedCQMessage | None:
    """Serialize unread buffered chat entries into one CTX block."""
    llm_entries = buffer.get_unconsumed_llm_chat_entries(chat_id)
    if not llm_entries:
        return None

    quoted_entries: list[MessageEntry] = []
    seen_message_ids = {entry.message_id for entry in llm_entries}
    for reply_target in extra_reply_targets or []:
        if reply_target.message_id in seen_message_ids:
            continue
        quoted_entries.append(reply_target)
        seen_message_ids.add(reply_target.message_id)

    for entry in llm_entries:
        if not entry.reply_to_message_id or entry.reply_to_message_id in seen_message_ids:
            continue
        reply_target = buffer.get(chat_id, entry.reply_to_message_id)
        if reply_target is None:
            continue
        quoted_entries.append(reply_target)
        seen_message_ids.add(reply_target.message_id)

    unread_message_count = sum(
        1 for entry in llm_entries if entry.metadata.get("event_kind") != "poke"
    )
    entries = quoted_entries + llm_entries
    return _CTXBuilder(
        chat_id=chat_id,
        entries=entries,
        self_id=self_id,
        self_nickname=self_nickname,
        message_ids=[entry.message_id for entry in llm_entries],
        media=_CTXBuilder._collect_media(llm_entries),
        count=unread_message_count,
    ).build()
