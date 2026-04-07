from __future__ import annotations

import re
import asyncio
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
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        "Referer": "https://live.bilibili.com/",
        "Origin": "https://live.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
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
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout, headers=self.DEFAULT_HEADERS, follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    if attempt > 0:
                        await asyncio.sleep(0.8 * attempt)

                    response = await client.get(url, params=params)

                    if response.status_code == 412:
                        await self._refresh_bilibili_cookies(client)
                        response = await client.get(url, params=params)

                    response.raise_for_status()
                    payload = response.json()

                    if payload.get("code") == -412:
                        raise ValueError("Bilibili API 请求被风控拦截（-412），请稍后重试")

                    if payload.get("code") != 0 or not payload.get("data"):
                        raise ValueError(payload.get("message") or "获取直播间信息失败")

                    data: dict[str, Any] = payload["data"]
                    resolved_room_id = int(data.get("room_id") or room_id)
                    return RoomInfo(
                        room_id=resolved_room_id,
                        room_url=f"https://live.bilibili.com/{resolved_room_id}",
                        title=str(data.get("title") or "未命名直播间"),
                        uname=str(
                            data.get("uname")
                            or data.get("anchor_info", {}).get("base_info", {}).get("uname")
                            or f"房间{resolved_room_id}"
                        ),
                        live_status=int(data.get("live_status") or 0),
                        area_name=str(data.get("area_name") or ""),
                    )
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code in {412, 429, 500, 502, 503, 504} and attempt < 2:
                        continue
                    if exc.response.status_code == 412:
                        raise ValueError("Bilibili API 返回 HTTP 412，可能触发了风控，请稍后重试") from exc
                    raise ValueError(f"Bilibili API 请求失败，HTTP {exc.response.status_code}") from exc
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        continue
                    raise

        raise ValueError(f"获取直播间信息失败：{last_error}") from last_error

    async def _refresh_bilibili_cookies(self, client: httpx.AsyncClient) -> None:
        await client.get("https://live.bilibili.com/", headers=self.DEFAULT_HEADERS)
