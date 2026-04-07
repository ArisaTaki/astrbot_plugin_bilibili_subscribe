from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BilibiliSubscribeConfig:
    check_interval_seconds: int = 60
    max_subscriptions_per_user: int = 20
    live_on_template: str = "【开播提醒】{title}\n主播：{uname}\n直播间：{room_url}"
    live_off_template: str = "【下播提醒】{uname} 的直播已结束\n直播间：{room_url}"
    bilibili_api_timeout_seconds: float = 10.0

    @classmethod
    def from_plugin_config(cls, raw_config: Any) -> "BilibiliSubscribeConfig":
        data = raw_config or {}

        def _get(key: str, default: Any):
            if isinstance(data, dict):
                return data.get(key, default)
            return getattr(data, key, default)

        return cls(
            check_interval_seconds=max(15, int(_get("check_interval_seconds", cls.check_interval_seconds))),
            max_subscriptions_per_user=max(1, int(_get("max_subscriptions_per_user", cls.max_subscriptions_per_user))),
            live_on_template=str(_get("live_on_template", cls.live_on_template)),
            live_off_template=str(_get("live_off_template", cls.live_off_template)),
            bilibili_api_timeout_seconds=max(3.0, float(_get("bilibili_api_timeout_seconds", cls.bilibili_api_timeout_seconds))),
        )
