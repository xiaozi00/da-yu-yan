"""guladuo 核心层。

core 不依赖任何界面框架，只通过事件（AgentEvent）向外部输出过程，
因此同一套业务逻辑可以被 tui / web / desktop / mobile 等任意前端复用。
"""

from .agent import Agent
from .config import Settings
from .conversation import Conversation
from .events import (
    AgentEvent,
    AgentFailed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from .factory import create_agent

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentFailed",
    "Conversation",
    "Settings",
    "TextDelta",
    "ToolCallFinished",
    "ToolCallStarted",
    "TurnFinished",
    "TurnStarted",
    "create_agent",
]
