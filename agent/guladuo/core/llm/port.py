"""LLM 端口定义。

core 只依赖这里的 Protocol；具体实现（OpenAI 兼容接口、假模型、其它厂商）
放在同目录下，通过构造注入，方便测试时替换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, Sequence


@dataclass(frozen=True)
class ToolCallDelta:
    """流式返回中工具调用的一个分片。"""

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


@dataclass(frozen=True)
class StreamDelta:
    """一次流式增量。"""

    content: str = ""
    tool_calls: Sequence[ToolCallDelta] = field(default_factory=tuple)


class LLMClient(Protocol):
    """任何模型客户端只需要实现这一个方法。"""

    def stream(
        self,
        messages: Sequence[dict],
        tools: Sequence[dict] | None = None,
    ) -> Iterator[StreamDelta]:
        ...
