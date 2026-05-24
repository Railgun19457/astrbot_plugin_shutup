"""Configuration parsing and normalisation utilities."""

from __future__ import annotations

import re
from datetime import datetime

from astrbot.api import logger

TIME_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# ------------------------------------------------------------------ #
#  Time-range parsing
# ------------------------------------------------------------------ #


def parse_time_ranges(time_config: list[str]) -> list[tuple[str, str]]:
    """Parse sleep time ranges from configuration list.

    Each non-empty, non-comment item should match ``HH:MM-HH:MM``.
    Cross-midnight ranges (e.g. ``23:00-07:00``) are supported.

    Args:
        time_config: List from ``sleep_time_ranges`` config.

    Returns:
        List of ``(start_time, end_time)`` string tuples.
    """
    time_ranges: list[tuple[str, str]] = []

    for item in time_config:
        line = str(item).strip()
        if not line or line.startswith("#"):
            continue

        match = re.match(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", line)
        if not match:
            logger.warning(f"[Shutup] 无法解析时间范围: {line}")
            continue

        start_time, end_time = match.groups()
        try:
            datetime.strptime(start_time, "%H:%M")
            datetime.strptime(end_time, "%H:%M")
            time_ranges.append((start_time, end_time))
        except ValueError:
            logger.warning(f"[Shutup] 无效的时间格式: {line}")

    return time_ranges


# ------------------------------------------------------------------ #
#  Command normalisation
# ------------------------------------------------------------------ #


def normalize_commands(raw: list[str], fallback: list[str] | None = None) -> list[str]:
    """Normalize a command list from config.

    The original order is preserved: the first item is used as the framework
    command name and the rest are registered as aliases.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for cmd in raw:
        cmd = str(cmd).strip()
        if not cmd or cmd in seen:
            continue
        normalized.append(cmd)
        seen.add(cmd)

    if not normalized and fallback:
        return normalize_commands(fallback)
    return normalized


# ------------------------------------------------------------------ #
#  Duration clamping
# ------------------------------------------------------------------ #


def clamp_duration(
    value: int | float,
    default: int = 600,
    min_val: int = 0,
    max_val: int = 86400,
    field_name: str = "duration",
) -> int:
    """Validate and clamp a duration value.

    Returns *default* (and logs a warning) when *value* is not a number
    or falls outside ``[min_val, max_val]``.

    Args:
        value: Raw duration from config.
        default: Fallback duration in seconds.
        min_val: Minimum allowed value.
        max_val: Maximum allowed value (24 h).
        field_name: Config field name used in warning logs.

    Returns:
        Clamped duration in seconds.
    """
    if not isinstance(value, (int, float)) or not (min_val <= value <= max_val):
        logger.warning(
            f"[Shutup] {field_name} ({value}) is invalid, falling back to {default}s"
        )
        return default
    return int(value)
