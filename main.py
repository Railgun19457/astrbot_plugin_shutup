"""AstrBot Shutup Plugin — main entry point."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.star_tools import StarTools

from .core.config import clamp_duration, normalize_commands, parse_time_ranges
from .core.group_card import GroupCardUpdater
from .core.handlers import MessageHandlers
from .core.state import SilenceStore
from .tools.shutup_tool import ShutupTool


class ShutupPlugin(Star):
    """让 bot 闭嘴 — 支持指令、定时、LLM 工具调用、群昵称显示。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        # -- Plugin identity --------------------------------------------- #
        self.plugin_priority: int = config.get("priority", 10000)

        # -- Wake & command config --------------------------------------- #
        self.wake_prefix: list[str] = self.context.get_config().get("wake_prefix", [])
        self.shutup_cmds: list[str] = normalize_commands(
            config.get("shutup_commands", ["闭嘴", "stop"])
        )
        self.unshutup_cmds: list[str] = normalize_commands(
            config.get("unshutup_commands", ["说话", "停止闭嘴"])
        )

        self.require_prefix: bool = config.get("require_prefix", False)
        self.require_admin: bool = config.get("require_admin", False)

        # -- Duration settings ------------------------------------------- #
        self.default_duration: int = clamp_duration(config.get("default_duration", 600))

        # -- Reply templates --------------------------------------------- #
        self.shutup_reply: str = config.get("shutup_reply", "好的，我闭嘴了~")
        self.unshutup_reply: str = config.get("unshutup_reply", "好的，我恢复说话了~")

        # -- Group card -------------------------------------------------- #
        self.group_card_enabled: bool = config.get("group_card_update_enabled", False)
        self.group_card_template: str = config.get(
            "group_card_template", "[闭嘴中 {remaining}分钟]"
        )

        # -- Scheduled shutup -------------------------------------------- #
        self.scheduled_enabled: bool = config.get("scheduled_shutup_enabled", False)
        self.scheduled_times_text: str = config.get(
            "scheduled_shutup_times", "23:00-07:00"
        )
        self.scheduled_time_ranges: list[tuple[str, str]] = parse_time_ranges(
            self.scheduled_times_text
        )
        if self.scheduled_enabled and not self.scheduled_time_ranges:
            logger.warning("[Shutup] 未配置有效的定时时间段，定时闭嘴将不会生效")

        # -- Sleep / wake ------------------------------------------------- #
        self.bot_name: str = config.get("bot_name", "小爱")
        self.sleep_mode_enabled: bool = config.get("sleep_mode_enabled", True)
        self.temp_wake_map: dict[str, float] = {}

        raw_temp_wake = config.get("temp_wake_duration", 300)
        try:
            twd = int(raw_temp_wake)
        except (TypeError, ValueError):
            logger.warning(
                f"[Shutup] Invalid temp_wake_duration={raw_temp_wake!r}, "
                "falling back to 300s"
            )
            twd = 300
        if twd < 0:
            logger.warning(
                f"[Shutup] temp_wake_duration is negative ({twd}), clamping to 0"
            )
            twd = 0
        self.temp_wake_duration: int = twd

        # -- Persisted state ---------------------------------------------- #
        data_dir = StarTools.get_data_dir("astrbot_plugin_shutup")
        self._store = SilenceStore(data_dir)

        # -- Sub-modules -------------------------------------------------- #
        self._group_card = GroupCardUpdater(self)
        self._handlers = MessageHandlers(self)

        # -- LLM tool (recommended pattern) ------------------------------ #
        self._register_llm_tools()

        # -- Load-time summary -------------------------------------------- #
        time_info = ""
        if self.scheduled_enabled:
            time_info = " | 定时: " + ", ".join(
                f"{s}-{e}" for s, e in self.scheduled_time_ranges
            )
        logger.info(
            f"[Shutup] 已加载 | 指令: {self.shutup_cmds} & {self.unshutup_cmds}"
            f" | 默认时长: {self.default_duration}s"
            f" | 优先级: {self.plugin_priority}{time_info}"
        )
        if self.group_card_enabled:
            logger.info(f"[Shutup] 群昵称更新已启用 | 模板: {self.group_card_template}")

    def _unregister_llm_tools(self) -> None:
        """Remove this plugin's LLM tool from AstrBot's tool list."""

        tool_mgr = self.context.get_llm_tool_manager()
        tool_mgr.func_list = [
            tool
            for tool in tool_mgr.func_list
            if not (
                tool.name == ShutupTool.name
                and getattr(tool, "handler_module_path", None) == self.__module__
            )
        ]

    def _register_llm_tools(self) -> None:
        """Register the LLM tool only when enabled in plugin config."""

        self._unregister_llm_tools()
        if not self.config.get("llm_tool_enabled", False):
            logger.info("[Shutup] LLM 工具未启用，跳过注册")
            return

        self.context.add_llm_tools(ShutupTool(plugin=self))
        logger.info("[Shutup] 已注册 LLM 工具: shutup")

    # ------------------------------------------------------------------ #
    #  Time helper (used by handlers)
    # ------------------------------------------------------------------ #

    def _is_in_scheduled_time(self) -> bool:
        if not self.scheduled_enabled or not self.scheduled_time_ranges:
            return False

        current_minutes = datetime.now().hour * 60 + datetime.now().minute
        for start_s, end_s in self.scheduled_time_ranges:
            sh, sm = map(int, start_s.split(":"))
            eh, em = map(int, end_s.split(":"))
            start_m = sh * 60 + sm
            end_m = eh * 60 + em

            if start_m <= end_m:
                in_range = start_m <= current_minutes <= end_m
            else:
                in_range = current_minutes >= start_m or current_minutes <= end_m
            if in_range:
                return True
        return False

    # ------------------------------------------------------------------ #
    #  Silence helpers (called by handlers + LLM tool)
    # ------------------------------------------------------------------ #

    def _apply_silence(
        self, origin: str, duration: int, event: AstrMessageEvent
    ) -> None:
        """Record a new silence entry and persist it."""
        self._store.set(origin, time.time() + duration)
        self._store.save()
        self._group_card.origin_to_event_map[origin] = event
        self._group_card.ensure_started()

    async def _run_llm_shutup(
        self, event: AstrMessageEvent, duration: int, unit: str = "m"
    ) -> str:
        """Entry point for the LLM function tool."""
        if not self.config.get("llm_tool_enabled", False):
            return "LLM 工具未启用"

        time_units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        duration_seconds = duration * time_units.get(unit, 60)

        if duration_seconds > 3600:
            duration_seconds = 3600
            logger.warning("[Shutup] LLM 请求的时长超过限制，已调整为最大值 3600s")

        origin = event.unified_msg_origin
        self._apply_silence(origin, duration_seconds, event)

        if self.group_card_enabled:
            remaining_minutes = max(1, int(duration_seconds / 60))
            await self._group_card.update(event, origin, remaining_minutes)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(self._store.get(origin) or time.time()),
        )
        logger.info(
            f"[Shutup] LLM 调用闭嘴 | 时长: {duration_seconds}s | 到期: {expiry_time}"
        )
        return f"已设置闭嘴 {int(duration_seconds / 60)} 分钟，到期时间: {expiry_time}"

    # ------------------------------------------------------------------ #
    #  Main handler
    # ------------------------------------------------------------------ #

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def handle_message(self, event: AstrMessageEvent) -> Any:
        """Intercept every message; delegate to handlers module."""
        result = await self._handlers.dispatch(event)
        if result is not None:
            yield result

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def terminate(self) -> None:
        await self._group_card.cancel()
        await self._group_card.restore_all()
        logger.info("[Shutup] 已卸载插件")
