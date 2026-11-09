"""TUI 专用控件。

这一层只负责"长什么样"：
- MessageBubble：用户 / 助手 / 系统 / 错误 消息气泡；
- ToolCard：可折叠的工具调用卡片（参数 JSON + 结果 + 耗时）；
- CommandMenu：斜杠命令补全引导面板（↑↓ 选择 / Tab 补全 / Enter 执行 / Esc 关闭）；
- PromptInput：能感知补全面板的输入框；
- SessionPanel / MCPPanel / ToolsPanel：右侧信息栏；
- HelpScreen / SessionListScreen / CompactLogScreen / ConfigNoticeScreen：模态面板。

不包含任何业务判断。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

# ---------- 消息气泡 ----------


def fmt_tokens(value: int) -> str:
    """12288 → 12.3k；128000 → 128k。"""
    if value < 1000:
        return str(value)
    k = value / 1000
    if k >= 100:
        return "%dk" % round(k)
    return ("%.1f" % k).rstrip("0").rstrip(".") + "k"


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

    def __init__(
        self,
        name: str,
        arguments: str,
        *,
        expanded: bool = True,
        output_limit: int = 1200,
        **kwargs,
    ) -> None:
        super().__init__("", **kwargs)
        self.tool_name = name
        self._arguments = arguments or "{}"
        self._output = ""
        self._ok: bool | None = None  # None = 执行中
        self._elapsed_ms: int | None = None
        self._expanded = expanded
        self._output_limit = max(200, int(output_limit))
        self.add_class("tool-card")
        self.add_class("tool-running")
        self._refresh_view()

    # 生命周期

    def finish(self, output: str, ok: bool, elapsed_ms: int | None) -> None:
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

    def _shown_output(self) -> str:
        text = self._output
        if len(text) <= self._output_limit:
            return text
        return text[: self._output_limit].rstrip() + "\n…（界面只显示前 %d 字符）" % self._output_limit

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
                for line in self._shown_output().splitlines() or [""]:
                    text.append("  %s\n" % line)
        text.rstrip()
        self.update(text)


def _fmt_elapsed(ms: int | None) -> str:
    if ms is None:
        return "历史"
    return "%d ms" % ms if ms < 1000 else "%.1f s" % (ms / 1000)


# ---------- 斜杠命令补全 ----------


@dataclass(frozen=True)
class CommandSpec:
    """一条斜杠命令：名字（带 /）、参数提示、说明。"""

    name: str
    usage: str
    description: str

    @property
    def label(self) -> str:
        return self.name if not self.usage else "%s %s" % (self.name, self.usage)


COMMANDS: list[CommandSpec] = [
    CommandSpec("/help", "", "打开帮助面板"),
    CommandSpec("/new", "", "新建会话（自动保存当前会话）"),
    CommandSpec("/resume", "", "历史会话列表：↑↓ 选择，Enter 恢复，Esc 取消"),
    CommandSpec("/save", "", "保存当前会话到 .agents/sessions"),
    CommandSpec("/rename", "<标题>", "给当前会话改名"),
    CommandSpec("/export", "<文件路径>", "把当前会话导出为 Markdown"),
    CommandSpec("/compact", "", "压缩上下文：归档原始记录 + 生成摘要"),
    CommandSpec("/clear", "", "清空界面（保留历史与上下文）"),
    CommandSpec("/tools", "", "列出已注册工具"),
    CommandSpec("/skills", "", "列出已发现技能"),
    CommandSpec("/mcp", "[reload]", "MCP 连接状态；reload 重新连接"),
    CommandSpec("/config", "", "显示生效配置（来自 config.ini）"),
    CommandSpec("/init", "", "生成 AGENTS.md 模板（已存在则不覆盖）"),
    CommandSpec("/model", "", "显示模型与接口"),
    CommandSpec("/cwd", "", "显示工作目录"),
    CommandSpec("/quit", "", "退出（自动保存会话）"),
]

COMMAND_MAP = {spec.name: spec for spec in COMMANDS}


class CommandMenu(Static):
    """输入 `/` 时弹出的命令引导面板（不抢焦点，方向键由输入框转交）。

    命令多的时候只显示选中项附近的一屏（默认 8 条），避免小终端被面板吃满。
    """

    VISIBLE_ROWS = 8

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._items: list[CommandSpec] = []
        self._index = 0
        self.display = False

    # ---------- 状态 ----------

    @property
    def active(self) -> bool:
        return self.display and bool(self._items)

    @property
    def selected(self) -> CommandSpec | None:
        if not self._items:
            return None
        return self._items[min(self._index, len(self._items) - 1)]

    # ---------- 操作 ----------

    def show_items(self, items: list[CommandSpec], index: int = 0) -> None:
        self._items = list(items)
        self._index = max(0, min(index, len(self._items) - 1))
        if not self._items:
            self.hide_menu()
            return
        self.display = True
        self.styles.height = min(len(self._items), self.VISIBLE_ROWS) + 1
        self._refresh_view()

    def move(self, delta: int) -> None:
        if not self._items:
            return
        self._index = (self._index + delta) % len(self._items)
        self._refresh_view()

    def hide_menu(self) -> None:
        self._items = []
        self._index = 0
        self.display = False

    # ---------- 渲染 ----------

    def _window(self) -> tuple[int, int]:
        total = len(self._items)
        rows = min(total, self.VISIBLE_ROWS)
        if total <= rows:
            return 0, total
        start = max(0, min(self._index - rows // 2, total - rows))
        return start, start + rows

    def _refresh_view(self) -> None:
        start, end = self._window()
        text = Text()
        text.append(" 命令 ", "bold")
        text.append("↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 关闭", "dim")
        if len(self._items) > (end - start):
            text.append("  %d/%d" % (self._index + 1, len(self._items)), "dim")
        text.append("\n")
        for position in range(start, end):
            spec = self._items[position]
            chosen = position == self._index
            text.append(" ▸ " if chosen else "   ", "bold cyan" if chosen else "dim")
            text.append("%-13s" % spec.label, "bold cyan" if chosen else "bold")
            text.append(" %s" % spec.description, "" if chosen else "dim")
            if position < end - 1:
                text.append("\n")
        self.update(text)


class PromptInput(Input):
    """输入框：先把按键交给补全面板，未被消费再走原生逻辑。"""

    def __init__(self, *args, key_hook=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._key_hook = key_hook

    async def _on_key(self, event) -> None:  # type: ignore[override]
        hook = self._key_hook
        if hook is not None:
            try:
                handled = hook(event.key)
            except Exception:
                handled = False
            if handled:
                event.stop()
                event.prevent_default()
                return
        await super()._on_key(event)


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
        title: str,
        turns: int,
        messages: int,
        skills: int,
        mcp: str,
        round_limit: int,
        busy: bool,
        context: str = "",
    ) -> None:
        text = Text()
        text.append("会话\n", "bold")
        rows = [
            ("标题", title or "（未命名）"),
            ("编号", session_id),
            ("模型", model),
            ("接口", base_url),
            ("目录", workspace),
            ("轮次", str(turns)),
            ("消息", str(messages)),
            ("上下文", context or "—"),
            ("技能", "%d 个（.agents/skills）" % skills if skills else "未发现"),
            ("MCP", mcp),
            ("上限", "%d 次模型调用/轮" % round_limit),
            ("状态", "推理中…" if busy else "就绪"),
        ]
        for key, value in rows:
            text.append("%-4s " % key, "dim")
            text.append("%s\n" % value)
        text.rstrip()
        self.update(text)


class MCPPanel(Static):
    """MCP 区域：每个 server 的连接状态 + 它提供的工具名。"""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._bindings: list = []

    def update_status(
        self,
        statuses,
        *,
        loading: bool = False,
        note: str = "",
        enabled: bool = True,
    ) -> None:
        text = Text()
        connected = sum(1 for item in statuses if item.connected)
        text.append("MCP", "bold")
        if not enabled:
            text.append(" 未启用\n", "dim")
            self.update(text)
            return
        text.append(" · %d/%d 已连接" % (connected, len(statuses)), "dim")
        if loading:
            text.append("  ⏳加载中", "yellow")
        text.append("\n")

        if not statuses:
            text.append("%s\n" % (note or "（.mcp.json 里没有可用 server）"), "dim")
            self.update(text)
            return

        for status in statuses:
            if status.connected:
                dot, style = "●", "bold green"
            elif status.loading:
                dot, style = "◐", "bold yellow"
            else:
                dot, style = "○", "bold red"
            name = status.name if len(status.name) <= 18 else status.name[:17] + "…"
            text.append("%s %s " % (dot, name), style)
            if status.connected:
                text.append("%d 工具\n" % status.tool_count, "dim")
            elif status.loading:
                text.append("连接中…\n", "yellow")
            else:
                text.append("未连接\n", "dim")
                reason = (status.error or "").strip().replace("\n", " ")
                if reason:
                    text.append("  %s\n" % (reason[:60] + ("…" if len(reason) > 60 else "")), "dim red")

        bindings = self._bindings
        if bindings:
            text.append("\n工具\n", "bold")
            current = None
            for binding in bindings:
                if binding.server != current:
                    current = binding.server
                    text.append("%s\n" % current, "dim")
                text.append("  • %s\n" % binding.remote_name, "cyan")
        text.rstrip()
        self.update(text)

    def set_bindings(self, bindings) -> None:
        self._bindings = list(bindings)


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


# ---------- 快捷键说明 ----------

SHORTCUTS = [
    ("Enter", "发送 / 执行选中的命令"),
    ("↑ ↓", "命令面板内选择"),
    ("Tab", "补全命令"),
    ("Esc", "关闭面板 / 中断生成 / 回输入框"),
    ("Ctrl+N", "新会话"),
    ("Ctrl+R", "历史会话（同 /resume）"),
    ("Ctrl+S", "保存会话（同 /save）"),
    ("Ctrl+L", "清屏"),
    ("Ctrl+B", "收起/展开侧栏"),
    ("F1", "帮助"),
    ("Ctrl+Q", "退出"),
]


# ---------- /help 弹窗 ----------


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
        text.append("• 输入 / 会弹出命令面板：↑↓ 选择，Tab 补全，Enter 执行。\n")
        text.append("• 需要查看/修改文件时会自动调用工具，工具卡片可点击展开。\n")
        text.append("• Esc 中断当前生成：已完成的问答仍会写入历史。\n")
        text.append("• 顶部「上下文 a / b」显示本轮占用；超过阈值会暂停提问，需 /compact。\n")
        text.append("• 当前目录有 AGENTS.md 时会在会话开始时注入一次，之后全程复用。\n\n")
        text.append("命令\n", "bold cyan")
        for spec in COMMANDS:
            text.append("%-16s" % spec.label, "bold yellow")
            text.append("  %s\n" % spec.description)
        text.append("\n快捷键\n", "bold cyan")
        for key, desc in SHORTCUTS:
            text.append("%-8s" % key, "bold yellow")
            text.append("  %s\n" % desc)
        return text


# ---------- /resume 会话列表 ----------


class SessionListScreen(ModalScreen):
    """历史会话：上下键选择，Enter 恢复，Esc 取消。"""

    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("f1", "cancel", "取消", show=False),
    ]

    def __init__(self, records, current_id: str = "") -> None:
        super().__init__()
        self._records = list(records)
        self._current_id = current_id

    def compose(self) -> ComposeResult:
        with Vertical(id="session-list-dialog") as dialog:
            dialog.border_title = "历史会话 · %d 个" % len(self._records)
            dialog.border_subtitle = "↑↓ 选择 · Enter 恢复 · Esc 取消"
            yield Static(self._header(), id="session-list-head")
            yield OptionList(id="session-options")
            yield Static(
                "提示：/new 与退出时都会自动保存当前会话，恢复不会丢东西。",
                id="session-list-hint",
            )

    def _header(self) -> Text:
        text = Text()
        text.append("选择要恢复的会话", "bold")
        text.append("（按最近保存时间排序）", "dim")
        return text

    def on_mount(self) -> None:
        options = self.query_one("#session-options", OptionList)
        options.clear_options()
        selected = 0
        for index, record in enumerate(self._records):
            label = Text()
            mark = "★ " if record.session_id == self._current_id else "  "
            label.append(mark, "bold yellow")
            label.append(record.session_id, "bold cyan")
            label.append("  %s" % record.title, "")
            label.append(
                "   %s · %d 条%s" % (record.updated or "?", record.messages, " · " + record.model if record.model else ""),
                "dim",
            )
            options.add_option(Option(label, id=record.session_id))
            if record.session_id == self._current_id:
                selected = index
        options.highlighted = selected
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---------- 配置缺失提示 ----------


class ConfigNoticeScreen(ModalScreen):
    """首次运行自动生成了 config.ini：告诉用户改哪里，改完要重启。"""

    BINDINGS = [
        Binding("escape", "quit_app", "退出"),
        Binding("q", "quit_app", "退出", show=False),
    ]

    def __init__(self, path: str, warnings: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._path = path
        self._warnings = warnings

    def compose(self) -> ComposeResult:
        with Vertical(id="config-dialog") as dialog:
            dialog.border_title = "需要配置"
            dialog.border_subtitle = "Esc / Enter 退出"
            yield Static(self._body(), id="config-body")
            with Horizontal(id="config-buttons"):
                yield Button("退出，去修改配置", id="config-quit", variant="primary")
                yield Button("仍然进入（只看看界面）", id="config-continue")

    def _body(self) -> Text:
        text = Text()
        text.append("首次运行，已为你生成一份配置模板\n\n", "bold")
        text.append("文件：", "dim")
        text.append("%s\n\n" % self._path)
        text.append("里面的模型与 API Key 都是 mock 值，直接运行无法正常工作。\n", "bold yellow")
        text.append("请修改下面这几项，然后**重新启动程序**：\n\n")
        for key in ("[llm] base_url", "[llm] api_key", "[llm] model", "[search] api_key", "[mcp] enabled"):
            text.append("  %s\n" % key, "bold cyan")
        text.append("\n其余参数（工具上限、上下文阈值、MCP 超时、UI 宽度…）都写在同一个文件里，")
        text.append("删掉任意一项就会退回内置默认值。\n")
        if self._warnings:
            text.append("\n解析告警\n", "bold red")
            for line in self._warnings:
                text.append("  ! %s\n" % line, "red")
        return text

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-quit":
            self.dismiss(False)
        else:
            self.dismiss(True)

    def action_quit_app(self) -> None:
        self.dismiss(False)


# ---------- 压缩日志 ----------


class CompactLogScreen(ModalScreen):
    """压缩日志：左栏压缩前原文、右栏压缩结果，均可滚动。"""

    BINDINGS = [("escape", "dismiss", "关闭"), ("f1", "dismiss", "关闭")]

    def __init__(self, archive_id: str, messages: int, transcript: str, summary: str) -> None:
        super().__init__()
        self._archive_id = archive_id
        self._messages = messages
        self._transcript = transcript
        self._summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="compact-log"):
            with Horizontal(id="compact-log-head"):
                yield Static(
                    Text.assemble(
                        ("压缩日志", "bold"),
                        ("　归档 %s · %d 条消息" % (self._archive_id, self._messages), "dim"),
                    ),
                    id="compact-log-title",
                )
                yield Button("关闭 (Esc)", id="compact-log-close")
            with Horizontal(id="compact-log-body"):
                with VerticalScroll(id="compact-log-left"):
                    yield Static(self._transcript, id="compact-log-left-text")
                with VerticalScroll(id="compact-log-right"):
                    yield Static(self._summary, id="compact-log-right-text")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "compact-log-close":
            self.dismiss()
