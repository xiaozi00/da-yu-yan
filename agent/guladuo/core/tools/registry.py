"""工具注册表：负责 schema 导出、参数解析与分发。"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..errors import ToolNotFound
from .base import Tool, ToolResult


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    # ---------- 注册 ----------

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError("工具重复注册：%s" % tool.name)
        self._tools[tool.name] = tool

    def extend(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    # ---------- 查询 ----------

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    @property
    def tools(self) -> list[Tool]:
        """按注册顺序返回全部工具（含描述），供前端展示。"""
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    # ---------- 执行 ----------

    def invoke(self, name: str, raw_arguments: str) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound("模型请求了未注册的工具：%s" % name)

        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            return ToolResult(
                name=name,
                output="参数不是合法 JSON：%r" % raw_arguments,
                ok=False,
            )

        if not isinstance(arguments, dict):
            return ToolResult(name=name, output="参数必须是 JSON 对象", ok=False)

        try:
            output = tool.handler(**arguments)
        except TypeError as exc:
            return ToolResult(name=name, output="参数不匹配：%s" % exc, ok=False, arguments=arguments)
        except Exception as exc:  # 工具内部异常不能炸掉整轮对话
            return ToolResult(name=name, output="工具执行异常：%s" % exc, ok=False, arguments=arguments)

        return ToolResult(name=name, output=str(output), ok=True, arguments=arguments)
