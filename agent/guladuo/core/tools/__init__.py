"""工具层统一出口：把系统工具、文件工具与联网工具组装进注册表。"""

from __future__ import annotations

from pathlib import Path

from ..skills import SkillRegistry
from .base import Tool, ToolResult
from .fetch import build_fetch_tool
from .files import FileTools
from .load_skill import build_load_skill_tool
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
    "build_default_registry",
]


def build_default_registry(
    workspace: str | Path,
    max_read_chars: int = 20000,
    search_api_key: str = "",
    skills: SkillRegistry | None = None,
) -> ToolRegistry:
    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry(build_system_tools(sandbox.root))
    registry.extend(FileTools(sandbox, max_read_chars=max_read_chars).tools())
    registry.register(build_search_tool(search_api_key))
    registry.register(build_fetch_tool())
    registry.register(build_runcli_tool(sandbox.root))
    registry.register(
        build_load_skill_tool(skills or SkillRegistry(root=sandbox.root))
    )
    return registry
