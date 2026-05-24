"""Message interception and command handling."""

from __future__ import annotations

import re
import time
from string import Formatter
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.api import logger

from .config import TIME_UNITS

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..main import ShutupPlugin


class MessageHandlers:
    """All message-handling logic for the ShutupPlugin."""

    def __init__(self, plugin: ShutupPlugin) -> None:
        self._p = plugin

    # ------------------------------------------------------------------ #
    #  Main dispatch
    # ------------------------------------------------------------------ #

    async def dispatch(self, event: AstrMessageEvent) -> str | None:
        """Entry point called from ``handle_message``.

        Returns a value that the Star handler should ``yield``, or
        ``None`` when the message should pass through.
        """
        text = event.get_message_str().strip()
        origin = event.unified_msg_origin

        if self._p._is_in_sleep_time():
            if self._p.sleep_interaction_enabled:
                return await self._handle_sleep_interaction(event, text, origin)
            logger.info("[Shutup] 睡眠时段生效中")
            if self._p.group_card_enabled:
                await self._p._sync_group_card_state(event, origin)
            self._p._stop_active_responses(event)
            event.should_call_llm(False)
            event.stop_event()
            return None

        expiry = self._p._store.get(origin)
        if expiry is not None:
            if self._p._store.is_permanent(origin):
                logger.info(f"[Shutup] 永久闭嘴中，消息已拦截 | 来源: {origin}")
                self._p._stop_active_responses(event)
                event.should_call_llm(False)
                event.stop_event()
            elif time.time() < expiry:
                remaining = int(expiry - time.time())
                logger.info(
                    f"[Shutup] 消息已拦截 | 来源: {origin} | 剩余: {remaining}s"
                )
                self._p._stop_active_responses(event)
                event.should_call_llm(False)
                event.stop_event()
            else:
                logger.info("[Shutup] 禁言已自动过期")
                if self._p.group_card_enabled:
                    await self._p._group_card.update(event, origin, 0)
                self._p._store.remove(origin)
                self._p._store.save()
        return None

    # ------------------------------------------------------------------ #
    #  Command helpers
    # ------------------------------------------------------------------ #

    def _parse_duration(self, duration_text: str) -> int:
        """Extract custom duration from a framework command argument."""
        match = re.match(r"^(\d+)([smhd])?", duration_text.strip().lower())
        if match:
            val = int(match.group(1))
            unit = match.group(2) or "s"
            return val * TIME_UNITS.get(unit, 1)
        return self._p.default_duration

    # ------------------------------------------------------------------ #
    #  Permission checks
    # ------------------------------------------------------------------ #

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        if not self._p.require_admin:
            return True

        admins = self._p.context.get_config().get("admins_id", [])
        sender_id = event.get_sender_id()
        return str(sender_id) in [str(a) for a in admins]

    def _format_template(self, template: str, **kwargs: object) -> str:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning(f"[Shutup] 回复模板占位符错误: {e}")
            return template

    def _format_llm_prompt(self, template: str, **kwargs: object) -> str | None:
        if not isinstance(template, str) or not template.strip():
            return None

        allowed_fields = set(kwargs)
        try:
            for _, field_name, _, _ in Formatter().parse(template):
                if field_name is None:
                    continue
                root_name = field_name.split(".", 1)[0].split("[", 1)[0]
                if root_name and root_name not in allowed_fields:
                    logger.warning(f"[Shutup] LLM 提示词包含未知占位符: {field_name}")
                    return None
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError) as e:
            logger.warning(f"[Shutup] LLM 提示词格式化失败: {e}")
            return None

    async def _generate_llm_reply(
        self, event: AstrMessageEvent, prompt: str, error_context: str
    ) -> str | None:
        try:
            provider_id = await self._p.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
            response = await self._p.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                contexts=[],
            )
            reply = (response.completion_text or "").strip()
            if reply:
                return reply
        except Exception as e:
            logger.warning(f"[Shutup] {error_context}，使用模板回复: {e}")
        return None

    async def _generate_command_reply(
        self,
        event: AstrMessageEvent,
        prompt_template: str,
        default_reply: str,
        command_type: str,
        duration: int = 0,
    ) -> str | None:
        sender_name = event.get_sender_name()
        prompt_vars = dict(
            sender_name=sender_name,
            user_name=sender_name,
            duration=duration,
            default_reply=default_reply,
        )
        prompt = self._format_llm_prompt(prompt_template, **prompt_vars)
        if prompt is None:
            return None

        return await self._generate_llm_reply(
            event,
            prompt,
            error_context=f"生成{command_type}回复失败",
        )

    # ------------------------------------------------------------------ #
    #  Shutup / unshutup
    # ------------------------------------------------------------------ #

    async def handle_shutup_command(
        self, event: AstrMessageEvent, duration_text: str = ""
    ) -> str:
        if self._p.require_admin and not self._check_admin(event):
            return "管理员才能使用此指令"

        origin = event.unified_msg_origin

        is_sleep_early = (
            self._p.sleep_interaction_enabled
            and self._p._is_in_sleep_time()
            and origin in self._p.temp_wake_map
        )
        if is_sleep_early:
            self._p.temp_wake_map.pop(origin, None)

        duration = self._parse_duration(duration_text)

        self._p._apply_silence(origin, duration, event)

        if self._p.group_card_enabled:
            await self._p._sync_group_card_state(event, origin)

        expiry_time = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(self._p._store.get(origin) or time.time()),
        )
        logger.info(f"[Shutup] 已禁言 | 时长: {duration}s | 到期: {expiry_time}")

        default_reply = self._format_template(
            self._p.shutup_reply,
            duration=duration,
            expiry_time=expiry_time,
        )
        if self._p.shutup_llm_reply_enabled:
            llm_reply = await self._generate_command_reply(
                event=event,
                prompt_template=self._p.shutup_llm_prompt,
                default_reply=default_reply,
                command_type="闭嘴",
                duration=duration,
            )
            if llm_reply:
                return llm_reply

        return default_reply

    async def handle_permanent_shutup_command(self, event: AstrMessageEvent) -> str:
        if self._p.require_admin and not self._check_admin(event):
            return "管理员才能使用此指令"

        origin = event.unified_msg_origin
        self._p.temp_wake_map.pop(origin, None)
        self._p._store.set_permanent(origin)
        self._p._store.save()
        self._p._stop_active_responses(event)

        if self._p.group_card_enabled:
            await self._p._sync_group_card_state(event, origin)

        logger.info(f"[Shutup] 已永久闭嘴 | 来源: {origin}")
        default_reply = self._format_template(self._p.permanent_shutup_reply)
        if self._p.permanent_shutup_llm_reply_enabled:
            llm_reply = await self._generate_command_reply(
                event=event,
                prompt_template=self._p.permanent_shutup_llm_prompt,
                default_reply=default_reply,
                command_type="永久闭嘴",
            )
            if llm_reply:
                return llm_reply

        return default_reply

    async def handle_unshutup_command(self, event: AstrMessageEvent) -> str | None:
        if self._p.require_admin and not self._check_admin(event):
            return "管理员才能使用此指令"

        origin = event.unified_msg_origin
        had_active_silence = origin in self._p._store
        old_expiry = self._p._store.get(origin)
        if old_expiry is not None and not self._p._store.is_permanent(origin):
            now = time.time()
            started_at = self._p._store.get_started_at(origin)
            if started_at is not None:
                duration = int(max(0, now - started_at))
            else:
                duration = int(max(0, now - (old_expiry - self._p.default_duration)))
        else:
            duration = 0

        now = time.time()
        was_already_awake = (
            origin in self._p.temp_wake_map and now < self._p.temp_wake_map[origin]
        )

        if not had_active_silence and not was_already_awake:
            logger.info("[Shutup] 当前未闭嘴，忽略解除闭嘴指令并继续后续流程")
            event.continue_event()
            return None

        if had_active_silence and self._p.group_card_enabled:
            await self._p._group_card.update(event, origin, 0)

        self._p._store.remove(origin)
        self._p._store.save()

        logger.info(f"[Shutup] 已解除禁言 | 已禁言: {duration}s")
        default_reply = self._format_template(
            self._p.unshutup_reply,
            duration=duration,
            expiry_time="已解除",
        )
        if self._p.unshutup_llm_reply_enabled:
            llm_reply = await self._generate_command_reply(
                event=event,
                prompt_template=self._p.unshutup_llm_prompt,
                default_reply=default_reply,
                command_type="说话",
                duration=duration,
            )
            if llm_reply:
                return llm_reply

        return default_reply

    async def handle_temp_wake_command(self, event: AstrMessageEvent) -> str | None:
        if self._p.require_admin and not self._check_admin(event):
            return "管理员才能使用此指令"

        origin = event.unified_msg_origin

        if not (self._p.sleep_interaction_enabled and self._p._is_in_sleep_time()):
            logger.info("[Shutup] 非睡眠时段忽略临时唤醒指令并继续后续流程")
            event.continue_event()
            return None

        now = time.time()
        was_already_awake = (
            origin in self._p.temp_wake_map and now < self._p.temp_wake_map[origin]
        )
        self._p.temp_wake_map[origin] = now + self._p.temp_wake_duration
        wake_minutes = self._p.temp_wake_duration // 60
        wake_command = self._p.temp_wake_cmds[0] if self._p.temp_wake_cmds else "醒醒"

        if self._p.group_card_enabled:
            await self._p._sync_group_card_state(event, origin)

        if was_already_awake:
            logger.info("[Shutup] 已处于临时唤醒状态，忽略临时唤醒指令并继续后续流程")
            event.continue_event()
            return None

        logger.info(f"[Shutup] 睡眠中被临时唤醒，清醒 {wake_minutes} 分钟")
        if self._p.temp_wake_llm_reply_enabled:
            llm_reply = await self._generate_temp_wake_reply(
                event=event,
                wake_minutes=wake_minutes,
                wake_command=wake_command,
            )
            if llm_reply:
                return llm_reply

        return self._format_template(
            self._p.temp_wake_reply,
            wake_minutes=wake_minutes,
            temporary_wake_duration=self._p.temp_wake_duration,
            wake_command=wake_command,
        )

    async def _generate_temp_wake_reply(
        self, event: AstrMessageEvent, wake_minutes: int, wake_command: str
    ) -> str | None:
        prompt = self._format_llm_prompt(
            self._p.temp_wake_llm_prompt,
            wake_minutes=wake_minutes,
            temporary_wake_duration=self._p.temp_wake_duration,
            wake_command=wake_command,
            sender_name=event.get_sender_name(),
        )
        if prompt is None:
            return None
        return await self._generate_llm_reply(
            event,
            prompt,
            error_context="生成临时唤醒回复失败",
        )

    # ------------------------------------------------------------------ #
    #  Sleep interaction
    # ------------------------------------------------------------------ #

    async def _handle_sleep_interaction(
        self, event: AstrMessageEvent, text: str, origin: str
    ) -> str | None:
        wake_expiry = self._p.temp_wake_map.get(origin)
        if wake_expiry is not None and time.time() < wake_expiry:
            self._p.temp_wake_map[origin] = time.time() + self._p.temp_wake_duration
            remaining = int(self._p.temp_wake_map[origin] - time.time())
            logger.info(f"[Shutup] bot 当前处于临时清醒状态 | 剩余: {remaining}s")
            if self._p.group_card_enabled:
                await self._p._sync_group_card_state(event, origin)
            return None

        self._p.temp_wake_map.pop(origin, None)
        logger.info("[Shutup] 睡眠时段生效中")

        if self._p.group_card_enabled:
            await self._p._sync_group_card_state(event, origin)

        if self._is_talking_to_bot(event, text):
            wake_word = self._p.temp_wake_cmds[0] if self._p.temp_wake_cmds else "醒醒"

            return self._format_template(
                self._p.sleep_prompt_reply,
                wake_word=wake_word,
                wake_command=wake_word,
                temporary_wake_duration=self._p.temp_wake_duration,
                wake_minutes=self._p.temp_wake_duration // 60,
            )

        self._p._stop_active_responses(event)
        event.should_call_llm(False)
        event.stop_event()
        return None

    def _is_talking_to_bot(self, event: AstrMessageEvent, text: str) -> bool:
        chain = event.get_messages()
        wake_prefixes = self._p.context.get_config().get("wake_prefix", [])
        if chain:
            first_seg = chain[0]
            if isinstance(first_seg, Comp.At) and str(first_seg.qq) == str(
                event.get_self_id()
            ):
                return True
            if isinstance(first_seg, Comp.Plain) and wake_prefixes:
                if any(first_seg.text.startswith(p) for p in wake_prefixes):
                    return True

        if event.is_private_chat() or event.is_at_or_wake_command:
            return True

        cmd_prefix = self._p.context.get_config().get("command_prefix", "/")
        prefixes = cmd_prefix if isinstance(cmd_prefix, list) else [cmd_prefix]
        return any(text.startswith(p) for p in prefixes if p)
