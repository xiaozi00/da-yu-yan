"""Agent 编排层。

职责：把「用户输入 → 模型流式输出 → 工具执行 → 结果回传 → 继续推理」
组织成一个可迭代的事件流。它不认识终端、窗口或网页。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Iterator

from .agents_md import AgentsDoc, load_agents_doc
from .context import (
    ARCHIVE_NOTE,
    COMPACT_PROMPT,
    DEFAULT_WINDOW,
    ContextStore,
    estimate_messages_tokens,
    new_archive_id,
    render_transcript,
)
from .conversation import Conversation
from .errors import GuLaduoError
from .events import (
    AgentEvent,
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
from .llm.port import LLMClient
from .skills import SkillRegistry
from .tools.registry import ToolRegistry

if TYPE_CHECKING:
    from .mcp.manager import MCPManager

# 压缩后塞进历史的那条消息的开头，明确告诉模型「这是摘要，不是用户新提的需求」
COMPACT_HEADER = (
    "以下是本会话此前的上下文摘要（由 /compact 生成，原始记录归档编号 %s），"
    "请把它当作已经发生的事实，继续完成其中的任务。\n\n"
)


@dataclass
class _PendingCall:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


def _clip(text: str, limit: int) -> str:
    """工具结果超过上限时截断，避免一条结果吃光上下文。"""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n（结果过长，已截断到 %d 字符）" % limit


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        conversation: Conversation | None = None,
        *,
        skills: SkillRegistry | None = None,
        mcp: "MCPManager | None" = None,
        context: ContextStore | None = None,
        context_window: int | None = None,
        tool_round_limit: int = 8,
        compact_threshold: float = 0.70,
        max_tool_result_chars: int = 8000,
        project_root: Path | str | None = None,
        agents_file: str = "AGENTS.md",
        agents_max_chars: int = 20000,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._conversation = conversation or Conversation()
        self._root = Path(project_root).resolve() if project_root else Path.cwd()
        self._skills = skills if skills is not None else SkillRegistry(root=self._root)
        self._mcp = mcp
        self._round_limit = max(1, int(tool_round_limit))
        self._threshold = min(max(float(compact_threshold), 0.1), 1.0)
        self._max_tool_result_chars = max(200, int(max_tool_result_chars))

        # AGENTS.md：会话开始时注入一次，之后全程复用历史里那条消息
        self._agents_file = agents_file
        self._agents_max_chars = agents_max_chars
        self._agents_doc: AgentsDoc | None = None

        # 上下文管理：归档目录默认跟技能同源（项目根）
        self._context = context or ContextStore(self._root / ".agents" / "context")
        self._window = context_window if context_window and context_window > 0 else None
        # 最近一次模型调用返回的 usage（None 表示要靠本地估算）
        self._api_tokens: int | None = None
        self._compacting = False

    # ---------- 启动注入 ----------

    def inject_agents_md(self) -> AgentsDoc | None:
        """把 AGENTS.md 作为一条固定消息插到 System Prompt 之后。

        只做一次：历史里已经有注入过的消息就直接返回 None。
        位置（index 1）与内容都固定，保证 prompt cache 的前缀稳定。
        """
        if self._conversation.has_agents_doc():
            return None
        doc = load_agents_doc(self._root, self._agents_file, self._agents_max_chars)
        if doc is None:
            return None
        self._conversation.insert_after_system(doc.as_message())
        self._agents_doc = doc
        return doc

    @property
    def agents_doc(self) -> AgentsDoc | None:
        return self._agents_doc

    def restore(self, messages: list[dict]) -> None:
        """/resume：整体恢复一份历史（缺 AGENTS.md 时补注入一次）。"""
        self._conversation.replace_all(messages)
        self._api_tokens = None
        if not self._conversation.has_agents_doc():
            self.inject_agents_md()

    # ---------- 只读状态 ----------

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def mcp(self):
        """MCP 连接管理器（未配置时为 None），只读用途。"""
        return self._mcp

    def close(self) -> None:
        """释放外部资源（目前只有 MCP 长连接）。"""
        if self._mcp is not None:
            self._mcp.close()
            self._mcp = None

    @property
    def skills(self) -> SkillRegistry:
        """初始化时发现的技能（只有 name / description，不含正文）。"""
        return self._skills

    @property
    def skill_names(self) -> list[str]:
        return self._skills.names

    @property
    def tool_names(self) -> list[str]:
        return self._tools.names

    @property
    def tools(self) -> list:
        """全部工具对象（含描述），供前端展示，不参与业务。"""
        return self._tools.tools

    def add_tools(self, tools) -> list[str]:
        """注册（或替换）一批外部工具，返回被更新的工具名。"""
        names: list[str] = []
        for tool in tools:
            self._tools.upsert(tool)
            names.append(tool.name)
        return names

    def reload_mcp(self) -> list[str]:
        """重新读取 `.mcp.json` 并重连，把新工具接进来。"""
        if self._mcp is None:
            return []
        return self.add_tools(self._mcp.reload())

    def apply_mcp_tools(self) -> list[str]:
        """把异步加载完成后新增的 MCP 工具注册进工具表。"""
        if self._mcp is None:
            return []
        return self.add_tools(self._mcp.take_new_tools())

    @property
    def mcp_loading(self) -> bool:
        return self._mcp is not None and self._mcp.loading

    @property
    def mcp_loaded(self) -> bool:
        return self._mcp is not None and self._mcp.loaded

    def reset(self) -> None:
        """新会话：清空历史后重新注入一次 AGENTS.md（改完文件无需重启就能生效）。"""
        self._conversation.clear()
        self._api_tokens = None
        self._agents_doc = None
        self.inject_agents_md()

    # ---------- 上下文状态 ----------

    @property
    def context_store(self) -> ContextStore:
        return self._context

    @property
    def context_window(self) -> int:
        """模型上下文窗口；没配置就用默认 128k。"""
        return self._window or DEFAULT_WINDOW

    @property
    def context_window_is_default(self) -> bool:
        return self._window is None

    @property
    def compact_threshold(self) -> float:
        return self._threshold

    @property
    def context_tokens(self) -> int:
        """当前上下文占用：优先用模型返回的 usage，否则本地估算。"""
        if self._api_tokens is not None:
            return self._api_tokens
        return estimate_messages_tokens(self._conversation.messages, self._tools.schemas())

    @property
    def context_ratio(self) -> float:
        window = self.context_window
        return self.context_tokens / window if window else 0.0

    @property
    def needs_compact(self) -> bool:
        """达到告警阈值：此时应当拒绝新的普通提问，直到压缩完成。"""
        return self.context_ratio >= self._threshold

    @property
    def compacting(self) -> bool:
        return self._compacting

    def context_usage(self) -> ContextUsageChanged:
        tokens = self.context_tokens
        window = self.context_window
        ratio = tokens / window if window else 0.0
        return ContextUsageChanged(
            tokens=tokens,
            window=window,
            ratio=ratio,
            source="api" if self._api_tokens is not None else "estimate",
            default_window=self.context_window_is_default,
            needs_compact=ratio >= self._threshold,
        )

    def archived_contexts(self):
        """已归档的上下文列表，供前端展示。"""
        return self._context.archives()

    # ---------- 主流程 ----------

    def run(self, user_input: str) -> Iterator[AgentEvent]:
        text = (user_input or "").strip()
        if not text:
            return
        if self._compacting:
            yield AgentFailed("正在压缩上下文，请等压缩完成后再提问。")
            return

        # 达到阈值后拒绝新的普通提问（前端也可以提前拦，这里是不依赖前端的保险）
        if self.needs_compact:
            usage = self.context_usage()
            yield AgentFailed(
                "上下文已占用 %.1f%%（%d / %d tokens），请先执行 /compact 压缩后再提问。"
                % (usage.ratio * 100, usage.tokens, usage.window)
            )
            return

        yield TurnStarted(text)
        self._conversation.add_user(text)
        self._api_tokens = None  # 新消息进来，上一次调用的 usage 已过期
        yield self.context_usage()

        try:
            for _ in range(self._round_limit):
                content = ""
                pending: dict[int, _PendingCall] = {}
                usage: int | None = None

                for delta in self._llm.stream(self._conversation.messages, self._tools.schemas()):
                    if delta.usage_tokens:
                        usage = delta.usage_tokens
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

                if usage:
                    self._api_tokens = usage
                yield self.context_usage()

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
                    output = _clip(output, self._max_tool_result_chars)

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

    # ---------- 上下文压缩 ----------

    def compact(self) -> Iterator[AgentEvent]:
        """把当前历史归档到 `.agents/context/`，再用摘要替换对话历史。

        顺序是硬约束：先落盘、再压缩、最后替换。任何一步失败都保持原上下文不变，
        用户可以直接重试 /compact。
        """
        if self._compacting:
            yield CompactFailed("已有压缩任务在执行。")
            return

        body = self._conversation.body()
        if not body:
            yield CompactFailed("当前还没有可压缩的对话历史（只有 System Prompt）。")
            return

        self._compacting = True
        before = self.context_tokens
        archive_id = new_archive_id()
        try:
            yield CompactStarted(archive_id=archive_id, tokens=before)

            try:
                record = self._context.save(body, archive_id)
            except OSError as exc:
                yield CompactFailed("保存原始上下文失败，已取消压缩（原上下文保持不变）：%s" % exc)
                return

            try:
                summary = self._summarize(body)
            except Exception as exc:  # 模型 / 网络 / 解析问题都算压缩失败
                yield CompactFailed(
                    "生成摘要失败，原上下文保持不变（原始记录已归档为 %s）：%s"
                    % (record.archive_id, exc)
                )
                return

            summary = summary.strip()
            if not summary:
                yield CompactFailed(
                    "模型返回了空摘要，已取消压缩（原始记录已归档为 %s）。" % record.archive_id
                )
                return

            note = ARCHIVE_NOTE.format(
                time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                archive_id=record.archive_id,
            )
            # 摘要持久化：重启程序后新会话仍能通过 system prompt 里的归档清单知道聊过什么
            try:
                self._context.save_summary(record.archive_id, summary)
            except OSError:
                pass  # 摘要写盘失败不阻断压缩，归档 JSONL 里仍有完整原文
            self._conversation.replace_body(
                [
                    {
                        "role": "user",
                        "content": (COMPACT_HEADER % record.archive_id) + summary + "\n\n---\n" + note,
                    }
                ]
            )
            self._api_tokens = None
            after = self.context_tokens

            yield CompactFinished(
                archive_id=record.archive_id,
                path=str(record.path),
                messages=record.messages,
                before_tokens=before,
                after_tokens=after,
            )
            yield self.context_usage()
        finally:
            self._compacting = False

    def _summarize(self, body: list[dict]) -> str:
        """调用一次不带工具的模型请求，生成摘要。压缩期间不可能触发 Function Calling。"""
        prompt = [
            {"role": "system", "content": COMPACT_PROMPT},
            {
                "role": "user",
                "content": "以下是需要压缩的会话历史（共 %d 条消息）：\n\n%s"
                % (len(body), render_transcript(body)),
            },
        ]
        chunks: list[str] = []
        for delta in self._llm.stream(prompt, None):
            if delta.content:
                chunks.append(delta.content)
        return "".join(chunks)
