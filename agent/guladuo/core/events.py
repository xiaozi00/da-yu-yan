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


@dataclass(frozen=True)
class ContextUsageChanged:
    """上下文用量变化（每次模型调用结束、压缩完成时都会发一次）。"""

    tokens: int
    window: int
    ratio: float
    source: str  # "api"（模型给的 usage）或 "estimate"（本地估算）
    default_window: bool  # 窗口大小是不是「默认 128k」兜底
    needs_compact: bool  # 已超过告警阈值，需先 /compact


@dataclass(frozen=True)
class CompactStarted:
    """上下文压缩开始，archive_id 是本轮归档编号。"""

    archive_id: str
    tokens: int


@dataclass(frozen=True)
class CompactFinished:
    """压缩成功：历史已被摘要替换。"""

    archive_id: str
    path: str
    messages: int
    before_tokens: int
    after_tokens: int


@dataclass(frozen=True)
class CompactFailed:
    """压缩失败：原上下文保持不变。"""

    message: str


AgentEvent = Union[
    TurnStarted,
    TextDelta,
    ToolCallStarted,
    ToolCallFinished,
    TurnFinished,
    AgentFailed,
    ContextUsageChanged,
    CompactStarted,
    CompactFinished,
    CompactFailed,
]
