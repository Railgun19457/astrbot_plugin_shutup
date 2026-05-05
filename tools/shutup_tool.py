"""LLM function tool — shutup.

Registered via ``context.add_llm_tools(ShutupTool(plugin=self))`` in
``main.py`` (AstrBot v4.5.1+ recommended pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import Field
from pydantic.dataclasses import dataclass as pydantic_dataclass

from astrbot.core.agent.tool import FunctionTool

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

TIME_UNITS: dict[str, int] = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_DURATION: int = 3600


@pydantic_dataclass
class ShutupTool(FunctionTool[None]):
    plugin: Any = Field(default=None, repr=False, exclude=True)

    name: str = "shutup"
    description: str = (
        "在指定时间内停止回复消息。当用户表达希望你暂时闭嘴,保持安静,"
        "不要再说话时,可以调用此工具"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "闭嘴时长数值，由 LLM 根据用户意图自主决定合适的时长，最长不超过 60 分钟",
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
