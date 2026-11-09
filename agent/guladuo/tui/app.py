"""Textual 前端。

这一层只做四件事：
1. 收集用户输入与斜杠命令；
2. 消费 core 抛出的事件流；
3. 把事件渲染成消息气泡 / 工具卡片；
4. 维护右侧信息栏（会话 / MCP / 工具）与底部状态条。

所有模型调用、工具执行都在子线程里跑，UI 线程不会被阻塞；
MCP 连接同样是异步的：界面先出现，连上以后再刷新侧栏。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Callable

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Static

from core.agent import Agent
from core.agents_md import AGENTS_TEMPLATE, is_agents_message
from core.config import Settings
from core.events import (
    AgentFailed,
    CompactFailed,
    CompactFinished,
    CompactStarted,
    ContextUsageChanged,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    TurnStarted,
)
from core.sessions import SessionStore, new_session_id

from .widgets import (
    COMMANDS,
    COMMAND_MAP,
    CommandMenu,
    CompactLogScreen,
    ConfigNoticeScreen,
    HelpScreen,
    MCPPanel,
    MessageBubble,
    PromptInput,
    SessionListScreen,
    SessionPanel,
    ToolCard,
    ToolsPanel,
    fmt_tokens,
)

INFO_HINTS = "Enter 发送   / 命令面板   Esc 中断   Ctrl+B 侧栏   F1 帮助"

AGENTS_INIT_TEMPLATE = """# AGENTS.md

> 本文件是给 AI Agent 的项目说明。程序启动时若当前目录存在 AGENTS.md，
> 会在会话开始时注入一次，之后全程复用（不会重复读取）。

## 项目概况

- 这是什么项目：
- 技术栈：
- 目录约定：

## 常用命令

```bash
# 安装依赖
# 运行
# 测试
```

## 约定与注意事项

- 代码风格：
- 不要改动的文件：
- 提交信息格式：
"""


class GuladoApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "guladuo"

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出", priority=True),
        Binding("ctrl+c", "quit", "退出", show=False),
        Binding("ctrl+s", "save_session", "保存"),
        Binding("ctrl+r", "resume_session", "历史"),
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
        session_store: SessionStore | None = None,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.settings = settings
        self._agent_factory = agent_factory
        self._busy = False
        self._stream_bubble: MessageBubble | None = None
        self._tool_cards: dict[str, ToolCard] = {}
        self._cancel = threading.Event()
        self._warned = False  # 阈值告警只弹一次，压缩/新会话后重置

        # 会话
        self._store = session_store or SessionStore(settings.session_dir, keep=settings.session_keep)
        self._session_id = new_session_id()
        self._session_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_title = ""

        # 统计
        self._turn_count = 0
        self._tool_calls = 0
        self._last_seconds: float | None = None

        # MCP 异步加载
        self._mcp_timer = None
        self._mcp_applied = agent.mcp is None

    # ---------- 布局 ----------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static(id="infocard")
                yield Static(id="context-bar")
                with VerticalScroll(id="transcript"):
                    pass
                yield CommandMenu(id="command-menu")
                yield PromptInput(
                    placeholder="输入问题后 Enter 发送；输入 / 查看命令；Esc 中断生成",
                    id="prompt",
                    key_hook=self._handle_prompt_key,
                )
                yield Static(id="status")
            with Vertical(id="sidebar"):
                yield SessionPanel(id="session-panel")
                with VerticalScroll(id="mcp-scroll"):
                    yield MCPPanel(id="mcp-panel")
                with VerticalScroll(id="tools-scroll"):
                    yield ToolsPanel(id="tools-panel")
        yield Static(id="footer-keys")

    def on_mount(self) -> None:
        self.sub_title = "%s · %s" % (self.settings.model, self.settings.workspace)
        self.query_one("#sidebar", Vertical).styles.width = self.settings.sidebar_width
        self._update_infocard()
        self._refresh_panels()
        self._refresh_context()
        self._refresh_status()
        self.query_one("#footer-keys", Static).update(
            "  ".join(
                "[%s]%s[/] %s" % ("bold yellow", key, desc)
                for key, desc in [
                    ("^q", "退出"), ("^n", "新会话"), ("^r", "历史"),
                    ("^s", "保存"), ("^b", "侧栏"), ("f1", "帮助"),
                ]
            )
        )
        self.query_one("#prompt", PromptInput).focus()

        if self.settings.created:
            self._show_config_notice()
        elif self.settings.warnings:
            self._add_bubble(
                "error",
                "config.ini 有 %d 处取值不合法，已改用默认值：\n%s"
                % (
                    len(self.settings.warnings),
                    "\n".join("• %s" % line for line in self.settings.warnings),
                ),
            )

        self._announce_agents()
        self._start_mcp_monitor()

    # ---------- 启动提示 ----------

    def _show_config_notice(self) -> None:
        def done(continue_anyway: bool | None) -> None:
            if not continue_anyway:
                self.exit()
                return
            self._add_bubble(
                "error",
                "当前使用 mock 配置运行，请求会失败。请修改 %s 后重启程序。"
                % self.settings.config_path,
            )

        self.push_screen(ConfigNoticeScreen(str(self.settings.config_path), self.settings.warnings), done)

    def _announce_agents(self) -> None:
        doc = self.agent.agents_doc
        if doc is None:
            return
        self._add_bubble(
            "system",
            "已注入 %s（%d 字符），本会话全程复用%s。"
            % (doc.path.name, len(doc.content), "，已截断" if doc.truncated else ""),
        )

    def _start_mcp_monitor(self) -> None:
        """MCP 异步加载：先让界面出来，连上以后动态刷新侧栏。"""
        mcp = self.agent.mcp
        if mcp is None:
            return
        if not mcp.loading:
            self._finish_mcp_load()
            return
        self._mcp_timer = self.set_interval(0.35, self._poll_mcp)

    def _poll_mcp(self) -> None:
        mcp = self.agent.mcp
        if mcp is None:
            self._stop_mcp_timer()
            return
        self._refresh_panels()
        if not mcp.loading:
            self._stop_mcp_timer()
            self._finish_mcp_load()

    def _stop_mcp_timer(self) -> None:
        if self._mcp_timer is not None:
            self._mcp_timer.stop()
            self._mcp_timer = None

    def _finish_mcp_load(self) -> None:
        if self._mcp_applied:
            return
        self._mcp_applied = True
        mcp = self.agent.mcp
        if mcp is None:
            return
        added = self.agent.apply_mcp_tools()
        self._refresh_panels()
        self._update_infocard()
        problems = mcp.diagnostics()
        connected = [s for s in mcp.server_status if s.connected]
        if connected:
            self._add_bubble(
                "system",
                "MCP 已就绪：%d 个 server / %d 个工具%s（侧栏 MCP 区域可查看）"
                % (
                    len(connected),
                    len(mcp.bindings),
                    "，新增 %d 个工具" % len(added) if added else "",
                ),
            )
        if problems:
            self._add_bubble(
                "error",
                "MCP 接入有问题：\n%s\n可用 /mcp 查看详情，或运行 python mcp_doctor.py 体检。"
                % "\n".join("• %s" % line for line in problems),
            )

    def on_unmount(self) -> None:
        """退出时保存会话并释放 MCP 长连接（否则后台线程会拖住进程退出）。"""
        try:
            self._save_session(silent=True)
        except Exception:
            pass
        try:
            self.agent.close()
        except Exception:
            pass

    def _update_infocard(self) -> None:
        text = Text()
        text.append("guladuo", "bold")
        text.append(
            "  ·  %d 工具  ·  %d 技能  ·  %s\n"
            % (len(self.agent.tool_names), len(self.agent.skills), self.settings.model)
        )
        text.append("工作目录 ", "dim")
        text.append("%s\n" % self.settings.workspace)
        text.append("配置 ", "dim")
        text.append("%s\n" % self.settings.config_path)
        text.append(INFO_HINTS, "dim")
        self.query_one("#infocard", Static).update(text)

    # ---------- 信息栏 / 状态条 ----------

    def _refresh_context(self, usage: ContextUsageChanged | None = None) -> None:
        """顶部醒目位置：当前上下文长度 / 模型窗口 / 占用百分比。"""
        usage = usage or self.agent.context_usage()
        percent = usage.ratio * 100
        if percent >= self.agent.compact_threshold * 100:
            style = "bold red"
        elif percent >= 50:
            style = "bold yellow"
        else:
            style = "bold green"

        text = Text()
        text.append(" 上下文 ", "dim")
        text.append(fmt_tokens(usage.tokens), "bold")
        text.append(" / ", "dim")
        text.append(fmt_tokens(usage.window), "bold")
        text.append("  ")
        text.append("%.1f%%" % percent, style)
        if usage.default_window:
            text.append("  默认", "dim")
        text.append("  %s" % ("usage" if usage.source == "api" else "估算"), "dim")
        if usage.needs_compact:
            text.append("   ⚠ 已超阈值，请执行 /compact", "bold red")
        self.query_one("#context-bar", Static).update(text)

    def _refresh_panels(self) -> None:
        mcp = self.agent.mcp
        mcp_info = "未启用" if mcp is None else mcp.summary()
        self.query_one("#session-panel", SessionPanel).update_info(
            model=self.settings.model,
            base_url=self.settings.base_url,
            workspace=str(self.settings.workspace),
            session_id=self._session_id,
            title=self._session_title,
            turns=self._turn_count,
            messages=len(self.agent.conversation),
            skills=len(self.agent.skills),
            mcp=mcp_info,
            round_limit=self.settings.tool_round_limit,
            busy=self._busy,
            context="%s / %s (%.1f%%)"
            % (
                fmt_tokens(self.agent.context_tokens),
                fmt_tokens(self.agent.context_window),
                self.agent.context_ratio * 100,
            ),
        )
        panel = self.query_one("#mcp-panel", MCPPanel)
        if mcp is None:
            panel.set_bindings([])
            panel.update_status([], enabled=False)
        else:
            panel.set_bindings(mcp.bindings)
            note = ""
            if mcp.config.error:
                note = "配置错误：%s" % mcp.config.error
            elif not mcp.config.exists:
                note = "未配置（%s 不存在）" % mcp.config.path
            elif mcp.loading:
                note = "连接中…（%d 个 server）" % len(mcp.config.servers)
            elif not mcp.config.servers:
                note = "没有可用的 server"
            panel.update_status(mcp.server_status, loading=mcp.loading, note=note)
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
        parts.append("上下文 %.1f%%" % (self.agent.context_ratio * 100))
        parts.append(self._session_id)
        status.update("  |  ".join(parts))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel.clear()
        self._refresh_status()
        self._refresh_panels()
        self._refresh_context()
        if not busy:
            self.query_one("#prompt", PromptInput).focus()

    # ---------- 命令补全 ----------

    def on_input_changed(self, event) -> None:
        value = event.value
        menu = self.query_one("#command-menu", CommandMenu)
        if not value.startswith("/") or " " in value:
            menu.hide_menu()
            return
        prefix = value.lower()
        items = [spec for spec in COMMANDS if spec.name.startswith(prefix)]
        if items:
            menu.show_items(items, 0)
        else:
            menu.hide_menu()

    def _handle_prompt_key(self, key: str) -> bool:
        """输入框里的按键先给补全面板；返回 True 表示已消费。"""
        menu = self.query_one("#command-menu", CommandMenu)
        if not menu.active:
            return False
        if key in ("up", "down"):
            menu.move(-1 if key == "up" else 1)
            return True
        if key == "tab":
            spec = menu.selected
            menu.hide_menu()
            if spec is not None:
                self._set_prompt(spec.name + (" " if spec.usage else ""))
            return True
        if key == "enter":
            spec = menu.selected
            menu.hide_menu()
            if spec is not None:
                self._set_prompt("")
                self._dispatch_command(spec.name)
            return True
        return False

    def _set_prompt(self, value: str) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.value = value
        prompt.cursor_position = len(value)
        prompt.focus()

    # ---------- 输入与命令 ----------

    def on_input_submitted(self, event: PromptInput.Submitted) -> None:
        text = event.value.strip()
        if not text or self._busy:
            return
        event.input.value = ""
        self.query_one("#command-menu", CommandMenu).hide_menu()

        if text.startswith("/"):
            self._dispatch_command(text)
            return

        # 超过阈值后不再接受普通提问，必须先压缩
        if self.agent.needs_compact:
            usage = self.agent.context_usage()
            self._add_bubble(
                "error",
                "上下文已占用 %.1f%%（%s / %s），已暂停接受新提问。\n请先执行 /compact 压缩上下文。"
                % (usage.ratio * 100, fmt_tokens(usage.tokens), fmt_tokens(usage.window)),
            )
            self.query_one("#prompt", PromptInput).focus()
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
        if name in ("/help", "/?"):
            self.action_help()
        elif name == "/clear":
            self.action_command_clear()
        elif name == "/new":
            self.action_new_session()
        elif name == "/resume":
            self.action_resume_session()
        elif name == "/save":
            self.action_save_session()
        elif name == "/rename":
            self._rename_session(arg)
        elif name == "/export":
            self._export_session(arg)
        elif name == "/tools":
            lines = ["已注册 %d 个工具：" % len(self.agent.tool_names)]
            lines += ["• %s — %s" % (t.name, t.description.splitlines()[0]) for t in self.agent.tools]
            self._add_bubble("system", "\n".join(lines))
        elif name == "/skills":
            self._show_skills()
        elif name == "/cwd":
            self._add_bubble("system", "工作目录：%s" % self.settings.workspace)
        elif name == "/config":
            self._show_config()
        elif name == "/init":
            self._init_agents_file()
        elif name == "/mcp":
            self._show_mcp(arg)
        elif name == "/compact":
            self._start_compact()
        elif name == "/model":
            self._add_bubble(
                "system",
                "模型：%s\n接口：%s" % (self.settings.model, self.settings.base_url),
            )
        elif name == "/quit":
            self.action_quit()
        else:
            known = " ".join(spec.name for spec in COMMANDS)
            self._add_bubble("error", "未知命令：%s\n可用命令：%s" % (name, known))

    # ---------- 信息类命令 ----------

    def _show_skills(self) -> None:
        registry = self.agent.skills
        if not len(registry):
            self._add_bubble("system", "未发现技能：%s 下没有合法的 SKILL.md" % (registry.root / ".agents" / "skills"))
            return
        lines = ["已发现 %d 个技能（正文用 loadSkill 按需加载）：" % len(registry)]
        for skill in registry:
            lines.append("• %s — %s" % (skill.name, skill.description))
        if registry.skipped:
            lines.append("")
            lines.append("跳过 %d 个目录：" % len(registry.skipped))
            for item in registry.skipped:
                lines.append("  - %s：%s" % (item.directory.name, item.reason))
        self._add_bubble("system", "\n".join(lines))

    def _show_config(self) -> None:
        lines = ["配置文件：%s" % self.settings.config_path]
        group = None
        for section, key, value in self.settings.describe():
            if section != group:
                group = section
                lines.append("")
                lines.append("[%s]" % section)
            lines.append("  %-22s %s" % (key, value))
        if self.settings.warnings:
            lines.append("")
            lines.append("解析告警：")
            lines += ["  ! %s" % line for line in self.settings.warnings]
        lines.append("")
        lines.append("修改后需重启程序生效（列表类参数除外）。")
        self._add_bubble("system", "\n".join(lines))

    def _init_agents_file(self) -> None:
        name = self.settings.agents_file
        path = Path(self.settings.project_root) / name
        if path.is_file():
            self._note("%s 已存在，未覆盖：%s" % (name, path))
            return
        try:
            path.write_text(AGENTS_INIT_TEMPLATE, encoding="utf-8")
        except OSError as exc:
            self._add_bubble("error", "创建 %s 失败：%s" % (name, exc))
            return
        self._note("已创建 %s，填好后 /new 或重启即可注入。\n%s" % (name, path))

    def _show_mcp(self, arg: str) -> None:
        """`/mcp` 看状态，`/mcp reload` 重读 .mcp.json 并重连。"""
        mcp = self.agent.mcp
        if mcp is None:
            self._note("未启用 MCP（config.ini 里 [mcp] enabled = false，或启动时没读到 .mcp.json）。")
            return

        if arg.lower() in {"reload", "refresh", "重连"}:
            self._note("正在重新读取 %s 并重连…" % mcp.config.path)
            self._mcp_applied = False
            names = self.agent.reload_mcp()
            self.agent.apply_mcp_tools()
            self._mcp_applied = True
            self._refresh_panels()
            self._note("已重新读取 %s，更新 %d 个工具。" % (mcp.config.path, len(names)))

        lines = [
            "MCP 配置：%s" % mcp.config.path,
            "状态：%s" % mcp.summary(),
        ]
        for status in mcp.server_status:
            if status.connected:
                mark = "✓ 已连接"
            elif status.loading:
                mark = "◐ 连接中"
            else:
                mark = "✗ 未连接"
            lines.append("  • %-14s %s  %d 工具  %s" % (status.name, mark, status.tool_count, status.url))
            if not status.connected and status.error:
                lines.append("      原因：%s" % status.error)
        for binding in mcp.bindings:
            lines.append("  - %s → %s/%s" % (binding.exposed_name, binding.server, binding.remote_name))
        for line in mcp.diagnostics():
            if "连接失败" in line:  # 已在上面按 server 展示过，不重复
                continue
            lines.append("  ! %s" % line)
        lines.append("提示：改完 .mcp.json 用 /mcp reload 重连，无需退出。")
        self._add_bubble("system", "\n".join(lines))

    # ---------- 会话 ----------

    def _has_user_content(self) -> bool:
        return any(
            message.get("role") == "user" and not is_agents_message(message)
            for message in self.agent.conversation.messages
        )

    def _save_session(self, silent: bool = False):
        if not self._has_user_content():
            if not silent:
                self._note("当前会话还没有内容，无需保存。")
            return None
        record = self._store.save(
            self._session_id,
            self.agent.conversation.messages,
            title=self._session_title,
            model=self.settings.model,
            tokens=self.agent.context_tokens,
            created=self._session_created,
        )
        if record is None:
            if not silent:
                self._add_bubble("error", "保存会话失败：无法写入 %s" % self._store.root)
            return None
        self._session_title = record.title
        self._session_created = record.created
        self._refresh_panels()
        if not silent:
            self._note("已保存会话 %s（%d 条消息）\n%s" % (record.session_id, record.messages, record.path))
        return record

    def action_save_session(self) -> None:
        self._save_session(silent=False)

    def action_new_session(self) -> None:
        """新会话：先保存旧会话，再重置历史、界面与统计。"""
        if self._busy:
            self._note("请等本轮结束或先 Esc 中断，再新建会话。")
            return
        saved = self._save_session(silent=True)
        self.agent.reset()  # 会重新注入一次 AGENTS.md
        self._session_id = new_session_id()
        self._session_created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._session_title = ""
        self._turn_count = 0
        self._tool_calls = 0
        self._last_seconds = None
        self._warned = False
        self._clear_transcript()
        self._add_bubble(
            "system",
            "已开始新会话 %s%s"
            % (
                self._session_id,
                ("（上一会话已保存为 %s）" % saved.session_id) if saved else "",
            ),
        )
        self._announce_agents()
        self._refresh_panels()
        self._refresh_status()
        self._refresh_context()

    def action_resume_session(self) -> None:
        if self._busy:
            self._note("请等本轮结束或先 Esc 中断，再打开历史会话。")
            return
        records = self._store.list(limit=50)
        if not records:
            self._note("还没有保存过任何会话。每次回答结束、/new、/save、退出时都会自动保存。")
            return
        self.push_screen(SessionListScreen(records, self._session_id), self._restore_session)

    def _restore_session(self, session_id: str | None) -> None:
        if not session_id or session_id == self._session_id:
            if session_id:
                self._note("已经在这个会话里了。")
            return
        record, messages = self._store.load(session_id)
        if record is None:
            self._add_bubble("error", "恢复失败：读不到会话 %s" % session_id)
            return

        # 先把当前会话存下来，切换不丢东西
        self._save_session(silent=True)

        self.agent.restore(messages)
        self._session_id = record.session_id
        self._session_title = record.title
        self._session_created = record.created or self._session_created
        self._turn_count = sum(
            1 for m in messages if m.get("role") == "user" and not is_agents_message(m)
        )
        self._tool_calls = 0
        self._last_seconds = None
        self._warned = False
        self._clear_transcript()
        self._replay(messages)
        self._note("已恢复会话 %s（%d 条消息，%s）" % (record.session_id, len(messages), record.updated))
        self._refresh_panels()
        self._refresh_status()
        self._refresh_context()

    def _replay(self, messages: list[dict]) -> None:
        """把一份历史渲染回界面（工具卡片用归档里的输出还原）。"""
        outputs = {
            str(m.get("tool_call_id")): str(m.get("content") or "")
            for m in messages
            if m.get("role") == "tool"
        }
        transcript = self.query_one("#transcript", VerticalScroll)
        for message in messages:
            role = message.get("role")
            if role == "system":
                continue
            if is_agents_message(message):
                self._add_bubble("system", "已载入 AGENTS.md（本次会话复用，不重复注入）")
                continue
            if role == "user":
                self._add_bubble("user", str(message.get("content") or ""))
            elif role == "assistant":
                content = str(message.get("content") or "")
                if content.strip():
                    self._add_bubble("assistant", content)
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    card = ToolCard(
                        str(function.get("name") or "?"),
                        str(function.get("arguments") or "{}"),
                        expanded=False,
                        output_limit=self.settings.tool_output_chars,
                    )
                    card.finish(
                        outputs.get(str(call.get("id")), "（历史记录里没有这条工具输出）"),
                        True,
                        None,
                    )
                    transcript.mount(card)
        self._scroll_bottom()

    def _rename_session(self, title: str) -> None:
        if not title:
            self._note("用法：/rename <新标题>")
            return
        self._session_title = title.strip()
        record = self._save_session(silent=True)
        if record is None:
            self._note("已改名为「%s」，但有内容后才会写入磁盘。" % self._session_title)
        else:
            self._note("会话已改名为「%s」。" % self._session_title)

    def _export_session(self, arg: str) -> None:
        if not arg:
            self._note("用法：/export <文件路径>（相对路径按工作目录解析）")
            return
        target = Path(arg).expanduser()
        if not target.is_absolute():
            target = Path(self.settings.workspace) / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self._render_markdown(), encoding="utf-8")
        except OSError as exc:
            self._add_bubble("error", "导出失败：%s" % exc)
            return
        self._note("已导出当前会话：%s" % target)

    def _render_markdown(self) -> str:
        lines = [
            "# guladuo 会话 %s" % self._session_id,
            "",
            "- 标题：%s" % (self._session_title or "（未命名）"),
            "- 模型：%s" % self.settings.model,
            "- 保存时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "---",
            "",
        ]
        for message in self.agent.conversation.messages:
            role = message.get("role")
            if role == "system":
                continue
            content = str(message.get("content") or "").strip()
            if is_agents_message(message):
                continue
            if role == "user":
                lines += ["## 你", "", content, ""]
            elif role == "assistant":
                if content:
                    lines += ["## guladuo", "", content, ""]
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    lines += [
                        "> 工具调用 `%s`%s" % (function.get("name"), function.get("arguments")),
                        "",
                    ]
            elif role == "tool":
                block = content if len(content) <= 800 else content[:800] + "\n…（已截断）"
                lines += ["```text", block, "```", ""]
        return "\n".join(lines)

    # ---------- 上下文压缩 ----------

    def _start_compact(self) -> None:
        if self._busy:
            self._note("请等本轮结束或先 Esc 中断，再执行 /compact。")
            return
        if len(self.agent.conversation) <= 1:
            self._note("当前只有 System Prompt，没有可压缩的对话历史。")
            return
        self._set_busy(True)
        self._run_compact()

    @work(thread=True, exclusive=True)
    def _run_compact(self) -> None:
        """压缩全程在子线程：先归档、再让模型写摘要、最后替换历史。"""
        try:
            for event in self.agent.compact():
                self.call_from_thread(self._handle_event, event)
        except Exception as exc:  # 兜底，避免线程静默死掉
            self.call_from_thread(self._handle_event, CompactFailed(str(exc)))
        finally:
            self.call_from_thread(self._finish_compact)

    def _finish_compact(self) -> None:
        self._set_busy(False)
        self._refresh_context()
        self._refresh_panels()
        self._scroll_bottom()

    def action_command_clear(self) -> None:
        """清空界面，但保留对话历史。"""
        if self._busy:
            return
        self._clear_transcript()
        self._add_bubble("system", "界面已清空（历史与上下文保留），继续提问即可。")

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Vertical)
        sidebar.display = not sidebar.display

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_quit(self) -> None:
        self._save_session(silent=True)
        self.exit()

    def action_escape(self) -> None:
        # 补全面板 / 弹窗优先关闭
        menu = self.query_one("#command-menu", CommandMenu)
        if menu.active:
            menu.hide_menu()
            return
        if len(self.screen_stack) > 1:  # 先关掉 /help、/resume 这类弹窗
            self.pop_screen()
            return
        if self._busy:
            if not self._cancel.is_set():
                self._cancel.set()
                self._note("已请求中断，正在收尾…")
        else:
            self.query_one("#prompt", PromptInput).focus()

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
            card = ToolCard(
                event.name,
                event.arguments,
                output_limit=self.settings.tool_output_chars,
            )
            self.query_one("#transcript", VerticalScroll).mount(card)
            self._tool_cards[event.call_id] = card
            self._scroll_bottom()

        elif isinstance(event, ToolCallFinished):
            self._tool_calls += 1
            card = self._tool_cards.get(event.call_id)
            if card is None:
                card = ToolCard(
                    event.name,
                    "{}",
                    output_limit=self.settings.tool_output_chars,
                )
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

        elif isinstance(event, ContextUsageChanged):
            self._apply_usage(event)

        elif isinstance(event, CompactStarted):
            self._add_bubble(
                "system",
                "开始压缩上下文：原始记录归档为 %s（当前 %s）…\n期间不调用任何工具。"
                % (event.archive_id, fmt_tokens(event.tokens)),
            )

        elif isinstance(event, CompactFinished):
            self._add_bubble(
                "system",
                "压缩完成：归档 %s（%d 条消息）\n上下文 %s → %s\n摘要已替换历史，可以继续提问。"
                % (
                    event.archive_id,
                    event.messages,
                    fmt_tokens(event.before_tokens),
                    fmt_tokens(event.after_tokens),
                ),
            )
            self._warned = False
            self._refresh_context()
            self._show_compact_log(event.archive_id, event.messages)
            self._save_session(silent=True)

        elif isinstance(event, CompactFailed):
            self._add_bubble(
                "error",
                "压缩失败：%s\n原上下文已保留，可以重试 /compact。" % event.message,
            )

    def _show_compact_log(self, archive_id: str, messages: int) -> None:
        """压缩完成后弹出双栏日志：左原文 / 右摘要，Esc 或按钮关闭。"""
        store = self.agent.context_store
        transcript = store.load_transcript(archive_id)
        summary = store.summary_of(archive_id) or "（该归档没有持久化摘要——可能是旧版本压缩产生的）"
        self.push_screen(CompactLogScreen(archive_id, messages, transcript, summary))

    def _apply_usage(self, event: ContextUsageChanged) -> None:
        self._refresh_context(event)
        if event.needs_compact and not self._warned:
            self._warned = True
            self._add_bubble(
                "error",
                "上下文已占用 %.1f%%（%s / %s），超过 %.0f%% 阈值。\n"
                "请执行 /compact 压缩上下文后再继续提问（压缩前原始记录会自动归档）。"
                % (
                    event.ratio * 100,
                    fmt_tokens(event.tokens),
                    fmt_tokens(event.window),
                    self.agent.compact_threshold * 100,
                ),
            )
        elif not event.needs_compact:
            self._warned = False

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
        if not cancelled:
            self._save_session(silent=True)  # 每轮结束顺手落盘，/resume 才不会少内容

    # ---------- 小工具 ----------

    def _add_bubble(self, role: str, text: str) -> MessageBubble:
        transcript = self.query_one("#transcript", VerticalScroll)
        bubble = MessageBubble(role, text)
        transcript.mount(bubble)
        self._scroll_bottom()
        return bubble

    def _note(self, text: str) -> None:
        self._add_bubble("system", text)

    def _clear_transcript(self) -> None:
        self.query_one("#transcript", VerticalScroll).remove_children()

    def _scroll_bottom(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)


def run(
    agent: Agent,
    settings: Settings,
    agent_factory: Callable[[], Agent] | None = None,
) -> None:
    GuladoApp(agent=agent, settings=settings, agent_factory=agent_factory).run()


def main(argv: list[str] | None = None) -> None:
    """TUI 自己的命令行入口（run.py 也会调用它）。"""
    import argparse

    from core.config import Settings
    from core.errors import GuLaduoError
    from core.factory import create_agent

    parser = argparse.ArgumentParser(prog="guladuo", description="guladuo TUI")
    parser.add_argument("--config", help="配置文件路径；默认依次找 ./config.ini、项目内 config.ini")
    parser.add_argument("--workspace", help="工作区目录，默认为当前目录")
    args = parser.parse_args(argv)

    try:
        settings = Settings.load(args.config, args.workspace)
    except GuLaduoError as exc:
        raise SystemExit("配置错误：%s" % exc) from exc

    run(create_agent(settings), settings)
