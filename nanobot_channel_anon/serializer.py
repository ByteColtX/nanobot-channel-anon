# ruff: noqa: RUF002, E501

r"""CQMSG/1 统一上下文格式设计.

# CQMSG/1 统一上下文格式设计

---

## 一、格式总览

```
<CQMSG/1 [g:{group_id}] bot:{bot_uid} n:{count}>
U|{uid}|{qq}|{name}[|bot]
I|{iid}|{filename}
M|{msg_id}|{sender}|{body}
F|{fid}|{node_count}[|{summary}]
N|{fid}.{idx}|{sender_ref}[|{src}]|{body}
</CQMSG/1>
```

### Header 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `g:{group_id}` | 可选 | 有此参数 = 群聊；无此参数 = 私聊 |
| `bot:{bot_uid}` | 必填 | bot 在 U 表中的 uid（群聊为 `uN`，私聊固定为 `me`）|
| `n:{count}` | 必填 | 本段上下文包含的 M 行总数 |

---

## 二、行类型定义

### U — 用户表

```
U|{uid}|{qq}|{name}[|bot]
```

- 每个出现过的用户只定义一次
- **私聊保留别名**：bot 自身固定 uid = `me`，对方固定 uid = `peer`
- 群聊第三方用户从 `u0` 开始递增
- 转发中出现的外部用户也写入 U 表

### I — 图片表

```
I|{iid}|{filename}
```

- `iid` 从 `i0` 开始递增
- 只保留文件名，去除本地路径
- 同一文件名在同一段上下文中只定义一次

### M — 消息行

```
M|{msg_id}|{sender}|{body}
```

- `msg_id` 直接保留原始值，不做字典化
- `sender` 为 U 表中的 `uid`（私聊则为 `me` / `peer`）
- body 使用短记号（见第三节）

### F — 转发容器

```
F|{fid}|{node_count}[|{summary}]
```

- `fid` 从 `f0` 开始递增
- F 行**必须紧跟**引用它的 M 行之后
- `summary` 可选，来自转发卡片的标题文字

### N — 转发节点

```
N|{fid}.{idx}|{sender_ref}[|{src}]|{body}
```

| 字段 | 说明 |
|------|------|
| `fid.idx` | 所属转发容器及节点序号，从 0 开始 |
| `sender_ref` | U 表 uid（可为 `me`/`peer`/`uN`）|
| `src` | 仅私聊时填写，节点原始来源，如 `g:123456789`；群聊省略此列 |
| `body` | 同 M 行，支持短记号 |

---

## 三、Body 短记号

| 短记号 | 含义 |
|--------|------|
| `@uN` | at 某用户 |
| `>m:{msg_id}` | 回复当前会话中的消息（可执行）|
| `>n:{fid}.{idx}` | forward 内节点互相引用（仅用于理解，不可执行）|
| `[iN]` | 引用图片 |
| `[F:fN]` | 当前消息包含一段 forward |

**作用域规则：**
- `>m:*` 是当前会话可执行的 reply 目标
- `>n:*` 仅表达 forward 内部关系，模型**不应**对此发起 reply
- 私聊 live 消息中 `@` 不作为可执行动作；forward 内出现的 `@uN` 只当转发原文理解

---

## 四、转义规则

| 原字符 | 转义写法 |
|--------|---------|
| `\` | `\\` |
| `\|` | `\|` |
| 换行 `\n` | `\n`（字面两字符）|

---

## 五、示例

### 群聊

```
<CQMSG/1 g:123456789 bot:u2 n:4>
U|u0|100000001|示例成员甲
U|u1|100000002|示例成员乙
U|u2|100000003|示例机器人|bot
U|u3|100000004|示例成员丙
I|i0|sample-image.png
M|1489689854|u1|[i0]
M|520815151|u0|>m:1489689854 @u2 这张图已收到
M|200000001|u3|[F:f0]
F|f0|3|一段转发消息示例
N|f0.0|u1||今天先同步一下进度
N|f0.1|u3||>n:f0.0 我这边继续跟进
N|f0.2|u3||晚点再统一回复
</CQMSG/1>
```

### 私聊

```
<CQMSG/1 bot:me n:4>
U|me|100000003|示例机器人|bot
U|peer|100000005|示例联系人
U|u0|100000001|示例成员甲
U|u1|100000004|示例成员丙
I|i0|sample-image.png
M|9001001|peer|请看下这张图 [i0]
M|9001002|me|>m:9001001 已收到
M|9001003|peer|[F:f0]
F|f0|3|群聊转发内容示例
N|f0.0|u0|g:123456789|今天先同步一下进度
N|f0.1|u1|g:123456789|>n:f0.0 我这边继续跟进
N|f0.2|u1|g:123456789|晚点再统一回复
</CQMSG/1>
```

---

## 六、约束与边界情况

| 场景 | 处理规则 |
|------|---------|
| reply 一条含 forward 的消息 | `>m:{msg_id}` 引用 M 行，对应 F/N 行跟在 M 后，不重复内联 |
| forward 内部互相 reply | 用 `>n:{fid}.{idx}`，不可展开为 live reply |
| 外层 reply 嵌套 forward | 只引用最外层 msg_id，内部 forward 正常展开为 F/N |
| 同一用户多次出现 | U 表只写一次，body 里复用相同 uid |
| 同一图片多次出现 | I 表只写一次，body 里复用相同 iid |
| 用户名含 `\|` 或 `\` | 按转义规则处理后写入 U 表 |


"""

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
    """A serialized CQMSG block plus the message IDs and media it consumed."""

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


class _CQMSGBuilder:
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
        self._message_ids = (
            list(message_ids)
            if message_ids is not None
            else [entry.message_id for entry in entries]
        )
        self._media = list(media) if media is not None else self._collect_media(entries)
        self._count = len(self._message_ids) if count is None else count
        self._private_peer_id = str(self.target_id) if self.is_private else None
        self._private_index = 0
        self._group_index = 0
        self._forward_index = 0

    def build(self) -> SerializedCQMessage:
        bot_uid = self._ensure_bot_user()
        if not self.is_private:
            for entry in self.entries:
                self._resolve_message_sender_uid(entry)
                self._collect_forward_users(entry.expanded_forwards)
        lines = [self._header(bot_uid)]
        message_lines: list[str] = []

        for entry in self.entries:
            sender_uid = self._resolve_message_sender_uid(entry)
            body, forward_lines = self._render_message_body(entry)
            message_lines.append(
                "|".join(
                    ("M", self._escape(entry.message_id), sender_uid, self._escape(body))
                )
            )
            message_lines.extend(forward_lines)

        lines.extend(self._render_user_rows())
        lines.extend(self._render_image_rows())
        lines.extend(message_lines)
        lines.append("</CQMSG/1>")
        return SerializedCQMessage(
            chat_id=self.chat_id,
            text="\n".join(lines),
            message_ids=list(self._message_ids),
            media=list(self._media),
            count=self._count,
        )

    def _header(self, bot_uid: str) -> str:
        if self.is_private:
            return f"<CQMSG/1 bot:{bot_uid} n:{len(self.entries)}>"
        return f"<CQMSG/1 g:{self.target_id} bot:{bot_uid} n:{len(self.entries)}>"

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

    def _render_message_body(self, entry: MessageEntry) -> tuple[str, list[str]]:
        body = entry.content
        body = self._replace_media_placeholders(body, entry.media)
        forward_lines: list[str] = []
        for forward in entry.expanded_forwards:
            fid = f"f{self._forward_index}"
            self._forward_index += 1
            body = body.replace("[forward]", f"[F:{fid}]", 1)
            forward_lines.extend(self._render_forward(fid, forward))
        if entry.reply_to_message_id:
            body = f">m:{entry.reply_to_message_id} {body}".strip()
        return body, forward_lines

    @staticmethod
    def _collect_media(entries: list[MessageEntry]) -> list[str]:
        """聚合同一 unread 窗口内应附带给模型的图片列表.

        当前只包含 live 消息的顶层图片；forward 节点图片仍只保留在
        CQMSG 文本引用中，不会进入多模态输入。这是有意为之，不是遗漏实现。

        """
        media: list[str] = []
        seen: set[str] = set()
        for entry in entries:
            for media_ref in entry.media:
                if media_ref in seen:
                    continue
                seen.add(media_ref)
                media.append(media_ref)
        return media

    def _render_forward(self, fid: str, forward: ForwardEntry) -> list[str]:
        lines = [self._render_forward_container(fid, forward)]
        message_id_to_ref: dict[str, str] = {}
        for index, node in enumerate(forward.nodes):
            if node.message_id:
                message_id_to_ref[node.message_id] = f">n:{fid}.{index}"
        for index, node in enumerate(forward.nodes):
            lines.append(self._render_forward_node(fid, index, node, message_id_to_ref))
        return lines

    def _collect_forward_users(self, forwards: list[ForwardEntry]) -> None:
        for forward in forwards:
            for node in forward.nodes:
                self._ensure_user(
                    node.sender_id,
                    node.sender_name,
                    sender_nickname=node.sender_nickname,
                    sender_card=node.sender_card,
                )

    def _render_forward_container(self, fid: str, forward: ForwardEntry) -> str:
        row = ["F", fid, str(len(forward.nodes))]
        if forward.summary:
            row.append(self._escape(forward.summary))
        return "|".join(row)

    def _render_forward_node(
        self,
        fid: str,
        index: int,
        node: ForwardNodeEntry,
        message_id_to_ref: dict[str, str],
    ) -> str:
        sender_uid = self._ensure_user(
            node.sender_id,
            node.sender_name,
            sender_nickname=node.sender_nickname,
            sender_card=node.sender_card,
        )
        body = self._replace_media_placeholders(node.content, node.media)
        reply_ref = message_id_to_ref.get(node.reply_to_message_id or "")
        if reply_ref:
            body = f"{reply_ref} {body}".strip()
        parts = ["N", f"{fid}.{index}", sender_uid]
        if self.is_private:
            parts.append(self._escape(node.source_chat_id or ""))
        parts.append(self._escape(body))
        return "|".join(parts)

    def _replace_media_placeholders(self, content: str, media_refs: list[str]) -> str:
        body = content
        for media_ref in media_refs:
            iid = self._ensure_image(media_ref)
            replacement = f"[{iid}]"
            body = self._replace_first_placeholder(body, replacement)
        return body

    @staticmethod
    def _replace_first_placeholder(content: str, replacement: str) -> str:
        for placeholder in ("[image]", "[video]", "[file]", "[voice]"):
            if placeholder in content:
                return content.replace(placeholder, replacement, 1)
        return f"{content}{replacement}" if content else replacement

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

    def _render_user_rows(self) -> list[str]:
        return [
            "|".join(self._user_row_parts(user))
            for user in self._users
        ]

    def _user_row_parts(self, user: _UserRow) -> list[str]:
        parts = ["U", user.uid, self._escape(user.qq), self._escape(user.name)]
        if user.is_bot:
            parts.append("bot")
        return parts

    def _render_image_rows(self) -> list[str]:
        return [
            f"I|{image.iid}|{self._escape(image.filename)}"
            for image in self._images
        ]

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
    """Serialize buffered chat entries into one CQMSG block."""
    if not entries:
        return None
    return _CQMSGBuilder(
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
    """Serialize unread buffered chat entries into one CQMSG block."""
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

    entries = quoted_entries + llm_entries
    return _CQMSGBuilder(
        chat_id=chat_id,
        entries=entries,
        self_id=self_id,
        self_nickname=self_nickname,
        message_ids=[entry.message_id for entry in llm_entries],
        media=_CQMSGBuilder._collect_media(llm_entries),
        count=len(llm_entries),
    ).build()
