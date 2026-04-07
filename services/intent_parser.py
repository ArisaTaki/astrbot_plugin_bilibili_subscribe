from __future__ import annotations

from dataclasses import dataclass

from services.bilibili_client import BilibiliClient


@dataclass
class ParsedIntent:
    room_id: int | None
    raw_text: str
    should_handle: bool
    mode: str | None = None


class IntentParser:
    KEYWORDS = ("订阅", "直播间", "bilibili", "哔哩", "b站", "开播提醒", "下播提醒")

    def __init__(self, bilibili_client: BilibiliClient):
        self.bilibili_client = bilibili_client

    def parse_subscribe_intent(self, text: str) -> ParsedIntent | None:
        content = (text or "").strip()
        if not content:
            return None

        lowered = content.lower()
        if not any(keyword in lowered or keyword in content for keyword in self.KEYWORDS):
            return None

        room_id = self.bilibili_client.extract_room_id(content)
        if room_id is None:
            return ParsedIntent(room_id=None, raw_text=content, should_handle=False)

        mode = self.detect_mode(content)
        return ParsedIntent(room_id=room_id, raw_text=content, should_handle=True, mode=mode)

    @staticmethod
    def detect_mode(text: str) -> str | None:
        content = (text or "").strip().lower()
        if any(keyword in content for keyword in ("私聊订阅", "私聊提醒", "私发", "私聊")):
            return "private"
        if any(keyword in content for keyword in ("群订阅", "群提醒", "群里提醒", "群聊")):
            return "group"
        return None

    @staticmethod
    def parse_mode_reply(text: str) -> str | None:
        content = (text or "").strip().lower()
        if content in {"私聊", "私聊订阅", "私聊提醒", "private"}:
            return "private"
        if content in {"群订阅", "群聊", "群里", "群提醒", "group"}:
            return "group"
        return None
