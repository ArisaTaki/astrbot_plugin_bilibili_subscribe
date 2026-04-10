from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, register

from .config import BilibiliSubscribeConfig
from .services.bilibili_client import BilibiliClient, RoomInfo
from .services.intent_parser import IntentParser
from .services.subscription_manager import SubscriptionManager
from .utils.json_storage import JsonStorage


@register(
    "astrbot_plugin_bilibili_subscribe",
    "OpenAI",
    "供 Agent 调用的 Bilibili 直播间订阅工具",
    "0.3.0",
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

    @filter.command("bili_subscribe")
    async def subscribe_command(self, event: AstrMessageEvent):
        if not self._can_process_direct_request(event):
            return

        text = self._extract_text_after_command(getattr(event, "message_str", ""))
        result = await self._handle_command_request(event, text)
        if result:
            yield event.plain_result(result)
            self._safe_stop(event)

    @filter.llm_tool(name="subscribe_bilibili_live_room")
    async def subscribe_bilibili_live_room(
        self,
        event: AstrMessageEvent,
        room_reference: str,
        mode: str = "",
        remark: str = "",
    ) -> str:
        """为当前用户创建 Bilibili 直播提醒订阅。

        当用户明确要求订阅 Bilibili 直播间时调用。
        如果用户还没给出房间号/链接，也应调用此工具，它会记录待确认状态并继续追问。

        Args:
            room_reference(string): 直播间链接、房间号，或包含这些信息的原始用户话语
            mode(string): 提醒方式，可填 group/群订阅 或 private/私聊；未说明时可留空
            remark(string): 房间备注，例如“夏老板”；未说明时可留空
        """
        if not self._can_process_direct_request(event):
            return "群聊里只有在用户明确 @ 机器人后，才能使用 Bilibili 订阅功能。"

        return await self._handle_subscription_request(
            event,
            room_reference,
            mode=mode,
            remark=remark,
            allow_pending=True,
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        pending_handled, result = await self._handle_pending_confirmation(event)
        if pending_handled:
            if result:
                yield event.plain_result(result)
            self._safe_stop(event)
            return

        if not self._can_process_direct_request(event):
            return

        result = await self._handle_direct_intent_fallback(event)
        if result:
            yield event.plain_result(result)
            self._safe_stop(event)
            return

    async def _handle_command_request(self, event: AstrMessageEvent, text: str) -> str | None:
        await self.subscription_manager.initialize()

        if not text.strip():
            return "目前只支持订阅 Bilibili 直播间。请提供直播间链接或房间号，例如：/bili_subscribe https://live.bilibili.com/123456 group"

        return await self._handle_subscription_request(event, text, allow_pending=True)

    async def _handle_direct_intent_fallback(self, event: AstrMessageEvent) -> str | None:
        text = getattr(event, "message_str", "") or ""
        parsed = self.intent_parser.parse_subscribe_intent(text)
        if not parsed:
            return None

        logger.info(
            "bilibili_subscribe fallback user=%s session=%s room_id=%s mode=%s text=%s",
            self._get_user_id(event),
            self._get_session_id(event),
            parsed.room_id,
            parsed.mode,
            text,
        )
        return await self._handle_subscription_request(event, text, mode=parsed.mode or "", allow_pending=True)

    async def _handle_pending_confirmation(self, event: AstrMessageEvent) -> tuple[bool, str | None]:
        await self.subscription_manager.initialize()

        user_id = self._get_user_id(event)
        session_id = self._get_session_id(event)
        pending = await self.subscription_manager.get_pending_confirmation(user_id, session_id, self._get_group_id(event))
        if not pending:
            return False, None

        text = getattr(event, "message_str", "") or ""
        pending_type = str(pending.get("pending_type") or ("mode" if pending.get("room_id") else "room_id"))

        if pending_type == "room_id":
            room_id = self.bilibili_client.extract_room_id(text)
            if room_id is None:
                return True, "目前只支持订阅 Bilibili 直播间。请直接发送直播间链接或房间号。"

            await self.subscription_manager.remove_pending_confirmation(pending["user_id"], pending["session_id"])
            mode = str(pending.get("mode") or "")
            remark = str(pending.get("remark") or "")
            return True, await self._handle_subscription_request(event, text, mode=mode, remark=remark, allow_pending=True)

        mode = self.intent_parser.parse_mode_reply(text)
        if mode is None:
            return True, "请回复“私聊”或“群订阅”。"

        return True, await self._finalize_pending_subscription(event, pending, mode)

    async def _handle_subscription_request(
        self,
        event: AstrMessageEvent,
        text: str,
        *,
        mode: str = "",
        remark: str = "",
        allow_pending: bool,
    ) -> str:
        await self.subscription_manager.initialize()

        requested_mode = self._normalize_mode(mode) or self.intent_parser.detect_mode(text)
        requested_remark = self._normalize_remark(remark) or self.intent_parser.extract_remark(text)
        if mode and requested_mode is None:
            return "提醒方式只支持 `private`/`私聊` 或 `group`/`群订阅`。"

        room_id = self.bilibili_client.extract_room_id(text)
        if room_id is None:
            if allow_pending:
                await self._save_pending_confirmation(
                    event,
                    pending_type="room_id",
                    mode=requested_mode,
                    remark=requested_remark,
                )
                return "目前只支持订阅 Bilibili 直播间。请把直播间链接或房间号发给我。"
            return "目前只支持订阅 Bilibili 直播间。请提供有效的直播间链接或房间号。"

        if requested_mode is None:
            if not allow_pending:
                return "请明确说明提醒方式：`私聊` 或 `群订阅`。"

            if not self._get_group_id(event):
                requested_mode = "private"
            else:
                await self._save_pending_confirmation(
                    event,
                    pending_type="mode",
                    room_id=room_id,
                    remark=requested_remark,
                )
                logger.info(
                    "bilibili_subscribe pending user=%s session=%s room_id=%s text=%s",
                    self._get_user_id(event),
                    self._get_session_id(event),
                    room_id,
                    text,
                )
                return "要订阅到哪里？请回复“私聊”或“群订阅”。"

        logger.info(
            "bilibili_subscribe request user=%s session=%s room_id=%s mode=%s text=%s",
            self._get_user_id(event),
            self._get_session_id(event),
            room_id,
            requested_mode,
            text,
        )
        return await self._create_subscription(event, room_id, requested_mode, requested_remark)

    async def _save_pending_confirmation(
        self,
        event: AstrMessageEvent,
        *,
        pending_type: str,
        room_id: int | None = None,
        mode: str | None = None,
        remark: str | None = None,
    ) -> None:
        payload = {
            "user_id": self._get_user_id(event),
            "session_id": self._get_session_id(event),
            "group_id": self._get_group_id(event),
            "room_id": room_id,
            "mode": mode,
            "remark": self._normalize_remark(remark),
            "pending_type": pending_type,
            "origin": getattr(event, "unified_msg_origin", ""),
            "platform_name": self._get_platform_name(event),
            "created_at": self._now_iso(),
        }
        if room_id is not None:
            payload["room_url"] = f"https://live.bilibili.com/{room_id}"
        await self.subscription_manager.add_pending_confirmation(payload)

    async def _finalize_pending_subscription(self, event: AstrMessageEvent, pending: dict[str, Any], mode: str) -> str:
        await self.subscription_manager.remove_pending_confirmation(pending["user_id"], pending["session_id"])
        return await self._create_subscription(event, int(pending["room_id"]), mode, str(pending.get("remark") or ""))

    async def _create_subscription(self, event: AstrMessageEvent, room_id: int, mode: str, remark: str = "") -> str:
        user_id = self._get_user_id(event)
        group_id = self._get_group_id(event) if mode == "group" else None
        normalized_remark = self._normalize_remark(remark)

        permission_error = self._validate_subscription_permission(event, mode)
        if permission_error:
            return permission_error

        existing = await self.subscription_manager.find_subscription(
            room_id=room_id,
            user_id=user_id,
            group_id=group_id,
            mode=mode,
        )
        if existing:
            room_url = existing.get("room_url") or f"https://live.bilibili.com/{room_id}"
            if normalized_remark and normalized_remark != str(existing.get("remark") or ""):
                updated = await self.subscription_manager.update_subscription_remark(existing, normalized_remark)
                display_name = self._display_name(updated or existing)
                if mode == "group":
                    return f"这个直播间在当前群里已经订阅过了，已更新备注为“{display_name}”：{room_url}"
                return f"你已经订阅过这个直播间了，已更新备注为“{display_name}”：{room_url}"
            if mode == "group":
                return f"这个直播间在当前群里已经订阅过了：{room_url}"
            return f"你已经订阅过这个直播间了：{room_url}"

        current_count = await self.subscription_manager.count_user_subscriptions(user_id)
        if current_count >= self.plugin_config.max_subscriptions_per_user:
            return f"订阅失败：你已达到最大订阅数量限制（{self.plugin_config.max_subscriptions_per_user}）。"

        try:
            room_info = await self.bilibili_client.get_room_info(room_id)
        except Exception as exc:
            logger.warning("fetch bilibili room failed: %s", exc)
            return f"订阅失败：无法获取直播间信息，{exc}"

        notify_origin = self._build_notify_origin(event, mode)
        if not notify_origin:
            return "订阅失败：当前上下文无法确定提醒目标。群订阅请在群里发起，私聊订阅请确保平台支持主动私聊。"

        subscription = await self.subscription_manager.upsert_subscription(
            room_info=room_info,
            user_id=user_id,
            group_id=group_id,
            mode=mode,
            remark=normalized_remark,
            notify_origin=notify_origin,
            session_id=self._get_session_id(event),
        )

        mode_text = "私聊订阅" if mode == "private" else "群订阅"
        display_name = self._display_name(subscription, room_info)
        anchor_suffix = f"（主播：{room_info.uname}）" if normalized_remark and normalized_remark != room_info.uname else ""
        result = (
            f"已为你创建{mode_text}：{display_name}{anchor_suffix} / {room_info.room_url}\n"
            f"当前状态：{self._status_text(room_info.live_status)}"
        )
        logger.info("subscription created user=%s room_id=%s mode=%s subscription=%s", user_id, room_id, mode, subscription)
        return result

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
        grouped_subscriptions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for subscription in subscriptions:
            grouped_subscriptions[int(subscription["room_id"])].append(subscription)

        for room_id, room_subscriptions in grouped_subscriptions.items():
            try:
                room_info = await self.bilibili_client.get_room_info(room_id)
            except Exception as exc:
                logger.warning("poll room failed room_id=%s error=%s", room_id, exc)
                continue

            for subscription in room_subscriptions:
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
        display_name = self._display_name(subscription, room_info)
        message = self._format_template(
            template,
            title=room_info.title,
            uname=room_info.uname,
            room_url=room_info.room_url,
            room_id=room_info.room_id,
            area_name=room_info.area_name,
            remark=str(subscription.get("remark") or ""),
            display_name=display_name,
            cover_url=room_info.cover_url,
        )
        chain_parts: list[Any] = [Plain(message)]
        if room_info.cover_url:
            chain_parts.append(Image.fromURL(room_info.cover_url))
        chain = MessageChain(chain_parts)
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
    def _get_session_id(event: AstrMessageEvent) -> str:
        return getattr(event, "session_id", "") or getattr(getattr(event, "message_obj", None), "session_id", "") or ""

    @staticmethod
    def _get_message_components(event: AstrMessageEvent) -> list[Any]:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            try:
                return list(getter() or [])
            except Exception:
                pass
        return list(getattr(getattr(event, "message_obj", None), "message", []) or [])

    def _get_bot_id(self, event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_self_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass

        message_obj = getattr(event, "message_obj", None)
        self_id = getattr(message_obj, "self_id", None)
        if self_id:
            return str(self_id)

        try:
            platform_insts = self.context.platform_manager.platform_insts
            if platform_insts:
                return str(getattr(platform_insts[0], "client_self_id", "") or "")
        except Exception:
            pass

        return ""

    def _can_process_direct_request(self, event: AstrMessageEvent) -> bool:
        if not self._get_group_id(event):
            return True

        bot_id = self._get_bot_id(event)
        if not bot_id:
            return False

        for comp in self._get_message_components(event):
            if type(comp).__name__ != "At":
                continue
            if str(getattr(comp, "qq", "") or "") == bot_id:
                return True
        return False

    @staticmethod
    def _normalize_mode(mode: str | None) -> str | None:
        content = (mode or "").strip().lower()
        if not content:
            return None
        if content in {"private", "private_message", "dm", "私聊", "私聊订阅", "私聊提醒", "私发", "私信", "私信提醒"}:
            return "private"
        if content in {"group", "group_message", "群", "群订阅", "群提醒", "群里提醒", "群聊", "群内提醒"}:
            return "group"
        return None

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

    @staticmethod
    def _get_group_id(event: AstrMessageEvent) -> str | None:
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass

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

    def _validate_subscription_permission(self, event: AstrMessageEvent, mode: str) -> str | None:
        if mode == "group":
            if not self._get_group_id(event):
                return "群订阅只能在群聊里发起。"
            if not self._is_group_admin(event):
                return "只有群管理员才能创建群订阅。"
            return None

        if mode == "private" and not self._is_private_chat(event):
            return "私聊订阅请先添加机器人好友，再在私聊里发起。"

        return None

    @staticmethod
    def _is_group_admin(event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass

        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        for attr in ("role", "permission", "group_role"):
            value = str(getattr(sender, attr, "") or "").strip().lower()
            if value in {"admin", "administrator", "owner"}:
                return True

        for attr in ("is_admin", "admin"):
            value = getattr(sender, attr, None)
            if isinstance(value, bool) and value:
                return True

        return False

    def _is_private_chat(self, event: AstrMessageEvent) -> bool:
        checker = getattr(event, "is_private_chat", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                pass
        return self._get_group_id(event) is None

    @staticmethod
    def _normalize_remark(value: str | None) -> str:
        return " ".join(str(value or "").strip().strip("\"'“”").split())

    @classmethod
    def _display_name(cls, subscription: dict[str, Any], room_info: RoomInfo | None = None) -> str:
        remark = cls._normalize_remark(str(subscription.get("remark") or ""))
        if remark:
            return remark

        if room_info and room_info.uname:
            return room_info.uname

        uname = str(subscription.get("last_uname") or "").strip()
        if uname:
            return uname

        room_id = subscription.get("room_id")
        return f"房间{room_id}" if room_id else "直播间"

    @staticmethod
    def _format_template(template: str, **kwargs: Any) -> str:
        class _SafeDict(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return str(template).format_map(_SafeDict(kwargs))
