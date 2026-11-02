"""组合根：把配置、模型客户端、工具、会话装配成一个 Agent。

前端只需要调用 create_agent(settings)，不关心内部对象怎么来的。
测试时可以传入自己的假 LLM。
"""

from __future__ import annotations

from pathlib import Path

from .agent import Agent
from .config import Settings
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
) -> Agent:
    """装配 Agent。

    project_root 决定「技能目录」和 `.mcp.json` 的位置，默认取启动时的 cwd
    （注意不是 settings.workspace：工作区是文件工具的沙箱范围，两者可以不同）。
    """
    client = llm or OpenAICompatibleClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=settings.request_timeout,
    )
    root = Path(project_root) if project_root is not None else Path.cwd()
    skills = discover_skills(root)
    mcp_manager = mcp if mcp is not None else create_mcp_manager(root)

    return Agent(
        llm=client,
        tools=build_default_registry(
            settings.workspace,
            max_read_chars=settings.max_read_chars,
            search_api_key=settings.search_api_key,
            skills=skills,
            extra_tools=mcp_manager.tools,
        ),
        conversation=Conversation(build_system_prompt(settings.system_prompt, skills)),
        skills=skills,
        mcp=mcp_manager,
        tool_round_limit=settings.tool_round_limit,
    )
