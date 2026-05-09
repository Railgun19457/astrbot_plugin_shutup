"""AstrBot Shutup Plugin — main entry point."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.provider import LLMResponse
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot.core.star.star_tools import StarTools
from astrbot.core.utils.active_event_registry import active_event_registry

from .core.config import clamp_duration, normalize_commands, parse_time_ranges
from .core.group_card import GroupCardUpdater
from .core.handlers import MessageHandlers
from .core.state import SilenceStore
from .tools.shutup_tool import build_llm_tools


class ShutupPlugin(Star):
    """让 bot 闭嘴 — 支持指令、定时睡眠、LLM 工具调用、群昵称显示。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        # -- Plugin identity --------------------------------------------- #
        self.plugin_priority: int = config.get("priority", 10000)

        command_settings = config.get("command_settings", {})
        sleep_settings = config.get("sleep_settings", {})
        group_card_settings = config.get("group_card_settings", {})

        # -- Command config ---------------------------------------------- #
        self.shutup_cmds: list[str] = normalize_commands(
            command_settings.get("shutup_commands", ["闭嘴"]),
            fallback=["闭嘴"],
        )
        self.unshutup_cmds: list[str] = normalize_commands(
            command_settings.get("unshutup_commands", ["说话"]),
            fallback=["说话"],
        )
        self.permanent_shutup_cmds: list[str] = normalize_commands(
            command_settings.get(
                "permanent_shutup_commands",
                ["永久闭嘴"],
            ),
            fallback=["永久闭嘴"],
        )
        self.temp_wake_cmds: list[str] = normalize_commands(
            sleep_settings.get("temporary_wake_commands", ["醒醒"]),
            fallback=["醒醒"],
        )
        self._dedupe_configured_commands()
        self._apply_configured_commands()

        self.require_admin: bool = config.get("require_admin", False)

        # -- Duration settings ------------------------------------------- #
        self.default_duration: int = clamp_duration(
            command_settings.get("default_duration", 600)
        )

        # -- Reply templates --------------------------------------------- #
        self.shutup_reply: str = command_settings.get("shutup_reply", "好的，我闭嘴了~")
        self.unshutup_reply: str = command_settings.get(
            "unshutup_reply", "好的，我恢复说话了~"
        )

        # -- Group card -------------------------------------------------- #
        self.group_card_enabled: bool = group_card_settings.get(
            "group_card_update_enabled",
            False,
        )
        self.group_card_template: str = group_card_settings.get(
            "group_card_template",
            "[闭嘴中 {remaining}分钟]",
        )

        # -- Sleep schedule ---------------------------------------------- #
        self.sleep_enabled: bool = sleep_settings.get("sleep_enabled", False)
        self.sleep_time_config: list[str] = sleep_settings.get(
            "sleep_time_ranges", ["23:00-07:00"]
        )
        self.sleep_time_ranges: list[tuple[str, str]] = parse_time_ranges(
            self.sleep_time_config
        )
        if self.sleep_enabled and not self.sleep_time_ranges:
            logger.warning("[Shutup] 未配置有效的睡眠时间段，定时睡眠将不会生效")

        # -- Sleep / wake ------------------------------------------------- #
        self.sleep_interaction_enabled: bool = sleep_settings.get(
            "sleep_interaction_enabled", True
        )
        self.temp_wake_map: dict[str, float] = {}

        raw_temp_wake = sleep_settings.get("temporary_wake_duration", 300)
        try:
            twd = int(raw_temp_wake)
        except (TypeError, ValueError):
            logger.warning(
                f"[Shutup] Invalid temporary_wake_duration={raw_temp_wake!r}, "
                "falling back to 300s"
            )
            twd = 300
        if twd < 0:
            logger.warning(
                f"[Shutup] temporary_wake_duration is negative ({twd}), clamping to 0"
            )
            twd = 0
        self.temp_wake_duration: int = twd
        self.temp_wake_reply: str = sleep_settings.get(
            "temporary_wake_reply",
            "我被叫醒了，还能陪你聊 {wake_minutes} 分钟哦。",
        )
        self.sleep_prompt_reply: str = sleep_settings.get(
            "sleep_prompt_reply",
            "我已经睡了，要临时叫醒我吗~（回复：{wake_word}）",
        )
        self.temp_wake_llm_reply_enabled: bool = sleep_settings.get(
            "temporary_wake_llm_reply_enabled",
            False,
        )
        self.temp_wake_llm_prompt: str = sleep_settings.get(
            "temporary_wake_llm_prompt",
            "用户刚刚在睡眠时段用“{wake_command}”叫醒了你。"
            "请用简短、自然、带一点刚睡醒感觉的中文回复用户，"
            "告诉用户你会临时陪聊 {wake_minutes} 分钟。不要解释规则。",
        )

        # -- Persisted state ---------------------------------------------- #
        data_dir = StarTools.get_data_dir("astrbot_plugin_shutup")
        self._store = SilenceStore(data_dir)

        # -- Sub-modules -------------------------------------------------- #
        self._group_card = GroupCardUpdater(self)
        self._handlers = MessageHandlers(self)

        # -- LLM tool(s) (recommended pattern) --------------------------- #
        # 支持多工具配置，通过 llm_tool_options 完全控制
        raw_llm_tools = config.get("llm_tool_options", [])
        if not isinstance(raw_llm_tools, list):
            raw_llm_tools = []
        self.llm_tool_options: set[str] = set(raw_llm_tools)

        self.LLM_TOOL_NAME_BY_OPTION = {
            "shutup": "shutup",
            "suppress_reply": "shutup_suppress_reply",
        }

        self._register_llm_tools()

        # -- Load-time summary -------------------------------------------- #
        time_info = ""
        if self.sleep_enabled:
            time_info = " | 睡眠: " + ", ".join(
                f"{s}-{e}" for s, e in self.sleep_time_ranges
            )
        logger.info(
            f"[Shutup] 已加载 | 指令: 说话={self.unshutup_cmds}"
            f" 闭嘴={self.shutup_cmds} 永久闭嘴={self.permanent_shutup_cmds}"
            f" 醒醒={self.temp_wake_cmds}"
            f" | 默认时长: {self.default_duration}s"
            f" | 优先级: {self.plugin_priority}{time_info}"
        )
        if self.group_card_enabled:
            logger.info(f"[Shutup] 群昵称更新已启用 | 模板: {self.group_card_template}")

    def _apply_configured_commands(self) -> None:
        """Apply configured command names to AstrBot framework command filters."""

        self._set_command_filter("shutup", self.shutup_cmds)
        self._set_command_filter("unshutup", self.unshutup_cmds)
        self._set_command_filter("permanent_shutup", self.permanent_shutup_cmds)
        self._set_command_filter("temp_wake", self.temp_wake_cmds)

    def _dedupe_configured_commands(self) -> None:
        """Avoid duplicate framework command names across the four command groups."""

        reserved = set(self.temp_wake_cmds) | set(self.permanent_shutup_cmds)
        self.shutup_cmds = [cmd for cmd in self.shutup_cmds if cmd not in reserved]
        self.unshutup_cmds = [cmd for cmd in self.unshutup_cmds if cmd not in reserved]

        if not self.shutup_cmds:
            self.shutup_cmds = ["闭嘴"]
        if not self.unshutup_cmds:
            self.unshutup_cmds = ["说话"]

    def _set_command_filter(self, handler_name: str, commands: list[str]) -> None:
        if not commands:
            return

        for handler in star_handlers_registry.get_handlers_by_module_name(
            self.__module__
        ):
            if handler.handler_name != handler_name:
                continue

            for event_filter in handler.event_filters:
                if isinstance(event_filter, CommandFilter):
                    event_filter.command_name = commands[0]
                    event_filter._original_command_name = commands[0]
                    event_filter.alias = set(commands[1:])
                    event_filter._cmpl_cmd_names = None
                    logger.info(
                        f"[Shutup] 已注册框架指令 {handler_name}: "
                        f"{commands[0]} | 别名: {commands[1:]}"
                    )
                    return

        logger.warning(f"[Shutup] 未找到框架指令处理器: {handler_name}")

    def _stopped_plain_result(
        self, event: AstrMessageEvent, text: str
    ) -> MessageEventResult:
        """Build a text result that is sent before stopping further propagation."""

        event.should_call_llm(False)
        return event.plain_result(text)

    def _stopped_optional_result(
        self, event: AstrMessageEvent, text: str | None
    ) -> MessageEventResult | None:
        """Build a stopped text result, or stop silently when text is empty."""

        if text is None:
            return None
        return self._stopped_plain_result(event, text)

    def _unregister_llm_tools(self) -> None:
        """Remove this plugin's LLM tool from AstrBot's tool list."""

        tool_mgr = self.context.get_llm_tool_manager()
        tool_names = set(self.LLM_TOOL_NAME_BY_OPTION.values())
        tool_mgr.func_list = [
            tool
            for tool in tool_mgr.func_list
            if not (
                getattr(tool, "handler_module_path", None) == self.__module__
                and tool.name in tool_names
            )
        ]

    def _register_llm_tools(self) -> None:
        """Register the LLM tool(s) according to plugin config options."""

        self._unregister_llm_tools()

        if not self.llm_tool_options:
            logger.info("[Shutup] LLM 工具未启用或未选择，跳过注册")
            return

        enabled_tool_names = {
            self.LLM_TOOL_NAME_BY_OPTION[key]
            for key in self.llm_tool_options
            if key in self.LLM_TOOL_NAME_BY_OPTION
        }

        tools = [t for t in build_llm_tools(self) if t.name in enabled_tool_names]
        if tools:
            # expand tools when adding
            self.context.add_llm_tools(*tools)
            logger.info("[Shutup] 已注册 LLM 工具: %s", ",".join(t.name for t in tools))
        else:
            logger.info(
                "[Shutup] 未找到匹配的 LLM 工具以注册: %s", sorted(enabled_tool_names)
            )

    # ------------------------------------------------------------------ #
    #  Time helper (used by handlers)
    # ------------------------------------------------------------------ #

    def _is_in_sleep_time(self) -> bool:
        if not self.sleep_enabled or not self.sleep_time_ranges:
            return False

        current_minutes = datetime.now().hour * 60 + datetime.now().minute
        for start_s, end_s in self.sleep_time_ranges:
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
        self._stop_active_responses(event)

    def _is_silenced(self, origin: str) -> bool:
        """Return whether the origin should currently stay silent."""

        if self.sleep_enabled and self._is_in_sleep_time():
            wake_expiry = self.temp_wake_map.get(origin)
            if (
                self.sleep_interaction_enabled
                and wake_expiry
                and time.time() < wake_expiry
            ):
                return False
            return True

        expiry = self._store.get(origin)
        if expiry is None:
            return False
        if self._store.is_permanent(origin):
            return True
        return time.time() < expiry

    def _mark_event_silenced(self, event: AstrMessageEvent) -> None:
        """Mark an event so platform streaming senders can stop emitting chunks."""

        event.set_extra("_shutup_silenced", True)

    async def _filtered_stream(
        self,
        event: AstrMessageEvent,
        stream: AsyncGenerator,
    ) -> AsyncGenerator:
        """Yield stream chunks until silence is requested for this event/session."""

        async for chunk in stream:
            if (
                event.is_stopped()
                or event.get_extra("_shutup_silenced")
                or self._is_silenced(event.unified_msg_origin)
            ):
                logger.info(
                    "[Shutup] 已停止闭嘴期间的流式输出 | "
                    f"来源: {event.unified_msg_origin}"
                )
                break
            yield chunk

    def _stop_active_responses(
        self,
        event: AstrMessageEvent,
    ) -> None:
        """Stop active responses in the same session so silence takes effect now."""

        stopped_count = active_event_registry.stop_all(
            event.unified_msg_origin,
            exclude=event,
        )
        self._mark_event_silenced(event)
        if stopped_count > 0:
            logger.info(f"[Shutup] 已停止当前会话中的 {stopped_count} 个进行中响应")

    def _is_suppressible_model_result(self, result: MessageEventResult) -> bool:
        """Return whether a prepared result comes from model execution."""

        return result.is_model_result() or result.result_content_type in {
            ResultContentType.STREAMING_RESULT,
            ResultContentType.STREAMING_FINISH,
        }

    async def _run_llm_shutup(
        self, event: AstrMessageEvent, duration: int, unit: str = "m"
    ) -> str:
        """Entry point for the LLM function tool."""
        if "shutup" not in self.llm_tool_options:
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
    #  Framework commands
    # ------------------------------------------------------------------ #

    @filter.command("闭嘴", priority=10001)
    async def shutup(self, event: AstrMessageEvent, duration: str = "") -> Any:
        """让机器人在当前会话中闭嘴一段时间。"""
        result = await self._handlers.handle_shutup_command(event, duration)
        yield self._stopped_plain_result(event, result)
        event.stop_event()

    @filter.command("永久闭嘴", priority=10001)
    async def permanent_shutup(self, event: AstrMessageEvent) -> Any:
        """让机器人永久闭嘴，直到使用说话指令解除。"""
        result = await self._handlers.handle_permanent_shutup_command(event)
        yield self._stopped_plain_result(event, result)
        event.stop_event()

    @filter.command("说话", priority=10001)
    async def unshutup(self, event: AstrMessageEvent) -> Any:
        """解除当前会话的闭嘴状态。"""
        result = await self._handlers.handle_unshutup_command(event)
        if result is None:
            yield
            return
        yield self._stopped_plain_result(event, result)
        event.stop_event()

    @filter.command("醒醒", priority=10001)
    async def temp_wake(self, event: AstrMessageEvent) -> Any:
        """在睡眠时段内临时唤醒机器人。"""
        result = await self._handlers.handle_temp_wake_command(event)
        if result is None:
            yield
            return
        yield self._stopped_plain_result(event, result)
        event.stop_event()

    # ------------------------------------------------------------------ #
    #  Main interception handler
    # ------------------------------------------------------------------ #

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def handle_message(self, event: AstrMessageEvent) -> Any:
        """Intercept every message; delegate to handlers module."""
        result = await self._handlers.dispatch(event)
        if result is not None:
            yield self._stopped_plain_result(event, result)
            event.stop_event()

    @filter.on_llm_response(priority=10000)
    async def stop_llm_response_when_silenced(
        self,
        event: AstrMessageEvent,
        response: LLMResponse,
    ) -> None:
        """Suppress model responses that finish after silence has started."""

        if not self._is_silenced(event.unified_msg_origin):
            return

        self._mark_event_silenced(event)
        logger.info(
            f"[Shutup] 已拦截闭嘴期间完成的 LLM 响应 | 来源: {event.unified_msg_origin}"
        )
        event.should_call_llm(False)

    @filter.on_decorating_result(priority=10000)
    async def suppress_model_result_when_silenced(
        self,
        event: AstrMessageEvent,
    ) -> None:
        """Drop model results that are already prepared during silence."""

        if not self._is_silenced(event.unified_msg_origin):
            return

        result = event.get_result()
        if result is None or not self._is_suppressible_model_result(result):
            return

        logger.info(
            "[Shutup] 已丢弃闭嘴期间待发送的模型回复 | "
            f"来源: {event.unified_msg_origin}"
        )
        event.clear_result()
        event.should_call_llm(False)

    @filter.on_decorating_result(priority=10002)
    async def suppress_tool_requested_no_reply(self, event: AstrMessageEvent) -> None:
        """Fallback: drop any prepared result when LLM requested silent handling."""

        if not event.get_extra("_shutup_suppress_this"):
            return

        result = event.get_result()
        if result is None:
            return

        logger.info(
            "[Shutup] 检测到 suppress 标记，兜底丢弃待发送结果 | 来源: %s",
            event.unified_msg_origin,
        )
        event.clear_result()
        event.should_call_llm(False)

    @filter.on_decorating_result(priority=10001)
    async def wrap_model_stream_when_not_silenced(
        self,
        event: AstrMessageEvent,
    ) -> None:
        """Wrap active model streams so future silence can cut them off."""

        result = event.get_result()
        if (
            result is None
            or result.result_content_type != ResultContentType.STREAMING_RESULT
        ):
            return
        if result.async_stream is None:
            return
        if event.get_extra("_shutup_stream_wrapped"):
            return

        result.set_async_stream(self._filtered_stream(event, result.async_stream))
        event.set_extra("_shutup_stream_wrapped", True)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def terminate(self) -> None:
        await self._group_card.cancel()
        await self._group_card.restore_all()
        logger.info("[Shutup] 已卸载插件")
