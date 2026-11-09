"""组合根：把配置、模型客户端、工具、会话装配成一个 Agent。

前端只需要调用 create_agent(settings)，不关心内部对象怎么来的。
测试时可以传入自己的假 LLM。

注意 `project_root`：技能目录 `.agents/skills`、MCP 配置 `.mcp.json`、
会话目录 `.agents/sessions`、`AGENTS.md` 都以它为根，默认取启动时的 cwd
（不是 settings.workspace —— 工作区只是文件工具的沙箱范围，两者可以不同）。
"""

from __future__ import annotations

from pathlib import Path

from .agent import Agent
from .config import Settings
from .context import ContextStore
from .conversation import Conversation
from .llm.openai_client import OpenAICompatibleClient
from .llm.port import LLMClient
from .mcp import MCPManager, create_mcp_manager
from .skills import build_system_prompt, discover_skills
from .tools import build_default_registry


def create_agent(
    settings: Settings,
    llm: LLMClient | None = None,
    *,
    project_root: str | Path | None = None,
    mcp: MCPManager | None = None,
    async_mcp: bool | None = None,
    on_mcp_ready=None,
) -> Agent:
    client = llm or OpenAICompatibleClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=settings.request_timeout,
    )
    root = Path(project_root) if project_root is not None else settings.project_root
    skills = discover_skills(
        root,
        max_frontmatter_bytes=settings.skill_max_frontmatter_bytes,
        max_description_chars=settings.skill_max_description_chars,
    )

    want_async = settings.mcp_async if async_mcp is None else async_mcp
    if mcp is not None:
        mcp_manager: MCPManager | None = mcp
    elif settings.mcp_enabled:
        # 异步：TUI 先渲染界面，连上以后由前端轮询刷新侧栏
        mcp_manager = create_mcp_manager(
            root,
            async_start=want_async,
            call_timeout=settings.mcp_call_timeout,
            connect_timeout=settings.mcp_connect_timeout,
            result_limit=settings.max_tool_result_chars,
            on_ready=on_mcp_ready,
        )
    else:
        mcp_manager = None

    context_store = ContextStore(root / ".agents" / "context", entry_limit=settings.context_entry_limit)

    # 历史归档清单注入 system prompt：重启后模型也知道过去聊过什么、细节找 recallContext
    system_prompt = build_system_prompt(settings.system_prompt, skills)
    digest = context_store.digest(
        max_archives=settings.digest_max_archives,
        summary_chars=settings.digest_summary_chars,
        topic_chars=settings.digest_topic_chars,
    )
    if digest:
        system_prompt = "%s\n\n%s" % (system_prompt, digest)

    agent = Agent(
        llm=client,
        tools=build_default_registry(
            settings,
            skills=skills,
            extra_tools=mcp_manager.tools if mcp_manager is not None else (),
            context_store=context_store,
        ),
        conversation=Conversation(system_prompt),
        skills=skills,
        mcp=mcp_manager,
        context=context_store,
        context_window=settings.context_window,
        tool_round_limit=settings.tool_round_limit,
        compact_threshold=settings.compact_threshold,
        max_tool_result_chars=settings.max_tool_result_chars,
        project_root=root,
        agents_file=settings.agents_file,
        agents_max_chars=settings.agents_max_chars,
    )

    # AGENTS.md：会话第一件事，插一条固定消息，之后全程复用
    agent.inject_agents_md()

    if mcp_manager is not None and not want_async:
        mcp_manager.take_new_tools()  # 同步模式下工具已随 extra_tools 注册，这里只清暂存

    return agent
