"""Focused tests for the new kernel inbound runtime path."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from loguru import logger
from nanobot.bus.queue import MessageBus
from pydantic import BaseModel

from nanobot_channel_anon.adapters import onebot_transport as onebot_transport_module
from nanobot_channel_anon.adapters.onebot_mapper import OneBotMapper
from nanobot_channel_anon.adapters.onebot_state import OneBotStateAdapter
from nanobot_channel_anon.adapters.onebot_transport import OneBotTransport
from nanobot_channel_anon.config import AnonConfig
from nanobot_channel_anon.domain import ConversationRef, NormalizedMessage
from nanobot_channel_anon.kernel import Kernel
from nanobot_channel_anon.onebot import OneBotAPIRequest


class FakeConnection:
    """测试用 WebSocket 连接."""

    def __init__(self) -> None:
        """初始化收发缓冲."""
        self.incoming: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.sent_json: list[dict[str, object]] = []
        self.closed = False

    async def receive_json(self) -> dict[str, object]:
        """读取下一条 JSON 消息."""
        payload = await self.incoming.get()
        if payload is None:
            raise EOFError
        return payload

    async def send_json(self, payload: dict[str, object]) -> None:
        """记录一次发送."""
        self.sent_json.append(payload)

    async def close(self) -> None:
        """关闭连接并唤醒读取方."""
        self.closed = True
        await self.incoming.put(None)

    async def push_event(self, payload: dict[str, object]) -> None:
        """向读取循环注入一条入站事件."""
        await self.incoming.put(payload)


class RecordingTransport:
    """测试用出站传输层."""

    def __init__(self) -> None:
        """初始化请求记录器."""
        self.requests: list[list[dict[str, object]]] = []

    async def start(self) -> None:
        """满足内核接口, 无需额外行为."""

    async def stop(self) -> None:
        """满足内核接口, 无需额外行为."""

    async def send_requests(self, requests: list[BaseModel]) -> None:
        """记录一次 OneBot 请求批次."""
        self.requests.append(
            [request.model_dump(exclude_none=False) for request in requests]
        )


def _config(**overrides: object) -> AnonConfig:
    """构造测试配置."""
    data: dict[str, object] = {
        "enabled": True,
        "allow_from": ["group:456", "private:123"],
        "ws_url": "ws://127.0.0.1:3001",
        "private_trigger_prob": 1.0,
        "group_trigger_prob": 0.0,
        "trigger_on_at": True,
        "trigger_on_reply": True,
    }
    data.update(overrides)
    return AnonConfig.model_validate(data)


def _group_message(
    *,
    content: str = "hello",
    message_id: str = "m1",
) -> NormalizedMessage:
    """构造一条标准化群消息."""
    return NormalizedMessage(
        message_id=message_id,
        conversation=ConversationRef(kind="group", id="456"),
        sender_id="123",
        sender_name="Alice",
        content=content,
    )


async def _wait_for_sent_action(
    connection: FakeConnection,
    index: int,
    action: str,
) -> dict[str, object]:
    """等待指定下标的发送请求出现并校验动作名."""
    for _ in range(20):
        if len(connection.sent_json) > index:
            request = connection.sent_json[index]
            assert request["action"] == action
            return request
        await asyncio.sleep(0)
    raise AssertionError(f"expected outbound action: {action}")


async def _wait_for_condition(
    predicate: Callable[[], bool],
    *,
    retries: int = 20,
) -> None:
    """等待某个断言条件变为真."""
    for _ in range(retries):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("expected condition to become true")


async def _complete_startup_sync(
    connection: FakeConnection,
    *,
    nickname: str = "AnonBot",
    group_list: list[dict[str, object]] | None = None,
    shut_lists: dict[str, list[dict[str, object]]] | None = None,
) -> None:
    """按当前发送队列依次回放启动同步响应."""
    first = await _wait_for_sent_action(connection, 0, "get_login_info")
    echo = first["echo"]
    assert isinstance(echo, str)
    await connection.push_event(
        {
            "echo": echo,
            "status": "ok",
            "retcode": 0,
            "data": {"user_id": 42, "nickname": nickname},
        }
    )

    second = await _wait_for_sent_action(connection, 1, "get_group_list")
    echo = second["echo"]
    assert isinstance(echo, str)
    await connection.push_event(
        {
            "echo": echo,
            "status": "ok",
            "retcode": 0,
            "data": [] if group_list is None else group_list,
        }
    )

    shut_lists = {} if shut_lists is None else shut_lists
    expected_groups = [
        str(item["group_id"])
        for item in ([] if group_list is None else group_list)
        if isinstance(item, dict)
        and item.get("group_id") is not None
        and str(item["group_id"]) in shut_lists
    ]
    for offset, group_id in enumerate(expected_groups, start=2):
        request = await _wait_for_sent_action(connection, offset, "get_group_shut_list")
        params = request.get("params")
        assert isinstance(params, dict)
        assert str(params["group_id"]) == group_id
        echo = request["echo"]
        assert isinstance(echo, str)
        await connection.push_event(
            {
                "echo": echo,
                "status": "ok",
                "retcode": 0,
                "data": shut_lists.get(group_id, []),
            }
        )
    await asyncio.sleep(0)


def test_transport_event_flows_through_mapper_kernel_and_bus() -> None:
    """传输层收到 OneBot 事件后应走完整新链路并发布到总线."""

    async def case() -> None:
        bus = MessageBus()
        state = OneBotStateAdapter()
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(
            config=_config(),
            mapper=OneBotMapper(),
            state=state,
            connect=connect,
        )
        kernel = Kernel(config=_config(), bus=bus, transport=transport, state=state)

        task = asyncio.create_task(kernel.start())
        await _complete_startup_sync(connection)
        await connection.push_event(
            {
                "self_id": 42,
                "post_type": "message",
                "message_type": "group",
                "message_id": 1001,
                "group_id": 456,
                "user_id": 123,
                "message": [
                    {"type": "at", "data": {"qq": "42"}},
                    {"type": "text", "data": {"text": " hello kernel "}},
                ],
                "sender": {"user_id": 123, "nickname": "Alice"},
                "time": 1776818315,
            }
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        await kernel.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert inbound.channel == "anon"
        assert inbound.chat_id == "group:456"
        assert inbound.sender_id == "123"
        assert inbound.content.startswith("<CTX/1 g:456 bot:u0 n:1>")
        assert "M|1001|u1|@u0 hello kernel" in inbound.content
        assert "U|u2|42|42" not in inbound.content
        assert inbound.metadata["trigger_reason"] == "mentioned_self"
        assert inbound.metadata["message"]["message_id"] == "1001"
        assert inbound.metadata["presented"]["conversation"]["id"] == "456"
        assert inbound.metadata["ctx_message_ids"] == ["1001"]
        assert inbound.metadata["ctx_count"] == 1
        assert state.self_id == "42"

    asyncio.run(case())


def test_transport_send_requests_completes_on_echo_response() -> None:
    """真实传输层出站请求应在收到 echo 响应后完成."""

    async def case() -> None:
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)
        task = asyncio.create_task(transport.start())
        await _complete_startup_sync(connection)

        send_task = asyncio.create_task(
            transport.send_requests(
                [
                    OneBotAPIRequest(
                        action="send_private_msg",
                        params={"user_id": 123, "message": "hello"},
                    )
                ]
            )
        )
        await asyncio.sleep(0)

        request = connection.sent_json[-1]
        assert request["action"] == "send_private_msg"
        echo = request["echo"]
        assert isinstance(echo, str)
        assert echo

        await connection.push_event({"echo": echo, "status": "ok", "retcode": 0})
        await asyncio.wait_for(send_task, timeout=1.0)

        await transport.stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(case())


def test_transport_send_requests_raises_on_failed_echo_response() -> None:
    """真实传输层出站请求不应把失败动作响应当作成功."""

    async def case() -> None:
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)
        task = asyncio.create_task(transport.start())
        await _complete_startup_sync(connection)

        send_task = asyncio.create_task(
            transport.send_requests(
                [
                    OneBotAPIRequest(
                        action="send_private_msg",
                        params={"user_id": 123, "message": "hello"},
                    )
                ]
            )
        )
        await asyncio.sleep(0)

        request = connection.sent_json[-1]
        assert request["action"] == "send_private_msg"
        echo = request["echo"]
        await connection.push_event({"echo": echo, "status": "failed", "retcode": 100})

        try:
            await asyncio.wait_for(send_task, timeout=1.0)
        except RuntimeError as exc:
            assert str(exc) == "OneBot action failed: send_private_msg"
        else:
            raise AssertionError("expected OneBot action failure")

        await transport.stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(case())


def test_transport_send_requests_times_out_without_echo_response() -> None:
    """真实传输层出站请求等待 echo 响应时应有超时上界."""

    async def case() -> None:
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)
        task = asyncio.create_task(transport.start())
        await _complete_startup_sync(connection)

        original_timeout = onebot_transport_module._RESPONSE_TIMEOUT_SECONDS
        onebot_transport_module._RESPONSE_TIMEOUT_SECONDS = 0.01
        try:
            try:
                await transport.send_requests(
                    [
                        OneBotAPIRequest(
                            action="send_private_msg",
                            params={"user_id": 123, "message": "hello"},
                        )
                    ]
                )
            except RuntimeError as exc:
                assert str(exc) == "OneBot action timed out: send_private_msg"
            else:
                raise AssertionError("expected OneBot action timeout")
        finally:
            onebot_transport_module._RESPONSE_TIMEOUT_SECONDS = original_timeout
            await transport.stop()
            await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(case())


def test_transport_startup_sync_restores_identity_mute_scan_and_logs() -> None:
    """启动链路应恢复 bot 信息、白名单群禁言扫描与关键日志."""

    async def case() -> None:
        connection = FakeConnection()
        records: list[str] = []
        sink_id = logger.add(records.append, format="{message}")

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(
            config=_config(allow_from=["group:456"]),
            connect=connect,
        )
        task = asyncio.create_task(transport.start())
        muted_until = int(time.time()) + 120
        await _complete_startup_sync(
            connection,
            group_list=[{"group_id": 456}, {"group_id": 999}],
            shut_lists={"456": [{"uin": 42, "shutUpTime": muted_until}]},
        )

        await _wait_for_condition(lambda: transport.state.self_id == "42")
        assert transport.state.self_nickname == "AnonBot"
        await _wait_for_condition(lambda: transport.state.is_group_muted("456"))

        await transport.stop()
        await asyncio.wait_for(task, timeout=1.0)
        logger.remove(sink_id)

        log_output = "\n".join(records)
        assert "Anon OneBot transport starting: ws://127.0.0.1:3001" in log_output
        assert "Anon WebSocket connected: ws://127.0.0.1:3001" in log_output
        assert "Anon bot identity loaded: self_id=42 nickname=AnonBot" in log_output
        assert "Anon mute sync discovered groups: 1" in log_output
        assert "Anon mute sync marked group as muted: group_id=456" in log_output
        assert "Anon mute sync completed: scanned_groups=1 muted_groups=1" in log_output

    asyncio.run(case())


def test_transport_startup_sync_scans_all_groups_when_allow_all_enabled() -> None:
    """allow_from=["*"] 时应扫描启动期可见的全部群禁言状态."""

    async def case() -> None:
        connection = FakeConnection()
        records: list[str] = []
        sink_id = logger.add(records.append, format="{message}")

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(
            config=_config(allow_from=["*"]),
            connect=connect,
        )
        task = asyncio.create_task(transport.start())
        muted_until = int(time.time()) + 120
        await _complete_startup_sync(
            connection,
            group_list=[{"group_id": 456}, {"group_id": 999}],
            shut_lists={
                "456": [{"uin": 42, "shutUpTime": muted_until}],
                "999": [{"uin": 42, "shutUpTime": muted_until}],
            },
        )

        await _wait_for_condition(lambda: transport.state.self_id == "42")
        await _wait_for_condition(lambda: transport.state.is_group_muted("456"))
        await _wait_for_condition(lambda: transport.state.is_group_muted("999"))

        await transport.stop()
        await asyncio.wait_for(task, timeout=1.0)
        logger.remove(sink_id)

        log_output = "\n".join(records)
        assert "Anon mute sync discovered groups: 2" in log_output
        assert "Anon mute sync marked group as muted: group_id=456" in log_output
        assert "Anon mute sync marked group as muted: group_id=999" in log_output
        assert "Anon mute sync completed: scanned_groups=2 muted_groups=2" in log_output

    asyncio.run(case())


def test_transport_group_ban_notice_updates_mute_state_without_bus_publish() -> None:
    """Bot 自身相关的群禁言通知应更新状态且不进入入站总线."""

    async def case() -> None:
        bus = MessageBus()
        state = OneBotStateAdapter()
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(
            config=_config(allow_from=["group:456"]),
            state=state,
            connect=connect,
        )
        kernel = Kernel(
            config=_config(allow_from=["group:456"]),
            bus=bus,
            transport=transport,
            state=state,
        )

        task = asyncio.create_task(kernel.start())
        await _complete_startup_sync(connection)

        await connection.push_event(
            {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "ban",
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
                "time": int(time.time()),
                "duration": 60,
            }
        )
        await asyncio.sleep(0)
        assert state.is_group_muted("456") is True
        assert bus.inbound_size == 0

        await connection.push_event(
            {
                "post_type": "message",
                "message_type": "group",
                "message_id": 2001,
                "group_id": 456,
                "user_id": 123,
                "message": [{"type": "text", "data": {"text": "hello"}}],
                "sender": {"user_id": 123, "nickname": "Alice"},
                "time": int(time.time()),
            }
        )
        await asyncio.sleep(0)
        assert bus.inbound_size == 0

        await connection.push_event(
            {
                "post_type": "notice",
                "notice_type": "group_ban",
                "sub_type": "lift_ban",
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
            }
        )
        await asyncio.sleep(0)
        assert state.is_group_muted("456") is False
        assert bus.inbound_size == 0

        await kernel.stop()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(case())


def test_transport_failed_initial_connect_leaves_instance_restartable() -> None:
    """首次连接失败后应保持停止态并允许后续重试启动."""

    async def case() -> None:
        connection = FakeConnection()
        attempts = 0

        async def connect() -> FakeConnection:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("dial failed")
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)

        try:
            await transport.start()
        except RuntimeError as exc:
            assert str(exc) == "dial failed"
        else:
            raise AssertionError("expected initial connect failure")

        assert transport.is_running is False

        task = asyncio.create_task(transport.start())
        await _complete_startup_sync(connection)
        assert transport.is_running is True
        assert attempts == 2

        await connection.push_event({"echo": "ignored", "status": "ok", "retcode": 0})
        await asyncio.sleep(0)

        await transport.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert transport.is_running is False

    asyncio.run(case())


def test_transport_non_eof_reader_failure_unwinds_start() -> None:
    """读取循环遇到非 EOF 致命错误时应让 start 退出而不是挂起."""

    class CrashingConnection(FakeConnection):
        """首次读取时抛出非 EOF 异常."""

        async def receive_json(self) -> dict[str, object]:
            raise RuntimeError("boom")

    async def case() -> None:
        connection = CrashingConnection()

        async def connect() -> CrashingConnection:
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)
        task = asyncio.create_task(transport.start())

        try:
            await asyncio.wait_for(task, timeout=1.0)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected reader failure to stop transport")

        assert transport.is_running is False
        assert connection.closed is True

    asyncio.run(case())


def test_kernel_enforces_allowlist_before_publish() -> None:
    """不在 allowlist 内的消息不应进入总线."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(allow_from=["private:999"]),
            bus=bus,
            transport=RecordingTransport(),
        )

        await kernel.handle_inbound(_group_message())

        assert bus.inbound_size == 0

    asyncio.run(case())


def test_kernel_enforces_trigger_policy_before_publish() -> None:
    """未命中触发策略的群消息不应进入总线."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(allow_from=["group:456"], trigger_on_at=False),
            bus=bus,
            transport=RecordingTransport(),
        )

        await kernel.handle_inbound(_group_message(content="plain text"))

        assert bus.inbound_size == 0

    asyncio.run(case())


def test_kernel_marks_reply_to_self_from_context_before_policy() -> None:
    """回复机器人历史消息时应先标记 reply_to_self 再触发."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(allow_from=["group:456"], trigger_on_at=False),
            bus=bus,
            transport=RecordingTransport(),
        )
        assistant_message = NormalizedMessage(
            message_id="assistant-1",
            conversation=ConversationRef(kind="group", id="456"),
            sender_id="42",
            sender_name="Bot",
            content="earlier reply",
            from_self=True,
        )
        await kernel.remember_message(assistant_message)

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="got it",
                reply_to_message_id="assistant-1",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert inbound.metadata["trigger_reason"] == "reply_to_self"
        assert inbound.metadata["message"]["reply_to_self"] is True
        assert "M|assistant-1|u0|earlier reply" in inbound.content
        assert "M|user-1|u1|^assistant-1 got it" in inbound.content

    asyncio.run(case())


def test_kernel_publishes_normal_slash_command_without_trigger_match() -> None:
    """普通斜杠命令应直接进入总线, 不受触发策略阻断."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                trigger_on_reply=False,
                group_trigger_prob=0.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
        )

        await kernel.handle_inbound(_group_message(content="/help status"))

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert inbound.chat_id == "group:456"
        assert inbound.metadata["trigger_reason"] == "slash_command"
        assert inbound.metadata["command"] == {
            "name": "help",
            "args": ["status"],
            "admin_only": False,
        }
        assert inbound.metadata["message"]["content"] == "/help status"

    asyncio.run(case())


def test_kernel_rejects_non_admin_slash_admin_command_at_runtime() -> None:
    """非管理员的 admin 命令不应绕过运行时策略进入总线."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                super_admins=["999"],
                trigger_on_at=False,
                trigger_on_reply=False,
                group_trigger_prob=0.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
        )

        await kernel.handle_inbound(_group_message(content="/admin reload"))

        assert bus.inbound_size == 0

    asyncio.run(case())


def test_kernel_publishes_admin_slash_command_for_super_admin() -> None:
    """超级管理员的 admin 命令应在运行时被识别并投递."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                super_admins=["123"],
                trigger_on_at=False,
                trigger_on_reply=False,
                group_trigger_prob=0.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
        )

        await kernel.handle_inbound(_group_message(content="/admin reload now"))

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert inbound.metadata["trigger_reason"] == "slash_command"
        assert inbound.metadata["command"] == {
            "name": "reload",
            "args": ["now"],
            "admin_only": True,
        }

    asyncio.run(case())
