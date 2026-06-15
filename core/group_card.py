"""Group card (nameplate) update helpers — aiocqhttp only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from astrbot.api import logger
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ..main import ShutupPlugin

GroupCardStatus = Literal["muted", "sleep", "temporary_wake"]
RestoreResult = Literal["updated", "retry", "skipped"]


@dataclass
class GroupCardTarget:
    """Runtime target required to update a group card without a message event."""

    bot: Any
    group_id: str
    self_id: str


class GroupCardUpdater:
    """Updates the bot's group card to show remaining quiet time.

    Only functional on the **aiocqhttp** QQ platform.
    """

    def __init__(self, plugin: ShutupPlugin) -> None:
        self._plugin = plugin
        self.origin_to_event_map: dict[str, AstrMessageEvent] = {}
        self.origin_to_target_map: dict[str, GroupCardTarget] = {}
        self._original_identity_cache: dict[str, tuple[str, str]] = {}
        self._task: asyncio.Task | None = None
        self._restore_task: asyncio.Task | None = None
        self._started: bool = False

    # -- task lifecycle --------------------------------------------------- #

    def ensure_started(self) -> None:
        if self._plugin.group_card_enabled and not self._started:
            self._started = True
            self._task = asyncio.create_task(self._update_loop())
            logger.info("[Shutup] 群昵称更新任务已启动")

    def is_tracking(self, origin: str) -> bool:
        return origin in self.origin_to_event_map or origin in self.origin_to_target_map

    def schedule_restore_from_store(self) -> None:
        """Re-apply persisted group-card state after plugin reload/startup."""

        if not self._plugin.group_card_enabled:
            return

        origins = {
            origin
            for origin in self._plugin._store.active_origins
            if not self.is_tracking(origin)
        }
        if not origins:
            return

        if self._restore_task is not None and not self._restore_task.done():
            return

        try:
            loop = asyncio.get_running_loop()
            self._restore_task = loop.create_task(
                self._restore_from_store_loop(origins),
            )
            logger.info(
                f"[Shutup] 已安排从持久化记录恢复 {len(origins)} 个群昵称状态",
            )
        except RuntimeError as e:
            logger.debug(f"[Shutup] 当前事件循环不可用，跳过群昵称状态恢复任务: {e}")

    async def cancel(self) -> None:
        for task in (self._restore_task, self._task):
            if task is None or task.done():
                continue

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._started = False

    async def restore_all(self) -> None:
        if not self._plugin.group_card_enabled:
            return
        origins = (
            set(self._plugin._store.active_origins)
            | set(self.origin_to_event_map)
            | set(self.origin_to_target_map)
        )
        for origin in origins:
            event = self.origin_to_event_map.get(origin)
            if event is not None:
                await self.update(event, origin, 0)
                continue

            target = self.origin_to_target_map.get(origin)
            if target is not None:
                await self.update_target(origin, target, 0)

    # -- persisted restore ----------------------------------------------- #

    async def _restore_from_store_loop(self, origins: set[str]) -> None:
        pending = set(origins)
        try:
            for _ in range(12):
                pending = await self._restore_from_store_once(pending)
                if not pending:
                    return
                await asyncio.sleep(5)

            logger.warning(
                f"[Shutup] 部分群昵称状态未能从持久化记录恢复: {sorted(pending)}",
            )
        except asyncio.CancelledError:
            logger.info("[Shutup] 群昵称状态恢复任务已停止")
            raise

    async def _restore_from_store_once(self, origins: set[str]) -> set[str]:
        pending: set[str] = set()
        changed = False

        for origin in list(origins):
            target, should_retry = await self._resolve_target(origin)
            if target is None:
                if should_retry:
                    pending.add(origin)
                continue

            group_card_state = self._plugin._get_group_card_state(origin)
            if group_card_state is None:
                if origin in self._plugin._store:
                    result = await self.update_target(origin, target, 0)
                    if result == "retry":
                        pending.add(origin)
                        continue
                    self._plugin._store.remove(origin)
                    changed = True
                continue

            status, remaining_minutes = group_card_state
            result = await self.update_target(
                origin,
                target,
                remaining_minutes,
                status=status,
            )
            if result == "retry":
                pending.add(origin)

        if changed:
            self._plugin._store.save()
        return pending

    async def _resolve_target(self, origin: str) -> tuple[GroupCardTarget | None, bool]:
        try:
            session = MessageSession.from_str(origin)
        except Exception as e:
            logger.debug(f"[Shutup] 无法解析群昵称恢复来源 {origin}: {e}")
            return None, False

        if session.message_type != MessageType.GROUP_MESSAGE:
            return None, False

        group_id = self._parse_group_id(session.session_id)
        if not group_id:
            logger.debug(f"[Shutup] 无法解析群号，跳过群昵称恢复: {origin}")
            return None, False

        platform = self._plugin.context.get_platform_inst(session.platform_id)
        if platform is None:
            return None, True
        if platform.meta().name != "aiocqhttp":
            logger.debug(
                f"[Shutup] 平台 {platform.meta().name} 不支持群昵称恢复: {origin}",
            )
            return None, False

        bot = getattr(platform, "bot", None)
        if bot is None:
            get_client = getattr(platform, "get_client", None)
            if callable(get_client):
                bot = get_client()
        if bot is None or not hasattr(bot, "call_action"):
            logger.debug(f"[Shutup] 平台客户端不支持 call_action，跳过: {origin}")
            return None, False

        try:
            login_info = await bot.call_action("get_login_info")
        except Exception as e:
            logger.debug(f"[Shutup] 获取 bot 登录信息失败，稍后重试: {e}")
            return None, True

        self_id = str(
            login_info.get("user_id")
            or login_info.get("self_id")
            or login_info.get("account")
            or "",
        )
        if not self_id:
            logger.debug(f"[Shutup] bot 登录信息缺少 user_id，跳过: {origin}")
            return None, False
        return GroupCardTarget(bot=bot, group_id=group_id, self_id=self_id), False

    @staticmethod
    def _parse_group_id(session_id: str) -> str:
        return session_id.rsplit("!", 1)[-1].rsplit("_", 1)[-1]

    # -- background loop -------------------------------------------------- #

    async def _update_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)

                changed = False
                origins = (
                    set(self._plugin._store.active_origins)
                    | set(self.origin_to_event_map)
                    | set(self.origin_to_target_map)
                )
                for origin in list(origins):
                    event = self.origin_to_event_map.get(origin)
                    target = self.origin_to_target_map.get(origin)
                    group_card_state = self._plugin._get_group_card_state(origin)

                    if group_card_state is not None:
                        if event is not None:
                            status, remaining_minutes = group_card_state
                            await self.update(
                                event,
                                origin,
                                remaining_minutes,
                                status=status,
                            )
                        elif target is not None:
                            status, remaining_minutes = group_card_state
                            await self.update_target(
                                origin,
                                target,
                                remaining_minutes,
                                status=status,
                            )
                        continue

                    restore_result: RestoreResult = "updated"
                    # 先 update(0) 恢复昵称，此时 store 还有 original_card
                    if event is not None:
                        await self.update(event, origin, 0)
                    elif target is not None:
                        restore_result = await self.update_target(origin, target, 0)
                    else:
                        self.origin_to_event_map.pop(origin, None)
                        self.origin_to_target_map.pop(origin, None)

                    if restore_result == "retry":
                        continue

                    # 昵称恢复完成后再清理 store
                    if origin in self._plugin._store:
                        self._plugin._store.remove(origin)
                        changed = True

                if changed:
                    self._plugin._store.save()

        except asyncio.CancelledError:
            logger.info("[Shutup] 群昵称更新任务已停止")
        except Exception as e:
            logger.error(f"[Shutup] 群昵称更新任务异常: {e}")

    # -- card update ------------------------------------------------------ #

    async def update(
        self,
        event: AstrMessageEvent,
        origin: str,
        remaining_minutes: int | None,
        status: GroupCardStatus = "muted",
    ) -> None:
        if not self._plugin.group_card_enabled:
            return

        is_active_status = remaining_minutes is None or remaining_minutes > 0
        if is_active_status:
            self.origin_to_event_map[origin] = event
            self.ensure_started()

        try:
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

            original_card, original_nick = self._get_original_identity(origin)

            if not original_card and not original_nick:
                try:
                    member_info = await bot.call_action(
                        "get_group_member_info",
                        group_id=int(group_id),
                        user_id=int(self_id),
                        no_cache=True,
                    )
                    original_card = member_info.get("card", "") or ""
                    original_nick = member_info.get("nickname", "") or ""
                    if self._plugin._store.get(origin) is None:
                        self._original_identity_cache[origin] = (
                            original_card,
                            original_nick,
                        )
                    else:
                        self._plugin._store.set_original_identity(
                            origin,
                            original_card=original_card,
                            original_nickname=original_nick,
                        )
                        self._plugin._store.save()
                    logger.debug(
                        f"[Shutup] 保存原始信息 | "
                        f"群昵称: {original_card} | "
                        f"QQ昵称: {original_nick}"
                    )
                except Exception as e:
                    logger.debug(f"[Shutup] 获取原始群昵称失败: {e}")
                    original_card = ""
                    original_nick = ""

            if is_active_status:
                card = self._format_active_card(
                    status,
                    remaining_minutes,
                    original_card,
                    original_nick,
                )
            else:
                card = original_card

            await bot.call_action(
                "set_group_card",
                group_id=int(group_id),
                user_id=int(self_id),
                card=card[:60],
            )
            logger.info(f"[Shutup] 已更新群昵称({status}): {card[:60]}")

        except Exception as e:
            logger.warning(f"[Shutup] 更新群昵称失败: {e}")
        finally:
            if not is_active_status:
                self.origin_to_event_map.pop(origin, None)
                self.origin_to_target_map.pop(origin, None)
                self._original_identity_cache.pop(origin, None)

    async def update_target(
        self,
        origin: str,
        target: GroupCardTarget,
        remaining_minutes: int | None,
        status: GroupCardStatus = "muted",
    ) -> RestoreResult:
        """Update a group card without relying on a cached message event."""

        if not self._plugin.group_card_enabled:
            return "skipped"

        is_active_status = remaining_minutes is None or remaining_minutes > 0
        if is_active_status:
            self.origin_to_target_map[origin] = target
            self.ensure_started()

        original_card, original_nick = self._get_original_identity(origin)
        if not original_card and not original_nick and not is_active_status:
            logger.warning(
                "[Shutup] 缺少持久化原始群昵称，无法从文件恢复群昵称状态 | "
                f"来源: {origin}",
            )
            return "skipped"

        if is_active_status:
            card = self._format_active_card(
                status,
                remaining_minutes,
                original_card,
                original_nick,
            )
        else:
            card = original_card

        try:
            await target.bot.call_action(
                "set_group_card",
                group_id=int(target.group_id),
                user_id=int(target.self_id),
                card=card[:60],
            )
            logger.info(f"[Shutup] 已恢复群昵称状态({status}): {card[:60]}")
            return "updated"
        except Exception as e:
            logger.warning(f"[Shutup] 从持久化记录恢复群昵称失败: {e}")
            return "retry"
        finally:
            if not is_active_status:
                self.origin_to_event_map.pop(origin, None)
                self.origin_to_target_map.pop(origin, None)
                self._original_identity_cache.pop(origin, None)

    def _get_original_identity(self, origin: str) -> tuple[str, str]:
        original_card, original_nick = self._plugin._store.get_original_identity(origin)
        if original_card or original_nick:
            return original_card, original_nick
        return self._original_identity_cache.get(origin, ("", ""))

    def _format_active_card(
        self,
        status: GroupCardStatus,
        remaining_minutes: int | None,
        original_card: str,
        original_nick: str,
    ) -> str:
        original_name = original_card if original_card else original_nick
        remaining_display = "永久" if remaining_minutes is None else remaining_minutes

        try:
            return self._get_template(status).format(
                remaining=remaining_display,
                original_card=original_card,
                original_nickname=original_nick,
                original_name=original_name,
                status=status,
            )
        except KeyError as e:
            logger.warning(f"[Shutup] 群昵称模板占位符错误: {e}，使用默认格式")
            return self._get_fallback_card(status, remaining_display)

    def _get_template(self, status: GroupCardStatus) -> str:
        if status == "sleep":
            return self._plugin.sleep_group_card_template
        if status == "temporary_wake":
            return self._plugin.temp_wake_group_card_template
        return self._plugin.group_card_template

    def _get_fallback_card(
        self,
        status: GroupCardStatus,
        remaining_display: str | int,
    ) -> str:
        if status == "sleep":
            return f"[睡眠中 {remaining_display}]"
        if status == "temporary_wake":
            return f"[临时唤醒 {remaining_display}]"
        return f"[闭嘴中 {remaining_display}]"
