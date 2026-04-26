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
from nanobot_channel_anon.domain import Attachment, ConversationRef, NormalizedMessage
from nanobot_channel_anon.kernel import Kernel
from nanobot_channel_anon.onebot import OneBotAPIRequest, OneBotRawEvent


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
        self.fetched_messages: dict[str, OneBotRawEvent] = {}
        self.get_message_calls: list[str] = []
        self.forward_messages: dict[str, object] = {}
        self.get_forward_message_calls: list[str] = []
        self.group_member_profiles: dict[tuple[str, str], dict[str, str]] = {}
        self.get_group_member_info_calls: list[tuple[str, str]] = []

    async def start(self) -> None:
        """满足内核接口, 无需额外行为."""

    async def stop(self) -> None:
        """满足内核接口, 无需额外行为."""

    async def send_requests(self, requests: list[BaseModel]) -> list[OneBotRawEvent]:
        """记录一次 OneBot 请求批次."""
        self.requests.append(
            [request.model_dump(exclude_none=False) for request in requests]
        )
        return []

    async def get_message(self, message_id: str) -> OneBotRawEvent | None:
        """按消息 ID 返回预置的回查结果."""
        self.get_message_calls.append(message_id)
        return self.fetched_messages.get(message_id)

    async def get_forward_message(self, forward_id: str) -> object | None:
        """按 forward_id 返回预置的合并转发载荷."""
        self.get_forward_message_calls.append(forward_id)
        return self.forward_messages.get(forward_id)

    async def get_group_member_info(
        self,
        group_id: str,
        user_id: str,
    ) -> dict[str, str] | None:
        """按群成员返回预置资料."""
        self.get_group_member_info_calls.append((group_id, user_id))
        return self.group_member_profiles.get((group_id, user_id))


class FakeInboundMediaEnricher:
    """测试用入站媒体增强器."""

    def __init__(self, enriched_message: NormalizedMessage | None = None) -> None:
        """保存预置增强结果."""
        self.enriched_message = enriched_message
        self.calls: list[str] = []

    async def enrich(self, message: NormalizedMessage) -> NormalizedMessage:
        """记录调用并返回增强结果."""
        self.calls.append(message.message_id)
        return self.enriched_message or message


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


def test_transport_get_message_returns_normalized_raw_event() -> None:
    """get_message() 应把 get_msg 响应转换为可映射的原始消息事件."""

    async def case() -> None:
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(config=_config(), connect=connect)
        task = asyncio.create_task(transport.start())
        await _complete_startup_sync(connection)

        fetch_task = asyncio.create_task(transport.get_message("9001"))
        await asyncio.sleep(0)

        request = connection.sent_json[-1]
        assert request["action"] == "get_msg"
        assert request["params"] == {"message_id": 9001}
        echo = request["echo"]
        await connection.push_event(
            {
                "echo": echo,
                "status": "ok",
                "retcode": 0,
                "data": {
                    "message_type": "group",
                    "message_id": 9001,
                    "group_id": 456,
                    "user_id": 42,
                    "message": [{"type": "text", "data": {"text": "hello"}}],
                    "sender": {"user_id": 42, "nickname": "Bot"},
                    "time": 1776818315,
                },
            }
        )
        raw = await asyncio.wait_for(fetch_task, timeout=1.0)

        assert raw is not None
        assert raw.post_type == "message"
        assert raw.message_type == "group"
        assert raw.message_id == 9001
        assert raw.group_id == 456
        assert raw.user_id == 42
        assert raw.self_id == "42"

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


def test_transport_group_reply_backfill_does_not_deadlock_reader_loop() -> None:
    """读循环中的 reply 回查应通过独立分发协程完成, 不阻塞 get_msg 响应."""

    async def case() -> None:
        bus = MessageBus()
        state = OneBotStateAdapter()
        connection = FakeConnection()

        async def connect() -> FakeConnection:
            return connection

        transport = OneBotTransport(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            mapper=OneBotMapper(),
            state=state,
            connect=connect,
        )
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
            state=state,
        )

        task = asyncio.create_task(kernel.start())
        await _complete_startup_sync(connection)
        await connection.push_event(
            {
                "self_id": 42,
                "post_type": "message",
                "message_type": "group",
                "message_id": 1002,
                "group_id": 456,
                "user_id": 123,
                "message": [
                    {"type": "reply", "data": {"id": "9001"}},
                    {"type": "text", "data": {"text": " hello after restart "}},
                ],
                "sender": {"user_id": 123, "nickname": "Alice"},
                "time": 1776818316,
            }
        )
        get_msg_request = await _wait_for_sent_action(connection, 2, "get_msg")
        echo = get_msg_request["echo"]
        assert isinstance(echo, str)
        await connection.push_event(
            {
                "echo": echo,
                "status": "ok",
                "retcode": 0,
                "data": {
                    "message_type": "group",
                    "message_id": 9001,
                    "group_id": 456,
                    "user_id": 42,
                    "message": [{"type": "text", "data": {"text": "earlier reply"}}],
                    "sender": {"user_id": 42, "nickname": "AnonBot"},
                    "time": 1776818315,
                },
            }
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        await kernel.stop()
        await asyncio.wait_for(task, timeout=1.0)

        assert inbound.metadata["trigger_reason"] == "reply_to_self"
        assert inbound.metadata["message"]["reply_to_self"] is True
        assert inbound.metadata["ctx_message_ids"] == ["1002"]
        assert "M|9001|u0|earlier reply" in inbound.content
        assert "M|1002|u1|^9001  hello after restart" in inbound.content

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


def test_kernel_skips_reply_backfill_for_unauthorized_message() -> None:
    """不在 allowlist 内的 reply 消息不应触发 get_msg 回查."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.fetched_messages["9001"] = OneBotRawEvent.model_validate(
            {
                "post_type": "message",
                "message_type": "group",
                "message_id": 9001,
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
                "message": [{"type": "text", "data": {"text": "earlier reply"}}],
                "sender": {"user_id": 42, "nickname": "Bot"},
                "time": 1776818315,
            }
        )
        kernel = Kernel(
            config=_config(allow_from=["private:999"]),
            bus=bus,
            transport=transport,
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-unauthorized-reply",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="got it",
                reply_to_message_id="9001",
            )
        )

        assert bus.inbound_size == 0
        assert transport.get_message_calls == []
        assert (
            kernel.context_store.get_message(
                ConversationRef(kind="group", id="456"),
                "user-unauthorized-reply",
            )
            is None
        )

    asyncio.run(case())


def test_kernel_skips_media_enrichment_for_unauthorized_message() -> None:
    """不在 allowlist 内的媒体消息不应触发入站增强."""

    async def case() -> None:
        bus = MessageBus()
        enricher = FakeInboundMediaEnricher(
            _group_message(content="placeholder", message_id="user-unauthorized-media")
        )
        kernel = Kernel(
            config=_config(allow_from=["private:999"], group_trigger_prob=1.0),
            bus=bus,
            transport=RecordingTransport(),
            inbound_media_enricher=enricher,
        )

        await kernel.handle_inbound(
            _group_message(content="[image]", message_id="user-unauthorized-media")
        )

        assert bus.inbound_size == 0
        assert enricher.calls == []
        assert (
            kernel.context_store.get_message(
                ConversationRef(kind="group", id="456"),
                "user-unauthorized-media",
            )
            is None
        )

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


def test_kernel_marks_reply_to_self_from_context_after_allowlist() -> None:
    """已授权消息仍应在 allowlist 之后完成 reply_to_self 判定并触发."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
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


def test_kernel_backfills_missing_reply_target_via_get_msg() -> None:
    """本地缺失 reply target 时应通过 get_msg 回补并按 reply_to_self 触发."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.fetched_messages["9001"] = OneBotRawEvent.model_validate(
            {
                "post_type": "message",
                "message_type": "group",
                "message_id": 9001,
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
                "message": [{"type": "text", "data": {"text": "earlier reply"}}],
                "sender": {"user_id": 42, "nickname": "Bot"},
                "time": 1776818315,
            }
        )
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )
        kernel.state.set_self_profile(user_id="42", nickname="Bot")

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="got it",
                reply_to_message_id="9001",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_message_calls == ["9001"]
        assert inbound.metadata["trigger_reason"] == "reply_to_self"
        assert inbound.metadata["message"]["reply_to_self"] is True
        assert inbound.metadata["ctx_message_ids"] == ["user-1"]
        assert inbound.metadata["ctx_count"] == 1
        assert "M|9001|u0|earlier reply" in inbound.content
        assert "M|user-1|u1|^9001 got it" in inbound.content

    asyncio.run(case())



def test_kernel_backfills_image_reply_target_via_get_msg() -> None:
    """缓存外纯图片 reply target 应经 get_msg 回补并进入 CTX."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.fetched_messages["9001"] = OneBotRawEvent.model_validate(
            {
                "post_type": "message",
                "message_type": "group",
                "message_id": 9001,
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": "quoted-image.png",
                            "url": "https://example.com/download",
                            "file_size": "123",
                        },
                    }
                ],
                "sender": {"user_id": 42, "nickname": "Bot"},
                "time": 1776818315,
            }
        )
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )
        kernel.state.set_self_profile(user_id="42", nickname="Bot")

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="你能看见我引用了什么吗",
                reply_to_message_id="9001",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_message_calls == ["9001"]
        assert inbound.metadata["trigger_reason"] == "reply_to_self"
        assert "I|i0|quoted-image.png" in inbound.content
        assert "M|9001|u0|[i0]" in inbound.content
        assert "M|user-1|u1|^9001 你能看见我引用了什么吗" in inbound.content

    asyncio.run(case())



def test_kernel_backfills_mixed_image_reply_target_via_get_msg() -> None:
    """缓存外混合文本/@/图片 reply target 应保持 render_segments 顺序."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.fetched_messages["9001"] = OneBotRawEvent.model_validate(
            {
                "post_type": "message",
                "message_type": "group",
                "message_id": 9001,
                "group_id": 456,
                "user_id": 42,
                "self_id": 42,
                "message": [
                    {"type": "text", "data": {"text": "先看 "}},
                    {"type": "at", "data": {"qq": "1001", "name": "原"}},
                    {"type": "text", "data": {"text": " 发的 "}},
                    {
                        "type": "image",
                        "data": {
                            "file": "download",
                            "url": "https://example.com/download",
                            "file_size": "123",
                        },
                    },
                    {"type": "text", "data": {"text": " 再说"}},
                ],
                "sender": {"user_id": 42, "nickname": "Bot"},
                "time": 1776818315,
            }
        )
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )
        kernel.state.set_self_profile(user_id="42", nickname="Bot")

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="引用一下",
                reply_to_message_id="9001",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_message_calls == ["9001"]
        assert "U|u1|1001|原" in inbound.content
        assert "I|i0|image" in inbound.content
        assert "I|i0|download" not in inbound.content
        assert "M|9001|u0|先看 @u1 发的 [i0] 再说" in inbound.content
        assert "M|user-1|u2|^9001 引用一下" in inbound.content

    asyncio.run(case())


def test_kernel_passthroughs_known_slash_command_for_super_admin_without_storing(
) -> None:
    """已知菜单命令遇到超级管理员时应直通总线、更新资料且不写入上下文."""

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

        message = _group_message(content="/help")
        await kernel.handle_inbound(message)

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        profile = kernel.state.get_member_profile(
            message.conversation,
            message.sender_id,
        )

        assert inbound.chat_id == "group:456"
        assert inbound.content == "/help"
        assert inbound.metadata["trigger_reason"] == "slash_command"
        assert inbound.metadata["message"]["content"] == "/help"
        assert inbound.metadata["ctx_message_ids"] == []
        assert inbound.metadata["ctx_count"] == 0
        assert "command" not in inbound.metadata
        assert profile is not None
        assert profile.user_id == "123"
        assert profile.card == "Alice"
        assert profile.nickname == "Alice"
        assert (
            kernel.context_store.get_message(
                message.conversation,
                message.message_id,
            )
            is None
        )

    asyncio.run(case())


def test_kernel_treats_known_slash_command_from_non_admin_as_normal_message() -> None:
    """非管理员发送已知菜单命令时应按普通消息触发并写入上下文."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                super_admins=["999"],
                trigger_on_at=False,
                trigger_on_reply=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
        )

        message = _group_message(content="/help")
        await kernel.handle_inbound(message)

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert inbound.metadata["trigger_reason"] == "group_probability"
        assert inbound.metadata["message"]["content"] == "/help"
        assert "command" not in inbound.metadata
        assert (
            kernel.context_store.get_message(
                message.conversation,
                message.message_id,
            )
            == message
        )

    asyncio.run(case())


def test_kernel_treats_unknown_slash_command_as_normal_message() -> None:
    """未知斜杠命令应按普通消息路径处理并写入上下文."""

    async def case() -> None:
        bus = MessageBus()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                super_admins=["123"],
                trigger_on_at=False,
                trigger_on_reply=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
        )

        message = _group_message(content="/foo")
        await kernel.handle_inbound(message)

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert inbound.metadata["trigger_reason"] == "group_probability"
        assert inbound.metadata["message"]["content"] == "/foo"
        assert "command" not in inbound.metadata
        assert (
            kernel.context_store.get_message(
                message.conversation,
                message.message_id,
            )
            == message
        )

    asyncio.run(case())


def test_kernel_renders_bot_name_from_self_profile_in_ctx() -> None:
    """CTX/1 应优先使用已同步的 bot 昵称."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )
        kernel.state.set_self_profile(user_id="42", nickname="咕咕")
        await kernel.remember_message(
            NormalizedMessage(
                message_id="assistant-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="42",
                sender_name="咕咕",
                content="在",
                from_self=True,
            )
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="hello",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert "U|u0|42|咕咕|bot" in inbound.content

    asyncio.run(case())


def test_kernel_backfills_group_mention_name_via_member_info() -> None:
    """群聊 mention 缺资料时应回填 card 并写回状态缓存."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.group_member_profiles[("456", "1001")] = {
            "user_id": "1001",
            "card": "原",
            "nickname": "擎天霹雳寄霸天",
        }
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )
        kernel.state.set_self_profile(user_id="42", nickname="咕咕")

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="hi",
                metadata={
                    "render_segments": [
                        {"type": "text", "text": "hi "},
                        {"type": "mention", "user_id": "1001", "name": ""},
                    ]
                },
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        profile = kernel.state.get_member_profile(
            ConversationRef(kind="group", id="456"),
            "1001",
        )

        assert transport.get_group_member_info_calls == [("456", "1001")]
        assert "U|u2|1001|原" in inbound.content
        assert "M|user-1|u1|hi @u2" in inbound.content
        assert profile is not None
        assert profile.card == "原"
        assert profile.nickname == "擎天霹雳寄霸天"

    asyncio.run(case())


def test_kernel_falls_back_to_group_nickname_when_card_missing() -> None:
    """群聊成员无 card 时应回退到 nickname."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.group_member_profiles[("456", "1002")] = {
            "user_id": "1002",
            "card": "",
            "nickname": "昵称",
        }
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="hi",
                metadata={
                    "render_segments": [
                        {"type": "text", "text": "hi "},
                        {"type": "mention", "user_id": "1002", "name": ""},
                    ]
                },
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert "U|u2|1002|昵称" in inbound.content

    asyncio.run(case())


def test_kernel_falls_back_to_user_id_when_group_member_info_missing() -> None:
    """群聊成员资料仍缺失时应最终回退到 QQ 号."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="hi",
                metadata={
                    "render_segments": [
                        {"type": "text", "text": "hi "},
                        {"type": "mention", "user_id": "1003", "name": ""},
                    ]
                },
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_group_member_info_calls == [("456", "1003")]
        assert "U|u2|1003|1003" in inbound.content

    asyncio.run(case())


def test_kernel_renders_forward_container_rows_from_fetcher() -> None:
    """入站 forward 应通过 get_forward_msg 恢复为 M/F/N 容器."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.forward_messages["fw-1"] = {
            "messages": [
                {
                    "data": {
                        "message_id": "node-1",
                        "user_id": "1001",
                        "nickname": "张三",
                        "content": [{"type": "text", "data": {"text": "第一条"}}],
                    }
                },
                {
                    "data": {
                        "message_id": "node-2",
                        "user_id": "1002",
                        "nickname": "李四",
                        "content": [
                            {"type": "reply", "data": {"id": "node-1"}},
                            {"type": "text", "data": {"text": "第二条"}},
                            {"type": "forward", "data": {"id": "nested-1"}},
                        ],
                    }
                },
            ]
        }
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
        )

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="user-forward-1",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="123",
                sender_name="Alice",
                content="看这个[forward]",
                metadata={
                    "forwards": [
                        {
                            "forward_id": "fw-1",
                            "summary": "聊天记录",
                            "nodes": [],
                        }
                    ],
                    "render_segments": [
                        {"type": "text", "text": "看这个"},
                        {"type": "forward"},
                    ],
                },
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_forward_message_calls == ["fw-1"]
        assert "M|user-forward-1|u1|看这个[F:f0]" in inbound.content
        assert "F|f0|2|聊天记录" in inbound.content
        assert "N|0|u2|第一条" in inbound.content
        assert "N|1|u3|^0 第二条[forward]" in inbound.content

    asyncio.run(case())


def test_kernel_forward_fetcher_preserves_outer_sender_image_and_local_reply() -> None:
    """真实 forward 节点应保留外层 sender、图片与局部 reply 关系."""

    async def case() -> None:
        bus = MessageBus()
        transport = RecordingTransport()
        transport.forward_messages["fw-2"] = {
            "messages": [
                {
                    "data": {
                        "message_id": "node-1",
                        "user_id": "1637649901",
                        "nickname": "千早奶龙",
                        "content": [
                            {
                                "type": "video",
                                "data": {
                                    "file": "video.mp4",
                                    "url": "https://example.com/video.mp4",
                                },
                            }
                        ],
                    }
                },
                {
                    "data": {
                        "message_id": "node-2",
                        "user_id": "1094950020",
                        "nickname": "囚心罪",
                        "content": [{"type": "text", "data": {"text": "🤔"}}],
                    }
                },
                {
                    "data": {
                        "message_id": "node-3",
                        "user_id": "1637649901",
                        "nickname": "千早奶龙",
                        "content": [{"type": "forward", "data": {"id": "nested-1"}}],
                    }
                },
                {
                    "sender": {"user_id": "123456", "nickname": "香港奶龙"},
                    "message_id": "node-4",
                    "content": [
                        {"type": "reply", "data": {"id": "node-3"}},
                        {
                            "type": "image",
                            "data": {
                                "file": "09D75D4956CA5B4C21139F1701173408.png",
                                "url": "https://example.com/09D75D4956CA5B4C21139F1701173408.png",
                                "file_size": "123",
                            },
                        },
                    ],
                },
                {
                    "data": {
                        "message_id": "node-5",
                        "user_id": "424155717",
                        "nickname": "原",
                        "content": [
                            {"type": "reply", "data": {"id": "node-4"}},
                            {"type": "text", "data": {"text": "缓外引用测试 other"}},
                        ],
                    }
                },
            ]
        }
        enriched_message = NormalizedMessage(
            message_id="763685243",
            conversation=ConversationRef(kind="group", id="456"),
            sender_id="424155717",
            sender_name="原",
            content="[forward]",
            metadata={
                "forwards": [
                    {
                        "forward_id": "fw-2",
                        "summary": "",
                        "nodes": [],
                    }
                ],
                "render_segments": [{"type": "forward"}],
                "forward_expanded": [
                    {
                        "forward_id": "fw-2",
                        "summary": "",
                        "nodes": [
                            {
                                "sender_id": "1637649901",
                                "sender_name": "千早奶龙",
                                "message_id": "node-1",
                                "content": "[video]",
                                "attachments": [
                                    {
                                        "kind": "video",
                                        "url": "https://example.com/video.mp4",
                                        "name": "video.mp4",
                                        "metadata": {},
                                    }
                                ],
                            },
                            {
                                "sender_id": "1094950020",
                                "sender_name": "囚心罪",
                                "message_id": "node-2",
                                "content": "🤔",
                                "attachments": [],
                            },
                            {
                                "sender_id": "1637649901",
                                "sender_name": "千早奶龙",
                                "message_id": "node-3",
                                "content": "[forward]",
                                "attachments": [],
                            },
                            {
                                "sender_id": "123456",
                                "sender_name": "香港奶龙",
                                "message_id": "node-4",
                                "reply_to_message_id": "node-3",
                                "content": "[image]",
                                "attachments": [
                                    {
                                        "kind": "image",
                                        "url": "https://example.com/09D75D4956CA5B4C21139F1701173408.png",
                                        "name": "09D75D4956CA5B4C21139F1701173408.png",
                                        "metadata": {
                                            "file_size": "123",
                                            "local_path": (
                                                "/tmp/09D75D4956CA5B4C21139F1701173408.png"
                                            ),
                                        },
                                    }
                                ],
                            },
                            {
                                "sender_id": "424155717",
                                "sender_name": "原",
                                "message_id": "node-5",
                                "reply_to_message_id": "node-4",
                                "content": "缓外引用测试 other",
                                "attachments": [],
                            },
                        ],
                    }
                ],
            },
        )
        enricher = FakeInboundMediaEnricher(enriched_message)
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=transport,
            inbound_media_enricher=enricher,
        )
        kernel.state.set_self_profile(user_id="1637649901", nickname="千早奶龙")

        await kernel.handle_inbound(
            NormalizedMessage(
                message_id="763685243",
                conversation=ConversationRef(kind="group", id="456"),
                sender_id="424155717",
                sender_name="原",
                content="[forward]",
                metadata={
                    "forwards": [
                        {
                            "forward_id": "fw-2",
                            "summary": "",
                            "nodes": [],
                        }
                    ],
                    "render_segments": [{"type": "forward"}],
                },
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert transport.get_forward_message_calls == ["fw-2"]
        assert enricher.calls == ["763685243"]
        assert inbound.media == ["/tmp/09D75D4956CA5B4C21139F1701173408.png"]
        assert "U|u3|123456|香港奶龙" in inbound.content
        assert "I|i0|09D75D4956CA5B4C21139F1701173408.png" in inbound.content
        assert "N|0|u0|[video]" in inbound.content
        assert "N|1|u2|🤔" in inbound.content
        assert "N|2|u0|[forward]" in inbound.content
        assert "N|3|u3|^2 [i0]" in inbound.content
        assert "N|4|u1|^3 缓外引用测试 other" in inbound.content

    asyncio.run(case())


def test_kernel_publishes_when_forward_expanded_contains_invalid_slot() -> None:
    """坏的 forward_expanded 槽位不应阻断 kernel 发布."""

    async def case() -> None:
        bus = MessageBus()
        enriched_message = NormalizedMessage(
            message_id="user-forward-invalid-slot",
            conversation=ConversationRef(kind="group", id="456"),
            sender_id="123",
            sender_name="Alice",
            content="看这个[forward][forward]",
            metadata={
                "render_segments": [
                    {"type": "text", "text": "看这个"},
                    {"type": "forward"},
                    {"type": "forward"},
                ],
                "forward_expanded": [
                    {"forward_id": "fw-1", "summary": "ok", "nodes": []},
                    {"forward_id": "bad", "summary": 123, "nodes": "oops"},
                ],
            },
        )
        enricher = FakeInboundMediaEnricher(enriched_message)
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
            inbound_media_enricher=enricher,
        )

        await kernel.handle_inbound(
            _group_message(
                content="看这个[forward][forward]",
                message_id="user-forward-invalid-slot",
            )
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert enricher.calls == ["user-forward-invalid-slot"]
        assert inbound.metadata["trigger_reason"] == "group_probability"
        assert "M|user-forward-invalid-slot|u1|看这个[F:f0][forward]" in inbound.content
        assert "F|f0|0|ok" in inbound.content

    asyncio.run(case())


def test_kernel_publishes_enriched_image_and_voice_media_refs() -> None:
    """增强后的图片和语音应作为结构化 media 传给上游."""

    async def case() -> None:
        bus = MessageBus()
        enriched_message = NormalizedMessage(
            message_id="user-media",
            conversation=ConversationRef(kind="group", id="456"),
            sender_id="123",
            sender_name="Alice",
            content="看图[image]听一下[voice]",
            attachments=[
                Attachment(
                    kind="image",
                    url="https://example.com/a.png",
                    name="a.png",
                    metadata={
                        "file_size": "123",
                        "local_path": "/tmp/a-local.png",
                    },
                ),
                Attachment(
                    kind="voice",
                    url="https://example.com/voice.amr",
                    name="voice.amr",
                    metadata={
                        "local_file_uri": "file:///tmp/voice.amr",
                        "transcription_input_local_file_uri": "file:///tmp/voice.wav",
                        "transcription_status": "success",
                        "transcription_text": "你好",
                    },
                ),
            ],
            metadata={
                "render_segments": [
                    {"type": "text", "text": "看图"},
                    {"type": "image"},
                    {"type": "text", "text": "听一下"},
                    {"type": "voice"},
                ]
            },
        )
        enricher = FakeInboundMediaEnricher(enriched_message)
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
            inbound_media_enricher=enricher,
        )

        await kernel.handle_inbound(
            _group_message(content="placeholder", message_id="user-media")
        )

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)

        assert enricher.calls == ["user-media"]
        assert inbound.media == ["/tmp/a-local.png", "file:///tmp/voice.wav"]
        assert "I|i0|a.png" in inbound.content
        assert "V|v0|voice.wav|=你好" in inbound.content

    asyncio.run(case())


def test_kernel_suppresses_unsupported_media_only_messages() -> None:
    """纯视频或文件消息应在内核层被抑制, 不进入总线."""

    async def case() -> None:
        bus = MessageBus()
        enriched_message = NormalizedMessage(
            message_id="video-only",
            conversation=ConversationRef(kind="group", id="456"),
            sender_id="123",
            sender_name="Alice",
            content="",
            metadata={"drop_reason": "unsupported_media_only"},
        )
        enricher = FakeInboundMediaEnricher(enriched_message)
        kernel = Kernel(
            config=_config(
                allow_from=["group:456"],
                trigger_on_at=False,
                group_trigger_prob=1.0,
            ),
            bus=bus,
            transport=RecordingTransport(),
            inbound_media_enricher=enricher,
        )

        await kernel.handle_inbound(
            _group_message(content="[video]", message_id="video-only")
        )

        assert enricher.calls == ["video-only"]
        assert bus.inbound_size == 0
        assert (
            kernel.context_store.get_message(
                ConversationRef(kind="group", id="456"),
                "video-only",
            )
            is None
        )

    asyncio.run(case())
