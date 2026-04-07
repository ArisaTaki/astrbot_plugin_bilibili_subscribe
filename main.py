from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from .config import BilibiliSubscribeConfig
from .services.bilibili_client import BilibiliClient, RoomInfo
from .services.intent_parser import IntentParser
from .services.subscription_manager import SubscriptionManager
from .utils.json_storage import JsonStorage


@register(
    "astrbot_plugin_bilibili_subscribe",
    "OpenAI",
    "根据自然语言订阅 Bilibili 直播间开播/下播提醒",
    "0.1.0",
)
class BilibiliSubscribePlugin(Star):
    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.context = context
        self.plugin_config = BilibiliSubscribeConfig.from_plugin_config(config)
        self.data_file = Path(__file__).parent / "data" / "subscriptions.json"
        self.storage = JsonStorage(self.data_file)
        self.subscription_manager = SubscriptionManager(self.storage)
        self.bilibili_client = BilibiliClient(self.plugin_config.bilibili_api_timeout_seconds)
        self.intent_parser = IntentParser(self.bilibili_client)
        self._polling_task: asyncio.Task | None = None
        self._startup_task: asyncio.Task | None = asyncio.create_task(self._startup())

    async def _startup(self) -> None:
        await self.subscription_manager.initialize()
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info("astrbot_plugin_bilibili_subscribe started with interval=%s", self.plugin_config.check_interval_seconds)

    async def terminate(self) -> None:
        for task in (self._startup_task, self._polling_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (self._startup_task, self._polling_task) if task), return_exceptions=True)

    @filter.command("bili_subscribe", alias={"订阅直播间", "b站订阅"})
    async def subscribe_command(self, event: AstrMessageEvent):
        text = self._extract_text_after_command(event.message_str)
        async for result in self._handle_message(event, text):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = getattr(event, "message_str", "") or ""
        async for result in self._handle_message(event, text):
            yield result

    async def _handle_message(self, event: AstrMessageEvent, text: str):
        await self.subscription_manager.initialize()

        user_id = self._get_user_id(event)
        session_id = getattr(event, "session_id", "") or getattr(getattr(event, "message_obj", None), "session_id", "") or ""

        pending = await self.subscription_manager.get_pending_confirmation(user_id, session_id)
        if pending:
            mode = self.intent_parser.parse_mode_reply(text)
            if mode is None:
                return
            async for result in self._finalize_pending_subscription(event, pending, mode):
                yield result
            return

        parsed = self.intent_parser.parse_subscribe_intent(text)
        if not parsed:
            return

        if parsed.room_id is None:
            yield event.plain_result("我识别到你想订阅 Bilibili 直播间，但没有找到有效的直播间链接或房间号。")
            self._safe_stop(event)
            return

        if parsed.mode is None:
            await self.subscription_manager.add_pending_confirmation(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "group_id": self._get_group_id(event),
                    "room_id": parsed.room_id,
                    "room_url": f"https://live.bilibili.com/{parsed.room_id}",
                    "origin": event.unified_msg_origin,
                    "platform_name": self._get_platform_name(event),
                    "created_at": self._now_iso(),
                }
            )
            yield event.plain_result("要订阅到哪里？请回复“私聊”或“群订阅”。")
            self._safe_stop(event)
            return

        async for result in self._create_subscription(event, parsed.room_id, parsed.mode):
            yield result

    async def _finalize_pending_subscription(self, event: AstrMessageEvent, pending: dict[str, Any], mode: str):
        await self.subscription_manager.remove_pending_confirmation(pending["user_id"], pending["session_id"])
        async for result in self._create_subscription(event, int(pending["room_id"]), mode):
            yield result

    async def _create_subscription(self, event: AstrMessageEvent, room_id: int, mode: str):
        user_id = self._get_user_id(event)
        current_count = await self.subscription_manager.count_user_subscriptions(user_id)
        if current_count >= self.plugin_config.max_subscriptions_per_user:
            yield event.plain_result(f"订阅失败：你已达到最大订阅数量限制（{self.plugin_config.max_subscriptions_per_user}）。")
            self._safe_stop(event)
            return

        try:
            room_info = await self.bilibili_client.get_room_info(room_id)
        except Exception as exc:
            logger.warning("fetch bilibili room failed: %s", exc)
            yield event.plain_result(f"订阅失败：无法获取直播间信息，{exc}")
            self._safe_stop(event)
            return

        notify_origin = self._build_notify_origin(event, mode)
        if not notify_origin:
            yield event.plain_result("订阅失败：当前上下文无法确定提醒目标。群订阅请在群里发起，私聊订阅请确保平台支持主动私聊。")
            self._safe_stop(event)
            return

        subscription = await self.subscription_manager.upsert_subscription(
            room_info=room_info,
            user_id=user_id,
            group_id=self._get_group_id(event) if mode == "group" else None,
            mode=mode,
            notify_origin=notify_origin,
            session_id=getattr(event, "session_id", ""),
        )

        mode_text = "私聊订阅" if mode == "private" else "群订阅"
        yield event.plain_result(
            f"已为你创建{mode_text}：{room_info.uname} / {room_info.room_url}\n"
            f"当前状态：{self._status_text(room_info.live_status)}"
        )
        logger.info("subscription created user=%s room_id=%s mode=%s subscription=%s", user_id, room_id, mode, subscription)
        self._safe_stop(event)

    async def _polling_loop(self) -> None:
        await self.subscription_manager.initialize()
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("bilibili polling loop error: %s", exc)
            await asyncio.sleep(self.plugin_config.check_interval_seconds)

    async def _poll_once(self) -> None:
        subscriptions = await self.subscription_manager.list_subscriptions()
        for subscription in subscriptions:
            room_id = int(subscription["room_id"])
            try:
                room_info = await self.bilibili_client.get_room_info(room_id)
            except Exception as exc:
                logger.warning("poll room failed room_id=%s error=%s", room_id, exc)
                continue

            previous_status = int(subscription.get("last_live_status", 0))
            current_status = int(room_info.live_status)
            await self.subscription_manager.update_subscription_state(subscription, room_info)

            if previous_status == current_status:
                continue

            if previous_status != 1 and current_status == 1:
                await self._send_notification(subscription, room_info, is_live=True)
                await self.subscription_manager.mark_notified(subscription, current_status)
            elif previous_status == 1 and current_status != 1:
                await self._send_notification(subscription, room_info, is_live=False)
                await self.subscription_manager.mark_notified(subscription, current_status)

    async def _send_notification(self, subscription: dict[str, Any], room_info: RoomInfo, *, is_live: bool) -> None:
        template = self.plugin_config.live_on_template if is_live else self.plugin_config.live_off_template
        message = template.format(
            title=room_info.title,
            uname=room_info.uname,
            room_url=room_info.room_url,
            room_id=room_info.room_id,
            area_name=room_info.area_name,
        )
        chain = MessageChain().message(message)
        try:
            await self.context.send_message(subscription["notify_origin"], chain)
            logger.info(
                "notification sent room_id=%s mode=%s target=%s status=%s",
                room_info.room_id,
                subscription.get("mode"),
                subscription.get("notify_origin"),
                room_info.live_status,
            )
        except Exception as exc:
            logger.warning("send notification failed room_id=%s error=%s", room_info.room_id, exc)

    @staticmethod
    def _get_user_id(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_sender_id", None)
        if callable(getter):
            return str(getter())
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        return str(getattr(sender, "user_id", ""))

    @staticmethod
    def _get_group_id(event: AstrMessageEvent) -> str | None:
        group_id = getattr(getattr(event, "message_obj", None), "group_id", "")
        return str(group_id) if group_id else None

    @staticmethod
    def _get_platform_name(event: AstrMessageEvent) -> str:
        platform_meta = getattr(event, "platform_meta", None)
        for attr in ("platform_name", "name"):
            value = getattr(platform_meta, attr, None)
            if value:
                return str(value)
        origin = getattr(event, "unified_msg_origin", "")
        return origin.split(":", 1)[0] if ":" in origin else "unknown"

    def _build_notify_origin(self, event: AstrMessageEvent, mode: str) -> str | None:
        if mode == "group":
            group_id = self._get_group_id(event)
            if not group_id:
                return None
            return getattr(event, "unified_msg_origin", None)

        user_id = self._get_user_id(event)
        platform_name = self._get_platform_name(event)
        if not user_id or not platform_name:
            return None
        return f"{platform_name}:private_message:{user_id}"

    @staticmethod
    def _extract_text_after_command(message: str) -> str:
        parts = (message or "").split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _status_text(status: int) -> str:
        if status == 1:
            return "直播中"
        if status == 2:
            return "轮播中"
        return "未开播"

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _safe_stop(event: AstrMessageEvent) -> None:
        stop_fn = getattr(event, "stop_event", None)
        if callable(stop_fn):
            stop_fn()
