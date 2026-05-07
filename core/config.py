"""Configuration parsing and normalisation utilities."""

from __future__ import annotations

import re
from datetime import datetime

from astrbot.api import logger

# ------------------------------------------------------------------ #
#  Time-range parsing
# ------------------------------------------------------------------ #


def parse_time_ranges(time_config: str | list[str]) -> list[tuple[str, str]]:
    """Parse scheduled shutup time ranges from configuration text.

    Each non-empty, non-comment item should match ``HH:MM-HH:MM``.
    Cross-midnight ranges (e.g. ``23:00-07:00``) are supported.

    Args:
        time_config: List or multi-line text from ``scheduled_shutup_times`` config.

    Returns:
        List of ``(start_time, end_time)`` string tuples.
    """
    time_ranges: list[tuple[str, str]] = []

    if isinstance(time_config, str):
        lines = time_config.strip().split("\n")
    else:
        lines = [str(item) for item in time_config]

    for line in lines:
        line = line.strip()
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


def normalize_commands(
    raw: str | list[str], fallback: list[str] | None = None
) -> list[str]:
    """Normalize a command list from config.

    Strings are split on whitespace/commas. The original order is preserved:
    the first item is used as the framework command name and the rest are
    registered as aliases.
    """
    if isinstance(raw, str):
        cmds = re.split(r"[\s,]+", raw)
    else:
        cmds = list(raw)

    normalized: list[str] = []
    seen: set[str] = set()
    for cmd in cmds:
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
) -> int:
    """Validate and clamp a duration value.

    Returns *default* (and logs a warning) when *value* is not a number
    or falls outside ``[min_val, max_val]``.

    Args:
        value: Raw duration from config.
        default: Fallback duration in seconds.
        min_val: Minimum allowed value.
        max_val: Maximum allowed value (24 h).

    Returns:
        Clamped duration in seconds.
    """
    if not isinstance(value, (int, float)) or not (min_val <= value <= max_val):
        logger.warning(
            f"[Shutup] default_duration ({value}) is invalid, "
            f"falling back to {default}s"
        )
        return default
    return int(value)
