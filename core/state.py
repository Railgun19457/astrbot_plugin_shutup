"""Silence state persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

PERMANENT_EXPIRY = -1.0


class SilenceStore:
    """Persisted silence state: ``origin -> expiry_timestamp``.

    Wraps a flat JSON file at ``<data_dir>/silence_map.json``.
    """

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._path: Path = data_dir / "silence_map.json"
        self._entries: dict[str, dict[str, Any]] = {}
        self.load()

    def _normalize_entry(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None

        expiry = raw.get("expiry")
        if expiry is None:
            return None

        entry = {
            "expiry": float(expiry),
        }
        started_at = raw.get("started_at")
        if isinstance(started_at, (int, float)):
            entry["started_at"] = float(started_at)
        if "original_card" in raw:
            entry["original_card"] = str(raw.get("original_card") or "")
        if "original_nickname" in raw:
            entry["original_nickname"] = str(raw.get("original_nickname") or "")
        return entry

    # -- persistence ------------------------------------------------------- #

    def load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    raw_entries = json.load(f)

                normalized_entries: dict[str, dict[str, Any]] = {}
                for origin, raw in raw_entries.items():
                    entry = self._normalize_entry(raw)
                    if entry is not None:
                        normalized_entries[origin] = entry

                self._entries = normalized_entries
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
        entry = self._entries.get(origin, {})
        entry["expiry"] = expiry
        entry["started_at"] = time.time()
        self._entries[origin] = entry

    def set_permanent(self, origin: str) -> None:
        """Set a permanent silence entry for *origin*."""
        entry = self._entries.get(origin, {})
        entry["expiry"] = PERMANENT_EXPIRY
        entry["started_at"] = time.time()
        self._entries[origin] = entry

    def set_original_identity(
        self,
        origin: str,
        original_card: str,
        original_nickname: str,
    ) -> None:
        """Persist original bot names for group-card restoration."""

        entry = self._entries.get(origin, {})
        entry["original_card"] = original_card
        entry["original_nickname"] = original_nickname
        self._entries[origin] = entry

    def remove(self, origin: str) -> None:
        """Remove *origin* from the store."""
        self._entries.pop(origin, None)

    def get(self, origin: str) -> float | None:
        """Return the expiry timestamp for *origin*, or ``None``."""
        entry = self._entries.get(origin)
        if entry is None:
            return None
        expiry = entry.get("expiry")
        return float(expiry) if expiry is not None else None

    def get_started_at(self, origin: str) -> float | None:
        """Return the silence start timestamp for *origin*, or ``None``."""

        entry = self._entries.get(origin)
        if entry is None:
            return None
        started_at = entry.get("started_at")
        return float(started_at) if isinstance(started_at, (int, float)) else None

    def get_original_identity(self, origin: str) -> tuple[str, str]:
        """Return persisted original ``(card, nickname)`` for *origin*."""

        entry = self._entries.get(origin, {})
        return (
            str(entry.get("original_card") or ""),
            str(entry.get("original_nickname") or ""),
        )

    def is_permanent(self, origin: str) -> bool:
        """Return whether *origin* is permanently silenced."""
        return self.get(origin) == PERMANENT_EXPIRY

    @property
    def active_origins(self) -> list[str]:
        """Return a snapshot of currently active origin keys."""
        return list(self._entries)

    def __contains__(self, origin: str) -> bool:
        return origin in self._entries

    def __len__(self) -> int:
        return len(self._entries)
