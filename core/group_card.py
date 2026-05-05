"""Group card (nameplate) update helpers — aiocqhttp only."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..main import ShutupPlugin


class GroupCardUpdater:
    """Updates the bot's group card to show remaining mute time.

    Only functional on the **aiocqhttp** QQ platform.
    """

    def __init__(self, plugin: ShutupPlugin) -> None:
        self._plugin = plugin
        self.origin_to_event_map: dict[str, AstrMessageEvent] = {}
        self._original_cards: dict[str, str] = {}
        self._original_nicks: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._started: bool = False

    # -- task lifecycle --------------------------------------------------- #

    def ensure_started(self) -> None:
        if self._plugin.group_card_enabled and not self._started:
            self._started = True
            self._task = asyncio.create_task(self._update_loop())
            logger.info("[Shutup] 群昵称更新任务已启动")

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def restore_all(self) -> None:
        if not self._plugin.group_card_enabled:
            return
        for origin in list(self._original_cards):
            event = self.origin_to_event_map.get(origin)
            if event is not None:
                await self.update(event, origin, 0)

    # -- background loop -------------------------------------------------- #

    async def _update_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)

                if not self._plugin._store:
                    continue

                import time

                now = time.time()
                for origin in self._plugin._store.active_origins:
                    expiry = self._plugin._store.get(origin)
                    if expiry is None:
                        continue
                    remaining_seconds = expiry - now
                    event = self.origin_to_event_map.get(origin)

                    if remaining_seconds > 0:
                        if event is not None:
                            remaining_minutes = max(1, int(remaining_seconds / 60))
                            await self.update(event, origin, remaining_minutes)
                    else:
                        if event is not None:
                            await self.update(event, origin, 0)
                        self._original_cards.pop(origin, None)
                        self._original_nicks.pop(origin, None)
                        self.origin_to_event_map.pop(origin, None)

        except asyncio.CancelledError:
            logger.info("[Shutup] 群昵称更新任务已停止")
        except Exception as e:
            logger.error(f"[Shutup] 群昵称更新任务异常: {e}")

    # -- card update ------------------------------------------------------ #

    async def update(
        self, event: AstrMessageEvent, origin: str, remaining_minutes: int
    ) -> None:
        if not self._plugin.group_card_enabled:
            return

        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (  # noqa: E501
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
            if origin not in self._original_cards:
                try:
                    member_info = await bot.call_action(
                        "get_group_member_info",
                        group_id=int(group_id),
                        user_id=int(self_id),
                        no_cache=True,
                    )
                    self._original_cards[origin] = member_info.get("card", "") or ""
                    self._original_nicks[origin] = member_info.get("nickname", "") or ""
                    logger.debug(
                        f"[Shutup] 保存原始信息 | "
                        f"群昵称: {self._original_cards[origin]} | "
                        f"QQ昵称: {self._original_nicks[origin]}"
                    )
                except Exception as e:
                    logger.debug(f"[Shutup] 获取原始群昵称失败: {e}")
                    self._original_cards[origin] = ""
                    self._original_nicks[origin] = ""

            if remaining_minutes > 0:
                original_card = self._original_cards.get(origin, "")
                original_nick = self._original_nicks.get(origin, "")
                original_name = original_card if original_card else original_nick

                try:
                    card = self._plugin.group_card_template.format(
                        remaining=remaining_minutes,
                        original_card=original_card,
                        original_nickname=original_nick,
                        original_name=original_name,
                    )
                except KeyError as e:
                    logger.warning(f"[Shutup] 群昵称模板占位符错误: {e}，使用默认格式")
                    card = f"[闭嘴中 {remaining_minutes}分钟]"
            else:
                card = self._original_cards.get(origin, "")

            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(self_id),
                card=card[:60],
            )
            logger.info(f"[Shutup] 已更新群昵称: {card[:60]}")

        except Exception as e:
            logger.warning(f"[Shutup] 更新群昵称失败: {e}")
