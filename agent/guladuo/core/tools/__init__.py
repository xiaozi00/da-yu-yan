"""工具层统一出口：把系统工具、文件工具与联网工具组装进注册表。"""

from __future__ import annotations

from typing import Iterable

from ..context import ContextStore
from ..skills import SkillRegistry
from .base import Tool, ToolResult
from .create_skill import build_create_skill_tool
from .fetch import build_fetch_tool
from .files import FileTools
from .load_skill import build_load_skill_tool
from .recall_context import build_recall_context_tool
from .registry import ToolRegistry
from .runcli import build_runcli_tool
from .sandbox import WorkspaceSandbox
from .search import build_search_tool
from .system import build_system_tools

__all__ = [
    "FileTools",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "WorkspaceSandbox",
    "build_system_tools",
    "build_search_tool",
    "build_fetch_tool",
    "build_runcli_tool",
    "build_load_skill_tool",
    "build_create_skill_tool",
    "build_recall_context_tool",
    "build_default_registry",
]


def build_default_registry(
    settings,
    *,
    skills: SkillRegistry | None = None,
    extra_tools: Iterable[Tool] = (),
    context_store: ContextStore | None = None,
) -> ToolRegistry:
    """按配置装配全部工具。

    `settings` 是 core.config.Settings —— 所有上限 / 超时都从它来，
    这里不出现任何魔法数字。
    """
    sandbox = WorkspaceSandbox(settings.workspace)
    registry = ToolRegistry(build_system_tools(sandbox.root))
    registry.extend(FileTools(sandbox, max_read_chars=settings.max_read_chars).tools())
    registry.register(build_search_tool(settings.search_api_key, timeout=settings.search_timeout))
    registry.register(
        build_fetch_tool(
            settings.fetch_timeout,
            default_chars=settings.fetch_max_chars,
            hard_limit=settings.fetch_hard_limit,
        )
    )
    registry.register(
        build_runcli_tool(
            sandbox.root,
            default_timeout=settings.runcli_timeout,
            default_output=settings.runcli_output_limit,
            hard_limit=settings.runcli_hard_limit,
        )
    )
    # 注意：SkillRegistry 定义了 __len__，空注册表是 falsy。
    # 不能写 `skills or SkillRegistry(...)`——那会为 loadSkill / createSkill
    # 各造一个新实例，createSkill 注册的新技能 loadSkill 看不见。
    skill_registry = skills if skills is not None else SkillRegistry(root=sandbox.root)
    registry.register(build_load_skill_tool(skill_registry))
    registry.register(build_create_skill_tool(skill_registry))
    # 历史上下文检索：归档目录与技能同源（项目根 = 启动时 cwd）
    store = context_store if context_store is not None else ContextStore(sandbox.root / ".agents" / "context")
    registry.register(
        build_recall_context_tool(store, limit=settings.recall_limit, max_chars=settings.recall_max_chars)
    )
    # MCP 工具（名称已带 mcp__server__tool 前缀，与本工具不会重名）
    registry.extend(extra_tools)
    return registry
