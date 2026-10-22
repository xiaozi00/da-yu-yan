"""对话历史管理。

只负责消息的累积与形态转换，不关心模型是谁、界面长什么样。
"""

from __future__ import annotations

from typing import Any


class Conversation:
    def __init__(self, system_prompt: str = "") -> None:
        self._messages: list[dict[str, Any]] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    @property
    def messages(self) -> list[dict[str, Any]]:
        """返回副本，避免外部直接改写内部状态。"""
        return list(self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_tool_calls(self, content: str, calls: list[dict[str, Any]]) -> None:
        """记录"模型决定调用工具"这条 assistant 消息。"""
        self._messages.append(
            {"role": "assistant", "content": content, "tool_calls": calls}
        )

    def add_tool_result(self, call_id: str, output: str) -> None:
        self._messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": output}
        )

    def clear(self) -> None:
        system = [m for m in self._messages if m["role"] == "system"]
        self._messages = system
