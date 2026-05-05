import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star


class ShutupPlugin(Star):
    TIME_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        self.plugin_priority: int = config.get("priority", 10000)

        self.wake_prefix: list[str] = self.context.get_config().get("wake_prefix", [])

        shutup_cmds = config.get("shutup_commands", ["闭嘴", "stop"])
        unshutup_cmds = config.get("unshutup_commands", ["说话", "停止闭嘴"])
        if isinstance(shutup_cmds, str):
            shutup_cmds = re.split(r"[\s,]+", shutup_cmds)
        if isinstance(unshutup_cmds, str):
            unshutup_cmds = re.split(r"[\s,]+", unshutup_cmds)
        self.shutup_cmds: list[str] = sorted(shutup_cmds, key=len, reverse=True)
        self.unshutup_cmds: list[str] = sorted(unshutup_cmds, key=len, reverse=True)

        self.require_prefix: bool = config.get("require_prefix", False)
        self.require_admin: bool = config.get("require_admin", False)

        duration_config = config.get("default_duration", 600)
        if not isinstance(duration_config, (int, float)) or not (
            0 <= duration_config <= 86400
        ):
            logger.warning(
                f"[Shutup] default_duration ({duration_config}) is invalid, "
                "falling back to 600s"
            )
            self.default_duration: int = 600
            config["default_duration"] = 600
            config.save_config()
        else:
            self.default_duration = int(duration_config)

        self.shutup_reply: str = config.get("shutup_reply", "好的，我闭嘴了~")
        self.unshutup_reply: str = config.get("unshutup_reply", "好的，我恢复说话了~")

        self.group_card_enabled: bool = config.get("group_card_update_enabled", False)
        self.group_card_template: str = config.get(
            "group_card_template", "[闭嘴中 {remaining}分钟]"
        )
        self.original_group_cards: dict[str, str] = {}
        self.original_nicknames: dict[str, str] = {}
        self.origin_to_event_map: dict[str, AstrMessageEvent] = {}
        self._update_task: asyncio.Task | None = None
        self._update_task_started: bool = False

        self.scheduled_enabled: bool = config.get("scheduled_shutup_enabled", False)
        self.scheduled_times_text: str = config.get(
            "scheduled_shutup_times", "23:00-07:00"
        )
        self.scheduled_time_ranges: list[tuple[str, str]] = self._parse_time_ranges(
            self.scheduled_times_text
        )

        self.silence_map: dict[str, float] = {}

        self.bot_name: str = config.get("bot_name", "小爱")
        self.sleep_mode_enabled: bool = config.get("sleep_mode_enabled", True)
        self.temp_wake_map: dict[str, float] = {}

        raw_temp_wake_duration = config.get("temp_wake_duration", 300)
        try:
            temp_wake_duration = int(raw_temp_wake_duration)
        except (TypeError, ValueError):
            logger.warning(
                f"[Shutup] Invalid temp_wake_duration={raw_temp_wake_duration!r}, "
                "falling back to 300s"
            )
            temp_wake_duration = 300
        if temp_wake_duration < 0:
            logger.warning(
                f"[Shutup] temp_wake_duration is negative ({temp_wake_duration}), "
                "clamping to 0"
            )
            temp_wake_duration = 0
        self.temp_wake_duration: int = temp_wake_duration

        self.data_dir: Path = (
            Path(__file__).parent.parent.parent
            / "plugin_data"
            / "astrbot_plugin_shutup"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.silence_map_path: Path = self.data_dir / "silence_map.json"
        self._load_silence_map()

        if self.scheduled_enabled:
            time_ranges_str = ", ".join(
                f"{s}-{e}" for s, e in self.scheduled_time_ranges
            )
            logger.info(
                f"[Shutup] 已加载 | 指令: {self.shutup_cmds} & {self.unshutup_cmds}"
                f" | 默认时长: {self.default_duration}s | 优先级: {self.plugin_priority}"
                f" | 定时: {time_ranges_str}"
            )
        else:
            logger.info(
                f"[Shutup] 已加载 | 指令: {self.shutup_cmds} & {self.unshutup_cmds}"
                f" | 默认时长: {self.default_duration}s | 优先级: {self.plugin_priority}"
            )

        if self.group_card_enabled:
            logger.info(f"[Shutup] 群昵称更新已启用 | 模板: {self.group_card_template}")

    # ------------------------------------------------------------------ #
    #  Config parsing helpers
    # ------------------------------------------------------------------ #

    def _parse_time_ranges(self, time_text: str) -> list[tuple[str, str]]:
        """Parse scheduled shutup time ranges from configuration text.

        Each non-empty, non-comment line should match ``HH:MM-HH:MM``.
        Cross-midnight ranges (e.g. ``23:00-07:00``) are supported.

        Args:
            time_text: Multi-line text from ``scheduled_shutup_times`` config.

        Returns:
            List of ``(start_time, end_time)`` string tuples.
        """
        time_ranges: list[tuple[str, str]] = []

        for line in time_text.strip().split("\n"):
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

        if not time_ranges and self.scheduled_enabled:
            logger.warning("[Shutup] 未配置有效的定时时间段，定时闭嘴将不会生效")

        return time_ranges

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #

    def _load_silence_map(self) -> None:
        try:
            if self.silence_map_path.exists():
                with open(self.silence_map_path, encoding="utf-8") as f:
                    self.silence_map = json.load(f)
                self.silence_map = {k: float(v) for k, v in self.silence_map.items()}
                if self.silence_map:
                    logger.info(f"[Shutup] 加载了 {len(self.silence_map)} 条禁言记录")
        except Exception as e:
            logger.warning(f"[Shutup] 加载禁言记录失败: {e}")

    def _save_silence_map(self) -> None:
        try:
            with open(self.silence_map_path, "w", encoding="utf-8") as f:
                json.dump(self.silence_map, f)
        except Exception as e:
            logger.warning(f"[Shutup] 保存禁言记录失败: {e}")

    # ------------------------------------------------------------------ #
    #  Time helper
    # ------------------------------------------------------------------ #

    def _is_in_scheduled_time(self) -> bool:
        """Check whether the current wall-clock time falls inside any configured time range.

        Returns:
            ``True`` if a scheduled shutup window is currently active.
        """
        if not self.scheduled_enabled or not self.scheduled_time_ranges:
            return False

        current_minutes = datetime.now().hour * 60 + datetime.now().minute

        for start_time_str, end_time_str in self.scheduled_time_ranges:
            start_h, start_m = map(int, start_time_str.split(":"))
            end_h, end_m = map(int, end_time_str.split(":"))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if start_minutes <= end_minutes:
                in_range = start_minutes <= current_minutes <= end_minutes
            else:
                in_range = (
                    current_minutes >= start_minutes or current_minutes <= end_minutes
                )

            if in_range:
                return True

        return False

    # ------------------------------------------------------------------ #
    #  Command helpers
    # ------------------------------------------------------------------ #

    def _find_matching_command(self, text: str) -> tuple[str | None, str | None]:
        """Find the longest command that *startswith* the given text.

        Returns:
            A tuple ``(shutup_cmd, unshutup_cmd)`` — exactly one will be
            non-None when a match is found, both None otherwise.
        """
        for cmd in self.shutup_cmds:
            if text.startswith(cmd):
                return cmd, None
        for cmd in self.unshutup_cmds:
            if text.startswith(cmd):
                return None, cmd
        return None, None

    def _parse_duration(self, text: str, shutup_cmd: str) -> int:
        """Extract a custom duration from a shutup command message.

        Expected format: ``<command>[ <number><unit>]`` where *unit* is one
        of ``s``, ``m``, ``h``, ``d``.

        Args:
            text: The full message text.
            shutup_cmd: The matched shutup command token.

        Returns:
            Duration in seconds.
        """
        match = re.match(rf"^{re.escape(shutup_cmd)}\s*(\d+)([smhd])?", text)
        if match:
            val = int(match.group(1))
            unit = match.group(2) or "s"
            return val * self.TIME_UNITS.get(unit, 1)
        return self.default_duration

    def _check_prefix(self, event: AstrMessageEvent) -> bool:
        """Verify that the message satisfies the prefix requirement.

        When ``require_prefix`` is disabled this always returns ``True``.
        Otherwise the first message component must be either:

        - a ``Plain`` segment that starts with a configured ``wake_prefix``, or
        - an ``At`` segment targeting the bot's own ID.
        """
        if not self.require_prefix:
            return True

        chain = event.get_messages()
        if not chain:
            return False

        first_seg = chain[0]
        if isinstance(first_seg, Comp.Plain):
            return any(first_seg.text.startswith(prefix) for prefix in self.wake_prefix)
        if isinstance(first_seg, Comp.At):
            return str(first_seg.qq) == str(event.get_self_id())
        return False

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        """Verify that the message sender is in the admin list.

        When ``require_admin`` is disabled this always returns ``True``.
        """
        if not self.require_admin:
            return True

        admins = self.context.get_config().get("admins_id", [])
        sender_id = event.get_sender_id()
        return str(sender_id) in [str(a) for a in admins]

    # ------------------------------------------------------------------ #
    #  Group card helpers
    # ------------------------------------------------------------------ #

    async def _update_group_card(
        self, event: AstrMessageEvent, origin: str, remaining_minutes: int
    ) -> None:
        """Update the bot's group card to reflect the remaining mute time.

        Only functional on the **aiocqhttp** platform.
        """
        if not self.group_card_enabled:
            return

        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )
        except ImportError:
            logger.debug("[Shutup] aiocqhttp 模块未安装，跳过群昵称更新")
            return

        if not isinstance(event, AiocqhttpMessageEvent):
            return

        group_id = event.get_group_id()
        if not group_id:
            return

        bot = getattr(event, "bot", None)
        if not bot or not hasattr(bot, "call_action"):
            logger.debug("[Shutup] bot 不支持 call_action，跳过群昵称更新")
            return

        self_id = event.get_self_id()
        if not self_id:
            return

        try:
            if origin not in self.original_group_cards:
                try:
                    member_info = await bot.call_action(
                        "get_group_member_info",
                        group_id=int(group_id),
                        user_id=int(self_id),
                        no_cache=True,
                    )
                    self.original_group_cards[origin] = (
                        member_info.get("card", "") or ""
                    )
                    self.original_nicknames[origin] = (
                        member_info.get("nickname", "") or ""
                    )
                    logger.debug(
                        f"[Shutup] 保存原始信息 | "
                        f"群昵称: {self.original_group_cards[origin]} | "
                        f"QQ昵称: {self.original_nicknames[origin]}"
                    )
                except Exception as e:
                    logger.debug(f"[Shutup] 获取原始群昵称失败: {e}")
                    self.original_group_cards[origin] = ""
                    self.original_nicknames[origin] = ""

            if remaining_minutes > 0:
                original_card = self.original_group_cards.get(origin, "")
                original_nickname = self.original_nicknames.get(origin, "")
                original_name = original_card if original_card else original_nickname

                try:
                    card = self.group_card_template.format(
                        remaining=remaining_minutes,
                        original_card=original_card,
                        original_nickname=original_nickname,
                        original_name=original_name,
                    )
                except KeyError as e:
                    logger.warning(f"[Shutup] 群昵称模板占位符错误: {e}，使用默认格式")
                    card = f"[闭嘴中 {remaining_minutes}分钟]"
            else:
                card = self.original_group_cards.get(origin, "")

            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(self_id),
                card=card[:60],
            )
            logger.info(f"[Shutup] 已更新群昵称: {card[:60]}")

        except Exception as e:
            logger.warning(f"[Shutup] 更新群昵称失败: {e}")

    async def _ensure_update_task_started(self) -> None:
        if self.group_card_enabled and not self._update_task_started:
            self._update_task_started = True
            self._update_task = asyncio.create_task(self._group_card_update_loop())
            logger.info("[Shutup] 群昵称更新任务已启动")

    async def _group_card_update_loop(self) -> None:
        """Background task that refreshes group cards every 60 seconds."""
        try:
            while True:
                await asyncio.sleep(60)

                if not self.silence_map:
                    continue

                now = time.time()
                for origin in list(self.silence_map):
                    expiry = self.silence_map[origin]
                    remaining_seconds = expiry - now
                    event = self.origin_to_event_map.get(origin)

                    if remaining_seconds > 0:
                        if event is not None:
                            remaining_minutes = max(1, int(remaining_seconds / 60))
                            await self._update_group_card(
                                event, origin, remaining_minutes
                            )
                    else:
                        if event is not None:
                            await self._update_group_card(event, origin, 0)
                        self.original_group_cards.pop(origin, None)
                        self.original_nicknames.pop(origin, None)
                        self.origin_to_event_map.pop(origin, None)

        except asyncio.CancelledError:
            logger.info("[Shutup] 群昵称更新任务已停止")
        except Exception as e:
            logger.error(f"[Shutup] 群昵称更新任务异常: {e}")

    # ------------------------------------------------------------------ #
    #  Main handler
    # ------------------------------------------------------------------ #

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def handle_message(self, event: AstrMessageEvent) -> Any:
        """Intercept every message and decide whether to block it."""
        text = event.get_message_str().strip()
        origin = event.unified_msg_origin

        shutup_cmd, unshutup_cmd = self._find_matching_command(text)

        if shutup_cmd is not None or unshutup_cmd is not None:
            yield await self._handle_control_command(
                event, text, origin, shutup_cmd, unshutup_cmd
            )
            return

        if self._is_in_scheduled_time():
            if self.sleep_mode_enabled:
                yield await self._handle_sleep_interaction(event, text, origin)
            else:
                logger.info("[Shutup] 定时闭嘴生效中")
                event.should_call_llm(False)
                event.stop_event()
            return

        expiry = self.silence_map.get(origin)
        if expiry is not None:
            if time.time() < expiry:
                remaining = int(expiry - time.time())
                logger.info(
                    f"[Shutup] 消息已拦截 | 来源: {origin} | 剩余: {remaining}s"
                )
                event.should_call_llm(False)
                event.stop_event()
            else:
                logger.info("[Shutup] 禁言已自动过期")
                self.silence_map.pop(origin, None)
                self._save_silence_map()

    # ------------------------------------------------------------------ #
    #  Sub-handlers
    # ------------------------------------------------------------------ #

    async def _handle_control_command(
        self,
        event: AstrMessageEvent,
        text: str,
        origin: str,
        shutup_cmd: str | None,
        unshutup_cmd: str | None,
    ) -> MessageEventResult | str:
        """Route a matched control command through permission checks."""
        if not self._check_prefix(event):
            return

        if self.require_admin and not self._check_admin(event):
            event.stop_event()
            return "管理员才能使用此指令"

        if shutup_cmd is not None:
            event.stop_event()
            return await self._handle_shutup_command(event, text, origin)

        if unshutup_cmd is not None:
            event.stop_event()
            return await self._handle_unshutup_command(event, origin)

        return ""

    async def _handle_shutup_command(
        self, event: AstrMessageEvent, text: str, origin: str
    ) -> str:
        """Execute a shutup command."""
        shutup_cmd, _ = self._find_matching_command(text)
        assert shutup_cmd is not None

        is_sleep_early = (
            self.sleep_mode_enabled
            and self._is_in_scheduled_time()
            and origin in self.temp_wake_map
        )
        if is_sleep_early:
            self.temp_wake_map.pop(origin, None)

        duration = self._parse_duration(text, shutup_cmd)

        self.silence_map[origin] = time.time() + duration
        self._save_silence_map()
        self.origin_to_event_map[origin] = event

        await self._ensure_update_task_started()

        if self.group_card_enabled:
            remaining_minutes = max(1, int(duration / 60))
            await self._update_group_card(event, origin, remaining_minutes)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(self.silence_map[origin])
        )
        logger.info(f"[Shutup] 已禁言 | 时长: {duration}s | 到期: {expiry_time}")

        if is_sleep_early:
            return f"那{self.bot_name}继续回被窝啦，晚安~"

        return self.shutup_reply.format(duration=duration, expiry_time=expiry_time)

    async def _handle_unshutup_command(
        self, event: AstrMessageEvent, origin: str
    ) -> str:
        """Execute an unshutup command."""
        old_expiry = self.silence_map.get(origin)
        if old_expiry is not None:
            now = time.time()
            duration = int(max(0, now - (old_expiry - self.default_duration)))
        else:
            duration = 0

        self.silence_map.pop(origin, None)
        self._save_silence_map()

        now = time.time()
        was_already_awake = (
            origin in self.temp_wake_map and now < self.temp_wake_map[origin]
        )

        if self.group_card_enabled:
            await self._update_group_card(event, origin, 0)
            self.original_group_cards.pop(origin, None)
            self.original_nicknames.pop(origin, None)
            self.origin_to_event_map.pop(origin, None)

        if self.sleep_mode_enabled and self._is_in_scheduled_time():
            self.temp_wake_map[origin] = now + self.temp_wake_duration
            wake_minutes = self.temp_wake_duration // 60

            if was_already_awake:
                return f"{self.bot_name} 已经醒啦，会再陪你聊 {wake_minutes} 分钟哦~"

            logger.info(f"[Shutup] 睡眠期间被叫醒，清醒 {wake_minutes} 分钟")
            return (
                f"谁呀...{self.bot_name}被叫醒了，还能强撑着陪你聊"
                f" {wake_minutes} 分钟哦..."
            )

        logger.info(f"[Shutup] 已解除禁言 | 已禁言: {duration}s")
        return f"{self.bot_name}早就醒着啦！随时可以陪你聊天哦~"

    async def _handle_sleep_interaction(
        self, event: AstrMessageEvent, text: str, origin: str
    ) -> MessageEventResult | str | None:
        """Handle a message received during the scheduled sleep window.

        Checks for @-mentions, wake-prefix usage, or global command
        prefixes to decide whether to offer a wake-up hint.  Otherwise
        silently blocks the message.
        """
        wake_expiry = self.temp_wake_map.get(origin)
        if wake_expiry is not None and time.time() < wake_expiry:
            remaining = int(wake_expiry - time.time())
            logger.info(f"[Shutup] bot正处于梦游清醒状态 | 剩余: {remaining}s")
            return None

        self.temp_wake_map.pop(origin, None)
        logger.info("[Shutup] 定时闭嘴(睡眠)生效中")

        is_talking_to_me = self._is_talking_to_bot(event, text)

        if is_talking_to_me:
            wake_word = (
                self.unshutup_cmds[0] if self.unshutup_cmds else f"{self.bot_name}醒醒"
            )

            prefix_display = ""
            if self.require_prefix:
                cmd_prefix = self.context.get_config().get("command_prefix", "/")
                if isinstance(cmd_prefix, list) and cmd_prefix:
                    prefix_display = cmd_prefix[0]
                elif isinstance(cmd_prefix, str):
                    prefix_display = cmd_prefix

            event.stop_event()
            return (
                f"{self.bot_name}已经睡了，要叫醒{self.bot_name}吗~"
                f"（回复：{prefix_display}{wake_word}）"
            )

        event.should_call_llm(False)
        event.stop_event()
        return None

    def _is_talking_to_bot(self, event: AstrMessageEvent, text: str) -> bool:
        """Check whether the message is explicitly directed at the bot."""
        chain = event.get_messages()
        if chain:
            first_seg = chain[0]
            if isinstance(first_seg, Comp.At) and str(first_seg.qq) == str(
                event.get_self_id()
            ):
                return True
            if isinstance(first_seg, Comp.Plain) and self.wake_prefix:
                if any(first_seg.text.startswith(p) for p in self.wake_prefix):
                    return True

        cmd_prefix = self.context.get_config().get("command_prefix", "/")
        prefixes = cmd_prefix if isinstance(cmd_prefix, list) else [cmd_prefix]
        if any(text.startswith(p) for p in prefixes if p):
            return True

        return False

    # ------------------------------------------------------------------ #
    #  LLM tool
    # ------------------------------------------------------------------ #

    @filter.llm_tool(name="shutup")
    async def llm_shutup(
        self, event: AstrMessageEvent, duration: int, unit: str = "m"
    ) -> str:
        """Stop replying to messages for a specified duration.

        Args:
            duration(number): Shutup duration in the chosen unit (max 60 min).
            unit(string): Time unit — ``s`` (seconds), ``m`` (minutes), or
                ``h`` (hours). Defaults to ``m``.
        """
        if not self.config.get("llm_tool_enabled", False):
            return "LLM 工具未启用"

        duration_seconds = duration * self.TIME_UNITS.get(unit, 60)

        max_duration = 3600
        if duration_seconds > max_duration:
            duration_seconds = max_duration
            logger.warning(
                f"[Shutup] LLM 请求的时长超过限制，已调整为最大值 {max_duration}s"
            )

        origin = event.unified_msg_origin
        self.silence_map[origin] = time.time() + duration_seconds
        self._save_silence_map()
        self.origin_to_event_map[origin] = event

        await self._ensure_update_task_started()

        if self.group_card_enabled:
            remaining_minutes = max(1, int(duration_seconds / 60))
            await self._update_group_card(event, origin, remaining_minutes)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(self.silence_map[origin]),
        )
        logger.info(
            f"[Shutup] LLM 调用闭嘴 | 时长: {duration_seconds}s | 到期: {expiry_time}"
        )

        return f"已设置闭嘴 {int(duration_seconds / 60)} 分钟，到期时间: {expiry_time}"

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def terminate(self) -> None:
        if self._update_task is not None and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        if self.group_card_enabled and self.original_group_cards:
            for origin in list(self.original_group_cards):
                event = self.origin_to_event_map.get(origin)
                if event is not None:
                    await self._update_group_card(event, origin, 0)

        logger.info("[Shutup] 已卸载插件")
