from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class JsonStorage:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._lock = asyncio.Lock()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    async def ensure_default(self, default_data: dict[str, Any]) -> None:
        async with self._lock:
            if self.file_path.exists():
                return
            self._write_unlocked(default_data)

    async def load(self) -> dict[str, Any]:
        async with self._lock:
            if not self.file_path.exists():
                return {}
            with self.file_path.open("r", encoding="utf-8") as f:
                return json.load(f)

    async def save(self, data: dict[str, Any]) -> None:
        async with self._lock:
            self._write_unlocked(data)

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        tmp_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.file_path)
