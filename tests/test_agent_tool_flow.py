from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


def _install_astrbot_stubs() -> None:
    if "astrbot" in sys.modules:
        return

    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    message_components_mod = types.ModuleType("astrbot.api.message_components")
    star_mod = types.ModuleType("astrbot.api.star")

    class _Logger:
        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def exception(self, *args, **kwargs):
            return None

    class _MessageChain:
        def __init__(self, chain=None):
            self.chain = list(chain or [])

        def message(self, text: str):
            self.chain.append(_Plain(text))
            return self

    class _Plain:
        def __init__(self, text: str):
            self.text = text

    class _Image:
        def __init__(self, url: str = ""):
            self.url = url

        @classmethod
        def fromURL(cls, url: str):
            return cls(url)

    class _Filter:
        class EventMessageType:
            ALL = "ALL"

        @staticmethod
        def command(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def llm_tool(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class _AstrMessageEvent:
        pass

    class _Star:
        def __init__(self, context, config=None):
            self.context = context
            self.config = config

    def _register(*args, **kwargs):
        def decorator(cls):
            return cls

        return decorator

    api_mod.logger = _Logger()
    event_mod.AstrMessageEvent = _AstrMessageEvent
    event_mod.MessageChain = _MessageChain
    event_mod.filter = _Filter
    message_components_mod.Image = _Image
    message_components_mod.Plain = _Plain
    star_mod.Context = object
    star_mod.Star = _Star
    star_mod.register = _register

    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = message_components_mod
    sys.modules["astrbot.api.star"] = star_mod


def _install_httpx_stub() -> None:
    if "httpx" in sys.modules:
        return

    httpx_mod = types.ModuleType("httpx")

    class _Timeout:
        def __init__(self, *args, **kwargs):
            pass

    class _HTTPError(Exception):
        pass

    class _HTTPStatusError(_HTTPError):
        def __init__(self, message="", *, response=None):
            super().__init__(message)
            self.response = response

    class _AsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise AssertionError("httpx.AsyncClient.get should be mocked in unit tests")

    httpx_mod.Timeout = _Timeout
    httpx_mod.HTTPError = _HTTPError
    httpx_mod.HTTPStatusError = _HTTPStatusError
    httpx_mod.AsyncClient = _AsyncClient
    sys.modules["httpx"] = httpx_mod


_install_astrbot_stubs()
_install_httpx_stub()

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

from astrbot_plugin_bilibili_subscribe.main import BilibiliSubscribePlugin
from astrbot_plugin_bilibili_subscribe.services.bilibili_client import RoomInfo
from astrbot_plugin_bilibili_subscribe.services.subscription_manager import SubscriptionManager
from astrbot_plugin_bilibili_subscribe.utils.json_storage import JsonStorage
from astrbot.api.message_components import Image, Plain


class At:
    def __init__(self, qq: str):
        self.qq = qq


class FakeEvent:
    def __init__(
        self,
        *,
        message_str: str,
        sender_id: str = "user-1",
        group_id: str | None = "group-1",
        self_id: str = "bot-123",
        session_id: str = "session-1",
        message_components: list | None = None,
        origin: str | None = None,
        is_admin: bool = True,
        platform_name: str = "qq",
    ):
        self.message_str = message_str
        self._sender_id = sender_id
        self._group_id = group_id
        self._self_id = self_id
        self.session_id = session_id
        self._is_admin = is_admin
        self._platform_name = platform_name
        if origin is None:
            origin = f"{platform_name}:GroupMessage:{group_id}" if group_id else f"{platform_name}:private_message:{sender_id}"
        self.unified_msg_origin = origin
        self._message_components = message_components or []
        self._stopped = False
        self.message_obj = SimpleNamespace(
            group_id=group_id,
            self_id=self_id,
            session_id=session_id,
            sender=SimpleNamespace(
                user_id=sender_id,
                role="admin" if is_admin else "member",
                is_admin=is_admin,
            ),
            message=list(self._message_components),
        )

    def get_sender_id(self):
        return self._sender_id

    def get_group_id(self):
        return self._group_id

    def get_self_id(self):
        return self._self_id

    def get_messages(self):
        return list(self._message_components)

    def get_platform_name(self):
        return self._platform_name

    def is_admin(self):
        return self._is_admin

    def is_private_chat(self):
        return self._group_id is None

    def plain_result(self, text: str):
        return text

    def chain_result(self, chain):
        return {"type": "chain", "chain": list(chain)}

    def stop_event(self):
        self._stopped = True


class AgentToolFlowTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "subscriptions.json"
        self.context = SimpleNamespace(
            send_message=AsyncMock(),
            platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(client_self_id="bot-123")]),
        )

        with patch.object(BilibiliSubscribePlugin, "_startup", new=AsyncMock(return_value=None)):
            self.plugin = BilibiliSubscribePlugin(self.context, config={})

        self.plugin.data_file = self.data_file
        self.plugin.storage = JsonStorage(self.data_file)
        self.plugin.subscription_manager = SubscriptionManager(self.plugin.storage)
        await self.plugin.subscription_manager.initialize()

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.temp_dir.cleanup()

    def assert_chain_reply(self, reply, expected_text: str, expected_image_url: str):
        self.assertIsInstance(reply, dict)
        self.assertEqual(reply.get("type"), "chain")
        chain = reply["chain"]
        self.assertEqual(chain[0].text, expected_text)
        self.assertEqual(chain[1].url, expected_image_url)

    async def test_group_message_requires_at(self):
        event_without_at = FakeEvent(message_str="帮我订阅直播间 123456", message_components=[])
        event_with_at = FakeEvent(message_str="@bot 帮我订阅直播间 123456", message_components=[At("bot-123")])

        self.assertFalse(self.plugin._can_process_direct_request(event_without_at))
        self.assertTrue(self.plugin._can_process_direct_request(event_with_at))

    async def test_direct_at_message_without_room_creates_pending(self):
        event = FakeEvent(
            message_str="@bot 我想订阅直播间",
            message_components=[At("bot-123")],
        )

        results = [item async for item in self.plugin.on_message(event)]
        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")

        self.assertEqual(results, ["目前只支持订阅 Bilibili 直播间。请把直播间链接或房间号发给我。"])
        self.assertIsNotNone(pending)
        self.assertEqual(pending["pending_type"], "room_id")
        self.assertIsNone(pending["room_id"])

    async def test_direct_at_message_with_room_creates_mode_pending(self):
        event = FakeEvent(
            message_str="@bot 我想订阅直播间，房间号是：32800932",
            message_components=[At("bot-123")],
        )

        results = [item async for item in self.plugin.on_message(event)]
        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")

        self.assertEqual(results, ["要订阅到哪里？请回复“私聊”或“群订阅”。"])
        self.assertIsNotNone(pending)
        self.assertEqual(pending["pending_type"], "mode")
        self.assertEqual(pending["room_id"], 32800932)

    async def test_llm_tool_creates_pending_when_group_mode_missing(self):
        event = FakeEvent(
            message_str="@bot 帮我订阅 https://live.bilibili.com/123456",
            message_components=[At("bot-123")],
        )

        result = await self.plugin.subscribe_bilibili_live_room(
            event,
            "帮我订阅 https://live.bilibili.com/123456",
        )

        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1")
        self.assertEqual(result, "要订阅到哪里？请回复“私聊”或“群订阅”。")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["room_id"], 123456)
        self.assertEqual(pending["pending_type"], "mode")

    async def test_llm_tool_prompts_for_bilibili_room_when_room_missing(self):
        event = FakeEvent(
            message_str="@bot 我想订阅直播间",
            message_components=[At("bot-123")],
        )

        result = await self.plugin.subscribe_bilibili_live_room(event, "我想订阅直播间")

        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")
        self.assertEqual(result, "目前只支持订阅 Bilibili 直播间。请把直播间链接或房间号发给我。")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["pending_type"], "room_id")
        self.assertIsNone(pending["room_id"])

    async def test_pending_confirmation_can_continue_without_at(self):
        await self.plugin.subscription_manager.add_pending_confirmation(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_id": "group-1",
                "room_id": 123456,
                "pending_type": "mode",
                "room_url": "https://live.bilibili.com/123456",
                "origin": "qq:group:group-1",
                "platform_name": "qq",
                "created_at": "2026-04-09T12:00:00",
            }
        )

        self.plugin.bilibili_client.get_room_info = AsyncMock(
            return_value=RoomInfo(
                room_id=123456,
                room_url="https://live.bilibili.com/123456",
                title="测试直播间",
                uname="测试主播",
                live_status=1,
                area_name="测试分区",
                cover_url="https://example.com/cover.jpg",
            )
        )

        no_at_event = FakeEvent(message_str="群", message_components=[])
        no_at_results = [item async for item in self.plugin.on_message(no_at_event)]
        pending_after = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1")

        self.assertEqual(len(no_at_results), 1)
        self.assertEqual(no_at_results, ["要加备注吗？请回复备注名，或回复“跳过”。"])
        self.assertIsNotNone(pending_after)
        self.assertEqual(pending_after["pending_type"], "remark")
        self.assertEqual(pending_after["mode"], "group")

    async def test_pending_room_request_can_continue_without_at(self):
        await self.plugin.subscription_manager.add_pending_confirmation(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_id": "group-1",
                "room_id": None,
                "mode": None,
                "pending_type": "room_id",
                "origin": "qq:group:group-1",
                "platform_name": "qq",
                "created_at": "2026-04-09T12:00:00",
            }
        )

        reply_event = FakeEvent(message_str="32800932", message_components=[])
        results = [item async for item in self.plugin.on_message(reply_event)]
        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")

        self.assertEqual(results, ["要订阅到哪里？请回复“私聊”或“群订阅”。"])
        self.assertIsNotNone(pending)
        self.assertEqual(pending["pending_type"], "mode")
        self.assertEqual(pending["room_id"], 32800932)

    async def test_group_subscription_is_unique_per_group_room_and_mode(self):
        await self.plugin.subscription_manager.add_pending_confirmation(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_id": "group-1",
                "room_id": 32800932,
                "pending_type": "mode",
                "remark": "测试备注",
                "room_url": "https://live.bilibili.com/32800932",
                "origin": "qq:group:group-1",
                "platform_name": "qq",
                "created_at": "2026-04-09T12:00:00",
            }
        )
        self.plugin.bilibili_client.get_room_info = AsyncMock(
            return_value=RoomInfo(
                room_id=32800932,
                room_url="https://live.bilibili.com/32800932",
                title="测试直播间",
                uname="测试主播",
                live_status=1,
                area_name="测试分区",
                cover_url="https://example.com/cover.jpg",
            )
        )

        first_event = FakeEvent(message_str="群", message_components=[])
        first_results = [item async for item in self.plugin.on_message(first_event)]

        duplicate_event = FakeEvent(
            message_str="@bot 帮我订阅直播间，房间号是：32800932 群订阅",
            sender_id="user-2",
            session_id="session-2",
            message_components=[At("bot-123")],
        )
        duplicate_results = [item async for item in self.plugin.on_message(duplicate_event)]
        duplicate_pending = await self.plugin.subscription_manager.get_pending_confirmation("user-2", "session-2", "group-1")
        self.assertEqual(duplicate_results, ["要加备注吗？请回复备注名，或回复“跳过”。"])
        self.assertIsNotNone(duplicate_pending)
        self.assertEqual(duplicate_pending["pending_type"], "remark")

        duplicate_skip_event = FakeEvent(
            message_str="跳过",
            sender_id="user-2",
            session_id="session-2",
            message_components=[],
        )
        duplicate_skip_results = [item async for item in self.plugin.on_message(duplicate_skip_event)]
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(first_results), 1)
        self.assertEqual(first_results[0]["type"], "chain")
        self.assertEqual(duplicate_skip_results, ["这个直播间在当前群里已经订阅过了：https://live.bilibili.com/32800932"])
        self.assertEqual(len(subscriptions), 1)

    async def test_group_subscription_requires_admin(self):
        event = FakeEvent(
            message_str="@bot 帮我订阅直播间 32800932 群订阅",
            message_components=[At("bot-123")],
            is_admin=False,
        )

        results = [item async for item in self.plugin.on_message(event)]

        self.assertEqual(results, ["只有群管理员才能创建群订阅。"])

    async def test_private_subscription_requires_private_chat(self):
        event = FakeEvent(
            message_str="@bot 帮我订阅直播间 32800932 私聊",
            message_components=[At("bot-123")],
        )

        results = [item async for item in self.plugin.on_message(event)]

        self.assertEqual(results, ["私聊订阅请先添加机器人好友，再在私聊里发起。"])

    async def test_private_subscription_succeeds_in_private_chat(self):
        self.plugin.bilibili_client.get_room_info = AsyncMock(
            return_value=RoomInfo(
                room_id=32800932,
                room_url="https://live.bilibili.com/32800932",
                title="测试直播间",
                uname="测试主播",
                live_status=1,
                area_name="测试分区",
                cover_url="https://example.com/cover.jpg",
            )
        )
        event = FakeEvent(
            message_str="帮我订阅直播间 32800932",
            group_id=None,
        )

        results = [item async for item in self.plugin.on_message(event)]
        self.assertEqual(len(results), 1)
        self.assertEqual(results, ["要加备注吗？请回复备注名，或回复“跳过”。"])

        reply_event = FakeEvent(
            message_str="跳过",
            group_id=None,
            session_id="session-1",
        )
        reply_results = [item async for item in self.plugin.on_message(reply_event)]
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(reply_results), 1)
        self.assertTrue(isinstance(reply_results[0], dict))
        self.assertEqual(len(subscriptions), 1)
        self.assertEqual(subscriptions[0]["notify_origin"], "qq:private_message:user-1")
        self.assertEqual(subscriptions[0]["remark"], "")

    async def test_pending_mode_keeps_remark(self):
        ask_event = FakeEvent(
            message_str='@bot 订阅直播间 32800932 备注“夏老板”',
            message_components=[At("bot-123")],
        )

        ask_results = [item async for item in self.plugin.on_message(ask_event)]
        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")

        self.assertEqual(ask_results, ["要订阅到哪里？请回复“私聊”或“群订阅”。"])
        self.assertEqual(pending["remark"], "夏老板")

        self.plugin.bilibili_client.get_room_info = AsyncMock(
            return_value=RoomInfo(
                room_id=32800932,
                room_url="https://live.bilibili.com/32800932",
                title="测试直播间",
                uname="测试主播",
                live_status=1,
                area_name="测试分区",
                cover_url="https://example.com/cover.jpg",
            )
        )
        reply_event = FakeEvent(message_str="群", message_components=[])

        reply_results = [item async for item in self.plugin.on_message(reply_event)]
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(reply_results), 1)
        self.assert_chain_reply(
            reply_results[0],
            "已为你创建群订阅：夏老板（主播：测试主播） / https://live.bilibili.com/32800932\n当前状态：直播中\n说明：该直播间当前正在直播，后续下播或再次开播时会继续提醒。",
            "https://example.com/cover.jpg",
        )
        self.assertEqual(subscriptions[0]["remark"], "夏老板")

    async def test_duplicate_subscription_can_update_remark(self):
        room_info = RoomInfo(
            room_id=32800932,
            room_url="https://live.bilibili.com/32800932",
            title="测试直播间",
            uname="测试主播",
            live_status=1,
            area_name="测试分区",
            cover_url="https://example.com/cover.jpg",
        )
        self.plugin.bilibili_client.get_room_info = AsyncMock(return_value=room_info)

        first_event = FakeEvent(message_str="帮我订阅直播间 32800932 备注旧名字", group_id=None)
        first_results = [item async for item in self.plugin.on_message(first_event)]

        second_event = FakeEvent(message_str="帮我订阅直播间 32800932 备注夏老板", group_id=None, session_id="session-2")
        second_results = [item async for item in self.plugin.on_message(second_event)]
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(first_results), 1)
        self.assertEqual(first_results[0]["type"], "chain")
        self.assertEqual(len(second_results), 1)
        self.assertIn("已更新备注为“夏老板”", second_results[0])
        self.assertEqual(subscriptions[0]["remark"], "夏老板")

    async def test_mode_reply_prompts_for_remark_when_missing(self):
        await self.plugin.subscription_manager.add_pending_confirmation(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_id": "group-1",
                "room_id": 32800932,
                "pending_type": "mode",
                "room_url": "https://live.bilibili.com/32800932",
                "origin": "qq:group:group-1",
                "platform_name": "qq",
                "created_at": "2026-04-09T12:00:00",
            }
        )

        reply_event = FakeEvent(message_str="群", message_components=[])
        results = [item async for item in self.plugin.on_message(reply_event)]
        pending = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1", "group-1")

        self.assertEqual(results, ["要加备注吗？请回复备注名，或回复“跳过”。"])
        self.assertIsNotNone(pending)
        self.assertEqual(pending["pending_type"], "remark")
        self.assertEqual(pending["mode"], "group")

    async def test_remark_skip_creates_subscription_with_cover(self):
        await self.plugin.subscription_manager.add_pending_confirmation(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_id": None,
                "room_id": 32800932,
                "mode": "private",
                "pending_type": "remark",
                "origin": "qq:private_message:user-1",
                "platform_name": "qq",
                "created_at": "2026-04-09T12:00:00",
            }
        )
        self.plugin.bilibili_client.get_room_info = AsyncMock(
            return_value=RoomInfo(
                room_id=32800932,
                room_url="https://live.bilibili.com/32800932",
                title="测试直播间",
                uname="测试主播",
                live_status=1,
                area_name="测试分区",
                cover_url="https://example.com/cover.jpg",
            )
        )

        reply_event = FakeEvent(message_str="跳过", group_id=None)
        results = [item async for item in self.plugin.on_message(reply_event)]
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(results), 1)
        self.assert_chain_reply(
            results[0],
            "已为你创建私聊订阅：测试主播 / https://live.bilibili.com/32800932\n当前状态：直播中\n说明：该直播间当前正在直播，后续下播或再次开播时会继续提醒。",
            "https://example.com/cover.jpg",
        )
        self.assertEqual(subscriptions[0]["remark"], "")

    async def test_notification_includes_cover_image(self):
        subscription = {
            "room_id": 32800932,
            "room_url": "https://live.bilibili.com/32800932",
            "remark": "夏老板",
            "mode": "group",
            "notify_origin": "qq:GroupMessage:group-1",
        }
        room_info = RoomInfo(
            room_id=32800932,
            room_url="https://live.bilibili.com/32800932",
            title="测试直播间",
            uname="测试主播",
            live_status=1,
            area_name="测试分区",
            cover_url="https://example.com/cover.jpg",
        )

        await self.plugin._send_notification(subscription, room_info, is_live=True)

        self.context.send_message.assert_awaited_once()
        _, sent_chain = self.context.send_message.await_args.args
        self.assertEqual(sent_chain.chain[0].text, "【开播提醒】夏老板 开播了\n标题：测试直播间\n直播间：https://live.bilibili.com/32800932")
        self.assertIsInstance(sent_chain.chain[0], Plain)
        self.assertIsInstance(sent_chain.chain[1], Image)
        self.assertEqual(sent_chain.chain[1].url, "https://example.com/cover.jpg")

    async def test_poll_once_fetches_each_room_only_once(self):
        initial_room_info = RoomInfo(
            room_id=32800932,
            room_url="https://live.bilibili.com/32800932",
            title="测试直播间",
            uname="测试主播",
            live_status=0,
            area_name="测试分区",
            cover_url="https://example.com/cover.jpg",
        )
        updated_room_info = RoomInfo(
            room_id=32800932,
            room_url="https://live.bilibili.com/32800932",
            title="测试直播间",
            uname="测试主播",
            live_status=1,
            area_name="测试分区",
            cover_url="https://example.com/cover-new.jpg",
        )

        await self.plugin.subscription_manager.upsert_subscription(
            room_info=initial_room_info,
            user_id="user-1",
            group_id="group-1",
            mode="group",
            remark="",
            notify_origin="qq:GroupMessage:group-1",
            session_id="session-1",
        )
        await self.plugin.subscription_manager.upsert_subscription(
            room_info=initial_room_info,
            user_id="user-2",
            group_id="group-2",
            mode="group",
            remark="夏老板",
            notify_origin="qq:GroupMessage:group-2",
            session_id="session-2",
        )

        self.plugin.bilibili_client.get_room_info = AsyncMock(return_value=updated_room_info)
        self.plugin._send_notification = AsyncMock()

        await self.plugin._poll_once()

        self.plugin.bilibili_client.get_room_info.assert_awaited_once_with(32800932)
        self.assertEqual(self.plugin._send_notification.await_count, 2)
