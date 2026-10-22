"""TUI 专用控件。

这一层只负责"长什么样"：
- MessageBubble：用户 / 助手 / 系统 / 错误 消息气泡；
- ToolCard：可折叠的工具调用卡片（参数 JSON + 结果 + 耗时）；
- SessionPanel / ToolsPanel：右侧信息栏；
- HelpScreen：/help 命令弹窗。

不包含任何业务判断。
"""

from __future__ import annotations

import json
import uuid

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Static

# ---------- 消息气泡 ----------

ROLE_TITLES = {
    "user": "你",
    "assistant": "guladuo",
    "system": "系统",
    "error": "错误",
}

ROLE_LABEL_STYLE = {
    "user": "bold yellow",
    "assistant": "bold cyan",
    "system": "dim",
    "error": "bold red",
}


class MessageBubble(Static):
    """一条消息气泡：第一行是角色标签，下面是内容。支持流式追加。"""

    def __init__(self, role: str, text: str = "", **kwargs) -> None:
        super().__init__("", **kwargs)
        self.role = role
        self._buffer = text
        self.add_class("bubble")
        self.add_class("role-%s" % role)
        self._refresh_view()

    @property
    def plain_text(self) -> str:
        return self._buffer

    def append_text(self, chunk: str) -> None:
        self._buffer += chunk
        self._refresh_view()

    def set_text(self, text: str) -> None:
        self._buffer = text
        self._refresh_view()

    def _refresh_view(self) -> None:
        label = ROLE_TITLES.get(self.role, self.role)
        style = ROLE_LABEL_STYLE.get(self.role, "bold")
        text = Text()
        text.append(label, style)
        text.append("\n")
        text.append(self._buffer)
        self.update(text)


# ---------- 工具调用卡片 ----------

class ToolCard(Static):
    """一次工具调用：头部一行摘要，点击可折叠/展开参数与结果。"""

    def __init__(self, name: str, arguments: str, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.tool_name = name
        self._arguments = arguments or "{}"
        self._output = ""
        self._ok: bool | None = None  # None = 执行中
        self._elapsed_ms: int | None = None
        self._expanded = True
        self.add_class("tool-card")
        self.add_class("tool-running")
        self._refresh_view()

    # 生命周期

    def finish(self, output: str, ok: bool, elapsed_ms: int) -> None:
        self._output = output
        self._ok = ok
        self._elapsed_ms = elapsed_ms
        self.remove_class("tool-running")
        self.add_class("tool-ok" if ok else "tool-failed")
        self._refresh_view()

    # 交互

    def on_click(self, event: Click) -> None:
        self._expanded = not self._expanded
        self._refresh_view()

    # 渲染

    def _args_summary(self) -> str:
        try:
            data = json.loads(self._arguments)
        except (json.JSONDecodeError, TypeError):
            return self._arguments
        if not isinstance(data, dict) or not data:
            return ""
        inner = ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in data.items())
        return "(%s)" % inner

    def _pretty_args(self) -> str:
        try:
            data = json.loads(self._arguments)
        except (json.JSONDecodeError, TypeError):
            return self._arguments
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _refresh_view(self) -> None:
        text = Text()
        marker = "▾" if self._expanded else "▸"
        if self._ok is None:
            status, status_style = "⋯", "bold yellow"
        elif self._ok:
            status, status_style = "✓", "bold green"
        else:
            status, status_style = "✗", "bold red"

        text.append(" %s " % marker, "dim")
        text.append(" %s " % status, status_style)
        text.append(" %s%s" % (self.tool_name, self._args_summary()), "bold")
        if self._ok is not None:
            text.append(" · %s" % _fmt_elapsed(self._elapsed_ms), "dim")

        if self._expanded:
            text.append("\n\n 参数\n", "bold")
            for line in self._pretty_args().splitlines():
                text.append("  %s\n" % line)
            if self._ok is not None:
                text.append("\n 结果\n", "bold")
                for line in self._output.splitlines() or [""]:
                    text.append("  %s\n" % line)
        text.rstrip()
        self.update(text)


def _fmt_elapsed(ms: int | None) -> str:
    if ms is None:
        return ""
    return "%d ms" % ms if ms < 1000 else "%.1f s" % (ms / 1000)


# ---------- 右侧信息栏 ----------

class SessionPanel(Static):
    """会话元信息：模型、接口、目录、轮次等。"""

    def update_info(
        self,
        *,
        model: str,
        base_url: str,
        workspace: str,
        session_id: str,
        turns: int,
        messages: int,
        round_limit: int,
        busy: bool,
    ) -> None:
        text = Text()
        text.append("会话\n", "bold")
        rows = [
            ("模型", model),
            ("接口", base_url),
            ("目录", workspace),
            ("会话", session_id),
            ("轮次", str(turns)),
            ("消息", str(messages)),
            ("上限", "%d 次模型调用/轮" % round_limit),
            ("状态", "推理中…" if busy else "就绪"),
        ]
        for key, value in rows:
            text.append("%-4s " % key, "dim")
            text.append("%s\n" % value)
        text.rstrip()
        self.update(text)


class ToolsPanel(Static):
    """已注册工具清单。"""

    def update_tools(self, tools) -> None:
        text = Text()
        text.append("工具 · %d\n" % len(tools), "bold")
        for tool in tools:
            text.append("• %s\n" % tool.name, "bold cyan")
            desc = tool.description.strip().replace("\n", " ")
            text.append("%s\n" % desc, "dim")
        text.rstrip()
        self.update(text)


# ---------- /help 弹窗 ----------

COMMANDS = [
    ("/help", "打开本帮助"),
    ("/clear", "清空界面（保留历史）"),
    ("/new", "新建会话（重置历史）"),
    ("/tools", "列出已注册工具"),
    ("/cwd", "显示工作目录"),
    ("/model", "显示模型与接口"),
    ("/quit", "退出"),
]

SHORTCUTS = [
    ("Enter", "发送"),
    ("Esc", "中断生成 / 回输入框"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+N", "新会话"),
    ("Ctrl+B", "收起/展开侧栏"),
    ("F1", "帮助"),
    ("Ctrl+Q", "退出"),
]


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "关闭"), ("f1", "dismiss", "关闭")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog") as dialog:
            dialog.border_title = "guladuo 帮助"
            dialog.border_subtitle = "Esc / F1 关闭"
            yield Static(self._build_body(), id="help-body")

    def _build_body(self) -> Text:
        text = Text()
        text.append("guladuo 帮助\n\n", "bold")
        text.append("使用方式\n", "bold cyan")
        text.append("• 在底部输入框输入问题，Enter 发送。\n")
        text.append("• 需要查看/修改文件时会自动调用工具，工具卡片可点击展开。\n")
        text.append("• Esc 中断当前生成：已完成的问答仍会写入历史。\n\n")
        text.append("命令\n", "bold cyan")
        for cmd, desc in COMMANDS:
            text.append("%-8s" % cmd, "bold yellow")
            text.append("  %s\n" % desc)
        text.append("\n快捷键\n", "bold cyan")
        for key, desc in SHORTCUTS:
            text.append("%-8s" % key, "bold yellow")
            text.append("  %s\n" % desc)
        return text


def new_session_id() -> str:
    """8 位短会话 ID。"""
    return uuid.uuid4().hex[:8]
