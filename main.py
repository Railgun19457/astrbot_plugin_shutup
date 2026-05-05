import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class ShutupPlugin(Star):
    # 时间单位转换(秒)
    TIME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 从配置读取优先级
        self.plugin_priority = config.get("priority", 10000)

        self.wake_prefix: list[str] = self.context.get_config().get("wake_prefix", [])
        # 直接获取配置项中的列表
        self.shutup_cmds = config.get("shutup_commands", ["闭嘴", "stop"])
        self.unshutup_cmds = config.get("unshutup_commands", ["说话", "停止闭嘴"])
        self.require_prefix = config.get("require_prefix", False)
        self.require_admin = config.get("require_admin", False)
        # 支持字符串配置，转换为列表
        if isinstance(self.shutup_cmds, str):
            self.shutup_cmds = re.split(r"[\s,]+", self.shutup_cmds)
        if isinstance(self.unshutup_cmds, str):
            self.unshutup_cmds = re.split(r"[\s,]+", self.unshutup_cmds)

        # 限制 default_duration 范围在 0-86400 秒(0-24小时)
        duration_config = config.get("default_duration", 600)
        if not isinstance(duration_config, (int, float)) or not (
            0 <= duration_config <= 86400
        ):
            logger.warning(
                f"[Shutup] ⚠️ default_duration 配置无效({duration_config})，使用默认值 600s"
            )
            self.default_duration = 600
            # 更新配置文件中的值为默认值
            config["default_duration"] = 600
            config.save_config()
        else:
            self.default_duration = int(duration_config)

        self.shutup_reply = config.get("shutup_reply", "好的，我闭嘴了~")
        self.unshutup_reply = config.get("unshutup_reply", "好的，我恢复说话了~")

        # 群昵称更新配置
        self.group_card_enabled = config.get("group_card_update_enabled", False)
        self.group_card_template = config.get(
            "group_card_template", "[闭嘴中 {remaining}分钟]"
        )
        self.original_group_cards = {}  # 存储原始群昵称
        self.original_nicknames = {}  # 存储原始QQ昵称
        self.origin_to_event_map = {}  # 存储 origin 到 event 的映射
        self._update_task = None  # 定时更新任务

        # 定时闭嘴配置
        self.scheduled_enabled = config.get("scheduled_shutup_enabled", False)
        self.scheduled_times_text = config.get("scheduled_shutup_times", "23:00-07:00")
        self.scheduled_time_ranges = self._parse_time_ranges(self.scheduled_times_text)

        self.silence_map = {}
        # ====== 新增：睡眠唤醒配置 ======
        self.bot_name = config.get("bot_name", "小爱")  # 默认叫小爱，可以在配置面板改喵
        self.sleep_mode_enabled = config.get(
            "sleep_mode_enabled", True
        )  # 新增：睡眠模式开关
        self.temp_wake_map = {}  # 记录谁把bot叫醒了
        # 睡眠唤醒持续时间（秒），确保为非负整数
        raw_temp_wake_duration = config.get("temp_wake_duration", 300)
        try:
            temp_wake_duration = int(raw_temp_wake_duration)
        except (TypeError, ValueError):
            logger.warning(
                f"[ShutupPlugin] Invalid temp_wake_duration={raw_temp_wake_duration!r}, "
                "falling back to default 300s."
            )
            temp_wake_duration = 300
        if temp_wake_duration < 0:
            logger.warning(
                f"[ShutupPlugin] temp_wake_duration is negative ({temp_wake_duration}), "
                "clamping to 0."
            )
            temp_wake_duration = 0
        self.temp_wake_duration = temp_wake_duration  # 默认清醒 300秒 (5分钟)
        # 使用 pathlib 优化路径处理
        self.data_dir = (
            Path(__file__).parent.parent.parent
            / "plugin_data"
            / "astrbot_plugin_shutup"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.silence_map_path = self.data_dir / "silence_map.json"
        self._load_silence_map()

        # 群昵称更新任务(延迟启动)
        self._update_task = None
        self._update_task_started = False

        if self.scheduled_enabled:
            time_ranges_str = ", ".join(
                [f"{start}-{end}" for start, end in self.scheduled_time_ranges]
            )
            logger.info(
                f"[Shutup] 已加载 | 指令: {self.shutup_cmds} & {self.unshutup_cmds} | 默认时长: {self.default_duration}s | 优先级: {self.plugin_priority} | 定时: {time_ranges_str}"
            )
        else:
            logger.info(
                f"[Shutup] 已加载 | 指令: {self.shutup_cmds} & {self.unshutup_cmds} | 默认时长: {self.default_duration}s | 优先级: {self.plugin_priority}"
            )

        if self.group_card_enabled:
            logger.info(f"[Shutup] 群昵称更新已启用 | 模板: {self.group_card_template}")

    def _parse_time_ranges(self, time_text: str) -> list[tuple[str, str]]:
        """解析时间范围文本

        Args:
            time_text: 时间范围文本，每行一个，格式: HH:MM-HH:MM

        Returns:
            list[tuple[str, str]]: 时间范围列表，每个元素是 (开始时间, 结束时间)
        """
        time_ranges = []

        for line in time_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            match = re.match(r"^(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})$", line)
            if not match:
                logger.warning(f"[Shutup] ⚠️ 无法解析时间范围: {line}")
                continue

            start_time, end_time = match.groups()
            try:
                datetime.strptime(start_time, "%H:%M")
                datetime.strptime(end_time, "%H:%M")
                time_ranges.append((start_time, end_time))
            except ValueError:
                logger.warning(f"[Shutup] ⚠️ 无效的时间格式: {line}")

        if not time_ranges and self.scheduled_enabled:
            logger.warning("[Shutup] ⚠️ 未配置有效的定时时间段，定时闭嘴将不会生效")

        return time_ranges

    def _load_silence_map(self):
        try:
            if self.silence_map_path.exists():
                with open(self.silence_map_path, "r", encoding="utf-8") as f:
                    self.silence_map = json.load(f)

                self.silence_map = {k: float(v) for k, v in self.silence_map.items()}
                if self.silence_map:
                    logger.info(f"[Shutup] 加载了 {len(self.silence_map)} 条禁言记录")
        except Exception as e:
            logger.warning(f"[Shutup] ⚠️ 加载禁言记录失败: {e}")

    def _save_silence_map(self):
        try:
            with open(self.silence_map_path, "w", encoding="utf-8") as f:
                json.dump(self.silence_map, f)
        except Exception as e:
            logger.warning(f"[Shutup] ⚠️ 保存禁言记录失败: {e}")

    def _is_in_scheduled_time(self) -> bool:
        """检查当前时间是否在定时闭嘴时间段内"""
        if not self.scheduled_enabled or not self.scheduled_time_ranges:
            return False

        current_minutes = datetime.now().hour * 60 + datetime.now().minute

        for start_time_str, end_time_str in self.scheduled_time_ranges:
            start_h, start_m = map(int, start_time_str.split(":"))
            end_h, end_m = map(int, end_time_str.split(":"))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            # 跨天：23:00-07:00 或 不跨天：08:00-18:00
            in_range = (
                start_minutes <= current_minutes <= end_minutes
                if start_minutes <= end_minutes
                else current_minutes >= start_minutes or current_minutes <= end_minutes
            )
            if in_range:
                return True

        return False

    def _check_prefix(self, event: AstrMessageEvent) -> bool:
        """检查消息是否满足前缀要求

        Returns:
            bool: True 表示满足前缀要求(或不需要前缀)，False 表示不满足前缀要求
        """
        if not self.require_prefix:
            return True

        chain = event.get_messages()
        if not chain:
            return False

        first_seg = chain[0]
        # 前缀触发
        if isinstance(first_seg, Comp.Plain):
            return any(first_seg.text.startswith(prefix) for prefix in self.wake_prefix)
        # @bot触发
        elif isinstance(first_seg, Comp.At):
            return str(first_seg.qq) == str(event.get_self_id())
        else:
            return False

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        if not self.require_admin:
            return True

        # 修正键名为 admins_id
        # 获取管理员列表
        admins = self.context.get_config().get("admins_id", [])
        sender_id = event.get_sender_id()

        # 检查发送者是否在管理员列表中
        return str(sender_id) in [str(admin) for admin in admins]

    async def _update_group_card(
        self, event: AstrMessageEvent, origin: str, remaining_minutes: int
    ) -> None:
        """更新群昵称显示剩余时长"""
        if not self.group_card_enabled:
            return

        # 只处理 aiocqhttp 平台的事件
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            if not isinstance(event, AiocqhttpMessageEvent):
                return
        except ImportError:
            logger.debug("[Shutup] aiocqhttp 模块未安装，跳过群昵称更新")
            return

        # 检查是否是群聊
        group_id = event.get_group_id()
        if not group_id:
            return

        # 获取 bot 实例
        bot = getattr(event, "bot", None)
        if not bot or not hasattr(bot, "call_action"):
            logger.debug("[Shutup] bot 不支持 call_action，跳过群昵称更新")
            return

        # 获取 bot 的 QQ 号
        self_id = event.get_self_id()
        if not self_id:
            return

        try:
            # 保存原始群昵称和QQ昵称(如果还没保存)
            if origin not in self.original_group_cards:
                try:
                    member_info = await bot.call_action(
                        "get_group_member_info",
                        group_id=int(group_id),
                        user_id=int(self_id),
                        no_cache=True,
                    )
                    # 群昵称(群名片)
                    self.original_group_cards[origin] = (
                        member_info.get("card", "") or ""
                    )
                    # QQ昵称
                    self.original_nicknames[origin] = (
                        member_info.get("nickname", "") or ""
                    )
                    logger.debug(
                        f"[Shutup] 保存原始信息 | 群昵称: {self.original_group_cards[origin]} | QQ昵称: {self.original_nicknames[origin]}"
                    )
                except Exception as e:
                    logger.debug(f"[Shutup] 获取原始群昵称失败: {e}")
                    self.original_group_cards[origin] = ""
                    self.original_nicknames[origin] = ""

            # 格式化群昵称
            if remaining_minutes > 0:
                # 获取原始信息用于占位符
                original_card = self.original_group_cards.get(origin, "")
                original_nickname = self.original_nicknames.get(origin, "")

                # 使用原始群昵称或QQ昵称(优先使用群昵称)
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
                # 恢复原始群昵称
                card = self.original_group_cards.get(origin, "")

            # 更新群昵称
            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(self_id),
                card=card[:60],  # QQ 群昵称最长 60 字符
            )
            logger.info(f"[Shutup] 已更新群昵称: {card[:60]}")

        except Exception as e:
            logger.warning(f"[Shutup] 更新群昵称失败: {e}")

    async def _ensure_update_task_started(self) -> None:
        """确保群昵称更新任务已启动"""
        if self.group_card_enabled and not self._update_task_started:
            self._update_task_started = True
            self._update_task = asyncio.create_task(self._group_card_update_loop())
            logger.info("[Shutup] 群昵称更新任务已启动")

    async def _group_card_update_loop(self) -> None:
        """定时更新群昵称的后台任务"""
        try:
            while True:
                await asyncio.sleep(60)  # 每分钟更新一次

                if not self.silence_map:
                    continue

                current_time = time.time()
                for origin, expiry in list(self.silence_map.items()):
                    remaining_seconds = expiry - current_time
                    if remaining_seconds > 0:
                        remaining_minutes = max(1, int(remaining_seconds / 60))
                        # 从映射中获取 event
                        event = self.origin_to_event_map.get(origin)
                        if event:
                            await self._update_group_card(
                                event, origin, remaining_minutes
                            )
                    else:
                        # 过期了，恢复原始群昵称
                        event = self.origin_to_event_map.get(origin)
                        if event:
                            await self._update_group_card(event, origin, 0)
                        self.original_group_cards.pop(origin, None)
                        self.original_nicknames.pop(origin, None)
                        self.origin_to_event_map.pop(origin, None)

        except asyncio.CancelledError:
            logger.info("[Shutup] 群昵称更新任务已停止")
        except Exception as e:
            logger.error(f"[Shutup] 群昵称更新任务异常: {e}")

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def handle_message(self, event: AstrMessageEvent):
        text = event.get_message_str().strip()
        origin = event.unified_msg_origin

        # 1. 检查是否是控制指令
        is_shutup_cmd = any(text.startswith(cmd) for cmd in self.shutup_cmds)
        is_unshutup_cmd = any(text.startswith(cmd) for cmd in self.unshutup_cmds)

        # 2. 处理控制指令(需要检查前缀和权限)
        if is_shutup_cmd or is_unshutup_cmd:
            if not self._check_prefix(event):
                return

            # 检查管理员权限
            if not self._check_admin(event):
                yield event.plain_result("⚠️ 只有管理员才能使用此指令")
                event.stop_event()
                return

            if is_shutup_cmd:
                yield event.plain_result(
                    await self._handle_shutup_command(event, text, origin)
                )
                event.stop_event()
                return

            if is_unshutup_cmd:
                yield event.plain_result(
                    await self._handle_unshutup_command(event, origin)
                )
                event.stop_event()
                return

        # 3. 检查定时闭嘴 (bot的睡眠时间)
        if self._is_in_scheduled_time():
            if self.sleep_mode_enabled:
                # 开启了睡眠模式：执行睡眠互动逻辑
                wake_expiry = self.temp_wake_map.get(origin)
                if wake_expiry and time.time() < wake_expiry:
                    remaining = int(wake_expiry - time.time())
                    logger.info(
                        f"[Shutup] ⏰ bot正处于梦游清醒状态 | 剩余: {remaining}s"
                    )
                else:
                    if wake_expiry:
                        self.temp_wake_map.pop(origin, None)

                    logger.info("[Shutup] ⏰ 定时闭嘴(睡眠)生效中，呼呼呼...")

                    # ====== 严格判断是否在明确呼叫 bot（防刷屏） ======
                    is_talking_to_me = False
                    chain = event.get_messages()
                    if chain:
                        first_seg = chain[0]
                        # 1. 检查是否明确 @ 了 bot
                        if isinstance(first_seg, Comp.At) and str(first_seg.qq) == str(
                            event.get_self_id()
                        ):
                            is_talking_to_me = True
                        # 2. 检查是否使用了设定的唤醒前缀 (wake_prefix)
                        elif isinstance(first_seg, Comp.Plain) and self.wake_prefix:
                            if any(
                                first_seg.text.startswith(p) for p in self.wake_prefix
                            ):
                                is_talking_to_me = True

                    # 3. 检查是否带有全局命令符 (如 /)
                    cmd_prefix = self.context.get_config().get("command_prefix", "/")
                    prefixes = (
                        cmd_prefix if isinstance(cmd_prefix, list) else [cmd_prefix]
                    )
                    if any(text.startswith(p) for p in prefixes if p):  # 确保前缀不为空
                        is_talking_to_me = True

                    if is_talking_to_me:
                        wake_word = (
                            self.unshutup_cmds[0]
                            if self.unshutup_cmds
                            else f"{self.bot_name}醒醒"
                        )

                        # 如果需要前缀才加上，不需要的话就直接显示命令文本
                        display_prefix = ""
                        if self.require_prefix:
                            display_prefix = (
                                cmd_prefix[0]
                                if isinstance(cmd_prefix, list) and cmd_prefix
                                else (
                                    "" if isinstance(cmd_prefix, list) else cmd_prefix
                                )
                            )

                        yield event.plain_result(
                            f"{self.bot_name}已经睡了，要叫醒{self.bot_name}吗~（回复：{display_prefix}{wake_word}）"
                        )

                    event.should_call_llm(False)
                    event.stop_event()
                    return
            else:
                # 没开启睡眠模式：直接安静拦截，不回复任何话
                logger.info("[Shutup] ⏰ 定时闭嘴生效中")
                event.should_call_llm(False)
                event.stop_event()
                return

        # 4. 检查手动禁言状态
        expiry = self.silence_map.get(origin)
        if expiry:
            if time.time() < expiry:
                remaining = int(expiry - time.time())
                logger.info(
                    f"[Shutup] 🔇 消息已拦截 | 来源: {origin} | 剩余: {remaining}s"
                )
                event.should_call_llm(False)
                event.stop_event()
            else:
                # 禁言已过期，自动清理
                logger.info("[Shutup] ⏰ 禁言已自动过期")
                self.silence_map.pop(origin, None)
                self._save_silence_map()
                return

    async def _handle_shutup_command(
        self, event: AstrMessageEvent, text: str, origin: str
    ) -> str:
        """处理闭嘴指令"""
        # ====== 判断是不是在半夜提前哄睡 ======
        is_sleep_early = (
            self.sleep_mode_enabled
            and self._is_in_scheduled_time()
            and origin in getattr(self, "temp_wake_map", {})
        )
        if is_sleep_early:
            self.temp_wake_map.pop(origin, None)  # 清除清醒倒计时
        # ==========================================

        # 解析时长
        for cmd in self.shutup_cmds:
            if text.startswith(cmd):
                match = re.match(rf"^{re.escape(cmd)}\s*(\d+)([smhd])?", text)
                if match:
                    val = int(match.group(1))
                    unit = match.group(2) or "s"
                    duration = val * self.TIME_UNITS.get(unit, 1)
                else:
                    duration = self.default_duration
                break

        # 设置禁言
        self.silence_map[origin] = time.time() + duration
        self._save_silence_map()

        # 保存 event 到映射（用于后台更新）
        self.origin_to_event_map[origin] = event

        # 启动群昵称更新任务(如果还没启动)
        await self._ensure_update_task_started()

        # 立即更新群昵称(如果启用)
        if self.group_card_enabled:
            remaining_minutes = max(1, int(duration / 60))
            await self._update_group_card(event, origin, remaining_minutes)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(self.silence_map[origin])
        )
        logger.info(f"[Shutup] 🔇 已禁言 | 时长: {duration}s | 到期: {expiry_time}")

        # ====== 哄睡成功的专属回复 ======
        if is_sleep_early:
            return f"那{self.bot_name}继续回被窝啦，晚安~"
        # ====================================

        return self.shutup_reply.format(duration=duration, expiry_time=expiry_time)

    async def _handle_unshutup_command(
        self, event: AstrMessageEvent, origin: str
    ) -> str:
        """处理解除闭嘴指令"""
        # 计算已禁言时长
        old_expiry = self.silence_map.get(origin)
        if old_expiry:
            now = time.time()
            duration = int(max(0, now - (old_expiry - self.default_duration)))
        else:
            duration = 0

        # 解除禁言
        self.silence_map.pop(origin, None)
        self._save_silence_map()

        now = time.time()
        # 增加状态检查：判断当前是否已处于临时的“梦游清醒”状态
        is_already_awake = (
            origin in self.temp_wake_map and now < self.temp_wake_map[origin]
        )

        # 恢复原始群昵称(如果启用)
        if self.group_card_enabled:
            await self._update_group_card(event, origin, 0)
            self.original_group_cards.pop(origin, None)
            self.original_nicknames.pop(origin, None)
            self.origin_to_event_map.pop(origin, None)

        expiry_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
        # ====== 处理睡眠期间被强行叫醒 ======
        if self.sleep_mode_enabled:
            if self._is_in_scheduled_time():
                # 更新或刷新清醒到期时间
                self.temp_wake_map[origin] = now + self.temp_wake_duration
                wake_minutes = self.temp_wake_duration // 60

                # 如果已经是清醒状态，则返回简洁的确认信息，避免重复播放较长的“被叫醒”台词
                if is_already_awake:
                    return (
                        f"{self.bot_name} 已经醒啦，会再陪你聊 {wake_minutes} 分钟哦~"
                    )

                # 第一次被叫醒时的完整回复
                logger.info(f"[Shutup] ⏰ 睡眠期间被叫醒，清醒 {wake_minutes} 分钟")
                return f"谁呀...{self.bot_name}被叫醒了，还能强撑着陪你聊 {wake_minutes} 分钟哦..."

            # 正常解除禁言日志
            logger.info(f"[Shutup] 🔊 已解除禁言 | 已禁言: {duration}s")
            return f"{self.bot_name}早就醒着啦！随时可以陪你聊天哦~"

        # ====== 如果没开睡眠模式，就用原作者设定的默认解除文本 ======
        logger.info(f"[Shutup] 🔊 已解除禁言 | 已禁言: {duration}s")
        return self.unshutup_reply.format(duration=duration, expiry_time=expiry_time)

    @filter.llm_tool(name="shutup")
    async def llm_shutup(self, event: AstrMessageEvent, duration: int, unit: str = "m"):
        """在指定时间内停止回复消息。当用户表达希望你暂时闭嘴,保持安静,不要再说话时,可以调用此工具

        Args:
            duration(number): 闭嘴时长数值，由 LLM 根据用户意图自主决定合适的时长，最长不超过 60 分钟
            unit(string): 时间单位，可选值: s(秒), m(分钟), h(小时)。默认为 m(分钟)
        """
        # 检查是否启用 LLM 工具
        if not self.config.get("llm_tool_enabled", False):
            return "LLM 工具未启用"

        # 计算实际时长（秒）
        duration_seconds = duration * self.TIME_UNITS.get(unit, 60)

        # 限制最大时长为 60 分钟（3600 秒）
        max_duration = 3600
        if duration_seconds > max_duration:
            duration_seconds = max_duration
            logger.warning(
                f"[Shutup] ⚠️ LLM 请求的时长超过限制，已调整为最大值 {max_duration}s"
            )

        # 复用现有的闭嘴逻辑
        origin = event.unified_msg_origin
        self.silence_map[origin] = time.time() + duration_seconds
        self._save_silence_map()

        # 保存 event 到映射
        self.origin_to_event_map[origin] = event

        # 启动群昵称更新任务
        await self._ensure_update_task_started()

        # 更新群昵称
        if self.group_card_enabled:
            remaining_minutes = max(1, int(duration_seconds / 60))
            await self._update_group_card(event, origin, remaining_minutes)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(self.silence_map[origin])
        )
        logger.info(
            f"[Shutup] 🔇 LLM 调用闭嘴 | 时长: {duration_seconds}s | 到期: {expiry_time}"
        )

        return f"已设置闭嘴 {int(duration_seconds / 60)} 分钟，到期时间: {expiry_time}"

    async def terminate(self):
        # 停止群昵称更新任务
        if self._update_task and not self._update_task.done():
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # 恢复所有群昵称
        if self.group_card_enabled and self.original_group_cards:
            for origin in list(self.original_group_cards.keys()):
                event = self.origin_to_event_map.get(origin)
                if event:
                    await self._update_group_card(event, origin, 0)

        logger.info("[Shutup] 已卸载插件")
