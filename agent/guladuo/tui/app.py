"""Textual 前端。

这一层只做四件事：
1. 收集用户输入与斜杠命令；
2. 消费 core 抛出的事件流；
3. 把事件渲染成消息气泡 / 工具卡片；
4. 维护右侧信息栏与底部状态条。

所有模型调用、工具执行都在子线程里跑，UI 线程不会被阻塞。
"""

from __future__ import annotations

import threading
from time import perf_counter
from typing import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Input, Static

from core.agent import Agent
from core.config import Settings
from core.events import (
    AgentFailed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)

from .widgets import (
    COMMANDS,
    HelpScreen,
    MessageBubble,
    SessionPanel,
    ToolCard,
    ToolsPanel,
    new_session_id,
)

INFO_HINTS = "Enter 发送   /help 命令   Esc 中断生成   Ctrl+B 侧栏   Ctrl+N 新会话"


class GuladoApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "guladuo"

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("ctrl+c", "quit", "退出", show=False),
        Binding("ctrl+l", "command_clear", "清屏"),
        Binding("ctrl+n", "new_session", "新会话"),
        Binding("ctrl+b", "toggle_sidebar", "侧栏"),
        Binding("f1", "help", "帮助"),
        Binding("escape", "escape", "中断 / 输入框", priority=True),
    ]

    def __init__(
        self,
        agent: Agent,
        settings: Settings,
        agent_factory: Callable[[], Agent] | None = None,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.settings = settings
        self._agent_factory = agent_factory
        self._busy = False
        self._stream_bubble: MessageBubble | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._cancel = threading.Event()

        # 统计
        self._session_id = new_session_id()
        self._turn_count = 0
        self._tool_calls = 0
        self._last_seconds: float | None = None

    # ---------- 布局 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static(id="infocard")
                with VerticalScroll(id="transcript"):
                    pass
                yield Input(
                    placeholder="输入问题后 Enter 发送；/help 查看命令；Esc 中断生成",
                    id="prompt",
                )
                yield Static(id="status")
            with Vertical(id="sidebar"):
                yield SessionPanel(id="session-panel")
                with VerticalScroll(id="tools-scroll"):
                    yield ToolsPanel(id="tools-panel")
        yield Static(id="footer-keys")

    def on_mount(self) -> None:
        self.sub_title = "%s · %s" % (self.settings.model, self.settings.workspace)
        self._update_infocard()
        self._refresh_panels()
        self.query_one("#footer-keys", Static).update(
            "  ".join(
                "[%s]%s[/] %s" % ("bold yellow", key, desc)
                for key, desc in [
                    ("^q", "退出"), ("^l", "清屏"), ("^n", "新会话"),
                    ("^b", "侧栏"), ("f1", "帮助"),
                ]
            )
        )
        self.query_one("#prompt", Input).focus()

    def _update_infocard(self) -> None:
        from rich.text import Text

        text = Text()
        text.append("guladuo", "bold")
        text.append("  ·  %d 工具  ·  %s\n" % (len(self.agent.tool_names), self.settings.model))
        text.append("工作目录 ", "dim")
        text.append("%s\n" % self.settings.workspace)
        text.append(INFO_HINTS, "dim")
        self.query_one("#infocard", Static).update(text)

    # ---------- 信息栏 / 状态条 ----------

    def _refresh_panels(self) -> None:
        self.query_one("#session-panel", SessionPanel).update_info(
            model=self.settings.model,
            base_url=self.settings.base_url,
            workspace=str(self.settings.workspace),
            session_id=self._session_id,
            turns=self._turn_count,
            messages=len(self.agent.conversation),
            round_limit=self.settings.tool_round_limit,
            busy=self._busy,
        )
        self.query_one("#tools-panel", ToolsPanel).update_tools(self.agent.tools)

    def _refresh_status(self) -> None:
        status = self.query_one("#status", Static)
        if self._busy:
            status.update("[yellow]●[/] 推理中  ·  Esc 中断")
            return
        parts = ["[green]●[/] 就绪", "%d 轮" % self._turn_count, self.settings.model]
        if self._tool_calls:
            parts.append("工具 %d 次" % self._tool_calls)
        if self._last_seconds is not None:
            parts.append("上轮 %.1fs" % self._last_seconds)
        status.update("  |  ".join(parts))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel.clear()
        self._refresh_status()
        self._refresh_panels()
        if not busy:
            self.query_one("#prompt", Input).focus()

    # ---------- 输入与命令 ----------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._busy:
            return
        event.input.value = ""

        if text.startswith("/"):
            self._dispatch_command(text)
            return

        self._add_bubble("user", text)
        self._set_busy(True)
        self._stream_bubble = None
        self._tool_cards.clear()
        self._turn_started_at = perf_counter()
        self._run_turn(text)

    def _dispatch_command(self, text: str) -> None:
        name = text.split()[0].lower()
        arg = text[len(name):].strip()
        if name == "/help":
            self.action_help()
        elif name == "/clear":
            self.action_command_clear()
        elif name == "/new":
            self.action_new_session()
        elif name == "/tools":
            lines = ["已注册 %d 个工具：" % len(self.agent.tool_names)]
            lines += ["• %s — %s" % (t.name, t.description.splitlines()[0]) for t in self.agent.tools]
            self._add_bubble("system", "\n".join(lines))
        elif name == "/cwd":
            self._add_bubble("system", "工作目录：%s" % self.settings.workspace)
        elif name == "/model":
            self._add_bubble(
                "system",
                "模型：%s\n接口：%s" % (self.settings.model, self.settings.base_url),
            )
        elif name == "/quit":
            self.exit()
        else:
            known = " ".join(cmd for cmd, _ in COMMANDS)
            self._add_bubble("error", "未知命令：%s\n可用命令：%s" % (name, known))

    def action_command_clear(self) -> None:
        """清空界面，但保留对话历史。"""
        if self._busy:
            return
        self.query_one("#transcript", VerticalScroll).remove_children()
        self._add_bubble("system", "界面已清空（历史保留），继续提问即可。")

    def action_new_session(self) -> None:
        """新会话：重置历史、界面与统计。"""
        if self._busy:
            self._note("请等本轮结束或先 Esc 中断，再新建会话。")
            return
        self.agent.reset()
        self._session_id = new_session_id()
        self._turn_count = 0
        self._tool_calls = 0
        self._last_seconds = None
        self.query_one("#transcript", VerticalScroll).remove_children()
        self._add_bubble("system", "已开始新会话（历史已重置）。")
        self._refresh_panels()
        self._refresh_status()

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.display = not sidebar.display

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_escape(self) -> None:
        if len(self.screen_stack) > 1:  # 先关掉 /help 这类弹窗
            self.pop_screen()
            return
        if self._busy:
            if not self._cancel.is_set():
                self._cancel.set()
                self._note("已请求中断，正在收尾…")
        else:
            self.query_one("#prompt", Input).focus()

    # ---------- 后台任务 ----------

    @work(thread=True, exclusive=True)
    def _run_turn(self, text: str) -> None:
        try:
            for event in self.agent.run(text):
                if self._cancel.is_set():
                    break  # 中断：停止消费事件流，已完成的工具/文本保留
                self.call_from_thread(self._handle_event, event)
        except Exception as exc:  # 兜底，避免线程静默死掉
            self.call_from_thread(self._handle_event, AgentFailed(str(exc)))
        finally:
            self.call_from_thread(self._finish_turn)

    # ---------- 事件渲染（始终在 UI 线程） ----------

    def _handle_event(self, event) -> None:
        if isinstance(event, TurnStarted):
            pass

        elif isinstance(event, TextDelta):
            if self._stream_bubble is None:
                self._stream_bubble = self._add_bubble("assistant", "")
            self._stream_bubble.append_text(event.text)
            self._scroll_bottom()

        elif isinstance(event, ToolCallStarted):
            card = ToolCard(event.name, event.arguments)
            self.query_one("#transcript", VerticalScroll).mount(card)
            self._tool_cards[event.call_id] = card
            self._scroll_bottom()

        elif isinstance(event, ToolCallFinished):
            self._tool_calls += 1
            card = self._tool_cards.get(event.call_id)
            if card is None:
                card = ToolCard(event.name, "{}")
                self.query_one("#transcript", VerticalScroll).mount(card)
            card.finish(event.output, event.ok, event.elapsed_ms)
            self._refresh_status()
            self._scroll_bottom()

        elif isinstance(event, TurnFinished):
            if self._stream_bubble is None:
                self._add_bubble("assistant", event.content or "（空回复）")
            self._stream_bubble = None

        elif isinstance(event, AgentFailed):
            self._add_bubble("error", event.message)
            self._stream_bubble = None

    def _finish_turn(self) -> None:
        cancelled = self._cancel.is_set()
        if self._busy and not cancelled and hasattr(self, "_turn_started_at"):
            self._last_seconds = perf_counter() - self._turn_started_at
            self._turn_count += 1
        if cancelled:
            self._note("已中断本轮。")
        self._stream_bubble = None
        self._tool_cards.clear()
        self._set_busy(False)
        self._scroll_bottom()

    # ---------- 小工具 ----------

    def _add_bubble(self, role: str, text: str) -> MessageBubble:
        transcript = self.query_one("#transcript", VerticalScroll)
        bubble = MessageBubble(role, text)
        transcript.mount(bubble)
        self._scroll_bottom()
        return bubble

    def _note(self, text: str) -> None:
        self._add_bubble("system", text)

    def _scroll_bottom(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)


def run(agent: Agent, settings: Settings, agent_factory: Callable[[], Agent] | None = None) -> None:
    GuladoApp(agent=agent, settings=settings, agent_factory=agent_factory).run()


def main(argv: list[str] | None = None) -> None:
    """TUI 自己的命令行入口（run.py 也会调用它）。"""
    import argparse

    from core.config import Settings
    from core.errors import GuLaduoError
    from core.factory import create_agent

    parser = argparse.ArgumentParser(prog="guladuo", description="guladuo TUI")
    parser.add_argument("--config", help="配置文件路径，默认使用项目内的 config.ini")
    parser.add_argument("--workspace", help="工作区目录，默认为当前目录")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(args.config, args.workspace)
    except GuLaduoError as exc:
        raise SystemExit("配置错误：%s" % exc) from exc

    run(create_agent(settings), settings)
