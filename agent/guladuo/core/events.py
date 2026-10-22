"""core 对外的输出契约。

前端只认识这些事件，不需要知道模型、工具、消息历史的任何细节。
新增前端时，只需实现"如何渲染这些事件"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class TurnStarted:
    """用户提交了一次输入，一轮处理开始。"""

    text: str


@dataclass(frozen=True)
class TextDelta:
    """模型输出的一个增量片段（流式）。"""

    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    """模型请求调用工具，即将执行。"""

    call_id: str
    name: str
    arguments: str
    index: int


@dataclass(frozen=True)
class ToolCallFinished:
    """工具执行完成。"""

    call_id: str
    name: str
    output: str
    ok: bool
    elapsed_ms: int


@dataclass(frozen=True)
class TurnFinished:
    """本轮回答结束，content 是完整文本。"""

    content: str


@dataclass(frozen=True)
class AgentFailed:
    """本轮因异常终止。"""

    message: str


AgentEvent = Union[
    TurnStarted,
    TextDelta,
    ToolCallStarted,
    ToolCallFinished,
    TurnFinished,
    AgentFailed,
]
