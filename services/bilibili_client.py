from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class RoomInfo:
    room_id: int
    room_url: str
    title: str
    uname: str
    live_status: int
    area_name: str


class BilibiliClient:
    ROOM_URL_RE = re.compile(r"(?:https?://)?live\.bilibili\.com/(\d+)", re.IGNORECASE)
    ROOM_ID_HINT_PATTERNS = (
        re.compile(r"(?:房间号|直播间|直播)\s*(?:是|为|:|：)?\s*(\d{3,12})", re.IGNORECASE),
        re.compile(r"(?:订阅|关注)\s*(?:直播间|直播)\s*(\d{3,12})", re.IGNORECASE),
    )
    GENERIC_LONG_NUMBER_RE = re.compile(r"(?<!\d)(\d{5,12})(?!\d)")

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def extract_room_id(self, text: str) -> int | None:
        match = self.ROOM_URL_RE.search(text)
        if match:
            return int(match.group(1))

        stripped = text.strip()
        if stripped.isdigit():
            return int(stripped)

        for pattern in self.ROOM_ID_HINT_PATTERNS:
            match = pattern.search(text)
            if match:
                return int(match.group(1))

        all_numbers = self.GENERIC_LONG_NUMBER_RE.findall(text)
        if len(all_numbers) == 1:
            return int(all_numbers[0])

        if len(all_numbers) > 1:
            return int(all_numbers[-1])

        return None

    async def get_room_info(self, room_id: int) -> RoomInfo:
        url = "https://api.live.bilibili.com/room/v1/Room/get_info"
        params = {"room_id": room_id}
        timeout = httpx.Timeout(self.timeout_seconds)

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 0 or not payload.get("data"):
            raise ValueError(payload.get("message") or "获取直播间信息失败")

        data: dict[str, Any] = payload["data"]
        resolved_room_id = int(data.get("room_id") or room_id)
        return RoomInfo(
            room_id=resolved_room_id,
            room_url=f"https://live.bilibili.com/{resolved_room_id}",
            title=str(data.get("title") or "未命名直播间"),
            uname=str(data.get("uname") or data.get("anchor_info", {}).get("base_info", {}).get("uname") or f"房间{resolved_room_id}"),
            live_status=int(data.get("live_status") or 0),
            area_name=str(data.get("area_name") or ""),
        )
