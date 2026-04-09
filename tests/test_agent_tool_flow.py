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
    star_mod = types.ModuleType("astrbot.api.star")

    class _Logger:
        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def exception(self, *args, **kwargs):
            return None

    class _MessageChain:
        def __init__(self):
            self.messages = []

        def message(self, text: str):
            self.messages.append(text)
            return self

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
    star_mod.Context = object
    star_mod.Star = _Star
    star_mod.register = _register

    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
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
        origin: str = "qq:group:group-1",
    ):
        self.message_str = message_str
        self._sender_id = sender_id
        self._group_id = group_id
        self._self_id = self_id
        self.session_id = session_id
        self.unified_msg_origin = origin
        self._message_components = message_components or []
        self._stopped = False
        self.message_obj = SimpleNamespace(
            group_id=group_id,
            self_id=self_id,
            session_id=session_id,
            sender=SimpleNamespace(user_id=sender_id),
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

    def plain_result(self, text: str):
        return text

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
            )
        )

        no_at_event = FakeEvent(message_str="群", message_components=[])
        no_at_results = [item async for item in self.plugin.on_message(no_at_event)]
        pending_after = await self.plugin.subscription_manager.get_pending_confirmation("user-1", "session-1")
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(no_at_results), 1)
        self.assertIn("已为你创建群订阅", no_at_results[0])
        self.assertIsNone(pending_after)
        self.assertEqual(len(subscriptions), 1)

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
        subscriptions = await self.plugin.subscription_manager.list_subscriptions()

        self.assertEqual(len(first_results), 1)
        self.assertIn("已为你创建群订阅", first_results[0])
        self.assertEqual(duplicate_results, ["这个直播间在当前群里已经订阅过了：https://live.bilibili.com/32800932"])
        self.assertEqual(len(subscriptions), 1)
