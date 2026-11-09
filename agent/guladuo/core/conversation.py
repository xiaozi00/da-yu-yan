"""对话历史管理。

只负责消息的累积与形态转换，不关心模型是谁、界面长什么样。
"""

from __future__ import annotations

from typing import Any

from .agents_md import is_agents_message


class Conversation:
    def __init__(self, system_prompt: str = "") -> None:
        self._messages: list[dict[str, Any]] = []
        if system_prompt:
            self._messages.insert(0, {"role": "system", "content": system_prompt})

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

    def insert_after_system(self, message: dict[str, Any]) -> int:
        """插到 System Prompt 之后（第 1 位），返回插入位置。

        位置固定 + 内容固定 = 前缀稳定，服务端 prompt cache 才能一直命中。
        """
        index = 1 if self._messages and self._messages[0].get("role") == "system" else 0
        self._messages.insert(index, message)
        return index

    # ---------- AGENTS.md ----------

    def has_agents_doc(self) -> bool:
        return any(is_agents_message(message) for message in self._messages)

    # ---------- 上下文压缩 / 会话恢复 ----------

    @property
    def system_prompt(self) -> str:
        for message in self._messages:
            if message["role"] == "system":
                return str(message.get("content") or "")
        return ""

    def body(self) -> list[dict[str, Any]]:
        """除 System Prompt 之外的全部历史（压缩时要归档的就是这些）。"""
        return [message for message in self._messages if message["role"] != "system"]

    def replace_body(self, messages: list[dict[str, Any]]) -> None:
        """用摘要替换全部历史。

        两样东西必须保留：System Prompt（永远在最前）和 AGENTS.md 注入
        （项目约定属于「每次都生效」的内容，被摘要吃掉就等于换了个人格）。
        """
        keep = [m for m in self._messages if m["role"] == "system" or is_agents_message(m)]
        self._messages = keep + list(messages)

    def replace_all(self, messages: list[dict[str, Any]]) -> None:
        """整体替换（/resume 恢复会话用）。若历史里没有 System Prompt 则补回当前的。"""
        system = self.system_prompt
        restored = [dict(message) for message in messages]
        if not any(m.get("role") == "system" for m in restored) and system:
            restored.insert(0, {"role": "system", "content": system})
        self._messages = restored

    def clear(self) -> None:
        system = [m for m in self._messages if m["role"] == "system"]
        self._messages = system
