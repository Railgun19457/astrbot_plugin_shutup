"""Silence state persistence."""

from __future__ import annotations

import json
from pathlib import Path

from astrbot.api import logger


class SilenceStore:
    """Persisted silence state: ``origin -> expiry_timestamp``.

    Wraps a flat JSON file at ``<data_dir>/silence_map.json``.
    """

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path: Path = data_dir / "silence_map.json"
        self._entries: dict[str, float] = {}
        self.load()

    # -- persistence ------------------------------------------------------- #

    def load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    self._entries = json.load(f)
                self._entries = {k: float(v) for k, v in self._entries.items()}
                if self._entries:
                    logger.info(f"[Shutup] 加载了 {len(self._entries)} 条禁言记录")
        except Exception as e:
            logger.warning(f"[Shutup] 加载禁言记录失败: {e}")

    def save(self) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._entries, f)
        except Exception as e:
            logger.warning(f"[Shutup] 保存禁言记录失败: {e}")

    # -- dict-like access -------------------------------------------------- #

    def set(self, origin: str, expiry: float) -> None:
        """Set an expiry timestamp for *origin*."""
        self._entries[origin] = expiry

    def remove(self, origin: str) -> None:
        """Remove *origin* from the store."""
        self._entries.pop(origin, None)

    def get(self, origin: str) -> float | None:
        """Return the expiry timestamp for *origin*, or ``None``."""
        return self._entries.get(origin)

    def clean_expired(self, now: float) -> None:
        """Remove all entries whose expiry has passed."""
        expired = [origin for origin, expiry in self._entries.items() if expiry <= now]
        for origin in expired:
            self._entries.pop(origin, None)

    @property
    def active_origins(self) -> list[str]:
        """Return a snapshot of currently active origin keys."""
        return list(self._entries)

    def __contains__(self, origin: str) -> bool:
        return origin in self._entries

    def __len__(self) -> int:
        return len(self._entries)
