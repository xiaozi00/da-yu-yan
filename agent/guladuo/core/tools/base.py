"""工具的公共抽象。

一个工具 = 元数据（给模型看的 schema） + 一个纯函数式的 handler。
handler 只接收业务参数、返回给模型看的文本，不接触 UI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        """转成 OpenAI function calling 需要的格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: str
    ok: bool = True
    arguments: dict[str, Any] = field(default_factory=dict)
