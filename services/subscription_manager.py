from __future__ import annotations

from datetime import datetime
from typing import Any

from services.bilibili_client import RoomInfo
from utils.json_storage import JsonStorage


class SubscriptionManager:
    def __init__(self, storage: JsonStorage):
        self.storage = storage

    @staticmethod
    def default_payload() -> dict[str, Any]:
        return {
            "subscriptions": [],
            "pending_confirmations": [],
        }

    async def initialize(self) -> None:
        await self.storage.ensure_default(self.default_payload())

    async def list_subscriptions(self) -> list[dict[str, Any]]:
        data = await self.storage.load()
        return list(data.get("subscriptions", []))

    async def add_pending_confirmation(self, pending: dict[str, Any]) -> None:
        data = await self.storage.load() or self.default_payload()
        pendings = data.setdefault("pending_confirmations", [])
        pendings = [p for p in pendings if not self._same_pending_scope(p, pending)]
        pendings.append(pending)
        data["pending_confirmations"] = pendings
        await self.storage.save(data)

    async def get_pending_confirmation(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        data = await self.storage.load() or self.default_payload()
        for pending in data.get("pending_confirmations", []):
            if pending.get("user_id") == user_id and pending.get("session_id") == session_id:
                return pending
        return None

    async def remove_pending_confirmation(self, user_id: str, session_id: str) -> None:
        data = await self.storage.load() or self.default_payload()
        data["pending_confirmations"] = [
            p for p in data.get("pending_confirmations", [])
            if not (p.get("user_id") == user_id and p.get("session_id") == session_id)
        ]
        await self.storage.save(data)

    async def count_user_subscriptions(self, user_id: str) -> int:
        subscriptions = await self.list_subscriptions()
        return sum(1 for item in subscriptions if item.get("user_id") == user_id)

    async def upsert_subscription(
        self,
        *,
        room_info: RoomInfo,
        user_id: str,
        group_id: str | None,
        mode: str,
        notify_origin: str,
        session_id: str,
    ) -> dict[str, Any]:
        data = await self.storage.load() or self.default_payload()
        subscriptions = data.setdefault("subscriptions", [])
        now = datetime.now().isoformat(timespec="seconds")

        found = None
        for item in subscriptions:
            if (
                item.get("room_id") == room_info.room_id
                and item.get("user_id") == user_id
                and item.get("mode") == mode
                and (item.get("group_id") or "") == (group_id or "")
            ):
                found = item
                break

        if found is None:
            found = {
                "room_id": room_info.room_id,
                "room_url": room_info.room_url,
                "user_id": user_id,
                "group_id": group_id,
                "mode": mode,
                "session_id": session_id,
                "notify_origin": notify_origin,
                "created_at": now,
                "last_live_status": room_info.live_status,
                "last_notified_status": room_info.live_status,
                "last_title": room_info.title,
                "last_uname": room_info.uname,
            }
            subscriptions.append(found)
        else:
            found.update(
                {
                    "room_url": room_info.room_url,
                    "notify_origin": notify_origin,
                    "session_id": session_id,
                    "last_title": room_info.title,
                    "last_uname": room_info.uname,
                }
            )

        await self.storage.save(data)
        return found

    async def update_subscription_state(self, subscription: dict[str, Any], room_info: RoomInfo) -> None:
        data = await self.storage.load() or self.default_payload()
        for item in data.get("subscriptions", []):
            if self._same_subscription(item, subscription):
                item["last_live_status"] = room_info.live_status
                item["last_title"] = room_info.title
                item["last_uname"] = room_info.uname
                break
        await self.storage.save(data)

    async def mark_notified(self, subscription: dict[str, Any], live_status: int) -> None:
        data = await self.storage.load() or self.default_payload()
        for item in data.get("subscriptions", []):
            if self._same_subscription(item, subscription):
                item["last_notified_status"] = live_status
                break
        await self.storage.save(data)

    @staticmethod
    def _same_subscription(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (
            left.get("room_id") == right.get("room_id")
            and left.get("user_id") == right.get("user_id")
            and (left.get("group_id") or "") == (right.get("group_id") or "")
            and left.get("mode") == right.get("mode")
        )

    @staticmethod
    def _same_pending_scope(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return left.get("user_id") == right.get("user_id") and left.get("session_id") == right.get("session_id")
