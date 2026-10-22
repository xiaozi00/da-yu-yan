"""Agent 编排层。

职责：把「用户输入 → 模型流式输出 → 工具执行 → 结果回传 → 继续推理」
组织成一个可迭代的事件流。它不认识终端、窗口或网页。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Iterator

from .conversation import Conversation
from .errors import GuLaduoError
from .events import (
    AgentEvent,
    AgentFailed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from .llm.port import LLMClient
from .tools.registry import ToolRegistry


@dataclass
class _PendingCall:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        conversation: Conversation | None = None,
        *,
        tool_round_limit: int = 8,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._conversation = conversation or Conversation()
        self._round_limit = tool_round_limit

    # ---------- 只读状态 ----------

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def tool_names(self) -> list[str]:
        return self._tools.names

    @property
    def tools(self) -> list:
        """全部工具对象（含描述），供前端展示，不参与业务。"""
        return self._tools.tools

    def reset(self) -> None:
        self._conversation.clear()

    # ---------- 主流程 ----------

    def run(self, user_input: str) -> Iterator[AgentEvent]:
        text = (user_input or "").strip()
        if not text:
            return

        yield TurnStarted(text)
        self._conversation.add_user(text)

        try:
            for _ in range(self._round_limit):
                content = ""
                pending: dict[int, _PendingCall] = {}

                for delta in self._llm.stream(self._conversation.messages, self._tools.schemas()):
                    if delta.content:
                        content += delta.content
                        yield TextDelta(delta.content)

                    for piece in delta.tool_calls:
                        call = pending.setdefault(piece.index, _PendingCall(index=piece.index))
                        if piece.id and not call.id:
                            call.id = piece.id
                        if piece.name and not call.name:
                            call.name = piece.name
                        if piece.arguments:
                            call.arguments += piece.arguments

                calls = [pending[key] for key in sorted(pending)]

                if not calls:
                    self._conversation.add_assistant(content)
                    yield TurnFinished(content)
                    return

                self._conversation.add_tool_calls(
                    content,
                    [
                        {
                            "id": call.id or "call_%d" % call.index,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in calls
                    ],
                )

                for call in calls:
                    call_id = call.id or "call_%d" % call.index
                    yield ToolCallStarted(
                        call_id=call_id,
                        name=call.name,
                        arguments=call.arguments,
                        index=call.index,
                    )

                    started = perf_counter()
                    try:
                        result = self._tools.invoke(call.name, call.arguments)
                        output, ok = result.output, result.ok
                    except GuLaduoError as exc:
                        output, ok = str(exc), False
                    elapsed_ms = int((perf_counter() - started) * 1000)

                    yield ToolCallFinished(
                        call_id=call_id,
                        name=call.name,
                        output=output,
                        ok=ok,
                        elapsed_ms=elapsed_ms,
                    )
                    self._conversation.add_tool_result(call_id, output)

            yield AgentFailed("工具调用轮次超过上限（%d 轮），已中止本轮" % self._round_limit)
        except GuLaduoError as exc:
            yield AgentFailed(str(exc))
