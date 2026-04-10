from __future__ import annotations

import re
from dataclasses import dataclass

from .bilibili_client import BilibiliClient


@dataclass
class ParsedIntent:
    room_id: int | None
    raw_text: str
    should_handle: bool
    mode: str | None = None


class IntentParser:
    KEYWORDS = ("订阅", "直播间", "bilibili", "哔哩", "b站", "开播提醒", "下播提醒")
    REMARK_PREFIX_RE = re.compile(r"(?:备注|别名|昵称)\s*(?:是|为|[:：=])?\s*", re.IGNORECASE)
    QUOTED_REMARK_RE = re.compile(r"(?:备注|别名|昵称)\s*(?:是|为|[:：=])?\s*[\"“](.+?)[\"”]", re.IGNORECASE)
    MODE_SUFFIXES = (
        "private",
        "group",
        "私聊订阅",
        "私聊提醒",
        "私聊",
        "私信提醒",
        "私信",
        "群订阅",
        "群提醒",
        "群聊",
        "群里提醒",
        "群里",
        "群内提醒",
    )
    REMARK_SKIP_REPLIES = {
        "跳过",
        "skip",
        "不用",
        "不用备注",
        "不加备注",
        "不需要",
        "不需要备注",
        "无需备注",
        "无",
        "没有",
        "none",
    }

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

    @classmethod
    def extract_remark(cls, text: str) -> str:
        content = (text or "").strip()
        if not content:
            return ""

        quoted_match = cls.QUOTED_REMARK_RE.search(content)
        if quoted_match:
            return cls._normalize_remark(quoted_match.group(1))

        prefix_match = cls.REMARK_PREFIX_RE.search(content)
        if not prefix_match:
            return ""

        remainder = content[prefix_match.end():].strip()
        if not remainder:
            return ""

        lower_remainder = remainder.lower()
        for suffix in sorted(cls.MODE_SUFFIXES, key=len, reverse=True):
            if lower_remainder.endswith(suffix.lower()):
                remainder = remainder[: -len(suffix)].strip()
                break

        return cls._normalize_remark(remainder)

    @staticmethod
    def detect_mode(text: str) -> str | None:
        content = (text or "").strip().lower()
        if any(keyword in content for keyword in ("私聊订阅", "私聊提醒", "私发", "私聊", "私信", "私信提醒")):
            return "private"
        if any(keyword in content for keyword in ("群订阅", "群提醒", "群里提醒", "群聊", "群里", "群内提醒")):
            return "group"
        return None

    @staticmethod
    def parse_mode_reply(text: str) -> str | None:
        content = (text or "").strip().lower()
        if content in {"私聊", "私聊订阅", "私聊提醒", "private", "dm", "私信"}:
            return "private"
        if content in {"群", "群订阅", "群聊", "群里", "群提醒", "group"}:
            return "group"
        return IntentParser.detect_mode(text)

    @classmethod
    def parse_remark_reply(cls, text: str) -> tuple[bool, str | None]:
        normalized = cls._normalize_remark(text)
        if not normalized:
            return False, None

        if normalized.lower() in cls.REMARK_SKIP_REPLIES:
            return True, ""

        remark = cls.extract_remark(text) or normalized
        normalized_remark = cls._normalize_remark(remark)
        if not normalized_remark:
            return False, None

        if normalized_remark.lower() in cls.REMARK_SKIP_REPLIES:
            return True, ""

        return False, normalized_remark

    @staticmethod
    def _normalize_remark(value: str) -> str:
        remark = str(value or "").strip().strip("\"'“”")
        return " ".join(remark.split())
