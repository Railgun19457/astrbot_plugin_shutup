"""LLM function tool — shutup.

Registered via ``context.add_llm_tools(ShutupTool(plugin=self))`` in
``main.py`` (AstrBot v4.5.1+ recommended pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

MAX_DURATION: int = 3600


@pydantic_dataclass
class ShutupTool(FunctionTool[None]):
    plugin: Any = Field(default=None, repr=False, exclude=True)

    name: str = "shutup"
    description: str = "在指定时间内停止回复消息。当用户表达希望你暂时闭嘴,保持安静,不要再说话时,可以调用此工具"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "闭嘴时长数值，由 LLM 根据用户意图自主决定合适的时长，最长不超过插件配置的最大闭嘴时长",
                },
                "unit": {
                    "type": "string",
                    "description": "时间单位，可选值: s(秒), m(分钟), h(小时)。默认为 m(分钟)",
                },
            },
            "required": ["duration"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        duration: int,
        unit: str = "m",
    ) -> str:
        if self.plugin is None:
            return "LLM 工具未就绪"

        return await self.plugin._run_llm_shutup(event, duration, unit)


@pydantic_dataclass
class NotReplyTool(FunctionTool[None]):
    """LLM 工具：请求不对当前触发消息产生回复。

    使用场景：当 LLM 决定不对唤醒它的那条消息进行回复时，调用此工具。
    """

    plugin: Any = Field(default=None, repr=False, exclude=True)

    name: str = "not_reply"
    description: str = "当你决定不回复当前这条消息时调用；适用于用户要求不要回复或当前消息无需回应的场景"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def run(self, event: AstrMessageEvent) -> str | None:
        if self.plugin is None:
            return "LLM 工具未就绪"

        try:
            event.set_extra("_shutup_not_reply_this", True)
            logger.info(
                "[Shutup] LLM 请求本条消息不回复，已设置 not_reply 标记 | 来源: %s",
                event.unified_msg_origin,
            )
            return None
        except Exception:
            return "请求不回复本条消息失败"


def build_llm_tools(plugin) -> list[FunctionTool[None]]:
    tools = [ShutupTool(plugin=plugin), NotReplyTool(plugin=plugin)]
    for t in tools:
        t.plugin = plugin
        if isinstance(t, ShutupTool):
            max_duration = getattr(plugin, "shutup_tool_max_duration", MAX_DURATION)
            t.parameters["properties"]["duration"]["description"] = (
                "闭嘴时长数值，由 LLM 根据用户意图自主决定合适的时长，"
                f"最长不超过 {max_duration} 秒"
            )
    return tools
